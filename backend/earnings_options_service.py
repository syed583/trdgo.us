"""
Engine 2 -- the Options Earnings Analyzer (100-point evidence score).

Strategy: a long straddle / strangle (buy a call and a put). The evidence here
scores one question -- will the realised move beat what the options cost? -- so
every parameter leans toward "the move will pay" (+) or "the premium is too rich
/ IV will crush" (-). Weights are exactly the structure document's:

    Implied Volatility .................... 10
    IV Crush Risk ........................ 15
    Expected Move vs Combined Premium .... 15
    Historical Earnings Movement ......... 10
    Combined Call + Put Premium .......... 10
    Theta Decay ........................... 5
    Delta, Gamma & Vega ................... 5
    Strike Selection & Expiry ............. 5
    Options Liquidity & Bid/Ask Spread .... 5
    Unusual Options Activity .............. 5
    Options Disparity .................... 5
    Options Flow & OI Changes ............. 5
    Dark Pool Confirmation ............... 5

Direction-agnostic parameters (unusual activity, flow, OI, disparity, dark pool)
contribute by their magnitude -- a big move brewing helps a straddle whichever
way it breaks.
"""

from __future__ import annotations

from typing import Optional

import earnings_engine as eng
from earnings_equity_service import _directional_signals, _darkpool_bias, _f, _avg

SOURCE = "Trdgo earnings model"


def _clamp(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def get_analysis(symbol: str) -> dict:
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return {"status": "INVALID_SYMBOL", "symbol": symbol, "source": SOURCE}

    # Volatility stats: iv, rv, variance-risk-premium, iv_rank, ranges.
    stats: dict = {}
    try:
        import uw_volatility_service as vol
        stats = (vol.get_volatility(symbol) or {}).get("stats") or {}
    except Exception:  # noqa: BLE001
        stats = {}

    levels: dict = {}
    try:
        import options_levels_service as ol
        levels = ol.get_levels(symbol)
    except Exception:  # noqa: BLE001
        levels = {}
    ok_levels = levels.get("status") == "OK"
    greeks = levels.get("greeks") if ok_levels else None
    em = (levels.get("expected_move") or {}) if ok_levels else {}

    iv = _f(stats.get("iv"))
    rv = _f(stats.get("rv"))
    iv_rank = _f(stats.get("iv_rank"))           # 0..1
    iv_low, iv_high = _f(stats.get("iv_low")), _f(stats.get("iv_high"))
    rv_low, rv_high = _f(stats.get("rv_low")), _f(stats.get("rv_high"))

    # --- derive each parameter's lean ---
    # IV level: cheap IV (low rank) is good for a buyer.
    b_iv = None if iv_rank is None else _clamp((0.5 - iv_rank) * 2)
    # IV crush: rich options (iv above realised) crush harder post-event.
    b_crush = None if (iv is None or rv is None or not iv) else _clamp(-((iv - rv) / iv) * 2)
    # Expected move vs premium: realised above implied means the straddle pays.
    b_emp = None if (iv is None or rv is None or not iv) else _clamp(((rv - iv) / iv) * 2)
    # Historical movement: where realised vol sits in its own range (big mover?).
    b_hist = None
    if rv is not None and rv_low is not None and rv_high is not None and rv_high > rv_low:
        b_hist = _clamp((rv - (rv_low + rv_high) / 2) / ((rv_high - rv_low) / 2))
    # Combined premium cheapness (correlated with IV; kept mild).
    b_prem = None if b_iv is None else _clamp(b_iv * 0.7)
    # Theta: a 1-2 day hold pays some decay -- a small standing cost.
    b_theta = -0.2 if ok_levels else None
    # Greeks: an ATM straddle is long gamma/vega, which helps on a move.
    b_greeks = 0.3 if greeks else None
    # Strike/expiry: constructible when a chain and an expiry exist.
    b_strike = 0.2 if ok_levels else None
    # Liquidity/spread: not yet computed from the chain -> labelled unknown.
    b_liq = None

    sig = _directional_signals(symbol)

    def mag(*names) -> Optional[float]:
        """Magnitude of a directional signal -- a move brewing, any direction."""
        v = _avg(*(sig.get(n) for n in names))
        return None if v is None else _clamp(abs(v))

    b_unusual = mag("flow_by_expiry", "volume_pcr", "options_flow")
    b_disp = mag("disparity")
    b_flowoi = mag("daily_oi_change", "oi_positioning")
    dpb = _darkpool_bias(symbol)
    b_dp = None if dpb is None else _clamp(abs(dpb))

    params = [
        eng.Param("iv", "Implied Volatility", 10, b_iv,
                  detail=(f"IV {iv}% (rank {round(iv_rank*100) if iv_rank is not None else '--'}%); "
                          "cheaper IV favours the buyer.")),
        eng.Param("iv_crush", "IV Crush Risk", 15, b_crush,
                  detail=f"IV {iv}% vs realised {rv}%; rich options crush harder."),
        eng.Param("em_vs_premium", "Expected Move vs Combined Premium", 15, b_emp,
                  detail="Realised vs implied move -- the straddle pays when realised wins."),
        eng.Param("historical_move", "Historical Earnings Movement", 10, b_hist,
                  detail="Where realised volatility sits in its range."),
        eng.Param("combined_premium", "Combined Call + Put Premium", 10, b_prem,
                  detail="Premium cheapness (correlated with IV)."),
        eng.Param("theta", "Theta Decay", 5, b_theta,
                  detail="A short hold pays some time decay."),
        eng.Param("greeks", "Delta, Gamma & Vega", 5, b_greeks,
                  detail="ATM straddle is long gamma and vega."),
        eng.Param("strike_expiry", "Strike Selection & Expiry", 5, b_strike,
                  detail="ATM strikes, first Friday after earnings.",
                  unavailable_reason="" if ok_levels else "No tradable chain."),
        eng.Param("liquidity", "Options Liquidity & Bid/Ask Spread", 5, b_liq,
                  detail="Chain spread not yet measured.",
                  unavailable_reason="Bid/ask spread not computed yet."),
        eng.Param("unusual_options", "Unusual Options Activity", 5, b_unusual,
                  detail="Volume above open interest / near-term positioning."),
        eng.Param("disparity", "Options Disparity", 5, b_disp,
                  detail="Price vs options-implied fair value (magnitude)."),
        eng.Param("flow_oi", "Options Flow & OI Changes", 5, b_flowoi,
                  detail="Net flow and overnight OI build (magnitude)."),
        eng.Param("dark_pool", "Dark Pool Confirmation", 5, b_dp,
                  detail="Off-lit activity confirming interest (magnitude)."),
    ]

    # For a buyer there is no "short" side: the negative end is simply NO TRADE.
    result = eng.score(params, pos_label="STRADDLE", neg_label="NO TRADE",
                       buy_at=58.0, sell_at=-1.0)

    # Split the positive decision into STRADDLE (strong) vs STRANGLE (moderate).
    s = result.get("score")
    if result["decision"] == "STRADDLE" and s is not None:
        result["decision"] = "STRADDLE" if s >= 66 else "STRANGLE"

    result.update({
        "symbol": symbol,
        "engine": "OPTIONS",
        "construction": {
            "spot": _f(levels.get("spot")) if ok_levels else None,
            "expected_move_percent": _f(em.get("percent")),
            "expected_range": em.get("range"),
            "implied_volatility": _f(em.get("implied_volatility")) or iv,
            "iv_rank": None if iv_rank is None else round(iv_rank * 100, 1),
            "realized_vol": rv,
            "variance_risk_premium": _f(stats.get("vrp")),
            "max_premium_at_risk": ("The combined debit paid -- the entire "
                                    "premium is at risk if price finishes between "
                                    "the strikes."),
            "expiry": "First eligible Friday after earnings.",
            "exit": "First regular US session after the release.",
        },
        "note": ("A straddle profits from a large move either way; it loses if "
                 "the realised move is smaller than the combined premium or IV "
                 "crushes. Weights are the structure's development settings."),
        "source": SOURCE,
        "status": "OK" if s is not None else "NO_DATA",
    })
    return result
