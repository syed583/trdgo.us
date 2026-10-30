"""BMO/AMC cutoff handling, daylight-saving and holiday boundaries, freshness classes and point-in-time leakage prevention."""
from datetime import date, datetime, timedelta, timezone

import pytest

from smvshadow import cutoff as co
from smvshadow import freshness as fr

UTC = timezone.utc
ET = co.ET


def et(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=ET)


def test_bmo_after_dst_end_uses_standard_time():
    p = co.plan_event(date(2026, 11, 3), "BMO")                       # US clocks went back on 2026-11-01
    assert p["assumed_release_utc"] == datetime(2026, 11, 3, 11, 0, tzinfo=UTC) and p["cutoff_utc"] == datetime(2026, 11, 2, 11, 0, tzinfo=UTC)
    assert p["cutoff_et"].strftime("%H:%M %Z") == "06:00 EST" and p["window_start_utc"] == datetime(2026, 11, 1, 11, 0, tzinfo=UTC)
    assert p["basis"] == "NOMINAL_BMO" and p["last_usable_session"] == date(2026, 10, 30) and not p["evidence_only"]


def test_amc_cutoff_session_is_not_yet_usable_at_the_cutoff():
    p = co.plan_event(date(2026, 11, 3), "AMC")
    assert p["cutoff_utc"] == datetime(2026, 11, 2, 21, 0, tzinfo=UTC)
    assert p["last_usable_session"] == date(2026, 10, 30)             # Monday's close + 45 min is after the cutoff
    assert p["base_session"] == date(2026, 11, 3) and p["reaction_session"] == date(2026, 11, 4) and p["tradable"]


def test_monday_morning_event_cannot_be_traded_from_a_24h_earlier_cutoff():
    p = co.plan_event(date(2026, 11, 9), "BMO")                        # entry would be Friday's close, before a Sunday cutoff
    assert p["base_session"] == date(2026, 11, 6) and p["cutoff_et"].strftime("%a %H:%M") == "Sun 06:00" and not p["tradable"]
    assert p["last_usable_session"] == date(2026, 11, 6)


def test_thanksgiving_and_early_close_boundaries():
    bmo = co.plan_event(date(2026, 11, 27), "BMO")                     # day after Thanksgiving (13:00 close)
    assert bmo["base_session"] == date(2026, 11, 25) and bmo["last_usable_session"] == date(2026, 11, 25) and not bmo["tradable"]
    amc = co.plan_event(date(2026, 11, 25), "AMC")
    assert amc["tradable"] and amc["cutoff_utc"] == datetime(2026, 11, 24, 21, 0, tzinfo=UTC)
    early = co.plan_event(date(2026, 11, 27), "AMC")                   # base session closes at 13:00 ET on an early-close day
    assert co.order_deadline_utc(date(2026, 11, 27)) == datetime(2026, 11, 27, 17, 50, tzinfo=UTC) and early["tradable"]


def test_dst_transition_between_cutoff_and_release_is_flagged():
    p = co.plan_event(date(2026, 3, 8), "AMC")                         # synthetic Sunday: cutoff before 02:00 transition, release after
    assert p["dst_between_cutoff_and_release"] is True
    assert co.plan_event(date(2026, 3, 10), "BMO")["dst_between_cutoff_and_release"] is False


def test_sec_acceptance_is_only_an_earlier_upper_bound_never_proof():
    nominal = co.assumed_release(date(2026, 10, 27), "BMO")[0]
    early = et(2026, 10, 26, 19, 0).astimezone(UTC)
    rel, basis = co.assumed_release(date(2026, 10, 27), "BMO", early)
    assert rel == early and basis == "SEC_ACCEPTANCE_UPPER_BOUND_EARLIER_THAN_NOMINAL" and "VERIFIED" not in basis
    late = et(2026, 10, 27, 9, 0).astimezone(UTC)
    assert co.assumed_release(date(2026, 10, 27), "BMO", late) == (nominal, "NOMINAL_BMO")        # a later filing says nothing
    with pytest.raises(ValueError):
        co.assumed_release(date(2026, 10, 27), "BMO", datetime(2026, 10, 26, 19, 0))               # naive time refused


def test_unknown_timing_is_evidence_only_and_never_tradable():
    p = co.plan_event(date(2026, 11, 3), "UNKNOWN")
    assert p["evidence_only"] and not p["tradable"] and p["basis"] == "UNKNOWN_TIMING_USES_EARLIEST_NOMINAL" and p["base_session"] is None
    with pytest.raises(co.UnknownTiming):
        co.assumed_release(date(2026, 11, 3), "UNKNOWN")


def test_freshness_classes():
    assert fr.classify(et(2026, 11, 3, 10), date(2026, 11, 2), None) == fr.FRESH                  # Tuesday morning, Monday's session
    assert fr.classify(et(2026, 11, 3, 17), date(2026, 11, 3), None) == fr.FRESH                  # after Tuesday's close + buffer
    assert fr.classify(et(2026, 11, 2, 8), date(2026, 10, 30), None) == fr.WEEKEND                # Monday morning: Friday's session
    assert fr.classify(et(2026, 11, 7, 12), date(2026, 11, 6), None) == fr.WEEKEND                # Saturday
    assert fr.classify(et(2026, 11, 27, 8), date(2026, 11, 25), None) == fr.HOLIDAY               # Thursday Thanksgiving in between
    assert fr.classify(et(2026, 11, 3, 10), date(2026, 10, 30), None) == fr.STALE                 # older than the latest completed session
    assert fr.classify(et(2026, 11, 3, 10), date(2026, 11, 3), None) == fr.STALE                  # a session that has not completed: invalid, not fresh
    assert fr.classify(et(2026, 11, 3, 10), None, None) == fr.UNSTAMPED
    assert fr.classify(et(2026, 11, 3, 10), date(2026, 11, 2), et(2026, 11, 3, 1), kind="headlines") == fr.STALE   # captured 9 h after the headline stamp (limit 6 h)


def test_stale_or_missing_evidence_is_not_usable():
    e = fr.FactorEvidence(factor=5, source_name="x", freshness_class=fr.STALE)
    assert not e.usable
    assert not fr.missing(5, "x", "NO_SOURCE").usable
    assert fr.FactorEvidence(factor=5, source_name="x", freshness_class=fr.WEEKEND).usable


def test_point_in_time_leakage_prevention():
    cut = et(2026, 11, 2, 21).astimezone(UTC)
    fr.assert_point_in_time(cut, cut)                                                              # equal is allowed
    with pytest.raises(fr.LeakageError):
        fr.assert_point_in_time(cut + timedelta(seconds=1), cut, "headline")
    with pytest.raises(fr.LeakageError):
        fr.assert_point_in_time(datetime(2026, 11, 2, 12, 0), cut)                                  # naive timestamp
    cap = cut - timedelta(hours=1)
    assert fr.availability_time(cap, cap - timedelta(hours=3)) == cap - timedelta(hours=3)         # a provider stamp can only move availability earlier
    assert fr.availability_time(cap, None) == cap
    with pytest.raises(fr.LeakageError):
        fr.availability_time(cap, cap + timedelta(hours=2))                                         # stamp later than the capture is impossible
