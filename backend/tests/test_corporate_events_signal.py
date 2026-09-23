"""
The four filing-based parameters, each answering its own question.

They were one blended "corporate events" score and are now separate, because
a takeover bid and a dividend cut disagree often and averaging them hid both.
What each test pins is the line between what is scored and what is only
reported: a deal has a direction, a CEO change does not.
"""

import directional_signals as sig


def _events(*rows):
    return {"status": "OK",
            "events": [{"headline": h, "days_ago": d, "category": c}
                       for h, d, c in rows]}


# --------------------------------------------------------------- mergers

def test_a_takeover_bid_is_bullish():
    out = sig.merger_activity(
        _events(("Takeover bid for this company", 3, "deal")))
    assert out.bias > 0.5


def test_an_agreement_falling_through_is_bearish():
    out = sig.merger_activity(_events(("Material agreement ended", 5, "deal")))
    assert out.bias < 0


def test_a_quiet_quarter_scores_zero_rather_than_going_missing():
    out = sig.merger_activity(_events(("Results released", 5, "earnings")))
    assert out.available is True and out.bias == 0.0


def test_an_old_deal_is_already_in_the_price():
    assert sig.merger_activity(
        _events(("Takeover bid for this company", 200, "deal"))).bias == 0.0


def test_no_filings_at_all_is_unavailable_not_neutral():
    out = sig.merger_activity({})
    assert out.available is False


# ------------------------------------------------------------ management

def test_a_management_change_is_reported_never_scored():
    out = sig.management_change(_events(("Management change", 10, "leadership")))
    assert out.bias == 0.0
    assert "read the filing" in out.detail
    assert out.evidence["newest_days_ago"] == 10


# ----------------------------------------------------------- fund flows

def _funds(net, up, down):
    return {"status": "OK", "net_share_change_pct": net, "funds_increasing": up,
            "funds_decreasing": down, "latest_quarter": "2026-Q2"}


def test_funds_adding_reads_bullish_and_selling_bearish():
    assert sig.fund_flows(_funds(4.0, 2500, 1500)).bias > 0.4
    assert sig.fund_flows(_funds(-4.0, 1500, 2500)).bias < -0.4


def test_breadth_tempers_a_move_made_by_one_large_holder():
    narrow = sig.fund_flows(_funds(4.0, 900, 1100)).bias
    broad = sig.fund_flows(_funds(4.0, 2500, 800)).bias
    assert broad > narrow


def test_filings_since_the_dataset_nudge_the_reading():
    live = {"filings": [{"positions": [{"symbol": "X", "share_change": 5000}]},
                        {"positions": [{"symbol": "X", "share_change": 9000}]}]}
    with_live = sig.fund_flows(_funds(0.0, 1000, 1000), live).bias
    assert with_live > sig.fund_flows(_funds(0.0, 1000, 1000)).bias
    assert with_live <= 0.2  # a nudge, not a vote


def test_no_holdings_on_record_is_unavailable():
    assert sig.fund_flows({"status": "NO_DATA", "detail": "none"}).available is False


# ------------------------------------------------------------- dividends

def test_a_rising_payout_is_bullish_and_a_cut_is_worse_than_a_rise_is_good():
    rising = sig.dividend_trend({"status": "OK", "pays_dividend": True,
                                 "trend": "rising", "growth_pct": 4.0,
                                 "latest": {"amount": 0.27}})
    cut = sig.dividend_trend({"status": "OK", "pays_dividend": True,
                              "trend": "cut", "growth_pct": -49.0,
                              "latest": {"amount": 0.125}})
    assert rising.bias > 0 and cut.bias < 0
    assert abs(cut.bias) > abs(rising.bias)


def test_a_company_that_pays_nothing_is_neutral_not_missing():
    out = sig.dividend_trend({"status": "NO_DATA", "pays_dividend": False})
    assert out.available is True and out.bias == 0.0 and "no dividend" in out.detail.lower()


def test_a_dead_dividend_feed_is_unavailable():
    out = sig.dividend_trend({"status": "PROVIDER_OFFLINE", "detail": "down"})
    assert out.available is False
