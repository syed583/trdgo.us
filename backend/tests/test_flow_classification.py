"""
Tests for what this app still does to an options tape.

The sweep/block clustering, the side classification and the point-in-time
quote lookup that used to fill this file are gone with the IBKR tape they
served. All three were reconstruction: the side of a print was inferred from
where it landed in the spread, and consecutive prints were grouped into
sweeps and blocks by timing and venue. The feed publishes the side and the
trade, counted rather than inferred, so there is nothing here left to test
-- and a test asserting our inference still behaves would be testing code no
screen reads.

What remains is ours: how a side becomes a sentiment label, and expiry
parsing.
"""

import pytest

import live_options_service as svc


# ---------------------------------------------------------------- sentiment


@pytest.mark.parametrize(
    "right,side,expected",
    [
        ("C", "Buy", "Bullish"),
        ("C", "Sell", "Bearish"),
        ("P", "Buy", "Bearish"),
        ("P", "Sell", "Bullish"),
    ],
)
def test_sentiment_follows_side_and_contract_type(right, side, expected):
    trade = {"right": right, "side": side}
    svc._apply_sentiment(trade)
    assert trade["sentiment"] == expected
    assert trade["sentiment_confidence"] == "HIGH"


def test_unclassified_side_is_flagged_low_confidence():
    trade = {"right": "C", "side": "Mid"}
    svc._apply_sentiment(trade)
    assert trade["sentiment_confidence"] == "LOW"


# ------------------------------------------------------------- expiry utils


def test_expiry_formatting_and_dte():
    assert svc._fmt_expiry("20260918") == "09/18/26"
    assert svc._fmt_expiry("garbage") == "garbage"
    assert svc._dte("20260918") is not None
    assert svc._dte("nonsense") is None
    # An expiry in the past floors at zero rather than going negative.
    assert svc._dte("20200101") == 0.0
