"""
Reported quarters, and the day the market actually answered them.

Two failures this pins. First: the app read earnings history from a Benzinga
cache somebody has to sync, so an unsynced symbol reported "no earnings
history" -- and the confidence score counted the component missing -- while
Finviz had five verified quarters a call away.

Second, and worse than missing: NVDA reports at 16:30, after the close, so
the market's answer is the *next* session. Measured on the report date it
came out -1.6% on a report the market answered with +8.7% -- a real figure,
for the day before the news, feeding the average-move statistic.
"""

import earnings_intelligence_service as ei
import finviz_service as fv


def test_the_clock_decides_which_session_reacted():
    assert fv._reporting_time("16:30") == "AMC"
    assert fv._reporting_time("07:00") == "BMO"
    assert fv._reporting_time("09:30") == "BMO"
    assert fv._reporting_time("12:00") == "DMT"
    assert fv._reporting_time("") is None
    assert fv._reporting_time(None) is None


def test_finviz_quarters_arrive_in_the_shape_the_model_reads(monkeypatch):
    monkeypatch.setattr(fv, "earnings_history", lambda s, q: {
        "status": "OK", "rows": [{
            "date": "2026-08-26", "report_time": "16:30",
            "reporting_time": "AMC", "eps_estimate": 2.09, "eps_actual": 2.22,
            "eps_surprise_percent": 6.22, "revenue_estimate": 92270.0,
            "revenue_actual": 96221.0, "revenue_surprise_percent": 4.28,
            "price_reaction_pct": 8.74, "company": "NVIDIA Corp"}]})

    rows = ei._from_finviz("NVDA", 8)
    assert len(rows) == 1
    row = rows[0]
    assert row["verified"] is True and row["source"] == "FINVIZ"
    assert row["beat"] is True and row["date_label"] == "Aug 26, 2026"
    assert row["reporting_time"] == "AMC", \
        "without the clock the move is measured a day early"
    assert row["time_label"] == "After Close"


def test_a_quarter_not_yet_reported_is_not_a_track_record(monkeypatch):
    monkeypatch.setattr(fv, "earnings_history", lambda s, q: {
        "status": "OK", "rows": [{"date": "2026-11-18", "eps_estimate": 2.4,
                                  "eps_actual": None}]})
    assert ei._from_finviz("NVDA", 8) == []


def test_an_after_close_report_is_measured_on_the_next_session(monkeypatch):
    bars = [{"t": f"2026-08-{d}T00:00:00", "close": c} for d, c in
            (("25", 213.05), ("26", 209.66), ("27", 227.98))]
    monkeypatch.setattr(ei.market, "get_chart",
                        lambda s, r: {"status": "OK", "bars": bars})

    amc = [{"date": "2026-08-26", "reporting_time": "AMC"}]
    bmo = [{"date": "2026-08-26", "reporting_time": "BMO"}]
    ei._attach_post_earnings_moves("NVDA", amc)
    ei._attach_post_earnings_moves("NVDA", bmo)

    assert amc[0]["post_earnings_move_percent"] == 8.74
    assert amc[0]["post_earnings_date"] == "2026-08-27"
    assert bmo[0]["post_earnings_move_percent"] == -1.59
