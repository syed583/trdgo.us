"""Deterministic checks on the option maths. No IBKR connection required."""

import math

import pytest

import black_scholes as bs


SPOT = 100.0
T = 30 / 365.0
R = 0.04


def test_put_call_parity_holds():
    """C - P must equal S*e^(-qT) - K*e^(-rT) for any vol."""
    for strike in (80.0, 100.0, 120.0):
        call = bs.price(SPOT, strike, T, 0.35, "C", R)
        put = bs.price(SPOT, strike, T, 0.35, "P", R)
        expected = SPOT - strike * math.exp(-R * T)
        assert call - put == pytest.approx(expected, abs=1e-6)


def test_price_is_monotonic_in_volatility():
    lower = bs.price(SPOT, 100.0, T, 0.20, "C", R)
    higher = bs.price(SPOT, 100.0, T, 0.60, "C", R)
    assert higher > lower


@pytest.mark.parametrize("strike", [85.0, 100.0, 115.0])
@pytest.mark.parametrize("right", ["C", "P"])
@pytest.mark.parametrize("vol", [0.15, 0.45, 0.90])
def test_implied_vol_round_trips(strike, right, vol):
    """Pricing at a known vol and solving back must recover that vol."""
    premium = bs.price(SPOT, strike, T, vol, right, R)
    solved = bs.implied_vol(premium, SPOT, strike, T, right, R)
    assert solved is not None
    assert solved == pytest.approx(vol, abs=1e-3)


def test_implied_vol_rejects_impossible_prices():
    # Below intrinsic value there is no solution.
    assert bs.implied_vol(0.01, SPOT, 50.0, T, "C", R) is None
    # At or above the underlying there is none either.
    assert bs.implied_vol(SPOT * 2, SPOT, 100.0, T, "C", R) is None
    assert bs.implied_vol(0.0, SPOT, 100.0, T, "C", R) is None
    assert bs.implied_vol(5.0, SPOT, 100.0, 0.0, "C", R) is None


def test_call_and_put_delta_bounds():
    call = bs.greeks(SPOT, 100.0, T, 0.35, "C", R)
    put = bs.greeks(SPOT, 100.0, T, 0.35, "P", R)

    assert 0.0 < call["delta"] < 1.0
    assert -1.0 < put["delta"] < 0.0
    # Same strike: delta_call - delta_put = e^(-qT) = 1 with q = 0.
    assert call["delta"] - put["delta"] == pytest.approx(1.0, abs=1e-3)
    assert call["gamma"] == pytest.approx(put["gamma"], abs=1e-9)
    assert call["vega"] == pytest.approx(put["vega"], abs=1e-9)


def test_deep_itm_call_delta_approaches_one():
    deep = bs.greeks(SPOT, 40.0, T, 0.25, "C", R)
    assert deep["delta"] > 0.97


def test_greeks_return_nones_without_a_volatility():
    out = bs.greeks(SPOT, 100.0, T, None, "C", R)
    assert all(v is None for v in out.values())


def test_years_to_expiry_floors_at_zero_dte():
    assert bs.years_to_expiry(0) > 0
    assert bs.years_to_expiry(365) == pytest.approx(1.0)
