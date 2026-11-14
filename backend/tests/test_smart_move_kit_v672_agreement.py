"""Kit v6.7.2: the agreement rule, built from the exact rows the 2026-11-14 production store held.

Every row below is copied from the read-only diagnosis of that store (sm_calendar_diag3), so these are not invented shapes:
the vendors, dates, labels, clocks, flags and observation times are the real ones.
"""
import hashlib
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND.parent / "docs" / "smart_move" / "shadow_impl"))

import smart_move_calendar_session as CS            # noqa: E402
import smart_move_calendar_tiers as CT              # noqa: E402
from smvshadow import calendar_confirm as cc        # noqa: E402
from smvshadow import calendar_validation as cv     # noqa: E402

NOW = datetime(2026, 11, 14, 11, 7, tzinfo=timezone.utc)


def row(src, sym, d, label=None, clock=None, flag=None, observed=None):
    return cc.CalObs(src, sym, d, label, clock, flag,
                     hashlib.sha256(("%s%s%s" % (src, sym, d)).encode()).hexdigest(),
                     observed if observed is not None else NOW - timedelta(hours=6))


def ev(sym, rows, history=None):
    return CS.evaluate_event(sym, rows, NOW, NOW.date(), history or {})


# ------------------------------------------------------------------ the eight one-day-apart cases
ANF_FRESH = [row("FINVIZ", "ANF", date(2026, 11, 24), None, "8:30 AM"),
             row("UW_CALENDAR", "ANF", date(2026, 11, 24), "premarket", None, "company"),
             row("FINNHUB", "ANF", date(2026, 11, 23), None, None, None, NOW - timedelta(minutes=34))]


def test_a_lone_finnhub_row_one_day_early_no_longer_decides_the_date():
    """The real ANF shape: two vendors on 11-24 BMO, Finnhub on 11-23 with no session, observed last."""
    before = CS._evaluate_once("ANF", ANF_FRESH, NOW, NOW.date(), {})
    assert before["event_date"] == date(2026, 11, 23), "the engine still picks the most recently observed source"
    assert not before["eligible_for_shadow"]
    after = ev("ANF", ANF_FRESH)
    assert after["event_date"] == date(2026, 11, 24)
    assert after["kind"] == "BMO"
    assert after["eligible_for_shadow"]
    assert after["confirmation"] == CS.CONF_TWO
    assert CS.REASON_AGREEMENT in after["reasons"]
    assert after["agreement_sources"] == ["FINVIZ", "UW_CALENDAR"]


def test_the_rule_does_not_rescue_rows_that_are_too_old():
    """ANF as the store actually held it: the agreeing rows were observed 2026-11-12 14:17, about 45 h before the pass, against
    the engine's 24 h limit for an event more than four days out. The rule fires, the engine still refuses, and that is right."""
    seen = datetime(2026, 11, 12, 14, 17, tzinfo=timezone.utc)
    rows = [row("FINVIZ", "ANF", date(2026, 11, 24), None, "8:30 AM", None, seen),
            row("UW_CALENDAR", "ANF", date(2026, 11, 24), "premarket", None, "company", seen),
            row("FINNHUB", "ANF", date(2026, 11, 23), None, None, None, NOW - timedelta(minutes=34))]
    assert (NOW - seen) > cv.freshness_limit(10)
    out = ev("ANF", rows)
    assert not out["eligible_for_shadow"]
    assert out["event_date"] == date(2026, 11, 23), "a stale agreement must not replace the engine's answer"


@pytest.mark.parametrize("sym,d,uw_label,fz_clock,kind", [
    ("BBWI", date(2026, 11, 19), "premarket", "8:30 AM", "BMO"),
    ("BKE", date(2026, 11, 20), "premarket", "8:30 AM", "BMO"),
    ("KR", date(2026, 12, 3), "premarket", "8:30 AM", "BMO"),
    ("GAP", date(2026, 11, 19), "postmarket", "4:30 PM", "AMC"),
    ("ROST", date(2026, 11, 19), "postmarket", "4:30 PM", "AMC"),
    ("URBN", date(2026, 11, 24), "postmarket", "4:30 PM", "AMC"),
    ("BURL", date(2026, 11, 24), "premarket", "8:30 AM", "BMO"),
])
def test_each_one_day_apart_case_resolves_to_the_agreed_date_and_session(sym, d, uw_label, fz_clock, kind):
    rows = [row("FINVIZ", sym, d, None, fz_clock),
            row("UW_CALENDAR", sym, d, uw_label, None, "company"),
            row("FINNHUB", sym, d - timedelta(days=1), None, None, None, NOW - timedelta(minutes=34))]
    out = ev(sym, rows)
    assert out["event_date"] == d
    assert out["kind"] == kind
    assert out["eligible_for_shadow"]


# ------------------------------------------------------------------ the three same-date session cases
@pytest.mark.parametrize("sym,d", [("ADI", date(2026, 11, 24)), ("DE", date(2026, 11, 25)), ("HRL", date(2026, 12, 3))])
def test_two_agreeing_vendors_beat_a_lone_finnhub_session_on_the_same_date(sym, d):
    """ADI / DE / HRL: every source on the same date, Finviz 8:30 AM and UW premarket agree, Finnhub alone says amc."""
    rows = [row("FINVIZ", sym, d, None, "8:30 AM"),
            row("UW_CALENDAR", sym, d, "premarket", None, "company"),
            row("FINNHUB", sym, d, "amc", None, None, NOW - timedelta(minutes=34))]
    before = CS._evaluate_once(sym, rows, NOW, NOW.date(), {})
    assert "TIMING_CONTRADICTED" in before["no_trade_reasons"]
    out = ev(sym, rows)
    assert out["kind"] == "BMO"
    assert out["eligible_for_shadow"]
    assert out["agreement_sources"] == ["FINVIZ", "UW_CALENDAR"]


# ------------------------------------------------------------------ the negative case the owner required
def test_mntk_stays_refused():
    """MNTK: Unusual Whales moved its OWN session from postmarket to premarket and Finnhub says amc. No two sources agree,
    so the rule must not fire and the event must stay refused."""
    rows = [row("UW_CALENDAR", "MNTK", date(2026, 11, 9), "postmarket", None, "company", datetime(2026, 11, 8, 16, 21, tzinfo=timezone.utc)),
            row("UW_CALENDAR", "MNTK", date(2026, 11, 16), "premarket", None, "company", datetime(2026, 11, 14, 10, 32, tzinfo=timezone.utc)),
            row("FINNHUB", "MNTK", date(2026, 11, 16), "amc", None, None, datetime(2026, 11, 14, 10, 33, tzinfo=timezone.utc))]
    assert CS.agreeing_rows("MNTK", rows, NOW) is None
    out = ev("MNTK", rows)
    assert not out["eligible_for_shadow"]
    assert "TIMING_CONTRADICTED" in out["no_trade_reasons"]


def test_a_session_less_source_can_never_form_an_agreement():
    """Two sources agreeing on a DATE but neither giving a session is not an agreement under this rule."""
    d = date(2026, 11, 24)
    rows = [row("FINNHUB", "ZZZ", d), row("FINVIZ", "ZZZ", d)]
    assert CS.agreeing_rows("ZZZ", rows, NOW) is None


def test_two_rival_agreeing_groups_do_not_fire():
    """Four sources, two saying BMO on one date and two saying AMC on another: the kit must not pick a winner."""
    rows = [row("FINVIZ", "ZZZ", date(2026, 11, 24), None, "8:30 AM"),
            row("UW_CALENDAR", "ZZZ", date(2026, 11, 24), "premarket", None, "company"),
            row("FINNHUB", "ZZZ", date(2026, 11, 25), "amc"),
            row("OTHER", "ZZZ", date(2026, 11, 25), "amc")]
    assert CS.agreeing_rows("ZZZ", rows, NOW) is None


def test_an_already_eligible_event_is_never_re_judged():
    d = date(2026, 11, 24)
    rows = [row("FINVIZ", "OK1", d, None, "8:30 AM"), row("UW_CALENDAR", "OK1", d, "premarket", None, "company")]
    first = CS._evaluate_once("OK1", rows, NOW, NOW.date(), {})
    assert first["eligible_for_shadow"]
    out = ev("OK1", rows)
    assert CS.REASON_AGREEMENT not in out["reasons"], "the rule must not touch an event that already passed"


# ------------------------------------------------------------------ the rule version reaches the record
def test_the_calendar_rule_is_published_with_every_row():
    out = ev("ANF", ANF_FRESH)
    fields = CS.calendar_fields(out)
    assert fields["calendar_rule"] == "agreement-v1"
    assert fields["agreement_sources"] == ["FINVIZ", "UW_CALENDAR"]


# ------------------------------------------------------------------ the slot refresh cap
def test_the_whole_horizon_is_reachable_within_the_engines_freshness_limit():
    """~22 trading days x 2 sides must be refreshable inside 24 h at four live runs a day."""
    slots = CT.horizon_slots(NOW, 30)
    open_slots = [s for s in slots if CT.classify(s, NOW)["tier"] != "CLOSED"]
    runs_per_day = 4
    assert CT.T1_RUN_MAX * runs_per_day >= len(open_slots), (
        "%d slots cannot be refreshed by %d runs x %d" % (len(open_slots), runs_per_day, CT.T1_RUN_MAX))
    assert CT.T1_MAX_AGE_H <= cv.freshness_limit(10).total_seconds() / 3600
