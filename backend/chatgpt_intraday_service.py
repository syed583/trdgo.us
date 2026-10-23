"""ChatGPT Intraday -- institutional 20-parameter intraday model (100 pts).

Scores the 20 institutional factors, mapping each to the LIVE intraday engine
(im2: VWAP, RVOL, opening range, rel strength, RSI, flow, unusual, levels, news,
market) or an equity-engine signal (OI change) where one exists. The factors that
need tick / Level-2 / volume-profile data we do NOT ingest -- market structure
(BOS/CHOCH), order flow (CVD/footprint/delta), liquidity sweeps, absorption /
icebergs, volume profile (POC/VAH/VAL), supply/demand & FVG, breakout quality,
volatility expansion, breadth/MTF -- are shown as "no data" (scored neutral, never
fabricated), with a coverage %. ~54% of the weight is currently covered.

All-positive 5-step scale, 0-100, BUY>=55 / SELL<=45. Reads caches so the board
is fast; same Trdgo Stock UI.
"""
from __future__ import annotations

from typing import Optional

import claude_last_service as cl
import intraday_safety

SOURCE = "ChatGPT Intraday — 20-param institutional"

# (key, label, weight, source) where source = ("live", pname) | ("eq", signame) | None
# The institutional 20-factor model. Factors needing tick/L2/volume-profile data
# we do not ingest (market structure, CVD/footprint, liquidity sweeps, absorption/
# icebergs, volume profile, supply/demand/FVG, breakout quality) are source=None
# -> shown as "no data" (scored neutral, never fabricated) with a coverage %.
PARAMS = [
    ("market_structure", "Market Structure (BOS/CHOCH, HH/HL)", 10, None),
    ("options_flow", "Institutional Options Flow (Sweeps+Prem)", 10, ("live", "flow")),
    ("order_flow", "Order Flow (Net Premium / Delta)", 9, ("enrich", "order_flow")),
    ("vwap_ema", "VWAP, Anchored VWAP & EMA", 9, ("live", "vwap_pos")),
    ("liquidity_sweeps", "Liquidity Sweeps / Stop Hunts", 8, None),
    ("rvol", "Relative Volume (RVOL) & Acceleration", 8, ("live", "rvol")),
    ("rel_strength", "Market & Sector Relative Strength", 7, ("live", "rel_strength")),
    ("absorption", "Absorption / Icebergs / Replenishment", 6, None),
    ("gamma", "Gamma Exposure & Dealer Positioning", 6, ("either", "levels", "gamma")),
    ("volume_profile", "Volume Profile (POC/VAH/VAL)", 5, None),
    ("supply_demand", "Supply/Demand Zones & Fair Value Gaps", 4, None),
    ("orb", "Opening Range Breakout / Breakdown", 4, ("live", "orb")),
    ("unusual", "Unusual Options Activity", 3, ("live", "unusual")),
    ("momentum", "Momentum (RSI / MACD / ROC / ADX)", 3, ("live", "rsi")),
    ("breakout_quality", "Breakout Quality & Price Acceptance", 2, None),
    ("catalyst", "News, Earnings & Economic Catalysts", 2, ("live", "news")),
    ("volatility", "Volatility Expansion / Compression", 1, None),
    ("oi_change", "Options OI Changes & Positioning", 1, ("eq", "daily_oi_change")),
    ("intermarket", "Intermarket Confirmation", 1, ("live", "market")),
    ("breadth_mtf", "Market Breadth (Tape)", 1, ("mkt", "breadth")),
]


def _attainable_live() -> float:
    """Weight of the parameters that HAVE a live data source on the board (the rest never have data)."""
    return float(sum(w for _k, _l, w, src in PARAMS if src and src[0] in ("live", "eq", "mkt", "either")))


def _attainable_equity() -> float:
    return float(sum(w for k, _l, w, _s in PARAMS
                     if _EQ_SRC.get(k) and (_EQ_SRC[k][0] or _EQ_SRC[k][1])))


def _score(symbol: str, s: dict, eq: Optional[dict], enrich: Optional[dict] = None) -> dict:
    live = {p.get("name"): p for p in (s.get("params") or [])}
    sig = (eq or {}).get("signals") or {}
    enrich = enrich or {}
    # Cheap market-wide breadth (shared, cached) -- used on board and detail.
    try:
        import intraday_enrich as ie
        mkt = {"breadth": ie.market_breadth_bias()}
    except Exception:  # noqa: BLE001
        mkt = {}

    def _live_bias(name) -> Optional[float]:
        p = live.get(name)
        if not p or not p.get("has_data"):
            return None
        mx, v = p.get("max"), p.get("value")
        return cl._clamp(v / mx) if (mx and v is not None) else None

    def _bias(source) -> Optional[float]:
        if not source:
            return None
        kind = source[0]
        if kind == "live":
            return _live_bias(source[1])
        if kind == "eq":
            v = sig.get(source[1])
            return cl._clamp(v) if v is not None else None
        if kind == "mkt":
            v = mkt.get(source[1])
            return cl._clamp(v) if v is not None else None
        if kind == "enrich":          # per-name (detail only)
            v = enrich.get(source[1])
            return cl._clamp(v) if v is not None else None
        if kind == "either":          # live source on the board, enrich overrides on detail
            _, livename, enrichname = source
            e = enrich.get(enrichname)
            return cl._clamp(e) if e is not None else _live_bias(livename)
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
            "coverage_attainable": _attainable_live() if not enrich else None,
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
    # Per-name GEX + net-premium flow only on the single-stock detail view
    # (fetch=True); the board (fetch=False) stays light to respect the UW limit.
    enrich = None
    if fetch:
        try:
            import intraday_enrich as ie
            enrich = ie.enrich(sym)
        except Exception:  # noqa: BLE001
            enrich = None
    return _score(sym, s, eq, enrich)


# Pre/after-hours fallback: the live 5-min engine has no bars, so score off the
# shared equity signals through THIS model's OWN weights. Each ChatGPT factor
# maps to an equity signal (or a sector fallback bias) where one exists; the rest
# stay "no data" (neutral). This keeps ChatGPT and Claude Pro DISTINCT when the
# market is closed instead of both echoing the identical raw pre-market board.
_EQ_SRC = {
    "options_flow": ("options_flow", "options_positioning"),
    "order_flow": ("disparity", "disparity"),
    "vwap_ema": ("ema_trend", "price_action"),
    "rvol": ("volume_pcr", None),
    "gamma": ("key_levels", None),
    "unusual": ("unusual_activity", "unusual_options"),
    "momentum": ("rsi", None),
    "catalyst": ("event_radar", None),
    "oi_change": ("daily_oi_change", None),
    "breadth_mtf": ("__breadth__", None),
}


def _score_equity(symbol: str, eq: dict) -> dict:
    """Score the 20-factor model from the equity signals (closed-market path)."""
    sig = (eq or {}).get("signals") or {}
    biases = {p.get("name"): p.get("bias") for p in (eq.get("params") or [])}
    try:
        import intraday_enrich as ie
        breadth = ie.market_breadth_bias()
    except Exception:  # noqa: BLE001
        breadth = None

    def _b(key):
        src = _EQ_SRC.get(key)
        if not src:
            return None
        s0, b0 = src
        if s0 == "__breadth__":
            return breadth
        v = sig.get(s0)
        if v is None and b0:
            v = biases.get(b0)
        return cl._clamp(v) if v is not None else None

    params: list[dict] = []
    for key, label, weight, _source in PARAMS:
        qb = cl._quant(_b(key))
        params.append({"name": key, "label": label, "max": weight,
                       "points": round(cl._pts(weight, qb), 2), "bias": qb,
                       "no_data": qb is None})
    score = round(sum(p["points"] for p in params), 1)
    cut = cl.cfg.V3_CUTOFFS
    signal = "BUY" if score >= cut["buy"] else "SELL" if score <= cut["sell"] else "NEUTRAL"
    covered = sum(p["max"] for p in params if not p["no_data"])
    return {"symbol": symbol, "score": score, "signal": signal, "params": params,
            "coverage": round(covered / 100.0 * 100, 1),
            "coverage_attainable": _attainable_equity(),
            "entry": None, "now": None, "tp": None, "sl": None,
            "model_status": "equity-fallback"}


def _equity_board() -> dict:
    import ai_trade_service as ats
    from concurrent.futures import ThreadPoolExecutor
    import intraday_grok_service as gk        # reuse its equity cache + level math
    syms = [s.upper() for s in getattr(ats, "UNIVERSE", [])]
    try:
        import intraday_indicators as _ind
        phase = _ind.market_phase()
    except Exception:  # noqa: BLE001
        phase = None

    def _one(s: str):
        try:
            eq = gk._eq_cached(s)
            if eq and eq.get("params"):
                eq, sig_age, src = intraday_safety.effective_eq(s, eq)
                r = _score_equity(s, eq)
                gk._levels(r)                  # entry = live/last price, +-ATR stop/target
                intraday_safety.gate(r, phase=phase, signals_age_s=sig_age,
                                     price_age=intraday_safety.price_age_s(s), inputs_source=src)
                return r
        except Exception:  # noqa: BLE001
            return None
        return None

    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for r in ex.map(_one, syms):
            if r:
                rows.append(r)
    return intraday_safety.compose(rows, SOURCE + " · closed-market (equity signals)", len(syms), phase)


def board() -> dict:
    # Before/after the session the live engine has no bars -> score off the equity
    # signals through ChatGPT's own weights (NOT the shared pre-market board).
    import swr
    try:
        import intraday_indicators as ind
        if ind.market_phase() in ("pre", "closed"):
            return swr.serve("chatgptintra:eqboard", _equity_board, 120.0)
    except Exception:  # noqa: BLE001
        pass
    import ai_trade_service as ats
    syms = [s.upper() for s in getattr(ats, "UNIVERSE", [])]
    try:
        import intraday_indicators as _ind
        phase = _ind.market_phase()
    except Exception:  # noqa: BLE001
        phase = None
    rows: list[dict] = []
    for sym in syms:
        try:
            r = score_one(sym)
            if r:
                age = intraday_safety.age_s(f"im2:{sym}")      # how old is the live engine score?
                intraday_safety.gate(r, phase=phase, signals_age_s=age, price_age=age,
                                     model_status=r.get("model_status"),
                                     inputs_label="live 5-minute engine score",
                                     inputs_source="live engine (im2)")
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
    return intraday_safety.compose(rows, SOURCE, len(syms), phase)
