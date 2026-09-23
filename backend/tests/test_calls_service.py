"""
Stored calls, the explanation built from them, and the scorecard's verdicts.

The rules pinned here are the ones that decide whether the scorecard means
anything: a call is judged against SPY, not in absolute terms; a withheld call
is not judged; a close is not final until a little after four; and the "why"
comes from the points that were stored, not from anything recomputed.
"""

import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

import calls_service as cs


def _row(**over):
    base = dict(
        decision="BUY", blocked_reasons="[]", explanation=None,
        parameters=json.dumps([
            {"label": "Options Flow", "available": True, "directional": True,
             "points": 4.2, "points_label": "+4.2 of 10", "detail": "calls lead"},
            {"label": "EMA / Trend", "available": True, "directional": True,
             "points": 3.1, "points_label": "+3.1 of 8", "detail": "above EMAs"},
            {"label": "RSI (14)", "available": True, "directional": True,
             "points": -1.5, "points_label": "-1.5 of 5", "detail": "stretched"},
            {"label": "Implied Volatility", "available": True, "directional": False,
             "points": 0.0, "points_label": "sizing only", "detail": "rank 40"},
            {"label": "Earnings Results", "available": False, "directional": True,
             "points": 0, "points_label": None, "detail": None},
        ]),
    )
    base.update(over)
    return SimpleNamespace(**base)


# --- why -------------------------------------------------------------------

def test_why_puts_the_reasons_for_a_buy_first_strongest_first():
    w = cs.why(_row())
    assert [i["label"] for i in w["for"]] == ["Options Flow", "EMA / Trend"]
    assert [i["label"] for i in w["against"]] == ["RSI (14)"]
    assert w["missing"] == ["Earnings Results"]


def test_why_flips_sides_for_a_sell():
    w = cs.why(_row(decision="STRONG SELL"))
    assert [i["label"] for i in w["for"]] == ["RSI (14)"]
    assert "Options Flow" in [i["label"] for i in w["against"]]


def test_non_directional_parameters_are_never_reasons():
    """Volatility sizes a call; it does not argue for a direction."""
    w = cs.why(_row())
    labels = [i["label"] for i in w["for"] + w["against"]]
    assert "Implied Volatility" not in labels


# --- judging ---------------------------------------------------------------

def test_a_buy_is_right_only_if_it_beat_spy():
    """
    A buy that rose on a day SPY rose further lagged the thing it was an
    alternative to. Counting it as a win would reward nothing but a rising
    market.
    """
    assert cs._judge("BUY", 0.4) == 1
    assert cs._judge("STRONG BUY", -0.2) == 0


def test_a_sell_is_right_if_it_lagged_spy():
    assert cs._judge("SELL", -0.9) == 1
    assert cs._judge("STRONG SELL", 0.1) == 0


def test_a_withheld_call_is_never_judged():
    assert cs._judge("DO NOT TRADE", 2.0) is None
    assert cs._judge("WAIT", -2.0) is None


def test_a_close_is_not_final_until_after_four_fifteen():
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    day = date(2026, 9, 16)
    before = datetime(2026, 9, 16, 16, 5, tzinfo=et).astimezone(timezone.utc)
    after = datetime(2026, 9, 16, 16, 20, tzinfo=et).astimezone(timezone.utc)

    assert cs._ready_to_judge(day, before) is False
    assert cs._ready_to_judge(day, after) is True
    assert cs._ready_to_judge(date(2026, 9, 17), after) is False


def test_tomorrow_is_the_next_weekday():
    from zoneinfo import ZoneInfo

    friday = datetime(2026, 9, 18, 17, 0, tzinfo=ZoneInfo("America/New_York"))
    made, target = cs._session_dates("TOMORROW", friday.astimezone(timezone.utc))
    assert made == date(2026, 9, 18)
    assert target == date(2026, 9, 21), "Friday's tomorrow is Monday"


# --- storage round trip -----------------------------------------------------

def test_a_call_is_stored_and_read_back_with_its_why():
    result = {
        "status": "OK", "decision": "BUY", "direction_score": 66.4,
        "confidence": 71.2, "agreement_pct": 74.0, "coverage_pct": 88.0,
        "actionable": True, "market": {"session": "OPEN"},
        "reasons": [], "blocked_reasons": [],
        "signals": json.loads(_row().parameters),
    }
    call_id = cs.record("ZZPYTEST", result, horizon="TODAY",
                        origin="analysis", price=100.0, spy=500.0)
    try:
        assert call_id
        got = cs.get(call_id)
        assert got["decision"] == "BUY"
        assert got["price_at_call"] == 100.0
        assert got["spy_at_call"] == 500.0
        assert [i["label"] for i in got["why"]["for"]][0] == "Options Flow"
        assert len(got["parameters"]) == 5
    finally:
        from database import SessionLocal
        from models_calls import TradeCall

        db = SessionLocal()
        db.query(TradeCall).filter(TradeCall.symbol == "ZZPYTEST").delete()
        db.commit()
        db.close()


def test_recording_a_failed_analysis_stores_nothing():
    assert cs.record("ZZPYTEST", {"status": "ERROR"}) is None
    assert cs.record("ZZPYTEST", {}) is None
