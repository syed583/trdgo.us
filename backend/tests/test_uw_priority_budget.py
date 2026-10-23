"""Phase 2: Unusual Whales priority, shared budget, 429 handling, request de-duplication.
Nothing here touches the network or the production database."""
import concurrent.futures as cf
import io
import json
import threading
import time
import urllib.error
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import uw_budget as bud
import uw_priority as pri

ET = ZoneInfo("America/New_York")


def ts(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=ET).timestamp()


# =========================================================== the priority gate =====
def _hold(gate, prio, started, release, log):
    gate.acquire(prio)
    started.set()
    release.wait(3)
    gate.release(prio)


def test_higher_priority_is_served_first_when_a_slot_frees():
    gate = pri.PriorityGate({"slots": 1, "class_cap": {1: 1, 2: 1, 3: 1, 4: 1},
                             "max_wait_s": {1: 5, 2: 5, 3: 5, 4: 5}})
    started, release = threading.Event(), threading.Event()
    threading.Thread(target=_hold, args=(gate, pri.WARMER, started, release, None), daemon=True).start()
    assert started.wait(2)
    order, lock = [], threading.Lock()

    def waiter(prio):
        gate.acquire(prio)
        with lock:
            order.append(prio)
        time.sleep(0.02)
        gate.release(prio)

    ts_ = []
    for p in (pri.WARMER, pri.DASHBOARD, pri.LIVE):           # arrival order: worst first
        t = threading.Thread(target=waiter, args=(p,), daemon=True)
        t.start()
        ts_.append(t)
        time.sleep(0.05)
    release.set()
    for t in ts_:
        t.join(3)
    assert order[0] == pri.LIVE                               # the live request jumped the queue
    assert order == sorted(order)


def test_warmer_can_never_fill_the_gate_and_live_always_finds_a_slot():
    gate = pri.PriorityGate({"slots": 4, "class_cap": {1: 4, 2: 4, 3: 2, 4: 2},
                             "max_wait_s": {1: 5, 2: 5, 3: 0.2, 4: 0.2}})
    gate.acquire(pri.WARMER)
    gate.acquire(pri.WARMER)
    with pytest.raises(pri.Busy):                             # a third warmer request is refused ...
        gate.acquire(pri.WARMER)
    t0 = time.monotonic()
    gate.acquire(pri.LIVE)                                    # ... while a live one is granted at once
    assert time.monotonic() - t0 < 0.1


def test_queues_are_bounded_and_waits_are_timed():
    gate = pri.PriorityGate({"slots": 1, "class_cap": {1: 1, 2: 1, 3: 1, 4: 1},
                             "queue_limit": {1: 5, 2: 5, 3: 5, 4: 1},
                             "max_wait_s": {1: 5, 2: 5, 3: 5, 4: 0.3}})
    gate.acquire(pri.LIVE)                                    # the only slot is taken
    errs = []

    def late():
        try:
            gate.acquire(pri.WARMER)
        except pri.Busy as e:
            errs.append(e.reason)

    t = threading.Thread(target=late, daemon=True)
    t.start()
    time.sleep(0.05)
    with pytest.raises(pri.Busy, match="queue is full"):      # second waiter: queue limit 1
        gate.acquire(pri.WARMER)
    t.join(2)
    assert errs and "waited" in errs[0]                       # the first gave up after its max wait
    assert gate.stats[pri.WARMER]["busy"] == 2


def test_waiting_requests_age_upward_so_essential_work_is_not_starved():
    gate = pri.PriorityGate({"aging_s": 10.0})
    old_p3 = [pri.DASHBOARD, 1, 0.0]
    fresh_p2 = [pri.ESSENTIAL, 2, 100.0]
    assert gate._eff(old_p3, now=100.0) == 2                  # 100 s of waiting lifts P3 to its floor
    assert (gate._eff(old_p3, 100.0), old_p3[1]) < (gate._eff(fresh_p2, 100.0), fresh_p2[1])
    old_p4 = [pri.WARMER, 3, 0.0]
    assert gate._eff(old_p4, 1000.0) == 3                     # the warmer never ages past its floor (3)


def test_live_latency_stays_low_while_the_warmer_floods_the_gate():
    gate = pri.PriorityGate({"slots": 4, "class_cap": {1: 4, 2: 4, 3: 2, 4: 2},
                             "queue_limit": {1: 100, 2: 100, 3: 100, 4: 100},
                             "max_wait_s": {1: 10, 2: 10, 3: 10, 4: 10}})
    stop = threading.Event()
    waits = {pri.LIVE: [], pri.WARMER: []}
    lock = threading.Lock()

    def job(prio, hold):
        t0 = time.monotonic()
        try:
            gate.acquire(prio)
        except pri.Busy:
            return
        with lock:
            waits[prio].append(time.monotonic() - t0)
        time.sleep(hold)
        gate.release(prio)

    ws = [threading.Thread(target=job, args=(pri.WARMER, 0.05), daemon=True) for _ in range(60)]
    for t in ws:
        t.start()
    time.sleep(0.05)
    ls = []
    for _ in range(20):
        t = threading.Thread(target=job, args=(pri.LIVE, 0.01), daemon=True)
        t.start()
        ls.append(t)
        time.sleep(0.02)
    for t in ls + ws:
        t.join(15)
    assert len(waits[pri.LIVE]) == 20
    assert max(waits[pri.LIVE]) < 0.25                        # live never queued behind the warmer
    assert sum(waits[pri.WARMER]) / len(waits[pri.WARMER]) > sum(waits[pri.LIVE]) / 20


def test_priority_label_reaches_worker_threads():
    with pri.use(pri.LIVE, "intraday_engine"):
        with cf.ThreadPoolExecutor(max_workers=2) as ex:
            got = list(ex.map(lambda _: pri.current()[:2], range(4)))
    assert set(got) == {(pri.LIVE, "intraday_engine")}
    assert pri.current()[:2] == (pri.DASHBOARD, "dashboard")  # and it does not leak


def test_tagged_never_downgrades_a_stricter_caller():
    @pri.tagged(pri.WARMER, "warmer")
    def inner():
        return pri.current()[:2]

    with pri.use(pri.LIVE, "intraday_engine"):
        assert inner() == (pri.LIVE, "intraday_engine")
    assert inner() == (pri.WARMER, "warmer")


# ====================================================== the budget controller ======
SETTINGS = {"daily_limit": 100, "emergency_reserve": 10, "intraday_budget": 60, "essential_budget": 40, "feed_hourly_cap": 700, "other_budget": 15,
            "warmer_budget": 20, "warmer_hourly_cap": 8, "reset_hour_et": 20,
            "dashboard_reading_max_age_s": 6 * 3600, "provider_headers_max_age_s": 600,
            "cooldown_429_s": {3: 120, 4: 900}}


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def ctl(t=None):
    c = Clock(t or ts(2026, 10, 22, 10))
    return bud.Controller(SETTINGS, now=c, persist=False), c


def spend(c, prio, module, n, outcome="ok"):
    for _ in range(n):
        assert c.admit(prio, module).ok
        c.record_call(prio, module, outcome)


def test_unverified_quota_keeps_the_warmer_paused_but_never_blocks_live_trading():
    c, _ = ctl()
    assert c.quota_state() == "UNVERIFIED"
    d = c.admit(pri.WARMER, "warmer")
    assert not d.ok and d.code == "UNVERIFIED_QUOTA"
    assert c.admit(pri.LIVE, "intraday_engine").ok and c.admit(pri.ESSENTIAL, "ai_boards").ok


def test_dashboard_reading_verifies_and_the_estimate_adds_calls_since():
    c, clk = ctl()
    c.record_dashboard_usage(40)
    assert c.quota_state() == "VERIFIED_DASHBOARD" and c.used_total() == 40
    spend(c, pri.LIVE, "intraday_engine", 5)
    assert c.used_total() == 45                               # not just our own 5 calls
    assert c.remaining() == 55
    clk.t += 7 * 3600                                         # an old reading stops being trusted
    assert c.quota_state() == "UNVERIFIED"


def test_provider_headers_are_authoritative_over_everything():
    c, _ = ctl()
    c.record_dashboard_usage(10)
    c.note_provider(used=90, limit=100)
    assert c.quota_state() == "VERIFIED_PROVIDER" and c.used_total() == 90 and c.remaining() == 10


def test_warmer_cannot_exhaust_the_budget_intraday_is_owed():
    c, clk = ctl()
    c.record_dashboard_usage(0)
    n = 0
    for _hour in range(6):                                    # try for six clock hours
        while c.admit(pri.WARMER, "warmer").ok:
            c.record_call(pri.WARMER, "warmer", "ok")
            n += 1
        clk.t += 3600
    assert n == SETTINGS["warmer_budget"]                      # its daily budget (20); hourly cap (8) paced it
    assert c.remaining() >= SETTINGS["intraday_budget"] + SETTINGS["emergency_reserve"]
    spend(c, pri.LIVE, "intraday_engine", SETTINGS["intraday_budget"])      # intraday still gets all it is owed
    assert c.remaining() >= SETTINGS["emergency_reserve"]


def test_even_a_huge_warmer_budget_stops_where_intraday_protection_starts():
    big = dict(SETTINGS, warmer_budget=10_000, warmer_hourly_cap=10_000)
    clk = Clock(ts(2026, 10, 22, 10))
    c = bud.Controller(big, now=clk, persist=False)
    c.record_dashboard_usage(0)
    n = 0
    while c.admit(pri.WARMER, "warmer").ok:
        c.record_call(pri.WARMER, "warmer", "ok")
        n += 1
    # allowance 100 - reserve 10 - intraday budget 60 = 30 calls are the most the warmer may ever take
    assert n == 30
    assert c.admit(pri.LIVE, "intraday_engine").ok and c.admit(pri.ESSENTIAL, "ai_boards").ok


def test_emergency_reserve_belongs_to_the_live_engine_only():
    c, _ = ctl()
    c.record_dashboard_usage(92)                              # 8 left, reserve is 10
    assert c.admit(pri.LIVE, "intraday_engine").ok
    for p in (pri.ESSENTIAL, pri.DASHBOARD, pri.WARMER):
        d = c.admit(p, "x")
        assert not d.ok and d.code == "RESERVE", p
    c.record_dashboard_usage(100)
    assert c.admit(pri.LIVE, "intraday_engine").code == "EXHAUSTED"       # nothing left for anyone


def test_dashboards_yield_to_intraday_and_have_their_own_budget():
    c, _ = ctl()
    c.record_dashboard_usage(0)
    spend(c, pri.DASHBOARD, "market_pulse", 15)
    d = c.admit(pri.DASHBOARD, "market_pulse")
    assert not d.ok and d.code == "OTHER_BUDGET"
    c2, _ = ctl()
    c2.record_dashboard_usage(35)                             # remaining 65 <= reserve 10 + intraday 60
    assert c2.admit(pri.DASHBOARD, "x").code == "YIELD_TO_INTRADAY"
    assert c2.admit(pri.ESSENTIAL, "ai_boards").ok            # essential data is not held back


def test_429_blocks_and_cools_down_the_low_priorities_and_is_counted():
    c, clk = ctl()
    c.record_dashboard_usage(0)
    c.admit(pri.LIVE, "intraday_engine")
    c.record_call(pri.LIVE, "intraday_engine", "429")
    snap = c.snapshot()
    assert snap["modules"]["intraday_engine"]["r429"] == 1 and snap["api_health"] == "RATE_LIMITED"
    assert c.admit(pri.WARMER, "warmer").code == "COOLDOWN_429"
    assert c.admit(pri.DASHBOARD, "market_pulse").code == "COOLDOWN_429"
    assert c.admit(pri.LIVE, "intraday_engine").ok            # the client's own pause governs live calls
    clk.t += 1000                                             # cooldown over (warmer waits 15 min)
    assert c.admit(pri.WARMER, "warmer").ok


def test_failed_calls_timeouts_and_total_are_tracked_per_module():
    c, _ = ctl()
    spend(c, pri.ESSENTIAL, "ai_boards", 6)
    spend(c, pri.ESSENTIAL, "ai_boards", 2, outcome="timeout")
    spend(c, pri.DASHBOARD, "market_pulse", 3, outcome="error")
    m = c.snapshot()["modules"]
    assert m["ai_boards"]["calls"] == 8 and m["ai_boards"]["timeouts"] == 2 and m["ai_boards"]["failed"] == 2
    assert m["market_pulse"]["failed"] == 3 and c.snapshot()["local_calls"] == 11


def test_provider_reset_discards_counters_and_the_new_day_starts_unverified():
    c, clk = ctl(ts(2026, 10, 22, 19, 55))                    # five minutes before the 8 pm ET reset
    c.record_dashboard_usage(10)
    spend(c, pri.WARMER, "warmer", 3)
    day1 = c.snapshot()["day"]
    assert c.quota_state() == "VERIFIED_DASHBOARD"
    clk.t = ts(2026, 10, 22, 20, 1)
    snap = c.snapshot()
    assert snap["day"] != day1 and snap["local_calls"] == 0 and snap["budgets"]["warmer"]["used"] == 0
    # the warmer does NOT resume just because a local counter reset: the quota is unverified again
    assert snap["quota_state"] == "UNVERIFIED"
    assert c.admit(pri.WARMER, "warmer").code == "UNVERIFIED_QUOTA"
    assert c.next_reset_epoch() == ts(2026, 10, 23, 20)


def test_warmer_gate_reports_why_and_for_how_long():
    c, _ = ctl()
    ok, why, wait = c.warmer_gate()
    assert not ok and "UNVERIFIED_QUOTA" in why and wait > 0
    c.record_dashboard_usage(0)
    assert c.warmer_gate()[0] is True
    c.set_warmer_paused(True)
    assert "PAUSED" in c.warmer_gate()[1]


# ======================================== the client: memo, de-dup, 429, timeout =======
class FakeResp:
    def __init__(self, payload, headers=None):
        self._b = json.dumps(payload).encode()
        self.headers = headers or {}

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls = []
        self.lock = threading.Lock()

    def open(self, request, timeout=None):
        with self.lock:
            self.calls.append(request.full_url)
        return self.behaviour(request)


@pytest.fixture
def client(monkeypatch):
    import unusualwhales_service as uw
    monkeypatch.setattr(uw, "configured", lambda: True)
    monkeypatch.setattr(uw, "MIN_INTERVAL", 0.0)
    monkeypatch.setattr(uw, "_blocked_until", 0.0)
    uw._memo.clear()
    uw._req_flights.clear()
    c = bud.reset_for_tests(bud.Controller(SETTINGS, persist=False))
    c.record_dashboard_usage(0)
    opener = FakeOpener(lambda r: FakeResp({"data": [{"x": 1}]}))
    monkeypatch.setattr(uw, "_OPENER", opener)
    yield uw, c, opener
    uw._memo.clear()
    bud.reset_for_tests()


def test_identical_requests_from_any_module_share_one_provider_call(client):
    uw, c, opener = client
    with pri.use(pri.ESSENTIAL, "ai_boards"):
        a = uw.get("/api/stock/AAPL/options-volume")
    with pri.use(pri.DASHBOARD, "market_pulse"):
        b = uw.get("/api/stock/AAPL/options-volume")
    uw.get("/api/stock/MSFT/options-volume")                  # a different symbol is a different request
    assert a is b and len(opener.calls) == 2
    assert c.snapshot()["local_calls"] == 2


def test_slow_data_is_held_long_and_a_live_quote_is_not(client, monkeypatch):
    uw, c, opener = client
    uw.get("/api/insider/AAPL")
    uw.get("/api/insider/AAPL")                               # ownership data: reused
    uw.get("/api/stock/AAPL/quote")
    uw.get("/api/stock/AAPL/quote")                           # within the 10 s quote window
    assert len(opener.calls) == 2
    with uw._memo_lock:                                       # age every memo entry by a minute
        for k, (t, v) in list(uw._memo.items()):
            uw._memo[k] = (t - 60, v)
    uw.get("/api/stock/AAPL/quote")                           # the quote window has passed: refetched
    uw.get("/api/insider/AAPL")                               # insider data is still reused
    assert len(opener.calls) == 3


def test_concurrent_identical_requests_make_exactly_one_call(client):
    uw, c, opener = client
    slow = FakeOpener(lambda r: (time.sleep(0.3), FakeResp({"data": [1]}))[1])
    uw._OPENER = slow
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        res = list(ex.map(lambda _: uw.get("/api/stock/AAPL/flow-alerts"), range(8)))
    assert len(slow.calls) == 1 and all(r["status"] == "OK" for r in res)


def test_warmer_requests_are_refused_without_a_network_call_when_not_permitted(client):
    uw, c, opener = client
    c.record_call(pri.LIVE, "intraday_engine", "429")         # puts the warmer into cooldown
    tr = pri.Tracker()
    with pri.use(pri.WARMER, "warmer", tr):
        out = uw.get("/api/stock/AAPL/ohlc/1d")
    assert out["status"] == "BUDGET_THROTTLED" and out["code"] == "COOLDOWN_429" and out["data"] is None
    assert tr.denied == 1 and opener.calls == []              # nothing was sent, and the job knows it
    with pri.use(pri.LIVE, "intraday_engine"):
        assert uw.get("/api/stock/AAPL/ohlc/5m")["status"] == "OK"     # live trading still goes through


def test_http_429_is_not_bypassed_and_is_recorded(client, monkeypatch):
    uw, c, opener = client

    def boom(request):
        raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", {"Retry-After": "7"},
                                     io.BytesIO(b'{"message": "slow down"}'))

    uw._OPENER = FakeOpener(boom)
    monkeypatch.setattr(uw.time, "sleep", lambda s: None)
    with pri.use(pri.LIVE, "intraday_engine"):
        out = uw.get("/api/stock/AAPL/flow-alerts")
    assert out["status"] == "RATE_LIMITED" and out["data"] is None
    assert c.snapshot()["modules"]["intraday_engine"]["r429"] >= 1
    n = len(uw._OPENER.calls)
    with pri.use(pri.LIVE, "intraday_engine"):
        again = uw.get("/api/stock/AAPL/net-prem-ticks")      # while the provider's pause is active
    assert again["status"] == "RATE_LIMITED" and len(uw._OPENER.calls) == n    # no request is sent


def test_provider_timeout_is_a_clean_failure_not_a_hang(client):
    uw, c, opener = client

    def slow(request):
        raise TimeoutError("timed out")

    uw._OPENER = FakeOpener(slow)
    with pri.use(pri.ESSENTIAL, "ai_boards"):
        out = uw.get("/api/stock/AAPL/option-contracts")
    assert out["status"] == "PROVIDER_OFFLINE" and out["data"] is None
    assert c.snapshot()["modules"]["ai_boards"]["timeouts"] == 1


def test_busy_gate_returns_busy_quickly_for_low_priority(client, monkeypatch):
    uw, c, opener = client
    gate = pri.PriorityGate({"slots": 1, "class_cap": {1: 1, 2: 1, 3: 1, 4: 1},
                             "max_wait_s": {1: 5, 2: 5, 3: 5, 4: 0.2}})
    monkeypatch.setattr(uw._inflight, "gate", gate)
    gate.acquire(pri.LIVE)                                    # the wire is full of live work
    t0 = time.monotonic()
    with pri.use(pri.WARMER, "warmer"):
        out = uw.get("/api/stock/AAPL/ohlc/1d")
    assert out["status"] == "BUSY" and time.monotonic() - t0 < 1.0
    assert c.snapshot()["modules"]["warmer"]["busy"] == 1 and opener.calls == []
    gate.release(pri.LIVE)


def test_provider_daily_headers_feed_the_controller(client):
    uw, c, opener = client
    uw._OPENER = FakeOpener(lambda r: FakeResp({"data": []}, {"x-uw-daily-req-count": "8123",
                                                              "x-uw-token-req-limit": "15000"}))
    uw.get("/api/stock/AAPL/flow-alerts")
    assert c.quota_state() == "VERIFIED_PROVIDER" and c.used_total() == 8123 and c.allowance() == 15000


def test_essential_data_cannot_eat_the_share_the_live_engine_is_owed():
    c, _ = ctl()
    c.record_dashboard_usage(0)
    spend(c, pri.ESSENTIAL, "ai_boards", SETTINGS["essential_budget"])      # the essential share is used up
    d = c.admit(pri.ESSENTIAL, "ai_boards")
    assert not d.ok and d.code == "ESSENTIAL_BUDGET"
    assert c.admit(pri.LIVE, "intraday_engine").ok                         # the live engine is unaffected
    snap = c.snapshot()["budgets"]
    assert snap["essential"]["used"] == SETTINGS["essential_budget"] and snap["live_engine"]["used"] == 0


def test_module_hourly_counters_roll_over_with_the_clock_hour():
    c, clk = ctl()
    spend(c, pri.ESSENTIAL, "ai_boards", 5)
    assert c.module_hour_calls("ai_boards") == 5
    clk.t += 3600
    assert c.module_hour_calls("ai_boards") == 0


def test_live_requests_keep_flowing_through_the_real_client_while_the_warmer_floods_it(client, monkeypatch):
    """End to end through uw.get(): 60 warmer requests are in flight while live requests arrive."""
    uw, c, opener = client
    c.record_dashboard_usage(0)                                           # quota verified: the warmer may run
    uw._OPENER = FakeOpener(lambda r: (time.sleep(0.03), FakeResp({"data": [1]}))[1])
    results = {"live": [], "warm": []}
    lat = {"live": [], "warm": []}
    lock = threading.Lock()

    def request(kind, i):
        prio, mod = (pri.LIVE, "intraday_engine") if kind == "live" else (pri.WARMER, "warmer")
        t0 = time.monotonic()
        with pri.use(prio, mod):
            out = uw.get(f"/api/stock/S{i}/ohlc/5m" if kind == "live" else f"/api/stock/W{i}/ohlc/5m")
        with lock:
            results[kind].append(out["status"])
            lat[kind].append(time.monotonic() - t0)

    ts_ = [threading.Thread(target=request, args=("warm", i), daemon=True) for i in range(60)]
    for t in ts_:
        t.start()
    time.sleep(0.05)
    for i in range(15):
        t = threading.Thread(target=request, args=("live", i), daemon=True)
        t.start()
        ts_.append(t)
        time.sleep(0.02)
    for t in ts_:
        t.join(30)
    assert results["live"].count("OK") == 15                              # every live request was served
    assert max(lat["live"]) < 0.6                                         # ... promptly, not behind the warmer
    assert results["warm"].count("OK") + results["warm"].count("BUSY") + \
        results["warm"].count("BUDGET_THROTTLED") == 60                   # the warmer was served or told no -- cleanly
