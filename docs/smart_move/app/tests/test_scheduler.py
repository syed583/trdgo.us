"""Scheduler decisions: stale calendar, closing-soonest priority, budgets, DST, missed-window alerts, kill switch, authorization."""
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_scheduler as S  # noqa: E402
from smvshadow import cutoff as co  # noqa: E402


def ev(s, d="2026-11-03", k="AMC"):
    return {"symbol": s, "earnings_date": d, "kind": k}


def win(d=date(2026, 11, 3), k="AMC"):
    p = co.plan_event(d, k)
    return p["window_start_utc"], p["cutoff_utc"]


def test_window_is_24h_and_computed_from_the_clock_across_the_dst_change():
    s, c = win()
    assert c - s == timedelta(hours=24)
    t = S.tick(s + timedelta(hours=12), [ev("AAPL")], set(), set(), S.Budget(), calendar_age_h=1.0, collection_enabled=True)
    assert t["open_windows"] == 1 and t["actions"][0]["action"] == "COLLECT" and t["execute"] is True
    # the same logic one week earlier, before the US clocks changed (EDT)
    s2, c2 = win(date(2026, 10, 27))
    assert c2 - s2 == timedelta(hours=24)
    assert s2.hour == 20 and s.hour == 21               # 16:00 ET is 20:00 UTC under EDT and 21:00 UTC under EST: the UTC time shifts, the ET rule does not


def test_stale_calendar_refreshes_first_and_blocks_collection():
    s, _ = win()
    t = S.tick(s + timedelta(hours=12), [ev("AAPL")], set(), set(), S.Budget(), calendar_age_h=9.0, collection_enabled=True)
    assert [a["action"] for a in t["actions"]] == ["REFRESH_CALENDAR"]


def test_budget_limits_the_batch_and_a_provider_stop_blocks_everything():
    s, _ = win()
    mid = s + timedelta(hours=12)
    evs = [ev("A"), ev("B"), ev("C")]
    t = S.tick(mid, evs, set(), set(), S.Budget(hourly_cap=S.SHARED_REQUESTS + 2 * S.REQUESTS_PER_STOCK), calendar_age_h=1.0, collection_enabled=True)
    a = t["actions"][0]
    assert len(a["symbols"]) == 2 and a["deferred"] == ["C"] and a["requests"] == S.SHARED_REQUESTS + 8
    t2 = S.tick(mid, evs, set(), set(), S.Budget(stopped=True), calendar_age_h=1.0, collection_enabled=True)
    assert t2["execute"] is False and any(x["type"] == "PROVIDER_STOPPED" for x in t2["alerts"]) and not any(x["action"] == "COLLECT" for x in t2["actions"])


def test_plan_only_unless_authorized_and_captured_events_are_not_repeated():
    s, _ = win()
    mid = s + timedelta(hours=12)
    t = S.tick(mid, [ev("A")], set(), set(), S.Budget(), calendar_age_h=1.0)
    assert t["execute"] is False and "blocked" in t["actions"][0] and t["kill_switch_engaged"] is True
    done = S.tick(mid, [ev("A")], {("A", "2026-11-03")}, set(), S.Budget(), calendar_age_h=1.0, collection_enabled=True)
    assert done["actions"] == [] and done["open_windows"] == 0


def test_missed_closing_and_unknown_timing_alerts():
    s, c = win()
    late = S.tick(c + timedelta(hours=1), [ev("A")], set(), set(), S.Budget(), calendar_age_h=1.0)
    assert late["missed"] == ["A"] and late["alerts"][0]["type"] == "MISSED_WINDOW"
    closing = S.tick(c - timedelta(minutes=60), [ev("A")], set(), set(), S.Budget(), calendar_age_h=1.0)
    assert any(x["type"] == "WINDOW_CLOSING" for x in closing["alerts"])
    unk = S.tick(s, [ev("U", k=None)], set(), set(), S.Budget(), calendar_age_h=1.0)
    assert unk["alerts"][0]["type"] == "UNKNOWN_TIMING"


def test_closing_soonest_goes_first_and_outcomes_only_when_due_and_enabled():
    sb, cb = win(date(2026, 11, 3), "BMO")                    # same earnings day: the before-open window closes first
    sa, ca = win(date(2026, 11, 3), "AMC")
    now = sa + timedelta(hours=3)
    assert sa < now < cb < ca
    t = S.tick(now, [ev("A", "2026-11-03", "AMC"), ev("B", "2026-11-03", "BMO")], set(), set(), S.Budget(), calendar_age_h=1.0, collection_enabled=True)
    assert t["actions"][0]["symbols"] == ["B", "A"]
    after = co._mc().session_close_utc(co.reaction_session(date(2026, 11, 2), "AMC")) + timedelta(minutes=31)
    o = S.tick(after, [ev("B", "2026-11-02")], {("B", "2026-11-02")}, set(), S.Budget(), calendar_age_h=1.0, collection_enabled=True, outcome_fetch_enabled=True)
    assert o["actions"][0] == {"action": "FETCH_OUTCOMES", "symbols": ["B"], "requests": 1}
    off = S.tick(after, [ev("B", "2026-11-02")], {("B", "2026-11-02")}, set(), S.Budget(), calendar_age_h=1.0, collection_enabled=True)
    assert off["actions"] == []


def test_daily_estimate_is_close_to_the_measured_full_run():
    assert abs(S.estimate_daily_requests(112) - 455) < 15       # the full-universe run used 455 attempts
