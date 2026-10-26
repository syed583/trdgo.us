"""The provider plan has no daily cap: no quota to verify and no warmer pause for it. What replaces the old
daily-quota logic: bounded queues, priorities, per-priority timeouts, background work that yields while live
requests queue, a per-module runaway guard and one health summary. Everything is mocked."""
import io
import json
import urllib.error

import pytest

import tab_settings as cfg
import unusualwhales_service as uw
import uw_budget as bud
import uw_health
import uw_priority as pri
import uw_slots

NOW = 1_760_000_000.0


def ctl(now=None, **over):
    clock = [NOW if now is None else now]
    return bud.Controller(dict(cfg.UW_BUDGET, **over), now=lambda: clock[0], persist=False), clock


def test_the_defaults_describe_an_unlimited_plan_not_a_15000_quota():
    b = cfg.UW_BUDGET
    assert b["plan_unlimited"] is True and b["daily_limit"] >= 1_000_000
    assert ctl()[0].quota_state() == "UNLIMITED"


def test_with_an_unlimited_plan_the_warmer_needs_no_verification():
    c, clock = ctl(now=1_760_000_000.0 + 3600 * 8)                      # outside the session on a weekday evening
    assert c.warmer_gate()[0] is True


def test_a_plan_that_does_have_a_cap_still_keeps_the_warmer_paused_until_it_is_verified():
    c, _ = ctl(plan_unlimited=False)
    ok, why, _wait = c.warmer_gate()
    assert not ok and "UNVERIFIED_QUOTA" in why
    c.record_dashboard_usage(0)
    assert c.quota_state() == "VERIFIED_DASHBOARD" and c.warmer_gate()[0]


def test_background_work_stands_aside_while_live_requests_queue_and_returns_when_they_stop():
    c, clock = ctl(plan_unlimited=True, yield_wait_s=2.0, yield_min_samples=5)
    for _ in range(4):
        c.note_wait(pri.LIVE, 9.0)
    assert c.admit(pri.DASHBOARD, "market_pulse").ok                     # too few samples: no judgement yet
    c.note_wait(pri.ESSENTIAL, 9.0)
    for p in (pri.DASHBOARD, pri.WARMER):
        d = c.admit(p, "x")
        assert not d.ok and d.code == "YIELD_TO_LIVE_QUEUE"
    assert c.admit(pri.LIVE, "intraday_engine").ok and c.admit(pri.ESSENTIAL, "ai_boards").ok
    assert c.snapshot()["background_yielding"] is True
    clock[0] += 61                                                       # the window passes with no new waits
    assert c.admit(pri.DASHBOARD, "market_pulse").ok and c.snapshot()["background_yielding"] is False


def test_short_waits_never_make_background_work_yield():
    c, _ = ctl(yield_wait_s=2.0)
    for _ in range(50):
        c.note_wait(pri.LIVE, 0.2)
    assert c.live_wait_p95() == 0.2 and c.admit(pri.DASHBOARD, "market_pulse").ok


def test_one_module_cannot_run_away_with_the_hour_but_live_work_and_other_modules_are_untouched():
    c, _ = ctl(module_hourly_runaway=5, other_session_hourly_cap=None)
    for _ in range(5):
        c.record_call(pri.DASHBOARD, "loop_gone_wrong", "ok")
    d = c.admit(pri.DASHBOARD, "loop_gone_wrong")
    assert not d.ok and d.code == "RUNAWAY_MODULE"
    assert c.admit(pri.DASHBOARD, "market_pulse").ok
    for _ in range(9):
        c.record_call(pri.LIVE, "intraday_engine", "ok")
    assert c.admit(pri.LIVE, "intraday_engine").ok                       # priority 1-2 are never runaway-blocked


# ------------------------------------------------------- the client: timeouts, waits ---
class Resp:
    headers = {}

    def read(self):
        return b'{"data": []}'

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Spy:
    def __init__(self):
        self.timeouts = []

    def open(self, request, timeout=None):
        self.timeouts.append(timeout)
        return Resp()


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(uw, "configured", lambda: True)
    monkeypatch.setattr(uw, "MIN_INTERVAL", 0.0)
    monkeypatch.setattr(uw, "_blocked_until", 0.0)
    monkeypatch.setattr(uw._inflight, "gate", pri.PriorityGate(uw._gate_config(3)))
    monkeypatch.setattr(uw._inflight, "machine", uw_slots.MachineSlots(3, str(tmp_path / "slots")))
    c = bud.reset_for_tests(bud.Controller(dict(cfg.UW_BUDGET), persist=False))
    uw._memo.clear()
    uw._memo_wide.clear()
    spy = Spy()
    monkeypatch.setattr(uw, "_OPENER", spy)
    yield uw, c, spy
    bud.reset_for_tests()


def test_live_and_essential_requests_get_a_short_timeout_and_background_a_longer_one(client):
    uw_, c, spy = client
    for prio, mod in ((pri.LIVE, "intraday_engine"), (pri.ESSENTIAL, "ai_boards"), (pri.DASHBOARD, "market_pulse")):
        with pri.use(prio, mod):
            uw_.get(f"/api/stock/T{prio}/ohlc/5m")
    assert spy.timeouts == [10.0, 10.0, 20.0]


def test_the_client_reports_live_and_essential_queue_waits_to_the_controller(client, monkeypatch):
    uw_, c, spy = client
    seen = []
    monkeypatch.setattr(c, "note_wait", lambda prio, w: seen.append((prio, w)))
    with pri.use(pri.LIVE, "intraday_engine"):
        uw_.get("/api/stock/AAA/ohlc/5m")
    with pri.use(pri.DASHBOARD, "market_pulse"):
        uw_.get("/api/stock/BBB/ohlc/5m")
    assert [p for p, _ in seen] == [pri.LIVE, pri.DASHBOARD] and all(w >= 0 for _, w in seen)


# --------------------------------------------------------------------- health ---
def test_health_is_ok_when_nothing_is_wrong_and_never_calls_the_provider(client):
    uw_, c, spy = client
    uw_._metrics.pop("c429_times", None)
    uw_._metrics["timeouts"] = 0
    uw_._metrics["err_times"].clear()
    uw_._metrics["lat"].clear()
    uw_._metrics["errors_by"].clear()
    h = uw_health.summary()
    assert h["status"] == "OK" and h["reasons"] == [] and spy.timeouts == []
    assert {"latency_ms", "queue_wait_ms", "errors", "queues", "concurrency", "background", "plan", "top_404"} <= set(h)
    json.dumps(h, default=str)


def test_health_degrades_and_goes_critical_for_the_right_reasons(client, monkeypatch):
    uw_, c, spy = client
    now = 1_760_000_000.0
    uw_._metrics["timeouts"] = 0
    uw_._metrics["err_times"].clear()
    uw_._metrics["lat"].clear()
    uw_._metrics["c429_times"] = __import__("collections").deque([now - 30], maxlen=200)
    h = uw_health.summary(now)
    assert h["status"] == "DEGRADED" and any("simultaneous" in r for r in h["reasons"])
    uw_._metrics["c429_times"].extend([now - 20] * 12)
    assert uw_health.summary(now)["status"] == "CRITICAL"
    uw_._metrics["c429_times"].clear()
    uw_._inflight.gate.brake()
    h = uw_health.summary(now)
    assert h["status"] == "DEGRADED" and any("brake" in r for r in h["reasons"])
    uw_._inflight.gate._brake = 0
    for _ in range(6):
        c.note_wait(pri.LIVE, 8.0)
    assert uw_health.summary()["status"] == "CRITICAL"


def test_health_is_critical_when_the_live_engines_hard_limit_is_reached(client):
    uw_, c, spy = client
    for _ in range(cfg.UW_BUDGET["live_hard_cap"]):
        c.state["groups"]["live"] += 1
    h = uw_health.summary()
    assert h["status"] == "CRITICAL" and any("live engine cannot call the API" in r for r in h["reasons"])


def test_the_health_route_returns_the_summary(client):
    import api_routes
    assert api_routes.uw_health_status()["status"] in ("OK", "DEGRADED", "CRITICAL")
