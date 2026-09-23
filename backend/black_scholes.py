"""
Black-Scholes pricing, implied volatility and greeks.

TWS only populates ``modelGreeks`` when an options analytics subscription is
attached to the market data line, and it returns nothing outside RTH. Since we
already receive real bid/ask for every contract, deriving IV and the greeks
locally is both faster (no extra round trip per strike) and available whenever
a quote exists.
"""

from __future__ import annotations

import math
from typing import Optional


# Short-dated equity options are barely sensitive to r; this is the front-end
# T-bill yield and can be overridden per call.
DEFAULT_RISK_FREE = 0.04
DAYS_PER_YEAR = 365.0

_SQRT_2PI = math.sqrt(2.0 * math.pi)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def _d1_d2(
    spot: float, strike: float, t: float, vol: float, r: float, q: float
) -> tuple[float, float]:
    vt = vol * math.sqrt(t)
    d1 = (math.log(spot / strike) + (r - q + 0.5 * vol * vol) * t) / vt
    return d1, d1 - vt


def price(
    spot: float,
    strike: float,
    t: float,
    vol: float,
    right: str,
    r: float = DEFAULT_RISK_FREE,
    q: float = 0.0,
) -> float:
    """European option price. ``t`` is in years, ``right`` is 'C' or 'P'."""
    if t <= 0 or vol <= 0 or spot <= 0 or strike <= 0:
        intrinsic = (spot - strike) if right.upper().startswith("C") else (strike - spot)
        return max(intrinsic, 0.0)

    d1, d2 = _d1_d2(spot, strike, t, vol, r, q)
    df_q = math.exp(-q * t)
    df_r = math.exp(-r * t)

    if right.upper().startswith("C"):
        return spot * df_q * _norm_cdf(d1) - strike * df_r * _norm_cdf(d2)
    return strike * df_r * _norm_cdf(-d2) - spot * df_q * _norm_cdf(-d1)


def implied_vol(
    market_price: float,
    spot: float,
    strike: float,
    t: float,
    right: str,
    r: float = DEFAULT_RISK_FREE,
    q: float = 0.0,
) -> Optional[float]:
    """
    Solve for volatility by bisection.

    Bisection rather than Newton-Raphson: vega collapses on deep ITM/OTM
    strikes and Newton diverges there, which is exactly the wing data the skew
    calculation depends on.
    """
    if market_price is None or market_price <= 0 or t <= 0 or spot <= 0 or strike <= 0:
        return None

    is_call = right.upper().startswith("C")

    # No-arbitrage bounds must be discounted. An in-the-money European put
    # legitimately trades below its undiscounted intrinsic value whenever
    # rates are positive (K*e^-rT - S < K - S), so comparing against plain
    # intrinsic would reject solvable quotes on every ITM put.
    fwd_spot = spot * math.exp(-q * t)
    disc_strike = strike * math.exp(-r * t)

    if is_call:
        lower_bound = max(fwd_spot - disc_strike, 0.0)
        upper_bound = fwd_spot
    else:
        lower_bound = max(disc_strike - fwd_spot, 0.0)
        upper_bound = disc_strike

    if market_price < lower_bound - 1e-6:
        return None
    if market_price >= upper_bound:
        return None

    lo, hi = 1e-4, 5.0
    p_lo = price(spot, strike, t, lo, right, r, q)
    p_hi = price(spot, strike, t, hi, right, r, q)
    if not (p_lo <= market_price <= p_hi):
        return None

    for _ in range(100):
        mid = 0.5 * (lo + hi)
        p_mid = price(spot, strike, t, mid, right, r, q)
        if abs(p_mid - market_price) < 1e-6:
            return mid
        if p_mid < market_price:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-6:
            break

    return 0.5 * (lo + hi)


def greeks(
    spot: float,
    strike: float,
    t: float,
    vol: float,
    right: str,
    r: float = DEFAULT_RISK_FREE,
    q: float = 0.0,
) -> dict:
    """Delta, gamma, vega (per 1 vol pt), theta (per day) and rho."""
    if t <= 0 or vol is None or vol <= 0 or spot <= 0 or strike <= 0:
        return {"delta": None, "gamma": None, "vega": None, "theta": None, "rho": None}

    is_call = right.upper().startswith("C")
    d1, d2 = _d1_d2(spot, strike, t, vol, r, q)
    df_q = math.exp(-q * t)
    df_r = math.exp(-r * t)
    sqrt_t = math.sqrt(t)
    pdf = _norm_pdf(d1)

    delta = df_q * (_norm_cdf(d1) if is_call else _norm_cdf(d1) - 1.0)
    gamma = df_q * pdf / (spot * vol * sqrt_t)
    vega = spot * df_q * pdf * sqrt_t / 100.0

    term1 = -(spot * df_q * pdf * vol) / (2.0 * sqrt_t)
    if is_call:
        theta = (
            term1
            - r * strike * df_r * _norm_cdf(d2)
            + q * spot * df_q * _norm_cdf(d1)
        )
        rho = strike * t * df_r * _norm_cdf(d2) / 100.0
    else:
        theta = (
            term1
            + r * strike * df_r * _norm_cdf(-d2)
            - q * spot * df_q * _norm_cdf(-d1)
        )
        rho = -strike * t * df_r * _norm_cdf(-d2) / 100.0

    return {
        "delta": round(delta, 4),
        "gamma": round(gamma, 6),
        "vega": round(vega, 4),
        "theta": round(theta / DAYS_PER_YEAR, 4),
        "rho": round(rho, 4),
    }


def years_to_expiry(days: float) -> float:
    """Days to expiry -> year fraction, floored so 0-DTE stays solvable."""
    return max(days, 0.25) / DAYS_PER_YEAR
