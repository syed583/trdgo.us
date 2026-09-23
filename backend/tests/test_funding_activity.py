"""
Funding & Debt: how the company pays for itself, and what it costs holders.

Kept apart from mergers on purpose. A takeover bid is what someone will pay
for the company; a share offering is the company asking holders to pay for
it. Averaged into one "events" score they cancel, which is how a dilutive
raise in a quarter with deal talk reads as neutral.
"""

import directional_signals as sig


def _events(*rows):
    return {"status": "OK",
            "events": [{"headline": h, "days_ago": d, "category": c}
                       for h, d, c in rows]}


def test_selling_shares_is_bearish_for_the_holder():
    out = sig.funding_activity(_events(("Shares sold privately", 5, "funding")))
    assert out.bias < -0.4


def test_debt_called_early_is_worse_than_taking_debt_on():
    early = sig.funding_activity(_events(("Debt acceleration", 5, "funding")))
    taken = sig.funding_activity(_events(("New debt or obligation", 5, "funding")))
    assert early.bias < taken.bias < 0


def test_write_downs_and_restructuring_count():
    out = sig.funding_activity(_events(("Asset write-down", 10, "restructuring")))
    assert out.bias < 0 and out.available


def test_a_quiet_balance_sheet_scores_zero_rather_than_going_missing():
    out = sig.funding_activity(
        _events(("Takeover bid for this company", 5, "deal")))
    assert out.available is True and out.bias == 0.0


def test_deals_are_not_scored_here_and_funding_is_not_scored_as_a_deal():
    raise_ = _events(("Shares sold privately", 5, "funding"))
    assert sig.merger_activity(raise_).bias == 0.0
    bid = _events(("Takeover bid for this company", 5, "deal"))
    assert sig.funding_activity(bid).bias == 0.0


def test_an_old_filing_is_already_in_the_price():
    assert sig.funding_activity(
        _events(("Shares sold privately", 200, "funding"))).bias == 0.0


def test_no_filings_at_all_is_unavailable():
    assert sig.funding_activity({}).available is False
