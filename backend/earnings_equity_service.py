"""
Engine 1 -- the Equity Earnings Analyzer (100-point evidence score).

The parameters and weights are exactly those in the TRDGO earnings structure:

    EPS Estimates & Revisions ............ 15
    Revenue Estimates & Revisions ........ 15
    Forward Guidance & Outlook ........... 15   (no structured feed -> labelled unknown)
    Historical Earnings Reaction ......... 15
    Price Action & Technical Trend ....... 10
    Options Flow & Institutional ......... 10
    Dark Pool Activity .................... 5
    Unusual Options Activity ............. 5
    Options Disparity .................... 5
    Sector & Market Trend ................ 5

Each parameter is read from the service that already produces it; anything that
does not return data is labelled unknown and left out of the covered weight,
rather than guessed. Output: BUY / SELL / NO TRADE, score/100, entry reference,
expected move, planned exit and maximum planned risk.
"""

from __future__ import annotations

from typing import Optional

import earnings_engine as eng

SOURCE = "Trdgo earnings model"


def _f(v) -> Optional[float]:
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def _avg(*vals) -> Optional[float]:
    xs = [v for v in vals if v is not None]
    return sum(xs) / len(xs) if xs else None


def _directional_signals(symbol: str) -> dict:
    """{signal_name: bias in -1..+1} from the directional engine, best effort."""
    try:
        import directional_score_service as dss
        out = dss.get_directional_score(symbol)
        return {s["name"]: s["bias"] for s in out.get("signals", [])
                if s.get("available") and s.get("bias") is not None}
    except Exception:  # noqa: BLE001
        return {}


def _estimate_bias(symbol: str) -> Optional[float]:
    """Combined EPS+revenue revision lean, -1..+1, from the estimate scorer."""
    try:
        from database import SessionLocal
        import estimate_score_service as es
        db = SessionLocal()
        try:
            r = es.score_estimates(db, symbol) or {}
        finally:
            db.close()
        if str(r.get("bias")) in ("TEST_DATA", "NO_DATA"):
            return None
        sc, mx = _f(r.get("score")), _f(r.get("max_score")) or 25.0
        return None if sc is None else max(-1.0, min(1.0, sc / mx))
    except Exception:  # noqa: BLE001
        return None


def _history_bias(symbol: str) -> Optional[float]:
    try:
        from database import SessionLocal
        import earnings_history_service as ehist
        import earnings_history_score_service as ehs
        db = SessionLocal()
        try:
            hist = ehist.get_earnings_history(db, symbol)
        finally:
            db.close()
        r = ehs.score_earnings_history(hist) or {}
        if str(r.get("bias")) == "NO_DATA":
            return None
        sc, mx = _f(r.get("score")), _f(r.get("max_score")) or 15.0
        return None if sc is None else max(-1.0, min(1.0, sc / mx))
    except Exception:  # noqa: BLE001
        return None


def _darkpool_bias(symbol: str) -> Optional[float]:
    """
    A low-confidence lean from off-lit prints. Dark-pool volume does not
    establish buy/sell direction on its own (per the structure's caveat), so a
    mild tilt is taken from where the heaviest prints sit relative to spot, and
    None is returned when nothing usable comes back.
    """
    try:
        import uw_darkpool_service as dp
        if not dp.configured():
            return None
        lv = dp.price_levels(symbol, top=12) or {}
        rows = lv.get("rows") or []
        import live_market_service as market
        spot = _f((market.get_quote(symbol) or {}).get("price"))
        if not rows or spot is None:
            return None
        # Weight each heavy level by its off-exchange volume; prints clustered
        # below spot are accumulation (mild bullish), above spot distribution.
        num = den = 0.0
        for r in rows:
            px = _f(r.get("price"))
            vol = _f(r.get("off_exchange_volume")) or _f(r.get("total_volume"))
            if px is None or vol is None:
                continue
            num += vol * (1 if px < spot else -1)
            den += vol
        if den == 0:
            return None
        return max(-0.5, min(0.5, num / den * 0.5))   # capped: low confidence
    except Exception:  # noqa: BLE001
        return None


def _sector_bias(symbol: str) -> Optional[float]:
    """Market tailwind from SPY's day change -- always available when open."""
    try:
        import live_market_service as market
        spy = _f((market.get_quote("SPY") or {}).get("change_percent"))
        return None if spy is None else max(-1.0, min(1.0, spy / 1.5))
    except Exception:  # noqa: BLE001
        return None


def _trade_refs(symbol: str) -> dict:
    """Entry reference, expected move, planned exit and max planned risk."""
    refs = {"entry_reference": None, "expected_move_percent": None,
            "expected_range": None, "planned_exit": None,
            "max_planned_risk_percent": None, "earnings_date": None}
    try:
        import options_levels_service as ol
        d = ol.get_levels(symbol)
        if d.get("status") == "OK":
            refs["entry_reference"] = _f(d.get("spot"))
            em = d.get("expected_move") or {}
            refs["expected_move_percent"] = _f(em.get("percent"))
            refs["expected_range"] = em.get("range")
            # The directional trade risks roughly one expected move against it.
            refs["max_planned_risk_percent"] = _f(em.get("percent"))
    except Exception:  # noqa: BLE001
        pass
    try:
        import earnings_intelligence_service as intel
        nxt = (intel.get_history(symbol, 1) or {})
        refs["earnings_date"] = (nxt.get("next_earnings")
                                 or nxt.get("next_date"))
    except Exception:  # noqa: BLE001
        pass
    refs["planned_exit"] = ("First regular US session after the earnings "
                            "release (compare 5 / 15 / 30 min after the open).")
    return refs


def get_analysis(symbol: str) -> dict:
    """The full equity earnings analysis for one ticker."""
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return {"status": "INVALID_SYMBOL", "symbol": symbol, "source": SOURCE}

    sig = _directional_signals(symbol)

    def s(*names) -> Optional[float]:
        return _avg(*(sig.get(n) for n in names))

    est = _estimate_bias(symbol)

    params = [
        eng.Param("eps_estimates", "EPS Estimates & Revisions", 15, est,
                  detail="EPS/revenue revision trend (shared revision model).",
                  unavailable_reason="" if est is not None else "No revision history."),
        eng.Param("revenue_estimates", "Revenue Estimates & Revisions", 15, est,
                  detail="Revenue revision trend (shared revision model).",
                  unavailable_reason="" if est is not None else "No revision history."),
        eng.Param("guidance", "Forward Guidance & Outlook", 15, None,
                  detail="No structured guidance feed.",
                  unavailable_reason="Guidance is not available as structured data."),
        eng.Param("historical_reaction", "Historical Earnings Reaction", 15,
                  _history_bias(symbol),
                  detail="Beat rate and post-earnings drift over recent quarters."),
        eng.Param("price_action", "Price Action & Technical Trend", 10,
                  s("price_action", "ema_trend", "rsi", "key_levels"),
                  detail="Trend, moving averages, RSI and key levels."),
        eng.Param("options_positioning", "Options Flow & Institutional Positioning", 10,
                  s("options_flow", "oi_positioning", "gamma_exposure"),
                  detail="Net options flow and open-interest positioning."),
        eng.Param("dark_pool", "Dark Pool Activity", 5, _darkpool_bias(symbol),
                  detail="Off-lit buy vs sell pressure (direction is indicative)."),
        eng.Param("unusual_options", "Unusual Options Activity", 5,
                  s("flow_by_expiry", "volume_pcr"),
                  detail="Volume above open interest / near-term positioning."),
        eng.Param("disparity", "Options Disparity", 5, s("disparity"),
                  detail="Price vs options-implied fair value."),
        eng.Param("sector_market", "Sector & Market Trend", 5, _sector_bias(symbol),
                  detail="Index breadth / market tailwind."),
    ]

    result = eng.score(params, pos_label="BUY", neg_label="SELL")
    result.update({
        "symbol": symbol,
        "engine": "EQUITY",
        "trade": _trade_refs(symbol),
        "note": ("Weights are the structure's proposed development settings, "
                 "not validated probabilities. Correlated options factors "
                 "(flow, unusual activity, disparity) are kept separate but may "
                 "overlap."),
        "source": SOURCE,
        "status": "OK" if result.get("score") is not None else "NO_DATA",
    })
    return result
