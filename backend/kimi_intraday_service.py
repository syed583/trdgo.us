"""Kimi Intraday -- live intraday direction on the framework's 6 categories.

Same live engine as Claude Pro Intraday (intraday_model: VWAP, intraday EMA,
opening range, RVOL, 0-2DTE flow, levels, market/sector) re-bucketed into the
Kimi framework's category structure (100 pts directional, VIX/IV sizing kept
separate):

  Trend & VWAP        16   Volume & Delta        16
  Options Tape        24   Market Context        18
  Catalyst & Events   12   Levels & Confirmation 14

Sub-factors the live engine does not produce (cumulative volume delta, large
prints, VIX, breadth, analyst actions, intraday short/borrow) are reported as
"no data" and scored neutral (never silently zero), with a coverage %. Reads
Trdgo Stock's live per-name cache so the board is instant.
"""
from __future__ import annotations

from typing import Optional

import claude_last_service as cl

SOURCE = "Kimi Intraday — Live 6-category Direction"

# block -> (label, [(sub_label, intraday_model_param_or_None, weight)])
BLOCKS = {
    "trend_vwap": ("Trend & VWAP", [
        ("VWAP relation & slope", "vwap_pos", 6),
        ("5/15-min structure", "ema_trend", 5),
        ("Opening range", "orb", 3),
        ("Gap fill / hold", "gap", 2)]),
    "volume_delta": ("Volume & Delta", [
        ("RVOL (time-of-day)", "rvol", 6),
        ("Cumulative volume delta", None, 6),
        ("Large prints / tape", None, 4)]),
    "options_tape": ("Options Tape (intraday)", [
        ("Premium-weighted flow", "flow", 8),
        ("Sweeps / blocks", "unusual", 6),
        ("Intraday skew shift", "disparity", 5),
        ("Flow by expiry (0DTE)", None, 5)]),
    "market_ctx": ("Market Context", [
        ("SPY/QQQ intraday trend", "market", 6),
        ("Sector ETF rel. strength", "sector", 5),
        ("VIX intraday move", None, 4),
        ("Breadth (TICK / A-D)", None, 3)]),
    "catalyst": ("Catalyst & Events", [
        ("News freshness", "news", 6),
        ("Earnings / event timing", None, 4),
        ("Analyst actions", None, 2)]),
    "levels": ("Levels & Confirmation", [
        ("Key levels (PM/prior/LOD-HOD)", "levels", 6),
        ("Put/Call vs norm", "pc_ratio", 4),
        ("Short interest / borrow", None, 4)]),
}


def score_from_model(symbol: str, s: dict) -> dict:
    pmap = {p.get("name"): p for p in (s.get("params") or [])}

    def _bias(name: Optional[str]) -> Optional[float]:
        if not name:
            return None
        p = pmap.get(name)
        if not p or not p.get("has_data"):
            return None
        mx, v = p.get("max"), p.get("value")
        if not mx or v is None:
            return None
        return cl._clamp(v / mx)

    params: list[dict] = []
    for key, (label, subs) in BLOCKS.items():
        srows = []
        for slabel, pname, w in subs:
            qb = cl._quant(_bias(pname))
            srows.append({"name": slabel, "max": w, "points": round(cl._pts(w, qb), 2),
                          "bias": qb, "no_data": qb is None})
        bmax = sum(s2["max"] for s2 in srows)
        bpts = round(sum(s2["points"] for s2 in srows), 2)
        bvals = [s2["bias"] for s2 in srows if s2["bias"] is not None]
        bbias = round(sum(bvals) / len(bvals), 3) if bvals else None
        params.append({"name": key, "label": label, "max": bmax, "points": bpts,
                       "bias": bbias, "sub": srows})

    score = round(sum(p["points"] for p in params), 1)
    cut = cl.cfg.V3_CUTOFFS
    signal = "BUY" if score >= cut["buy"] else "SELL" if score <= cut["sell"] else "NEUTRAL"
    covered = sum(s2["max"] for p in params for s2 in p["sub"] if not s2["no_data"])

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
            "coverage": round(covered / 100.0 * 100, 1),
            "entry": entry, "now": now, "tp": tp, "sl": sl, "spot": entry,
            "atr": round(atr, 2) if atr else None,
            "move_size": round(atr / spot * 100, 2) if (atr and spot) else None,
            "model_status": s.get("status")}


def score_one(symbol: str, fetch: bool = False) -> Optional[dict]:
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
    # Before the open the live engine has no bars -> pre-market board fallback.
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
