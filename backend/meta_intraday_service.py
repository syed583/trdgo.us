"""Meta Intraday -- 8-signal short-term directional model (live board).

Same live board as Grok Intraday (the liquid universe, same UI as Trdgo Stock),
scored with an 8-signal intraday matrix weighted toward options flow:

  Options Flow (sweeps+prem)  25   Price Trend & Structure   20
  Unusual Activity (filtered) 15   Gamma / Key Option Levels 12
  Volume + Put/Call           10   Near-Term OI Change        8
  Event / Catalyst             5   RSI / Momentum             5

All-positive 5-step scale, 0-100, BUY>=55 / SELL<=45. Reads the live equity
signals; reuses Grok Intraday's board plumbing (cache + entry/TP/SL levels).
"""
from __future__ import annotations

import uw_priority as _uwp

from datetime import date, datetime
from typing import Optional

import tab_settings as cfg
import intraday_safety
import claude_last_service as cl
import intraday_grok_service as ig   # reuse _eq_cached / _price_atr / _levels

SOURCE = "Meta Intraday — Short-term Direction (8 signals)"

_LABEL = {
    "options_flow": "Options Flow (Sweeps + Premium)",
    "ema_trend_price": "Price Trend & Structure",
    "unusual_activity": "Unusual Activity (Filtered)",
    "key_levels": "Gamma / Key Option Levels",
    "volume_pcr": "Volume + Put/Call",
    "daily_oi_change": "Near-Term OI Change",
    "event_radar": "Event / Catalyst",
    "rsi_momentum": "RSI / Momentum",
}
# Display/scoring order follows the ranked weighting (highest first).
_ORDER = list(_LABEL.keys())


def score_one(symbol: str, eq: dict, trade: Optional[dict] = None,
              row: Optional[dict] = None) -> dict:
    biases = {p.get("name"): p.get("bias") for p in (eq.get("params") or [])}
    sig = eq.get("signals") or {}
    row = row or {}
    W = cfg.META_INTRADAY_WEIGHTS

    raw: dict[str, Optional[float]] = {
        "options_flow": sig.get("options_flow", biases.get("options_positioning")),
        "ema_trend_price": cl._avg([sig.get("ema_trend"),
                                    sig.get("price_action", biases.get("price_action"))]),
        "unusual_activity": sig.get("unusual_activity", biases.get("unusual_options")),
        "key_levels": sig.get("key_levels"),
        "volume_pcr": sig.get("volume_pcr"),
        "daily_oi_change": sig.get("daily_oi_change"),
        "event_radar": sig.get("event_radar"),
        "rsi_momentum": sig.get("rsi"),
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

    days_to = None
    d = row.get("date")
    if d:
        try:
            days_to = (datetime.fromisoformat(str(d)[:10]).date() - date.today()).days
        except Exception:  # noqa: BLE001
            days_to = None

    return {"symbol": symbol, "company": row.get("company"),
            "date": d, "date_label": row.get("date_label"),
            "time_label": row.get("short_label"), "days_to": days_to,
            "report_timing": row.get("time_label") or row.get("timing_label"),
            "prior_close": row.get("prior_close"), "market_cap": row.get("market_cap"),
            "sector": row.get("sector"),
            "score": score, "signal": signal, "params": params,
            "coverage": round(coverage * 100, 1), "agreement": round(agreement * 100, 1),
            "low_confidence": low_conf}


@_uwp.tagged(_uwp.ESSENTIAL, "ai_boards")
def board() -> dict:
    """Live board over the liquid universe, scored with the 7-signal model."""
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
    return intraday_safety.compose(rows, SOURCE, len(syms), phase)
