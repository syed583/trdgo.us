"""
The earnings calendar, and the preview the options market implies.

Three traps here, each of which would read as a fact on the screen:

* their expected move arrives as a fraction (0.0377) and the screen talks in
  percent -- 3.77% rendered as 0%;
* a report two months out has no front-month straddle, so there is no priced
  move; an empty cell reads like missing data rather than "not priced yet";
* the counts above the table and the rows inside it must come from the same
  source, or the header says "0 THIS WEEK" over twenty-two companies.
"""

from datetime import date, timedelta

import uw_earnings_calendar as cal


def _stub(monkeypatch, payload):
    import unusualwhales_service as uw
    monkeypatch.setattr(uw, "_cached",
                        lambda key, path, params=None, ttl=0: {
                            "status": "OK", "data": payload, "source": uw.SOURCE})


def test_the_expected_move_is_expressed_in_percent(monkeypatch):
    _stub(monkeypatch, [{"symbol": "CTAS", "full_name": "CINTAS",
                         "report_time": "premarket", "country_code": "US",
                         "expected_move_perc": "0.037721",
                         "expected_move": "7.50", "street_mean_est": "1.35"}])
    row = cal._for_date("2026-09-23")[0]
    assert row["expected_move_percent"] == 3.77, "a fraction rendered as 0%"
    assert row["expected_move"] == 7.5
    assert row["reporting_time"] == "BMO"


def test_a_figure_already_in_percent_is_left_alone(monkeypatch):
    _stub(monkeypatch, [{"symbol": "X", "report_time": "postmarket",
                         "country_code": "US", "expected_move_perc": "11.5"}])
    assert cal._for_date("2026-09-23")[0]["expected_move_percent"] == 11.5


def test_a_report_nobody_has_priced_says_so_rather_than_blank():
    said = cal._verdict(None, 4.0)
    assert "not priced" in said and "4.0%" in said

    # And with no history either, it does not invent a comparison.
    assert "No measured history" in cal._verdict(7.0, None)


def test_the_comparison_is_stated_in_both_directions():
    wide = cal._verdict(9.0, 4.0)
    tight = cal._verdict(3.0, 6.0)
    same = cal._verdict(4.1, 4.0)
    assert "more than this stock usually moves" in wide
    assert "less than this stock usually moves" in tight
    assert "about what this stock" in same
    # None of them tells the reader what to do about it.
    for line in (wide, tight, same):
        for word in ("buy", "sell", "should"):
            assert word not in line.lower()


def test_weekends_are_not_requested(monkeypatch):
    asked = []
    monkeypatch.setattr(cal, "_for_date", lambda when: asked.append(when) or [])

    monday = date(2026, 9, 21)
    cal.calendar(start=monday.isoformat(),
                 end=(monday + timedelta(days=6)).isoformat())
    assert asked == [(monday + timedelta(days=i)).isoformat() for i in range(5)]


def test_a_day_holds_both_sessions(monkeypatch):
    calls = []

    import unusualwhales_service as uw

    def cached(key, path, params=None, ttl=0):
        calls.append(path)
        return {"status": "OK", "source": uw.SOURCE, "data": [
            {"symbol": "A" if "pre" in path else "B",
             "report_time": "premarket" if "pre" in path else "postmarket",
             "country_code": "US"}]}

    monkeypatch.setattr(uw, "_cached", cached)
    rows = cal._for_date("2026-09-23")
    assert [r["symbol"] for r in rows] == ["A", "B"]
    assert len(calls) == 2, "before the open and after the close are both days"


def test_a_non_us_listing_is_left_out(monkeypatch):
    _stub(monkeypatch, [{"symbol": "FOO", "country_code": "CA",
                         "report_time": "premarket"}])
    assert cal._for_date("2026-09-23") == []
