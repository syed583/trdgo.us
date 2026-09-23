"""
Ownership and insider activity from a per-ticker feed.

What the bulk-download path got wrong, and what must not come back:

* a ticker had to be matched to a CUSIP by company name, and five of the
  largest names in the market failed that match and reported as though no
  institution held them;
* share count and holder count can disagree -- one large seller against many
  small buyers -- and calling that a direction is a judgement dressed as a
  measurement;
* Form 4 documents were read one at a time behind a sixty-filing cap, so on
  a heavily-filing issuer the oldest trades in the window vanished silently.
"""

import uw_ownership_service as uwo


def _stub(monkeypatch, rows, name="institutional_ownership"):
    import unusualwhales_service as uw
    monkeypatch.setattr(uw, name, lambda *a, **k: {
        "status": "OK", "data": rows, "source": uw.SOURCE})


def test_a_position_opened_this_quarter_is_a_new_position(monkeypatch):
    _stub(monkeypatch, [
        {"name": "NORGES BANK", "units": "60999693",
         "units_changed": "60999693", "report_date": "2026-06-30"},
        {"name": "AMUNDI", "units": "0", "units_changed": "-24511156",
         "report_date": "2026-06-30"}])

    out = uwo.institutional_activity("XOM")
    states = {h["fund"]: h["state"] for h in out["top_buyers"] + out["top_sellers"]}
    assert states["NORGES BANK"] == "NEW POSITION"
    assert states["AMUNDI"] == "CLOSED"
    assert out["new_positions"] == 1 and out["closed_positions"] == 1


def test_shares_and_holders_disagreeing_is_not_a_direction():
    """One big buyer against many small sellers says nothing either way."""
    assert uwo._signal(12.0, up=2, down=40) == "neutral"
    assert uwo._signal(-12.0, up=40, down=2) == "neutral"
    assert uwo._signal(12.0, up=40, down=2) == "bullish"
    assert uwo._signal(-12.0, up=2, down=40) == "bearish"
    assert uwo._signal(0.4, up=20, down=20) == "neutral"


def test_the_quarter_is_read_from_the_report_date():
    assert uwo._quarter("2026-06-30") == "2026-Q2"
    assert uwo._quarter("2026-06-30", back=1) == "2026-Q1"
    # And across a year boundary, where naive arithmetic gives Q0.
    assert uwo._quarter("2026-01-31", back=1) == "2025-Q4"
    assert uwo._quarter("") is None


def test_an_empty_answer_is_not_an_ownership_reading(monkeypatch):
    _stub(monkeypatch, [])
    assert uwo.institutional_activity("ZZZZZ")["status"] == "NO_DATA"


def test_a_sale_is_read_from_the_sign_not_guessed(monkeypatch):
    import unusualwhales_service as uw
    monkeypatch.setattr(uw, "get", lambda p, params=None: {
        "status": "OK", "source": uw.SOURCE, "data": [
            {"ticker": "NVDA", "amount": -1366000, "price": "219.72",
             "transaction_code": "S", "owner_name": "STEVENS MARK",
             "transaction_date": "2026-09-18", "filing_date": "2026-09-22",
             "is_director": True, "is_10b5_1": False},
            {"ticker": "NVDA", "amount": 5000, "price": "100.00",
             "transaction_code": "P", "owner_name": "BUYER ONE",
             "transaction_date": "2026-09-17", "filing_date": "2026-09-18",
             "is_officer": True, "is_10b5_1": True}]})

    out = uwo.insider_transactions("NVDA")
    sale, buy = out["transactions"][0], out["transactions"][1]
    assert sale["direction"] == "Sell" and sale["shares"] == 1366000
    assert sale["acquired_disposed"] == "D" and sale["discretionary"] is True
    assert buy["direction"] == "Buy" and buy["acquired_disposed"] == "A"
    # A trade running off a pre-adopted plan is not a decision taken today.
    assert buy["planned_10b5_1"] is True and buy["discretionary"] is False


def test_trades_outside_the_window_are_left_out(monkeypatch):
    import unusualwhales_service as uw
    monkeypatch.setattr(uw, "get", lambda p, params=None: {
        "status": "OK", "source": uw.SOURCE, "data": [
            {"ticker": "NVDA", "amount": -100, "transaction_code": "S",
             "transaction_date": "2019-01-02", "filing_date": "2019-01-03"}]})

    assert uwo.insider_transactions("NVDA", days=180)["transactions"] == []
