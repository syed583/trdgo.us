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

import uw_priority as _uwp

from typing import Optional

import claude_last_service as cl
import intraday_safety

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


def score_from_model(symbol: str, s: dict, enrich: Optional[dict] = None) -> dict:
    """Score the V2 8-factor model from an intraday_model.score() result. `enrich`
    (per-name GEX from the detail view) overrides gamma_levels when present; the
    cheap market-wide breadth is folded into market_context on board and detail."""
    pmap = {p.get("name"): p for p in (s.get("params") or [])}
    enrich = enrich or {}
    try:
        import intraday_enrich as ie
        breadth = ie.market_breadth_bias()
    except Exception:  # noqa: BLE001
        breadth = None

    def _bias(name: str) -> Optional[float]:
        p = pmap.get(name)
        if not p or not p.get("has_data"):
            return None
        mx = p.get("max")
        v = p.get("value")
        if not mx or v is None:
            return None
        return cl._clamp(v / mx)      # intraday_model values are signed, scaled to max

    params: list[dict] = []
    for key, (label, weight, members) in BLOCKS.items():
        vals = [x for x in (_bias(m) for m in members) if x is not None]
        if key == "market_context" and breadth is not None:
            vals.append(breadth)                       # A: market-wide tape breadth
        b = (sum(vals) / len(vals)) if vals else None
        if key == "gamma_levels" and enrich.get("gamma") is not None:
            b = cl._clamp(enrich["gamma"])             # B: real dealer GEX flip (detail)
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
    # Per-name GEX only on the single-stock detail view (fetch=True).
    enrich = None
    if fetch:
        try:
            import intraday_enrich as ie
            enrich = ie.enrich(symbol)
        except Exception:  # noqa: BLE001
            enrich = None
    return score_from_model(symbol, s, enrich)


# Pre/after-hours fallback: the live 5-min engine has no bars, so score off the
# shared equity signals through Claude Pro's OWN V2 weights. Each block maps to an
# equity signal where one exists; the rest stay "no data" (neutral). This keeps
# Claude Pro DISTINCT from ChatGPT when the market is closed, instead of both
# echoing the identical raw pre-market board.
_EQ_SRC = {
    "options_flow": ("options_flow", "options_positioning"),
    "price_structure": ("ema_trend", "price_action"),
    "gamma_levels": ("key_levels", None),
    "volume_pc": ("volume_pcr", None),
    "market_context": ("__breadth__", None),
    "unusual_activity": ("unusual_activity", "unusual_options"),
    "momentum": ("rsi", None),
    "premarket_bias": (None, None),            # no equity equivalent -> no data
}


def _score_equity(symbol: str, eq: dict) -> dict:
    """Score the V2 8-factor model from the equity signals (closed-market path)."""
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
        if s0 is None:
            return None
        v = sig.get(s0)
        if v is None and b0:
            v = biases.get(b0)
        return cl._clamp(v) if v is not None else None

    params: list[dict] = []
    for key, (label, weight, _members) in BLOCKS.items():
        qb = cl._quant(_b(key))
        params.append({"name": key, "label": label, "max": weight,
                       "points": round(cl._pts(weight, qb), 2), "bias": qb})
    score = round(sum(p["points"] for p in params), 1)
    cut = cl.cfg.V3_CUTOFFS
    signal = "BUY" if score >= cut["buy"] else "SELL" if score <= cut["sell"] else "NEUTRAL"
    covered = sum(p["max"] for p in params if p["bias"] is not None)
    return {"symbol": symbol, "score": score, "signal": signal, "params": params,
            "coverage": round(covered / 100.0 * 100, 1),
            "coverage_attainable": float(sum(w for k, (_l, w, _m) in BLOCKS.items()
                                             if _EQ_SRC.get(k) and (_EQ_SRC[k][0] or _EQ_SRC[k][1]))),
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
                                     price_age=intraday_safety.price_age_s(s), inputs_source=src,
                                     meta=eq.get("_meta"))
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


@_uwp.tagged(_uwp.ESSENTIAL, "ai_boards")
def board() -> dict:
    """Live board over the liquid universe. Reads Trdgo Stock's live per-name
    scores (im2:{sym}); if too few are cached, kicks a Trdgo Stock scan so the
    next refresh is populated. Before/after the session the 5-min engine has no
    bars, so score off the equity signals through Claude Pro's own weights."""
    import swr
    try:
        import intraday_indicators as ind
        if ind.market_phase() in ("pre", "closed"):
            return swr.serve("claudepro:eqboard", _equity_board, 120.0)
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
    return intraday_safety.compose(rows, SOURCE, len(syms), phase)
