"""UW unlimited-plan policy: bounded 429 handling, 404 handling, live priority, and a warmer that completes.

No network and no database: the HTTP opener, the clock sleeps and the warm-state persistence are faked.
"""
from __future__ import annotations

import io
import json
import socket
import threading
import time
import urllib.error

import pytest

import local_warmer as lw
import unusualwhales_service as uw


# ----------------------------------------------------------------------------- client ----
class _Resp:
    def __init__(self, body=b'{"data": {"ok": 1}}'):
        self._b = body
        self.headers = {}

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http(code, body=b"{}", headers=None):
    return urllib.error.HTTPError("http://x", code, "err", headers or {}, io.BytesIO(body))


class _Opener:
    def __init__(self, script):
        self.script, self.calls = list(script), 0

    def open(self, request, timeout=None):
        self.calls += 1
        item = self.script.pop(0) if self.script else _Resp()
        item = item() if callable(item) else item
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(uw, "api_key", lambda: "k" * 40)
    monkeypatch.setattr(uw, "_backoff", lambda attempt, floor=0.0: 0.0)
    monkeypatch.setattr(uw, "MIN_INTERVAL", 0.0)
    monkeypatch.setattr(uw, "_blocked_until", 0.0)
    monkeypatch.setitem(uw._bg_cooldown, "until", 0.0)
    monkeypatch.setitem(uw._bg_cooldown, "level", 0)
    uw._metrics["errors_by_path"].clear()
    uw.set_background(False)
    yield
    uw.set_background(False)


def _use(monkeypatch, *script):
    op = _Opener(script)
    monkeypatch.setattr(uw, "_OPENER", op)
    return op


def test_200_ok(client, monkeypatch):
    op = _use(monkeypatch, _Resp())
    assert uw.get("/api/stock/AAPL/quote")["status"] == "OK" and op.calls == 1


def test_404_is_no_data_and_never_retried(client, monkeypatch):
    op = _use(monkeypatch, _http(404))
    assert uw.get("/api/stock/ZZZZ/dividends")["status"] == "NO_DATA"
    assert op.calls == 1
    assert uw._metrics["errors_by_path"]["404 /api/stock/ZZZZ/dividends"] == 1


def test_concurrency_429_retries_with_backoff_and_does_not_pause_the_app(client, monkeypatch):
    op = _use(monkeypatch, _http(429, b'{"message":"concurrent_limit_exceeded"}'), _Resp())
    assert uw.get("/api/stock/AAPL/quote")["status"] == "OK"
    assert op.calls == 2 and uw._blocked_until == 0.0


def test_persistent_429_is_bounded_foreground(client, monkeypatch):
    op = _use(monkeypatch, *[lambda: _http(429, b'{"message":"concurrent_limit_exceeded"}')] * 50)
    assert uw.get("/api/stock/AAPL/quote")["status"] == "RATE_LIMITED"
    assert op.calls == 3                       # 1 + 2 retries, never an endless loop
    assert uw._blocked_until == 0.0            # a concurrency 429 does not take the app off the air


def test_real_rate_429_pauses_with_retry_after(client, monkeypatch):
    _use(monkeypatch, *[lambda: _http(429, b'{"message":"rate"}', {"Retry-After": "7"})] * 3)
    out = uw.get("/api/stock/AAPL/quote")
    assert out["status"] == "RATE_LIMITED" and out["retry_in"] == 7
    assert uw._blocked_until > time.time() + 3


def test_background_429_is_bounded_then_backs_off_without_blocking_foreground(client, monkeypatch):
    op = _use(monkeypatch, *[lambda: _http(429, b'{"message":"concurrent_limit_exceeded"}')] * 50)
    uw.set_background(True)
    assert uw.get("/api/stock/AAPL/quote")["status"] == "RATE_LIMITED"
    assert op.calls == uw.BG_MAX_RETRIES + 1
    sent = op.calls
    assert uw.get("/api/stock/MSFT/quote")["status"] == "RATE_LIMITED" and op.calls == sent   # cooldown: nothing sent
    assert uw.background_cooldown_remaining() > 0 and uw._blocked_until == 0.0
    uw.set_background(False)                                   # foreground is untouched by the background cooldown
    op.script = [_Resp()]
    assert uw.get("/api/stock/MSFT/quote")["status"] == "OK"


def test_500_timeout_and_connection_failure_are_not_retried(client, monkeypatch):
    for exc in (_http(500), socket.timeout("timed out"), urllib.error.URLError("refused")):
        op = _use(monkeypatch, exc)
        assert uw.get("/api/stock/AAPL/quote")["status"] == "PROVIDER_OFFLINE"
        assert op.calls == 1


def test_never_more_than_three_in_flight_and_background_yields(client):
    cur = {"n": 0, "max": 0}
    lock = threading.Lock()

    def work(bg):
        with uw._slot(bg):
            with lock:
                cur["n"] += 1
                cur["max"] = max(cur["max"], cur["n"])
            time.sleep(0.02)
            with lock:
                cur["n"] -= 1

    ts = [threading.Thread(target=work, args=(i % 3 == 0,)) for i in range(18)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert cur["max"] <= uw.PROVIDER_CONCURRENCY == 3

    entered = threading.Event()
    uw._fg_waiting = 1                                          # a live request is waiting for a connection

    def bg():
        with uw._slot(True):
            entered.set()

    t = threading.Thread(target=bg)
    t.start()
    assert not entered.wait(0.3)                                # background stands aside
    uw._fg_waiting = 0
    assert entered.wait(2.0)
    t.join()


# ---------------------------------------------------------------------------- warmer ----
@pytest.fixture()
def warm(monkeypatch):
    state = {"saved": 0}
    monkeypatch.setattr(lw, "save_state", lambda: state.__setitem__("saved", state["saved"] + 1))
    monkeypatch.setattr(lw, "load_state", lambda: {"status": "NOT_STARTED"})
    monkeypatch.setattr(lw, "warm_ages", lambda q: {})
    monkeypatch.setattr(lw, "_sleep", lambda s: None)
    monkeypatch.setattr(lw, "_calls", lambda: 0)
    monkeypatch.setattr(lw, "_provider_health", lambda: ("OK", ""))
    monkeypatch.setattr(lw, "_hourly_gate", lambda: (True, "", 0.0))
    monkeypatch.setattr(lw, "_plan", lambda: {"unlimited_daily": True, "max_tries_per_symbol": 3, "retry_delay_s": 0, "provider_retry_s": 0})
    import db_cache
    monkeypatch.setattr(db_cache, "namespace", lambda: "t")
    monkeypatch.setattr(lw, "_state", {"status": "IDLE"})
    return state


def _queue(monkeypatch, names):
    monkeypatch.setattr(lw, "build_queue", lambda: (list(names), len(names)))


def test_unlimited_plan_has_no_daily_ceiling(monkeypatch):
    monkeypatch.setattr(lw, "_plan", lambda: {"unlimited_daily": True})
    monkeypatch.setattr(lw, "_state", {"day_id": lw.day_id(), "calls_day": 500_000})
    assert lw._daily_gate() == (True, "")


def test_capped_plan_still_stops_at_its_quota(monkeypatch):
    monkeypatch.setattr(lw, "_plan", lambda: {"unlimited_daily": False, "daily_limit_if_capped": 13000})
    monkeypatch.setattr(lw, "_state", {"day_id": lw.day_id(), "calls_day": 12000})
    ok, why = lw._daily_gate()
    assert not ok and "daily quota" in why


def test_all_names_complete(warm, monkeypatch):
    _queue(monkeypatch, [f"S{i}" for i in range(6)])
    monkeypatch.setattr(lw, "_warm_one", lambda s: "ok")
    lw._run_once()
    st = lw._state
    assert st["status"] == "COMPLETED" and st["complete"] == 6 and lw.summary()["pending"] == 0
    assert st["last_success_at"] and st["plan_unlimited"] is True


def test_missing_data_is_partial_never_warmed_and_the_job_continues(warm, monkeypatch):
    _queue(monkeypatch, ["A", "THIN", "B"])
    calls = {"THIN": 0}

    def one(s):
        if s == "THIN":
            calls["THIN"] += 1
            return "partial"
        return "ok"

    monkeypatch.setattr(lw, "_warm_one", one)
    lw._run_once()
    c = lw.summary()
    assert lw._state["status"] == "PARTIAL_DATA"
    assert c["complete"] == 2 and c["partial"] == 1 and c["pending"] == 0
    assert calls["THIN"] == 3                                   # bounded tries, then given up for the day


def test_an_exception_on_one_symbol_does_not_stop_the_job(warm, monkeypatch):
    _queue(monkeypatch, ["A", "BOOM", "C"])

    def one(s):
        if s == "BOOM":
            raise RuntimeError("bad payload")
        return "ok"

    monkeypatch.setattr(lw, "_warm_one", one)
    lw._run_once()
    c = lw.summary()
    assert c["complete"] == 2 and c["failed"] + c["partial"] == 1


def test_provider_throttling_does_not_burn_a_symbols_tries(warm, monkeypatch):
    _queue(monkeypatch, ["A", "B"])
    seen = {"n": 0}
    monkeypatch.setattr(lw, "_calls", lambda: 0)

    def one(s):
        seen["n"] += 1
        if seen["n"] == 1:
            state["hs"] = ("THROTTLED", "429")                    # the provider throttles DURING the first build
        return "partial" if seen["n"] <= 2 else "ok"

    state = {"hs": ("THROTTLED", "429")}

    def health():
        return state["hs"]

    monkeypatch.setattr(lw, "_provider_health", health)
    monkeypatch.setattr(lw, "_sleep", lambda s: state.__setitem__("hs", ("OK", "")))   # the throttle ends on the first wait
    lw._run_once()
    assert lw.summary()["complete"] == 2 and lw.summary()["partial"] == 0


def test_paused_manually_reports_its_reason_and_resumes(warm, monkeypatch):
    _queue(monkeypatch, ["A"])
    monkeypatch.setattr(lw, "_warm_one", lambda s: "ok")
    monkeypatch.setenv("WARM_PAUSED", "1")
    seen = []

    def sleep(s):
        seen.append((lw._state["status"], lw._state.get("pause_reason")))
        monkeypatch.delenv("WARM_PAUSED", raising=False)

    monkeypatch.setattr(lw, "_sleep", sleep)
    lw._run_once()
    assert seen[0][0] == "PAUSED_MANUALLY" and "WARM_PAUSED" in seen[0][1]
    assert lw._state["status"] == "COMPLETED"


def test_fresh_names_are_skipped_not_refetched(warm, monkeypatch):
    _queue(monkeypatch, ["A", "B"])
    monkeypatch.setattr(lw, "warm_ages", lambda q: {"A": 60.0})
    built = []
    monkeypatch.setattr(lw, "_warm_one", lambda s: built.append(s) or "ok")
    lw._run_once()
    assert built == ["B"] and lw._state["skipped_fresh"] == 1 and lw._state["complete"] == 2


def test_restart_resumes_retry_state(warm, monkeypatch):
    _queue(monkeypatch, ["A", "B"])
    monkeypatch.setattr(lw, "load_state", lambda: {"day_id": lw.day_id(), "calls_day": 10,
                                                   "symbols": {"A": {"s": "partial", "tries": 3}}})
    built = []
    monkeypatch.setattr(lw, "_warm_one", lambda s: built.append(s) or "ok")
    lw._run_once()
    assert built == ["B"]                                       # A was already given up on today: not retried


def test_hourly_gate_is_small_in_live_hours(monkeypatch):
    monkeypatch.setattr(lw, "_plan", lambda: {"hourly_cap_live_hours": 400, "hourly_cap_off_hours": 6000})
    monkeypatch.setattr(lw, "in_live_hours", lambda now=None: True)
    monkeypatch.setattr(lw, "_state", {"hour_id": time.strftime("%Y%m%d%H"), "hour_calls": 400})
    ok, why, wait = lw._hourly_gate()
    assert not ok and "market is open" in why and wait > 0
    monkeypatch.setattr(lw, "in_live_hours", lambda now=None: False)
    assert lw._hourly_gate()[0] is True


def test_a_permanently_failing_provider_cannot_cause_an_endless_loop(warm, monkeypatch):
    _queue(monkeypatch, ["A"])
    n = {"calls": 0}

    def one(s):
        n["calls"] += 1
        uw._metrics["net_errors"] = int(uw._metrics.get("net_errors", 0)) + 1     # every build hits a provider error
        return "partial"

    monkeypatch.setattr(lw, "_warm_one", one)
    lw._run_once()
    assert n["calls"] <= 6 + 3 + 1                                # 6 provider faults, then the normal bounded tries
    assert lw._state["status"] in ("PARTIAL_DATA", "WARMING") and lw.summary()["complete"] == 0
