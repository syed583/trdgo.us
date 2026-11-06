"""The provider allows THREE simultaneous requests per token (all machines together). These tests cover
the machine-wide cap, the gate's low-priority limit and adaptive brake, the 429 `concurrent_limit_exceeded`
handling (jittered backoff outside the slot, no app-wide pause), the market-hours caps on background work
and the 404 log. Everything is mocked: no API calls, no production database."""
import io
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import tab_settings as cfg
import unusualwhales_service as uw
import uw_budget as bud
import uw_priority as pri
import uw_slots

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ET = ZoneInfo("America/New_York")


# ----------------------------------------------------------- machine-wide slots ---
def test_slots_are_shared_by_separate_processes(tmp_path):
    """Six processes each want a slot for 0.4 s; at most three may hold one at any moment."""
    log = tmp_path / "log.txt"
    code = (
        "import sys, time\n"
        f"sys.path.insert(0, {BACKEND!r})\n"
        "import uw_slots\n"
        f"s = uw_slots.MachineSlots(3, {str(tmp_path / 'slots')!r})\n"
        "h = s.acquire(30)\n"
        "assert h is not None\n"
        f"open({str(log)!r}, 'a').write('S %f\\n' % time.time())\n"
        "time.sleep(0.4)\n"
        f"open({str(log)!r}, 'a').write('E %f\\n' % time.time())\n"
        "s.release(h)\n")
    procs = [subprocess.Popen([sys.executable, "-I", "-c", code]) for _ in range(6)]
    assert all(p.wait(timeout=60) == 0 for p in procs)
    events = sorted((float(line.split()[1]), 1 if line[0] == "S" else -1)
                    for line in log.read_text().splitlines())
    assert len(events) == 12
    held = peak = 0
    for _t, d in sorted(events, key=lambda e: (e[0], e[1])):          # an end sorts before a start at the same instant
        held += d
        peak = max(peak, held)
    assert peak <= 3, peak


def test_a_slot_is_released_when_its_holder_dies(tmp_path):
    d = str(tmp_path / "slots")
    code = (f"import sys, time; sys.path.insert(0, {BACKEND!r}); import uw_slots\n"
            f"s = uw_slots.MachineSlots(1, {d!r}); assert s.acquire(5) is not None; time.sleep(60)\n")
    p = subprocess.Popen([sys.executable, "-I", "-c", code])
    mine = uw_slots.MachineSlots(1, d)
    for _ in range(100):                                              # wait until the child holds the only slot
        h = mine.acquire(0.0)
        if h is None:
            break
        mine.release(h)
        time.sleep(0.05)
    assert mine.acquire(0.05) is None                                  # held: nothing free
    p.kill()
    p.wait(timeout=10)
    h = mine.acquire(5.0)
    assert h is not None                                               # the OS freed it with the process
    mine.release(h)


def test_threads_never_exceed_the_cap_through_the_gate_and_the_machine_slots(tmp_path):
    gate = pri.PriorityGate({"slots": 3, "class_cap": {1: 3, 2: 3, 3: 1, 4: 1}, "low_cap": 1,
                             "max_wait_s": {1: 30, 2: 30, 3: 30, 4: 30}})
    machine = uw_slots.MachineSlots(3, str(tmp_path / "slots"))
    now = peak = 0
    lock = threading.Lock()

    def work(prio):
        nonlocal now, peak
        gate.acquire(prio)
        h = machine.acquire(30)
        try:
            with lock:
                now += 1
                peak = max(peak, now)
            time.sleep(0.01)
            with lock:
                now -= 1
        finally:
            machine.release(h)
            gate.release(prio)

    ts = [threading.Thread(target=work, args=(1 + i % 4,)) for i in range(60)]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert peak <= 3 and gate.peak_inflight <= 3 and machine.stats["acquired"] == 60


def test_an_unusable_lock_directory_fails_open_instead_of_taking_the_app_off_the_air(tmp_path):
    blocker = tmp_path / "afile"
    blocker.write_text("x")
    s = uw_slots.MachineSlots(3, str(blocker / "sub"))                  # a directory below a FILE: cannot be made
    h = s.acquire(0.1)
    assert h is not None and s.stats["failopen"] == 1
    s.release(h)


# --------------------------------------------------------------------- the gate ---
def test_dashboards_and_the_warmer_together_never_hold_more_than_one_of_three_slots():
    g = pri.PriorityGate({"slots": 3, "class_cap": {1: 3, 2: 3, 3: 1, 4: 1}, "low_cap": 1})
    g.acquire(pri.DASHBOARD)
    with pytest.raises(pri.Busy):
        g2 = pri.PriorityGate({"slots": 3, "class_cap": {1: 3, 2: 3, 3: 1, 4: 1}, "low_cap": 1,
                               "max_wait_s": {1: .05, 2: .05, 3: .05, 4: .05}})
        g2.acquire(pri.DASHBOARD)
        g2.acquire(pri.WARMER)                                          # the one low-priority slot is taken
    for _ in range(3):
        g.release(pri.DASHBOARD) if g.inflight else None
    g3 = pri.PriorityGate({"slots": 3, "class_cap": {1: 3, 2: 3, 3: 1, 4: 1}, "low_cap": 1})
    g3.acquire(pri.WARMER)
    for p in (pri.LIVE, pri.ESSENTIAL):
        g3.acquire(p)                                                   # live and essential still get the other two
    assert g3.inflight == 3


def test_the_default_gate_for_three_slots_has_the_right_caps():
    c = uw._gate_config(3)
    assert c["slots"] == 3 and c["class_cap"] == {1: 3, 2: 3, 3: 1, 4: 1}
    assert uw._gate_config(8)["class_cap"][3] == 6


def test_the_adaptive_brake_takes_slots_away_after_a_concurrency_refusal_and_gives_them_back():
    t = [0.0]
    g = pri.PriorityGate({"slots": 3, "brake_hold_s": 60.0}, clock=lambda: t[0])
    assert g.effective_slots() == 3
    assert g.brake() == 1 and g.effective_slots() == 2
    assert g.brake() == 2 and g.effective_slots() == 1
    assert g.brake() == 2                                               # never below one slot
    t[0] = 61.0
    assert g.effective_slots() == 2                                     # one step back after a quiet minute
    t[0] = 125.0
    assert g.effective_slots() == 3


def test_max_inflight_follows_the_environment_and_never_exceeds_the_providers_three(monkeypatch):
    monkeypatch.delenv("UW_MAX_INFLIGHT", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    assert uw._max_inflight() == 3
    monkeypatch.setenv("APP_ENV", "development")
    assert uw._max_inflight() == 1                                      # a dev machine shares production's three
    monkeypatch.setenv("UW_MAX_INFLIGHT", "2")
    assert uw._max_inflight() == 2
    monkeypatch.setenv("UW_MAX_INFLIGHT", "9")
    assert uw._max_inflight() == 3
    monkeypatch.setenv("UW_MAX_INFLIGHT", "0")
    assert uw._max_inflight() == 1


# --------------------------------------------------------- 429 concurrent_limit ---
class Resp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()
        self.headers = {}

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def err429(message="concurrent_limit_exceeded", retry_after=None):
    h = {"Retry-After": str(retry_after)} if retry_after else {}
    return urllib.error.HTTPError("https://x/api", 429, "Too Many Requests", h,
                                  io.BytesIO(json.dumps({"message": message}).encode()))


class Script:
    """An opener that answers from a list: an Exception is raised, anything else is returned."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = 0

    def open(self, request, timeout=None):
        self.calls += 1
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return Resp(step)


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(uw, "configured", lambda: True)
    monkeypatch.setattr(uw, "MIN_INTERVAL", 0.0)
    monkeypatch.setattr(uw, "_blocked_until", 0.0)
    gate = pri.PriorityGate(uw._gate_config(3))
    monkeypatch.setattr(uw._inflight, "gate", gate)
    monkeypatch.setattr(uw._inflight, "machine", uw_slots.MachineSlots(3, str(tmp_path / "slots")))
    ctl = bud.reset_for_tests(bud.Controller(dict(cfg.UW_BUDGET), persist=False))
    sleeps = []
    monkeypatch.setattr(uw.time, "sleep", lambda s: sleeps.append((s, gate.inflight)))
    uw._memo.clear()
    uw._memo_wide.clear()
    uw._req_flights.clear()
    yield uw, ctl, gate, sleeps
    uw._memo.clear()
    bud.reset_for_tests()


def test_a_concurrency_429_backs_off_outside_the_slot_and_succeeds_without_pausing_the_app(client, monkeypatch):
    uw_, ctl, gate, sleeps = client
    op = Script([err429(), err429(), {"data": [1]}])
    monkeypatch.setattr(uw_, "_OPENER", op)
    with pri.use(pri.LIVE, "intraday_engine"):
        out = uw_.get("/api/stock/AAA/ohlc/5m")
    assert out["status"] == "OK" and op.calls == 3
    assert [round(s) for s, _ in sleeps if s > 0.1][:2] and all(inflight == 0 for _s, inflight in sleeps if _s > 0.1)
    assert uw_._blocked_until == 0.0                                    # no app-wide pause
    assert gate.effective_slots() == 1                                  # the brake took slots away
    snap = ctl.snapshot()
    assert snap["modules"]["intraday_engine"]["r429"] == 2


def test_a_concurrency_429_that_keeps_coming_returns_rate_limited_without_stalling_everything(client, monkeypatch):
    uw_, ctl, gate, sleeps = client
    op = Script([err429() for _ in range(10)])
    monkeypatch.setattr(uw_, "_OPENER", op)
    with pri.use(pri.DASHBOARD, "market_pulse"):
        out = uw_.get("/api/stock/AAA/ohlc/5m")
    assert out["status"] == "RATE_LIMITED" and op.calls == uw_._RETRIES[pri.DASHBOARD]
    assert uw_._blocked_until == 0.0


def test_a_real_rate_429_still_pauses_after_its_one_retry(client, monkeypatch):
    uw_, ctl, gate, sleeps = client
    op = Script([err429("Too many requests per minute", retry_after=3), err429("Too many requests per minute", retry_after=3)])
    monkeypatch.setattr(uw_, "_OPENER", op)
    with pri.use(pri.ESSENTIAL, "ai_boards"):
        out = uw_.get("/api/stock/AAA/quote")
    assert out["status"] == "RATE_LIMITED" and op.calls == 2
    assert uw_._blocked_until > 0.0                                     # the old behaviour is kept for a genuine rate


def test_a_concurrency_429_cools_the_low_priorities_briefly_not_for_a_quarter_of_an_hour():
    clock = [1_760_000_000.0]
    c = bud.Controller(dict(cfg.UW_BUDGET), now=lambda: clock[0], persist=False)
    c.record_call(pri.LIVE, "intraday_engine", "429c")
    assert c.admit(pri.WARMER, "warmer").code == "COOLDOWN_429"
    clock[0] += cfg.UW_BUDGET["cooldown_429c_s"][4] + 1
    assert c.admit(pri.WARMER, "warmer").code != "COOLDOWN_429"
    c.record_call(pri.LIVE, "intraday_engine", "429")                   # a real rate limit: the long cool-down
    clock[0] += 120
    assert c.admit(pri.WARMER, "warmer").code == "COOLDOWN_429"


def test_the_backoff_is_jittered_exponential_capped_and_never_below_retry_after():
    for attempt in range(8):
        step = min(4.0, 0.4 * 2 ** attempt)
        draws = [uw._backoff(attempt) for _ in range(200)]
        assert all(step / 2 <= d <= step for d in draws)
        assert len({round(d, 3) for d in draws}) > 20                   # genuinely random
    assert uw._backoff(0, retry_after=3.0) == 3.0
    assert uw._is_concurrency_429(b'{"message": "concurrent_limit_exceeded"}')
    assert not uw._is_concurrency_429(b'{"message": "rate limit"}')


# ------------------------------------------------------------ market-hours caps ---
def _ctl(now_et, **over):
    ts = [now_et.timestamp()]
    return bud.Controller(dict(cfg.UW_BUDGET, **over), now=lambda: ts[0], persist=False), ts


def test_in_session_is_the_us_regular_session_on_weekdays():
    for when, expect in ((datetime(2026, 10, 21, 9, 29, tzinfo=ET), False), (datetime(2026, 10, 21, 9, 30, tzinfo=ET), True),
                         (datetime(2026, 10, 21, 15, 59, tzinfo=ET), True), (datetime(2026, 10, 21, 16, 0, tzinfo=ET), False),
                         (datetime(2026, 10, 24, 11, 0, tzinfo=ET), False)):
        assert _ctl(when)[0].in_session() is expect


def test_the_warmer_stays_small_while_the_market_is_open_and_runs_wide_at_night():
    c, ts = _ctl(datetime(2026, 10, 21, 11, 0, tzinfo=ET), warmer_hourly_cap=3, warmer_offsession_hourly_cap=10)
    c.record_dashboard_usage(0)                                          # quota "verified" so only the cap decides
    for _ in range(3):
        assert c.admit(pri.WARMER, "warmer").ok
        c.record_call(pri.WARMER, "warmer", "ok")
    d = c.admit(pri.WARMER, "warmer")
    assert not d.ok and d.code == "HOURLY_CAP" and "market open" in d.reason
    c2, _ = _ctl(datetime(2026, 10, 21, 22, 0, tzinfo=ET), warmer_hourly_cap=3, warmer_offsession_hourly_cap=10)
    c2.record_dashboard_usage(0)
    for _ in range(9):
        assert c2.admit(pri.WARMER, "warmer").ok
        c2.record_call(pri.WARMER, "warmer", "ok")
    assert c2.admit(pri.WARMER, "warmer").ok                              # the 10th
    c2.record_call(pri.WARMER, "warmer", "ok")
    assert c2.admit(pri.WARMER, "warmer").code == "HOURLY_CAP"


def test_dashboards_are_capped_per_hour_only_while_the_market_is_open():
    c, _ = _ctl(datetime(2026, 10, 21, 11, 0, tzinfo=ET), other_session_hourly_cap=2)
    for _ in range(2):
        assert c.admit(pri.DASHBOARD, "market_pulse").ok
        c.record_call(pri.DASHBOARD, "market_pulse", "ok")
    d = c.admit(pri.DASHBOARD, "market_pulse")
    assert d.code == "OTHER_SESSION_CAP"
    assert c.admit(pri.LIVE, "intraday_engine").ok and c.admit(pri.ESSENTIAL, "ai_boards").ok   # live work is not capped
    c2, _ = _ctl(datetime(2026, 10, 21, 22, 0, tzinfo=ET), other_session_hourly_cap=2)
    for _ in range(5):
        c2.record_call(pri.DASHBOARD, "market_pulse", "ok")
    assert c2.admit(pri.DASHBOARD, "market_pulse").ok                     # night: no hourly cap


# ------------------------------------------------------------------ the 404 log ---
def test_404s_are_logged_by_endpoint_and_ticker_without_query_strings_and_stay_bounded(client, monkeypatch):
    uw_, ctl, gate, sleeps = client
    uw_._metrics.pop("no_data_by", None)
    nf = urllib.error.HTTPError("https://x", 404, "Not Found", {}, io.BytesIO(b'{"message":"not found"}'))
    monkeypatch.setattr(uw_, "_OPENER", Script([nf, urllib.error.HTTPError("https://x", 404, "Not Found", {}, io.BytesIO(b"{}")),
                                                 urllib.error.HTTPError("https://x", 404, "Not Found", {}, io.BytesIO(b"{}"))]))
    uw_.get("/api/insider/ZZZZ", {"limit": 5})
    uw_.get("/api/stock/ZZZZ/option-contracts", {"limit": 5})
    uw_.get("/api/stock/YYYY/option-contracts", {"limit": 5})
    top = dict(uw_.metrics()["no_data_top"])
    assert top["/api/insider/{T} | ZZZZ"] == 1
    assert top["/api/stock/{T}/option-contracts | ZZZZ"] == 1 and top["/api/stock/{T}/option-contracts | YYYY"] == 1
    assert not any("limit" in k or "?" in k for k in top)
    for i in range(700):
        uw_._note_404(f"/api/stock/S{i}/quote")
    assert len(uw_._metrics["no_data_by"]) <= uw_._NO_DATA_MAX


def test_the_admin_metrics_report_the_concurrency_picture(client):
    uw_, ctl, gate, sleeps = client
    c = uw_.metrics()["concurrency"]
    assert c["limit"] == 3 and {"effective_slots", "peak_in_flight", "concurrent_429", "machine_slots"} <= set(c)


def test_background_gets_one_connection_in_session_and_two_outside_it(client, monkeypatch):
    uw_, ctl, gate, sleeps = client
    monkeypatch.setattr(uw_, "_OPENER", Script([{"data": []}, {"data": []}]))
    monkeypatch.setattr(ctl, "in_session", lambda: True)
    with pri.use(pri.DASHBOARD, "market_pulse"):
        uw_.get("/api/stock/AAA/quote")
    assert gate.cfg["low_cap"] == 1
    monkeypatch.setattr(ctl, "in_session", lambda: False)
    with pri.use(pri.DASHBOARD, "market_pulse"):
        uw_.get("/api/stock/BBB/quote")
    assert gate.cfg["low_cap"] == 2


def test_the_admin_metrics_report_p99_waits_and_timeouts(client, monkeypatch):
    uw_, ctl, gate, sleeps = client
    uw_._metrics["lat"].clear()
    uw_._metrics["lat"].extend([100.0] * 98 + [900.0, 2000.0])
    m = uw_.metrics()
    assert m["p95_latency_ms"] == 100 and m["p99_latency_ms"] == 2000
    assert {"p95_wait_ms", "p99_wait_ms", "timeouts"} <= set(m)
    monkeypatch.setattr(uw_, "_OPENER", Script([TimeoutError("slow")]))
    with pri.use(pri.LIVE, "intraday_engine"):
        out = uw_.get("/api/stock/TOUT/quote")
    assert out["status"] == "PROVIDER_OFFLINE" and uw_.metrics()["timeouts"] >= 1


def test_background_slots_by_environment_session_and_how_many_connections_production_has(monkeypatch):
    in_s = [True]
    monkeypatch.setattr(uw._uwb, "get", lambda: type("C", (), {"in_session": staticmethod(lambda: in_s[0])})())
    monkeypatch.setenv("APP_ENV", "production")
    assert [uw._background_slots(n) for n in (3, 2, 1)] == [1, 0, 0]       # market open: 3 -> one; squeezed -> none
    in_s[0] = False
    assert [uw._background_slots(n) for n in (3, 2, 1)] == [2, 1, 1]       # outside the session: all but one
    monkeypatch.setenv("APP_ENV", "development")
    in_s[0] = True
    assert uw._background_slots(1) == 1                                    # a dev machine's pages must still load
