"""
Two options metrics that were wrong in ways the UI could not reveal.

Both failures looked like working software: IV rank rendered blank while the
capability panel reported it available, and a nonsense skew was clamped into a
confident-looking 100 that then carried 23% of the options bias score. Neither
raised, so only a test pinning the numeric behaviour keeps them fixed.
"""

from __future__ import annotations

import live_options_analytics as analytics


def _chain(call_iv: float, put_iv: float) -> dict:
    """Minimal chain with 25-delta legs at the given implied vols."""
    return {
        "symbol": "TEST",
        "spot": 100.0,
        "expiry_label": "2026-09-18",
        "dte": 5,
        "rows": [
            {"expiry": "20260918", "right": "C", "strike": 105.0, "delta": 0.25, "iv": call_iv,
             "mid": 1.0, "volume": 10, "open_interest": 100},
            {"expiry": "20260918", "right": "P", "strike": 95.0, "delta": -0.25, "iv": put_iv,
             "mid": 1.0, "volume": 10, "open_interest": 100},
            {"expiry": "20260918", "right": "C", "strike": 100.0, "delta": 0.50, "iv": call_iv,
             "mid": 2.0, "volume": 10, "open_interest": 100},
            {"expiry": "20260918", "right": "P", "strike": 100.0, "delta": -0.50, "iv": put_iv,
             "mid": 2.0, "volume": 10, "open_interest": 100},
        ],
        "other_expiry_rows": [],
        "status": "OK",
    }


def test_absurd_solved_skew_is_discarded_not_clamped():
    """
    A near-dated wing has almost no extrinsic value, so Black-Scholes inverts
    to hundreds of vol points. Clamping that to 100 invented a maximally
    bullish signal; it must drop out instead.
    """
    metrics = analytics.get_metrics(_chain(call_iv=4.5, put_iv=0.3))

    assert metrics["skew_25d"] is None
    assert metrics["skew_source"] is None


def test_plausible_solved_skew_is_kept():
    metrics = analytics.get_metrics(_chain(call_iv=0.28, put_iv=0.33))

    assert metrics["skew_25d"] == -5.0
    assert metrics["skew_source"] == "chain"


def test_provider_skew_overrides_the_chain():
    """A constant-maturity 30-day skew beats whatever the front expiry implies."""
    metrics = analytics.get_metrics(
        _chain(call_iv=0.28, put_iv=0.33),
        iv_history={"skew_25d_30d": -0.02, "source": "Unusual Whales"},
    )

    assert metrics["skew_25d"] == -2.0
    assert metrics["skew_source"] == "Unusual Whales 25d/30d"


def test_iv_rank_flows_through_from_the_provider():
    metrics = analytics.get_metrics(
        _chain(call_iv=0.28, put_iv=0.33),
        iv_history={
            "iv_rank": 46.2, "iv_percentile": 43.0, "hv": 30.5,
            "source": "Unusual Whales", "as_of": "2026-09-11",
        },
    )

    assert metrics["iv_rank"] == 46.2
    assert metrics["iv_percentile"] == 43.0
    assert metrics["historical_volatility"] == 30.5
    assert metrics["iv_rank_source"] == "Unusual Whales"


def test_data_basis_reports_iv_rank_from_the_value_not_the_code_path():
    """
    This entry used to be hardcoded available. It claimed OK while IBKR
    returned NO_DATA, so the panel promised a figure it never had.
    """
    chain = _chain(call_iv=0.28, put_iv=0.33)

    missing = analytics.get_data_basis(chain, None, {"iv_rank": None})
    assert missing["supported"]["iv_rank_percentile"]["available"] is False

    present = analytics.get_data_basis(
        chain, None, {"iv_rank": 46.2, "iv_rank_source": "Unusual Whales"})
    assert present["supported"]["iv_rank_percentile"]["available"] is True


def test_gamma_exposure_moves_out_of_unsupported_when_a_provider_answers():
    chain = _chain(call_iv=0.28, put_iv=0.33)
    positioning = {
        "status": "OK", "net_gex": 1.2e9, "total_premium": 5.0e8,
        "gamma_regime": "LONG_GAMMA",
    }

    basis = analytics.get_data_basis(chain, None, {}, positioning)

    assert "gamma_exposure" not in basis["unsupported"]
    assert basis["supported"]["gamma_exposure"]["available"] is True

    without = analytics.get_data_basis(chain, None, {}, {"status": "NO_DATA"})
    assert "gamma_exposure" in without["unsupported"]


def test_premium_share_feeds_the_bias_score():
    """Premium weighs conviction better than contract counts, so it must count."""
    chain = _chain(call_iv=0.28, put_iv=0.33)
    metrics = analytics.get_metrics(chain)

    base = analytics.get_sentiment(chain, {}, metrics)
    with_premium = analytics.get_sentiment(
        chain, {}, metrics,
        {"status": "OK", "call_premium_share": 90.0, "gamma_regime": "SHORT_GAMMA"},
    )

    names = [c["name"] for c in with_premium["components"]]
    assert "Call share of premium" in names
    assert with_premium["score"] > base["score"]
    assert with_premium["gamma_regime"] == "SHORT_GAMMA"
