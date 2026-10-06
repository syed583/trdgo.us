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


def _atm_straddle(symbol: str, spot: Optional[float],
                  after: Optional[str] = None) -> Optional[dict]:
    """
    Build the real ATM straddle from UW's option chain -- the first expiry on/after
    the earnings date (or, lacking it, a few days out so a 0-DTE weekly does not
    distort theta) with an at-the-money call and put -- and read the actual greeks
    and NBBO. Powers the theta, greeks, strike/expiry and liquidity parameters.
    """
    try:
        import unusualwhales_service as uw
        import contract_detail_service as cd
        from datetime import date, timedelta
        rows = uw._rows(uw.option_contracts(symbol, limit=1500))
        if not rows or spot is None:
            return None
        # First expiry we'll accept: the earnings date if known, else +3 days to
        # skip 0-DTE/near weeklies whose theta is not the earnings trade's.
        floor = after or (date.today() + timedelta(days=3)).isoformat()

        parsed = []
        for r in rows:
            occ = r.get("option_symbol")
            p = cd.parse_occ(occ) if occ else None
            if not p or not p.get("expiry") or p["expiry"] < floor:
                continue
            parsed.append((p, r))
        if not parsed:
            return None

        # Nearest qualifying expiry that has both a call and a put.
        expiries = sorted({p["expiry"] for p, _ in parsed})
        target = None
        for e in expiries:
            rights = {p["right"] for p, _ in parsed if p["expiry"] == e}
            if "C" in rights and "P" in rights:
                target = e
                break
        if not target:
            return None

        def nearest(right: str):
            cands = [(p, r) for p, r in parsed
                     if p["expiry"] == target and p["right"] == right
                     and p.get("strike")]
            if not cands:
                return None
            return min(cands, key=lambda pr: abs(pr[0]["strike"] - spot))

        call, put = nearest("C"), nearest("P")
        if not call or not put:
            return None

        def mid(r):
            b, a = _f(r.get("nbbo_bid")), _f(r.get("nbbo_ask"))
            if b is not None and a is not None:
                return (b + a) / 2, max(0.0, a - b)
            lp = _f(r.get("last_price"))
            return lp, None

        c_mid, c_spr = mid(call[1])
        p_mid, p_spr = mid(put[1])
        premium = (c_mid or 0) + (p_mid or 0)
        theta = (_f(call[1].get("theta")) or 0) + (_f(put[1].get("theta")) or 0)
        vega = (_f(call[1].get("vega")) or 0) + (_f(put[1].get("vega")) or 0)
        gamma = (_f(call[1].get("gamma")) or 0) + (_f(put[1].get("gamma")) or 0)
        spread = ((c_spr or 0) + (p_spr or 0)) if (c_spr is not None or p_spr is not None) else None

        return {
            "expiry": target,
            "call_strike": call[0]["strike"], "put_strike": put[0]["strike"],
            "premium": round(premium, 2) if premium else None,
            "theta": theta, "vega": vega, "gamma": gamma,
            "spread": spread,
            "spread_pct": (spread / premium) if (spread is not None and premium) else None,
        }
    except Exception:  # noqa: BLE001
        return None


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

    # Real ATM straddle from the chain powers theta / greeks / strike / liquidity.
    spot_px = _f(levels.get("spot")) if ok_levels else iv and None
    if spot_px is None:
        try:
            import live_market_service as market
            spot_px = _f((market.get_quote(symbol) or {}).get("price"))
        except Exception:  # noqa: BLE001
            spot_px = None
    # The report date, so the straddle lands on the first expiry after earnings.
    earnings_date = None
    try:
        import uw_earnings_calendar as uwcal
        earnings_date = (uwcal.preview(symbol) or {}).get("next_report")
    except Exception:  # noqa: BLE001
        earnings_date = None
    straddle = _atm_straddle(symbol, spot_px, after=earnings_date)

    b_theta = b_greeks = b_strike = b_liq = None
    if straddle and straddle.get("premium"):
        prem = straddle["premium"]
        # Theta: daily decay as a share of premium -- a standing cost (negative).
        if straddle.get("theta") is not None:
            b_theta = _clamp(-(abs(straddle["theta"]) / prem) / 0.05)
        # Greeks: vega per premium -- how much the straddle gains per vol point.
        if straddle.get("vega") is not None:
            b_greeks = _clamp((straddle["vega"] / prem) / 0.15)
        # Strike/expiry: ATM strikes exist at a usable expiry; tighter to spot is
        # better. Full marks when the strike sits within ~1.5% of spot.
        if spot_px:
            off = abs(straddle["call_strike"] - spot_px) / spot_px
            b_strike = _clamp(1.0 - off / 0.015)
        # Liquidity: combined bid/ask spread as a share of premium.
        if straddle.get("spread_pct") is not None:
            b_liq = _clamp((0.08 - straddle["spread_pct"]) / 0.08)

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
        eng.Param("iv", "Implied Volatility (IV)", 10, b_iv,
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
                  detail=(f"ATM straddle theta {round(straddle['theta'], 2)}/day on "
                          f"{straddle['premium']} premium (UW)." if straddle
                          else "No chain for the ATM straddle."),
                  unavailable_reason="" if b_theta is not None else "No ATM chain."),
        eng.Param("greeks", "Delta, Gamma & Vega", 5, b_greeks,
                  detail=(f"ATM straddle vega {round(straddle['vega'], 3)}, gamma "
                          f"{round(straddle['gamma'], 3)} (UW)." if straddle
                          else "No chain greeks."),
                  unavailable_reason="" if b_greeks is not None else "No ATM chain."),
        eng.Param("strike_expiry", "Strike Selection & Expiry", 5, b_strike,
                  detail=(f"ATM {straddle['call_strike']}C / {straddle['put_strike']}P, "
                          f"expiry {straddle['expiry']} (UW)." if straddle
                          else "No tradable chain."),
                  unavailable_reason="" if b_strike is not None else "No ATM chain."),
        eng.Param("liquidity", "Options Liquidity & Bid/Ask Spread", 5, b_liq,
                  detail=(f"ATM spread {round(straddle['spread_pct']*100, 1)}% of premium (UW)."
                          if straddle and straddle.get("spread_pct") is not None
                          else "No NBBO on the ATM chain."),
                  unavailable_reason="" if b_liq is not None else "No ATM NBBO."),
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
            "spot": spot_px,
            "expected_move_percent": _f(em.get("percent")),
            "expected_range": em.get("range"),
            "implied_volatility": _f(em.get("implied_volatility")) or iv,
            "iv_rank": None if iv_rank is None else round(iv_rank * 100, 1),
            "realized_vol": rv,
            "variance_risk_premium": _f(stats.get("vrp")),
            "call_strike": straddle.get("call_strike") if straddle else None,
            "put_strike": straddle.get("put_strike") if straddle else None,
            "combined_premium": straddle.get("premium") if straddle else None,
            "expiry": (straddle.get("expiry") if straddle
                       else "First eligible Friday after earnings."),
            "max_premium_at_risk": ("The combined debit paid -- the entire "
                                    "premium is at risk if price finishes between "
                                    "the strikes."),
            "exit": "First regular US session after the release.",
        },
        "note": ("A straddle profits from a large move either way; it loses if "
                 "the realised move is smaller than the combined premium or IV "
                 "crushes. Weights are the structure's development settings."),
        "source": SOURCE,
        "status": "OK" if s is not None else "NO_DATA",
    })
    return result
