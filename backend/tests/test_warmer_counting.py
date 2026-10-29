"""Cache warmer progress counting, resumption and age enforcement (mocks only: no provider, no database).

Regression for the audit finding: after the first pass of a provider day, every later pass reported WARMING 0/446 forever
(resumed names were skipped without being counted), the per-day `done` flag let a cache age past MAX_AGE all day, and each
such pass spun 200 empty loops."""

from __future__ import annotations

import json

import pytest

import earnings_clock as ec
import local_warmer as lw

Q = [f"S{i:03d}" for i in range(446)]
H = 3600.0


@pytest.fixture
def run(monkeypatch):
    def _run(queue, ages, prev=None, day="2026-10-29", result="ok", dates=None):
        warmed, sleeps, summaries = [], [], []
        lw._state.clear()
        lw._state.update({"status": "IDLE"})
        lw._event_dates.clear()
        lw._event_dates.update(dates or {})
        monkeypatch.setattr(lw, "build_queue", lambda: (list(queue), len(queue)))
        monkeypatch.setattr(lw, "warm_ages", lambda syms: dict(ages))
        monkeypatch.setattr(lw, "load_state", lambda: json.loads(json.dumps(prev or {})))
        monkeypatch.setattr(lw, "save_state", lambda: None)
        monkeypatch.setattr(lw, "day_id", lambda now=None: day)
        monkeypatch.setattr(lw, "_sleep", lambda s: sleeps.append(s))
        monkeypatch.setattr(lw, "_calls", lambda: 0)
        monkeypatch.setattr(lw, "_plan", lambda: {"unlimited_daily": True, "max_tries_per_symbol": 3, "retry_delay_s": 0,
                                                  "provider_retry_s": 0, "hourly_cap_live_hours": 400, "hourly_cap_off_hours": 6000})
        monkeypatch.setattr(lw, "_provider_health", lambda: ("OK", ""))
        monkeypatch.setattr(lw, "_warm_one", lambda sym: (warmed.append(sym), result)[1])
        real = lw.summary
        monkeypatch.setattr(lw, "summary", lambda state=None: (summaries.append(1), real(state))[1])
        lw._run_pass()
        return dict(lw._state), warmed, len(summaries), sleeps
    return _run


def done_map(names=Q):
    return {s: {"s": "ok", "done": 1} for s in names}


def test_first_pass_warms_everything_and_completes(run):
    st, warmed, _, _ = run(Q, {}, {})
    assert len(warmed) == 446 and st["status"] == "COMPLETED" and st["complete"] == 446 and st["done"] == 446


def test_resumed_pass_with_every_name_done_reports_completed_not_warming_zero(run):
    st, warmed, n, _ = run(Q, {s: 3 * H for s in Q}, {"day_id": "2026-10-29", "symbols": done_map()})
    assert warmed == []
    assert st["status"] == "COMPLETED" and st["complete"] == 446 and st["skipped_fresh"] == 446 and st["done"] == 446
    assert st["pause_reason"] is None
    assert n - 1 == 1                                              # one pass, not the 201 empty passes of the defect


def test_partial_resume_counts_the_finished_names_and_warms_only_the_rest(run):
    prev = {"day_id": "2026-10-29", "symbols": done_map(Q[:300])}
    st, warmed, _, _ = run(Q, {s: 3 * H for s in Q[:300]}, prev)
    assert warmed == Q[300:]
    assert st["complete"] == 446 and st["status"] == "COMPLETED"


def test_the_per_day_done_flag_no_longer_lets_a_cache_age_past_the_limit(run):
    st, warmed, _, _ = run(Q, {s: 30 * H for s in Q}, {"day_id": "2026-10-29", "symbols": done_map()})
    assert len(warmed) == 446                                       # 30 h old > 20 h: refreshed although flagged done today
    assert st["status"] == "COMPLETED" and st["complete"] == 446 and st["skipped_fresh"] == 0


def test_a_name_warmed_in_this_run_is_not_warmed_twice(run):
    st, warmed, _, _ = run(Q[:5], {}, {})
    assert warmed == Q[:5] and len(set(warmed)) == 5


def test_new_provider_day_skips_young_caches_and_refreshes_old_ones(run):
    young = {s: 19 * H for s in Q}
    st, warmed, _, _ = run(Q, young, {"day_id": "2026-10-28", "symbols": done_map()}, day="2026-10-29")
    assert warmed == [] and st["complete"] == 446
    old = {s: 21 * H for s in Q}
    st, warmed, _, _ = run(Q, old, {"day_id": "2026-10-28", "symbols": done_map()}, day="2026-10-29")
    assert len(warmed) == 446


def test_names_reporting_within_two_days_are_refreshed_sooner(run):
    today = ec.et_today().isoformat()
    far = "2999-01-01"
    dates = {Q[0]: today, Q[1]: far}
    ages = {Q[0]: 15 * H, Q[1]: 15 * H}
    st, warmed, _, _ = run(Q[:2], ages, {}, dates=dates)
    assert warmed == [Q[0]]                                         # 15 h > 12 h for the imminent name; 15 h < 20 h for the far one
    assert st["complete"] == 2 and st["status"] == "COMPLETED"


def test_max_age_for_falls_back_when_the_policy_cannot_be_read(monkeypatch):
    import freshness_policy as fp
    monkeypatch.setattr(fp, "warm_max_age_seconds", lambda d: (_ for _ in ()).throw(RuntimeError("boom")))
    assert lw.max_age_for("X") == lw.MAX_AGE


def test_failed_and_partial_names_are_reported_not_hidden(run):
    st, warmed, _, _ = run(Q[:3], {}, {}, result="partial")
    assert st["status"] == "PARTIAL_DATA" and st["failed"] == 3 and st["complete"] == 0


def test_queue_empty_run_terminates(run):
    st, warmed, _, _ = run([], {}, {})
    assert warmed == [] and st["status"] == "COMPLETED"


@pytest.mark.parametrize("hour_utc,expected_day", [(23, "2026-10-29"), (0, "2026-10-30")])
def test_provider_day_flips_at_8pm_eastern(hour_utc, expected_day):
    from datetime import datetime, timezone, timedelta
    base = datetime(2026, 10, 29, 0, 0, tzinfo=timezone.utc)
    when = base + timedelta(hours=hour_utc) if hour_utc == 23 else base + timedelta(days=1)
    assert lw.day_id(when) == expected_day


# ---------------------------------------------------------------- the existing provider request caps still bind
class StopWarming(Exception):
    """Raised by the fake sleep the first time the warmer decides to WAIT, so a test can read the state at that moment."""


@pytest.fixture
def capped(monkeypatch):
    def _run(queue, ages, *, cap=None, health=("OK", ""), plan_extra=None, dates=None):
        warmed = []
        plan = {"unlimited_daily": True, "max_tries_per_symbol": 3, "retry_delay_s": 0, "provider_retry_s": 0,
                "hourly_cap_live_hours": cap if cap is not None else 10 ** 6, "hourly_cap_off_hours": cap if cap is not None else 10 ** 6}
        plan.update(plan_extra or {})
        calls = {"n": 0}

        def warm(sym):
            warmed.append(sym)
            calls["n"] += 1                                  # one provider request per warm
            return "ok"
        lw._state.clear()
        lw._state.update({"status": "IDLE"})
        lw._event_dates.clear()
        lw._event_dates.update(dates or {})
        monkeypatch.setattr(lw, "build_queue", lambda: (list(queue), len(queue)))
        monkeypatch.setattr(lw, "warm_ages", lambda syms: dict(ages))
        monkeypatch.setattr(lw, "load_state", lambda: {})
        monkeypatch.setattr(lw, "save_state", lambda: None)
        monkeypatch.setattr(lw, "day_id", lambda now=None: "2026-10-29")
        monkeypatch.setattr(lw, "_plan", lambda: plan)
        monkeypatch.setattr(lw, "_calls", lambda: calls["n"])
        monkeypatch.setattr(lw, "_provider_health", lambda: health)
        monkeypatch.setattr(lw, "_warm_one", warm)

        def sleep(seconds):                                  # the short pause after a successful warm is fine; any WAIT state ends the run
            if lw._state.get("status") != "WARMING":
                raise StopWarming()
        monkeypatch.setattr(lw, "_sleep", sleep)
        try:
            lw._run_pass()
        except StopWarming:
            pass
        return dict(lw._state), warmed
    return _run


def test_the_hourly_request_cap_still_stops_warming(capped):
    st, warmed = capped(Q[:20], {}, cap=5)
    assert len(warmed) == 5
    assert st["status"] == "THROTTLED" and "hourly cap reached (5 of 5" in st["pause_reason"]


def test_a_tighter_refresh_age_for_imminent_names_cannot_bypass_the_cap(capped):
    today = ec.et_today().isoformat()
    names = Q[:20]
    st, warmed = capped(names, {s: 15 * H for s in names}, cap=3, dates={s: today for s in names})
    assert len(warmed) == 3                                  # all 20 are due (15 h > 12 h) but only the cap's worth is requested
    assert st["status"] == "THROTTLED"


def test_a_429_cooldown_stops_all_warming(capped):
    st, warmed = capped(Q[:10], {}, health=("THROTTLED", "Unusual Whales answered 429; warming backs off for 30s"))
    assert warmed == [] and st["status"] == "THROTTLED"


def test_manual_pause_still_wins(capped, monkeypatch):
    monkeypatch.setenv("WARM_PAUSED", "1")
    st, warmed = capped(Q[:10], {})
    assert warmed == [] and st["status"] == "PAUSED_MANUALLY"


def test_a_capped_plan_still_stops_at_its_daily_limit(capped, monkeypatch):
    import unusualwhales_service as uw
    monkeypatch.setattr(uw, "budget", lambda: {"used_by_this_app": 13000, "daily_limit_known": True})
    st, warmed = capped(Q[:10], {}, plan_extra={"unlimited_daily": False, "daily_limit_if_capped": 13000})
    assert warmed == [] and st["status"] == "THROTTLED" and "daily quota used" in st["pause_reason"]


def test_no_extra_requests_when_everything_is_fresh(capped):
    st, warmed = capped(Q, {s: 2 * H for s in Q}, cap=1)
    assert warmed == [] and st["status"] == "COMPLETED"
