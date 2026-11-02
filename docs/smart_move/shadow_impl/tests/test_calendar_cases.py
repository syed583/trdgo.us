"""Regression tests (characterisation of the frozen cutoff rules): reschedule, BMO<->AMC change, weekend, holiday, early close and DST. No rule is changed."""
from datetime import date, datetime, timezone

from smvshadow import cutoff as co

UTC = timezone.utc


def test_rescheduled_event_gets_a_new_window_and_the_old_one_is_unchanged():
    old, new = co.plan_event(date(2026, 11, 3), "AMC"), co.plan_event(date(2026, 11, 5), "AMC")
    assert old["cutoff_utc"] == datetime(2026, 11, 2, 21, 0, tzinfo=UTC) and new["cutoff_utc"] == datetime(2026, 11, 4, 21, 0, tzinfo=UTC)
    assert new["window_start_utc"] > old["cutoff_utc"] and new["reaction_session"] == date(2026, 11, 6)


def test_bmo_to_amc_change_moves_release_cutoff_and_sessions_and_never_invents_a_clock_time():
    b, a = co.plan_event(date(2026, 11, 3), "BMO"), co.plan_event(date(2026, 11, 3), "AMC")
    assert b["basis"] == "NOMINAL_BMO" and a["basis"] == "NOMINAL_AMC"
    assert (a["assumed_release_utc"] - b["assumed_release_utc"]).total_seconds() == 10 * 3600           # 06:00 -> 16:00 ET, labelled assumptions
    assert b["base_session"] == date(2026, 11, 2) and b["reaction_session"] == date(2026, 11, 3)
    assert a["base_session"] == date(2026, 11, 3) and a["reaction_session"] == date(2026, 11, 4)


def test_unknown_timing_is_evidence_only_and_not_tradable():
    p = co.plan_event(date(2026, 11, 3), "UNKNOWN")
    assert p["evidence_only"] and not p["tradable"] and p["basis"] == "UNKNOWN_TIMING_USES_EARLIEST_NOMINAL" and p["reaction_session"] is None


def test_amc_on_the_day_before_a_holiday_reacts_after_it():
    p = co.plan_event(date(2026, 11, 25), "AMC")                       # Thanksgiving 2026-11-26 is closed
    assert p["base_session"] == date(2026, 11, 25) and p["reaction_session"] == date(2026, 11, 27)


def test_bmo_after_the_holiday_uses_the_last_session_before_it_as_reference():
    p = co.plan_event(date(2026, 11, 27), "BMO")
    assert p["base_session"] == date(2026, 11, 25) and p["reaction_session"] == date(2026, 11, 27)


def test_weekend_release_date_resolves_to_sessions_and_is_not_tradable():
    p = co.plan_event(date(2026, 11, 28), "AMC")                       # Saturday
    assert p["base_session"] == date(2026, 11, 27) and p["reaction_session"] == date(2026, 11, 30) and not p["tradable"]


def test_early_close_day_session_close_is_13_00_et():
    assert co._mc().session_close_utc(date(2026, 11, 27)) == datetime(2026, 11, 27, 18, 0, tzinfo=UTC)


def test_dst_changes_are_handled_in_et_and_the_window_is_always_24_absolute_hours():
    fall = co.plan_event(date(2026, 11, 2), "BMO")                     # cutoff Sunday 2026-11-01 06:00, after clocks went back
    assert fall["cutoff_et"].strftime("%H:%M %Z") == "06:00 EST"
    spring = co.plan_event(date(2027, 3, 15), "BMO")                   # DST starts 2027-03-14
    assert spring["assumed_release_et"].strftime("%H:%M") == "06:00" and (spring["assumed_release_utc"] - spring["cutoff_utc"]).total_seconds() == 24 * 3600
    assert spring["dst_between_cutoff_and_release"] is False
