"""Claude Pro Intraday -- a true intraday direction board (critique-driven).

Reuses the LIVE intraday engine (intraday_model, 5-min bars: VWAP, intraday EMA,
opening range, RVOL, 0-2DTE flow, levels, market/sector context) and scores the
V2 8-factor intraday model:

  Options Flow (Sweeps + Premium)   25   Price Structure (VWAP/EMA/ORB)  20
  Gamma / Key Option Levels         15   Volume + Put/Call               12
  Market Context (SPY/QQQ/Sector)   10   Unusual Activity                 8
  Momentum (RSI / VWAP slope)        5   Pre-market Bias (Gap + OI)       5

IV / expected move is kept OUT of the direction score and reported separately as
`move_size` (sizing/risk). Recomputed every ~45s via intraday_model's cache.
BUY>=55 / NEUTRAL / SELL<=45.
"""
from __future__ import annotations

from typing import Optional

import claude_last_service as cl

SOURCE = "Claude Pro Intraday — Live V2 8-factor Direction"

# V2 8-factor intraday model (directional, 100 pts). Each factor merges the live
# intraday_model params it maps to. Inputs we do not ingest -- true dealer GEX
# gamma-flip (gamma_levels uses OI/key levels instead), 5m/15m swing structure,
# and VIX -- are absent, so those merges fall back to "no data" (neutral) rather
# than being fabricated. V2's trading gates (BLOCKED on session/RVOL/spread/event)
# are not applied here so Claude Pro stays comparable in the All-Intraday matrix.
BLOCKS = {
    "options_flow": ("Options Flow (Sweeps + Premium)", 25, ["flow", "disparity"]),
    "price_structure": ("Price Structure (VWAP/EMA/ORB)", 20, ["vwap_pos", "ema_trend", "orb"]),
    "gamma_levels": ("Gamma / Key Option Levels", 15, ["levels"]),
    "volume_pc": ("Volume + Put/Call", 12, ["rvol", "pc_ratio"]),
    "market_context": ("Market Context (SPY/QQQ/Sector)", 10, ["market", "sector"]),
    "unusual_activity": ("Unusual Activity", 8, ["unusual"]),
    "momentum": ("Momentum (RSI / VWAP slope)", 5, ["rsi"]),
    "premarket_bias": ("Pre-market Bias (Gap + OI)", 5, ["gap", "coi"]),
}


def score_from_model(symbol: str, s: dict) -> dict:
    """Re-bucket an intraday_model.score() result into the 7 blocks."""
    pmap = {p.get("name"): p for p in (s.get("params") or [])}

    def _bias(name: str) -> Optional[float]:
        p = pmap.get(name)
        if not p or not p.get("has_data"):
            return None
        mx = p.get("max")
        v = p.get("value")
        if not mx or v is None:
            return None
        return cl._clamp(v / mx)      # intraday_model values are signed, scaled to max

    gates = s.get("gates") or {}
    params: list[dict] = []
    for key, (label, weight, members) in BLOCKS.items():
        if key == "liquidity":
            g = gates.get("G7_liquidity")
            b = 1.0 if g is True else -1.0 if g is False else None
        else:
            vals = [x for x in (_bias(m) for m in members) if x is not None]
            b = (sum(vals) / len(vals)) if vals else None
        qb = cl._quant(b)
        params.append({"name": key, "label": label, "max": weight,
                       "points": round(cl._pts(weight, qb), 2), "bias": qb})

    score = round(sum(p["points"] for p in params), 1)
    cut = cl.cfg.V3_CUTOFFS
    signal = "BUY" if score >= cut["buy"] else "SELL" if score <= cut["sell"] else "NEUTRAL"

    covered = sum(p["max"] for p in params if p["bias"] is not None)
    coverage = round(covered / 100.0 * 100, 1)

    # Entry / stop / target from the live spot + ATR the engine already has.
    spot = cl._f(s.get("spot"))
    atr = cl._f(s.get("atr"))
    entry = now = sl = tp = None
    if spot is not None:
        entry = now = round(spot, 2)
        if atr and signal in ("BUY", "SELL"):
            sgn = 1 if signal == "BUY" else -1
            sl = round(spot - sgn * 0.5 * atr, 2)
            tp = round(spot + sgn * 1.0 * atr, 2)

    return {"symbol": symbol, "score": score, "signal": signal, "params": params,
            "coverage": coverage, "entry": entry, "now": now, "tp": tp, "sl": sl,
            "spot": entry, "atr": round(atr, 2) if atr else None,
            "move_size": round(atr / spot * 100, 2) if (atr and spot) else None,
            "model_status": s.get("status"), "spy_above_vwap": s.get("spy_above_vwap")}


def score_one(symbol: str, fetch: bool = False) -> Optional[dict]:
    """Re-bucket the live intraday score for one name. By default reads Trdgo
    Stock's live cache (im2:{sym}, kept fresh by its own ~minute scan); only
    fetches bars itself when `fetch=True` (the single-name detail endpoint)."""
    import swr
    s = swr.peek(f"im2:{symbol.upper()}")
    if not s and fetch:
        try:
            import intraday_model as im
            s = im.score(symbol)
        except Exception:  # noqa: BLE001
            s = None
    if not s:
        return None
    return score_from_model(symbol, s)


def board() -> dict:
    """Live board over the liquid universe. Reads Trdgo Stock's live per-name
    scores (im2:{sym}); if too few are cached, kicks a Trdgo Stock scan so the
    next refresh is populated. Before the open the 5-min engine has no bars, so
    fall back to the pre-market board (gap + overnight flow)."""
    import swr
    try:
        import intraday_indicators as ind
        if ind.market_phase() in ("pre", "closed"):
            import intraday_premarket as ipm
            return swr.serve("pm:board", lambda: ipm.board(), 120.0)
    except Exception:  # noqa: BLE001
        pass
    import ai_trade_service as ats
    syms = [s.upper() for s in getattr(ats, "UNIVERSE", [])]

    rows: list[dict] = []
    for sym in syms:
        try:
            r = score_one(sym)
            if r:
                rows.append(r)
        except Exception:  # noqa: BLE001
            pass

    # If Trdgo Stock hasn't populated enough names, trigger its scan in the
    # background so the board fills on the next refresh.
    if len(rows) < len(syms) * 0.5:
        import threading
        def _kick():
            try:
                import intraday_signal_v2 as eng
                eng.scan_once()
            except Exception:  # noqa: BLE001
                pass
        threading.Thread(target=_kick, daemon=True).start()
    buyers = sorted([r for r in rows if r["signal"] == "BUY"], key=lambda r: r["score"], reverse=True)
    sellers = sorted([r for r in rows if r["signal"] == "SELL"], key=lambda r: r["score"])
    neutral = [r for r in rows if r["signal"] == "NEUTRAL"]
    try:
        import intraday_indicators as ind
        phase = ind.market_phase()
    except Exception:  # noqa: BLE001
        phase = None
    return {"status": "OK", "source": SOURCE, "phase": phase,
            "buyers": buyers, "sellers": sellers, "neutral": neutral,
            "counts": {"buy": len(buyers), "sell": len(sellers), "neutral": len(neutral)},
            "universe": len(syms), "scored": len(rows)}
