"""Gemini Intraday -- 80/20 market/company execution & flow model (live board).

The demo scorecard's structure: heavy real-time options tape + momentum (market
pillar, 80) plus a small company/catalyst pillar (20). IV / expected move is
SIZING ONLY and excluded from the directional score (reported as move_size).

Directional sub-weights summed to 91 in the spec; rescaled x100/91 to 100.
Reads the equity-engine signals (like Grok/Meta Intraday), board over the liquid
universe, same Trdgo Stock UI. BUY>=55 / SELL<=45.
"""
from __future__ import annotations

from typing import Optional

import tab_settings as cfg
import intraday_safety
import claude_last_service as cl
import intraday_grok_service as ig   # _eq_cached / _levels

SOURCE = "Gemini Intraday — 80/20 Execution & Flow"

_LABEL = {
    "options_flow": "Options Flow",
    "unusual_activity": "Unusual Activity",
    "disparity": "Options Disparity",
    "volume_pcr": "Volume & Put/Call",
    "key_levels": "Key Option Levels",
    "oi_change": "Daily OI Change + Expiry",
    "ema_trend": "EMA / Trend Alignment",
    "rsi_price": "RSI + Price Action",
    "insider_ownership": "Insider & Ownership",
    "earnings_catalyst": "Earnings / M&A / Funding",
    "dividends_event": "Dividends & Event Radar",
}
_ORDER = list(_LABEL.keys())


def score_one(symbol: str, eq: dict, trade: Optional[dict] = None,
              row: Optional[dict] = None) -> dict:
    biases = {p.get("name"): p.get("bias") for p in (eq.get("params") or [])}
    sig = eq.get("signals") or {}
    row = row or {}
    W = cfg.GEMINI_INTRADAY_WEIGHTS

    raw: dict[str, Optional[float]] = {
        "options_flow": sig.get("options_flow", biases.get("options_positioning")),
        "unusual_activity": sig.get("unusual_activity", biases.get("unusual_options")),
        "disparity": sig.get("disparity", biases.get("disparity")),
        "volume_pcr": sig.get("volume_pcr"),
        "key_levels": sig.get("key_levels"),
        "oi_change": cl._avg([sig.get("daily_oi_change"), sig.get("flow_by_expiry"),
                              sig.get("oi_positioning")]),
        "ema_trend": sig.get("ema_trend"),
        "rsi_price": cl._avg([sig.get("rsi"), sig.get("price_action", biases.get("price_action"))]),
        "insider_ownership": cl._avg([sig.get("insider_activity"), biases.get("short_insider")]),
        "earnings_catalyst": cl._avg([sig.get("earnings_results"), sig.get("merger_activity"),
                                      sig.get("funding_activity")]),
        "dividends_event": cl._avg([sig.get("dividend_trend"), sig.get("event_radar")]),
    }

    params: list[dict] = []
    for name in _ORDER:
        qb = cl._quant(raw.get(name))
        params.append({"name": name, "label": _LABEL[name], "max": W[name],
                       "points": round(cl._pts(W[name], qb), 2), "bias": qb})

    score = round(sum(p["points"] for p in params), 1)
    cut = cfg.V3_CUTOFFS
    signal = "BUY" if score >= cut["buy"] else "SELL" if score <= cut["sell"] else "NEUTRAL"

    covered = sum(p["max"] for p in params if p["bias"] is not None)
    pos_w = sum(p["max"] for p in params if (p["bias"] or 0) > 0.05)
    neg_w = sum(p["max"] for p in params if (p["bias"] or 0) < -0.05)
    coverage = round(covered / 100.0, 3)
    side = 1 if score >= 50 else -1
    agreement = round(((pos_w if side > 0 else neg_w) / covered), 3) if covered else 0.0
    conf = cfg.V3_CONFIDENCE
    low_conf = coverage < conf["min_coverage"] or agreement < conf["min_agreement"]

    # IV / expected move -> sizing only (move %), never direction.
    ms = None
    if trade:
        em = (trade.get("expected_move") or {})
        ms = cl._f(em.get("percent"))

    return {"symbol": symbol, "company": row.get("company"),
            "date": row.get("date"), "sector": row.get("sector"),
            "score": score, "signal": signal, "params": params,
            "coverage": round(coverage * 100, 1), "agreement": round(agreement * 100, 1),
            "move_size": round(ms, 2) if ms else None, "low_confidence": low_conf}


def board() -> dict:
    """Live board over the liquid universe (equity signals), same UI as Trdgo Stock."""
    import ai_trade_service as ats
    from concurrent.futures import ThreadPoolExecutor
    syms = [s.upper() for s in getattr(ats, "UNIVERSE", [])]
    try:
        import intraday_indicators as _ind
        phase = _ind.market_phase()
    except Exception:  # noqa: BLE001
        phase = None

    def _one(s: str):
        try:
            eq = ig._eq_cached(s)
            if eq and eq.get("params"):
                eq, sig_age, src = intraday_safety.effective_eq(s, eq)
                r = score_one(s, eq, None, {"symbol": s})
                ig._levels(r)
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
    return intraday_safety.compose(rows, SOURCE, len(syms), phase)
