"""
Earnings, dividends, analysts and headlines from one paid feed.

Each of these replaced a feed that failed in its own way, and each has a
shape trap of its own that would pass silently:

* their earnings feed carries annual rows beside quarterly ones, and rows
  that have not been reported yet -- counting either would inflate a beat
  rate that the screen presents as a track record;
* their older dividend rows carry the *string* "None" rather than a null,
  which renders as the word None on a date field;
* a headline tone counted from one matched word in twenty-five reads on
  screen exactly like a considered reading of the news.
"""

import uw_company_service as uwc
import uw_news_adapter as uwnews


def _stub(monkeypatch, name, payload):
    import unusualwhales_service as uw
    monkeypatch.setattr(uw, name, lambda *a, **k: {
        "status": "OK", "data": payload, "source": uw.SOURCE})


def test_an_annual_row_is_not_a_quarter(monkeypatch):
    _stub(monkeypatch, "earnings_history", [
        {"report_type": "quarterly", "report_date": "2026-08-26",
         "reported_eps": "2.22", "estimated_eps": "2.09", "surprise": "0.13"},
        {"report_type": "annual", "report_date": "2026-07-31",
         "reported_eps": "4.09", "estimated_eps": None, "surprise": None},
    ])
    out = uwc.earnings_history("NVDA")
    assert out["count"] == 1 and out["rows"][0]["date"] == "2026-08-26"


def test_a_quarter_not_yet_reported_is_not_a_track_record(monkeypatch):
    _stub(monkeypatch, "earnings_history", [
        {"report_type": "quarterly", "report_date": "2026-11-18",
         "reported_eps": None, "estimated_eps": "2.40"}])
    assert uwc.earnings_history("NVDA")["rows"] == []


def test_the_surprise_is_expressed_against_the_estimate(monkeypatch):
    _stub(monkeypatch, "earnings_history", [
        {"report_type": "quarterly", "report_date": "2026-08-26",
         "reported_eps": "2.22", "estimated_eps": "2.00", "surprise": "0.22"}])
    assert uwc.earnings_history("NVDA")["rows"][0]["eps_surprise_percent"] == 11.0


def test_the_word_none_is_not_a_date(monkeypatch):
    _stub(monkeypatch, "dividends", {"ticker": "KO", "dividends": [
        {"amount": 0.42, "ex_date": "2021-06-14", "record_date": "None",
         "declaration_date": "None", "payment_date": "None"}]})
    payment = uwc.dividends("KO")["payments"][0]
    assert payment["record_date"] is None and payment["pay_date"] is None
    assert payment["ex_date"] == "2021-06-14"


def test_dividends_come_back_newest_first(monkeypatch):
    _stub(monkeypatch, "dividends", {"dividends": [
        {"amount": 0.51, "ex_date": "2025-09-15"},
        {"amount": 0.53, "ex_date": "2026-09-15"},
        {"amount": 0.53, "ex_date": "2026-06-15"}]})
    dates = [p["ex_date"] for p in uwc.dividends("KO")["payments"]]
    assert dates == ["2026-09-15", "2026-06-15", "2025-09-15"]


def test_an_upgrade_and_a_downgrade_are_counted_apart(monkeypatch):
    _stub(monkeypatch, "analyst_actions", [
        {"ticker": "NVDA", "action": "initiated", "recommendation": "buy",
         "target": "300", "firm": "A", "timestamp": "2026-09-10T14:03:29Z"},
        {"ticker": "NVDA", "action": "downgraded", "recommendation": "sell",
         "target": "180", "firm": "B", "timestamp": "2026-09-09T14:03:29Z"},
        {"ticker": "NVDA", "action": "maintained", "recommendation": "hold",
         "target": "230", "firm": "C", "timestamp": "2026-09-08T14:03:29Z"},
    ])
    out = uwc.analyst_actions("NVDA")
    assert out["upgrades"] == 1 and out["downgrades"] == 1 and out["net"] == 0
    assert [r["lean"] for r in out["rows"]] == ["UP", "DOWN", "FLAT"]


def test_one_matched_headline_is_not_a_tone(monkeypatch):
    monkeypatch.setattr(uwnews, "get_news", lambda s, limit=20: {
        "status": "OK", "items": [
            {"headline": "NVDA beats on earnings", "sentiment_score": 1.0,
             "sentiment": "POSITIVE"}]
        + [{"headline": f"Routine item {i}", "sentiment_score": None,
            "sentiment": "NEUTRAL"} for i in range(24)]})

    out = uwnews.get_sentiment("NVDA")
    assert out["status"] == "INSUFFICIENT_DATA"
    assert out["score"] is None, "a label needs a sample behind it"
    assert "1 of 25" in out["detail"]


def test_a_real_sample_is_scored_and_says_what_it_is(monkeypatch):
    monkeypatch.setattr(uwnews, "get_news", lambda s, limit=20: {
        "status": "OK", "items": [
            {"headline": f"beats {i}", "sentiment_score": 1.0,
             "sentiment": "POSITIVE"} for i in range(4)]})

    out = uwnews.get_sentiment("NVDA")
    assert out["status"] == "OK" and out["score"] == 100
    assert out["label"] == "BULLISH"
    assert "keyword estimate" in out["detail"], \
        "a counted score must never pass for a provider's model"
