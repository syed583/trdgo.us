"""
Tests for the options-tape logic: sweep/block clustering, side classification
and the point-in-time quote lookup. All synthetic - no IBKR connection.
"""

from datetime import datetime, timedelta, timezone

import pytest

import live_options_service as svc


BASE = datetime(2026, 9, 9, 19, 30, tzinfo=timezone.utc)


def mk(offset_ms, price, size, exchange):
    return {
        "time": BASE + timedelta(milliseconds=offset_ms),
        "price": price,
        "size": size,
        "exchange": exchange,
        "side": "MID",
        "notional": price * size * 100.0,
    }


# --------------------------------------------------------------- clustering


def test_multi_venue_burst_is_a_sweep():
    prints = [
        mk(0, 2.35, 10, "ISE"),
        mk(120, 2.35, 20, "PHLX"),
        mk(300, 2.36, 15, "BATS"),
    ]
    out = svc._cluster_prints(prints)
    assert len(out) == 1
    assert out[0]["kind"] == "SWEEP"
    assert out[0]["size"] == 45
    assert out[0]["prints"] == 3
    assert set(out[0]["exchanges"]) == {"ISE", "PHLX", "BATS"}


def test_single_large_print_on_one_venue_is_a_block():
    out = svc._cluster_prints([mk(0, 4.20, svc.BLOCK_MIN_SIZE + 10, "AMEX")])
    assert len(out) == 1
    assert out[0]["kind"] == "BLOCK"


def test_small_lone_print_is_a_single():
    out = svc._cluster_prints([mk(0, 1.10, 2, "CBOE")])
    assert out[0]["kind"] == "SINGLE"


def test_same_venue_burst_is_a_split_not_a_sweep():
    prints = [mk(0, 2.0, 5, "ISE"), mk(100, 2.0, 5, "ISE")]
    out = svc._cluster_prints(prints)
    assert len(out) == 1
    assert out[0]["kind"] == "SPLIT"


def test_prints_outside_the_window_do_not_merge():
    prints = [
        mk(0, 2.0, 5, "ISE"),
        mk(svc.SWEEP_WINDOW_MS + 500, 2.0, 5, "PHLX"),
    ]
    out = svc._cluster_prints(prints)
    assert len(out) == 2


def test_cluster_vwap_is_size_weighted():
    prints = [mk(0, 2.00, 10, "ISE"), mk(100, 3.00, 30, "PHLX")]
    out = svc._cluster_prints(prints)
    # (2*10 + 3*30) / 40 = 2.75
    assert out[0]["price"] == pytest.approx(2.75, abs=1e-6)


def test_cluster_side_follows_the_majority_of_size():
    prints = [mk(0, 2.0, 5, "ISE"), mk(100, 2.0, 40, "PHLX")]
    prints[0]["side"] = "SELL"
    prints[1]["side"] = "BUY"
    out = svc._cluster_prints(prints)
    assert out[0]["side"] == "BUY"


def test_empty_input_yields_nothing():
    assert svc._cluster_prints([]) == []


def test_unordered_prints_are_sorted_before_clustering():
    prints = [mk(300, 2.36, 15, "BATS"), mk(0, 2.35, 10, "ISE")]
    out = svc._cluster_prints(prints)
    assert len(out) == 1
    assert out[0]["time"] == BASE


# ---------------------------------------------------------- side resolution


@pytest.mark.parametrize(
    "price,expected",
    [
        (4.25, "BUY"),    # lifted the offer
        (4.20, "SELL"),   # hit the bid
        (4.225, "MID"),   # midpoint
        (4.24, "MID"),    # a penny of price improvement is ambiguous
        (4.21, "MID"),
    ],
)
def test_classify_side_on_a_penny_wide_quote(price, expected):
    """A 5c spread leaves the 15% touch band under a cent wide."""
    assert svc._classify_side(price, 4.20, 4.25) == expected


@pytest.mark.parametrize(
    "price,expected",
    [
        (5.00, "BUY"),
        (4.90, "BUY"),    # within 15% of a $1.00 spread
        (4.00, "SELL"),
        (4.10, "SELL"),
        (4.50, "MID"),
    ],
)
def test_classify_side_on_a_wide_quote(price, expected):
    """On a $1.00 spread the touch band widens to 15c either side."""
    assert svc._classify_side(price, 4.00, 5.00) == expected


def test_classify_side_without_a_quote_is_mid():
    assert svc._classify_side(4.2, None, None) == "MID"
    assert svc._classify_side(4.2, 4.30, 4.20) == "MID"  # crossed / bad quote


# ------------------------------------------------------------ quote lookup


def test_quote_at_returns_the_last_quote_at_or_before_the_trade():
    quotes = [
        (BASE, 1.0, 1.1),
        (BASE + timedelta(seconds=5), 2.0, 2.1),
        (BASE + timedelta(seconds=10), 3.0, 3.1),
    ]
    assert svc._quote_at(quotes, BASE + timedelta(seconds=7)) == (2.0, 2.1)
    assert svc._quote_at(quotes, BASE + timedelta(seconds=10)) == (3.0, 3.1)
    assert svc._quote_at(quotes, BASE + timedelta(seconds=99)) == (3.0, 3.1)


def test_quote_at_before_any_quote_is_unknown():
    quotes = [(BASE, 1.0, 1.1)]
    assert svc._quote_at(quotes, BASE - timedelta(seconds=1)) == (None, None)
    assert svc._quote_at([], BASE) == (None, None)


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
