"""ChatGPT Intraday -- the recommended TRDGO 18-parameter intraday model (100 pts).

Blends the LIVE intraday engine (im2: VWAP, RVOL, opening range, breadth, rel
strength, EMA, RSI, flow, unusual, disparity, levels) with a few equity-engine
signals (price action, daily OI change, flow by expiry, OI positioning). Two
parameters have no source yet (cumulative volume delta, liquidity sweep / BOS /
CHOCH) and are shown as "no data" (scored neutral, not silent zero) with a
coverage %.

All-positive 5-step scale, 0-100, BUY>=55 / SELL<=45. Reads caches so the board
is fast; same Trdgo Stock UI.
"""
from __future__ import annotations

from typing import Optional

import claude_last_service as cl

SOURCE = "ChatGPT Intraday — TRDGO 18-param"

# (key, label, weight, source) where source = ("live", pname) | ("eq", signame) | None
PARAMS = [
    ("options_flow", "Options Flow", 10, ("live", "flow")),
    ("unusual", "Unusual Options Activity", 7, ("live", "unusual")),
    ("disparity", "Options Disparity", 5, ("live", "disparity")),
    ("volume_pcr", "Volume & Put/Call", 5, ("live", "pc_ratio")),
    ("key_levels", "Key Option Levels", 4, ("live", "levels")),
    ("daily_oi_change", "Daily OI Change", 2, ("eq", "daily_oi_change")),
    ("flow_by_expiry", "Flow by Expiry", 1, ("eq", "flow_by_expiry")),
    ("oi_positioning", "OI Positioning", 1, ("eq", "oi_positioning")),
    ("ema_trend", "EMA / Trend", 10, ("live", "ema_trend")),
    ("rsi", "RSI (14)", 3, ("live", "rsi")),
    ("price_action", "Price Action", 9, ("eq", "price_action")),
    ("vwap", "VWAP / Anchored VWAP", 8, ("live", "vwap_pos")),
    ("rvol", "Relative Volume (RVOL)", 7, ("live", "rvol")),
    ("breadth", "Market Breadth / SPY-QQQ", 7, ("live", "market")),
    ("cum_delta", "Order Flow / Cumulative Delta", 6, None),
    ("liq_sweep", "Liquidity Sweep / BOS / CHOCH", 5, None),
    ("orb", "Opening Range Breakout", 5, ("live", "orb")),
    ("rel_strength", "Relative Strength vs Sector", 5, ("live", "rel_strength")),
]


def _score(symbol: str, s: dict, eq: Optional[dict]) -> dict:
    live = {p.get("name"): p for p in (s.get("params") or [])}
    sig = (eq or {}).get("signals") or {}

    def _bias(source) -> Optional[float]:
        if not source:
            return None
        kind, name = source
        if kind == "live":
            p = live.get(name)
            if not p or not p.get("has_data"):
                return None
            mx, v = p.get("max"), p.get("value")
            return cl._clamp(v / mx) if (mx and v is not None) else None
        if kind == "eq":
            v = sig.get(name)
            return cl._clamp(v) if v is not None else None
        return None

    params: list[dict] = []
    for key, label, weight, source in PARAMS:
        qb = cl._quant(_bias(source))
        params.append({"name": key, "label": label, "max": weight,
                       "points": round(cl._pts(weight, qb), 2), "bias": qb,
                       "no_data": qb is None})

    score = round(sum(p["points"] for p in params), 1)
    cut = cl.cfg.V3_CUTOFFS
    signal = "BUY" if score >= cut["buy"] else "SELL" if score <= cut["sell"] else "NEUTRAL"
    covered = sum(p["max"] for p in params if not p["no_data"])

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
    sym = symbol.upper()
    s = swr.peek(f"im2:{sym}")
    if not s and fetch:
        try:
            import intraday_model as im
            s = im.score(sym)
        except Exception:  # noqa: BLE001
            s = None
    if not s:
        return None
    eq = swr.peek(f"earn:equity:{sym}")
    if not eq and fetch:
        try:
            import earnings_equity_service as eq_svc
            eq = eq_svc.get_analysis(sym)
        except Exception:  # noqa: BLE001
            eq = None
    return _score(sym, s, eq)


def board() -> dict:
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
