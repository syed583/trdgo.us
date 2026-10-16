"""ChatGPT -- US Pre-Earnings Direction Model V1 (TRDGO.ai spec, 16 features).

Predicts BULLISH / NEUTRAL / BEARISH direction from the last pre-earnings close
to the first post-announcement close, from 16 normalised (-1..+1) features scored
on an all-positive 0-100 scale (neutral/missing = half). Each feature is read
from the already-warmed equity signals (earn:equity) + trade summary (earn:trade)
so the whole-universe scan needs no extra per-name calls; analyst dispersion is
the one exception (Finnhub) and is computed on the detail page only.

Per the spec the output also carries data COVERAGE, UNCERTAINTY and a RAW
probability (calibration is deferred -- it needs the 90-day forward outcomes we
do not have yet, so the probability is flagged uncalibrated rather than faked).
Thresholds are configurable (tab_settings.CHATGPT_THRESHOLDS).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import tab_settings as cfg
import claude_last_service as cl
import meta_service as mt   # reuse _implied_vs_hist_bias
import grok_service as gk   # reuse _analyst_bias (Finnhub, cached)

SOURCE = "ChatGPT — US Pre-Earnings Direction V1"
MODEL_VERSION = "chatgpt-dir-v1"

_LABEL = {
    "eps_revisions": "EPS Revisions",
    "revenue_revisions": "Revenue Revisions",
    "forward_guidance": "Forward Guidance",
    "prior_history": "Prior Earnings/Guidance History",
    "net_premium_flow": "Net Options Premium Flow",
    "unusual_options": "Unusual Options Activity",
    "skew_disparity": "Options Skew / Disparity",
    "hist_reactions": "Historical Earnings Reactions",
    "price_action_rs": "Price Action / Relative Strength",
    "peer_readthrough": "Peer Read-through",
    "analyst_dispersion": "Analyst Estimate Dispersion",
    "short_interest": "Short Interest",
    "institutional": "Institutional / Fund Activity",
    "insider": "Insider Activity",
    "implied_vs_realized": "Implied vs Realized Move",
    "ema_trend": "EMA Trend Confirmation",
}
_ORDER = list(_LABEL.keys())


def score_one(symbol: str, eq: dict, trade: Optional[dict] = None,
              row: Optional[dict] = None, with_analyst: bool = True) -> dict:
    biases = {p.get("name"): p.get("bias") for p in (eq.get("params") or [])}
    sig = eq.get("signals") or {}
    trade = trade or {}
    row = row or {}
    W = cfg.CHATGPT_WEIGHTS

    pa = sig.get("price_action", biases.get("price_action"))
    raw: dict[str, Optional[float]] = {
        "eps_revisions": biases.get("eps_estimates"),
        "revenue_revisions": biases.get("revenue_estimates"),
        "forward_guidance": biases.get("guidance"),
        "prior_history": sig.get("earnings_results"),
        "net_premium_flow": sig.get("options_flow", biases.get("options_positioning")),
        "unusual_options": sig.get("unusual_activity", biases.get("unusual_options")),
        "skew_disparity": sig.get("disparity", biases.get("disparity")),
        "hist_reactions": cl._hist_reaction_bias(trade.get("historical_moves")),
        "price_action_rs": pa,
        "peer_readthrough": biases.get("peer_readthrough"),
        "analyst_dispersion": (gk._analyst_bias(symbol) if with_analyst else None),
        "short_interest": cl._short_interest_bias(eq.get("short_interest"), pa),
        "institutional": sig.get("funding_activity"),
        "insider": sig.get("insider_activity", biases.get("short_insider")),
        "implied_vs_realized": mt._implied_vs_hist_bias(trade),
        "ema_trend": sig.get("ema_trend"),
    }

    params: list[dict] = []
    for name in _ORDER:
        qb = cl._quant(raw.get(name))
        params.append({"name": name, "label": _LABEL[name], "max": W[name],
                       "points": round(cl._pts(W[name], qb), 2), "bias": qb})

    score = round(sum(p["points"] for p in params), 1)
    th = cfg.CHATGPT_THRESHOLDS
    # Spec vocabulary: BULLISH / NEUTRAL / BEARISH. Keep BUY/SELL on `signal` so
    # the shared list UI colours the card; `direction` is the spec label.
    if score >= th["bullish"]:
        signal, direction = "BUY", "BULLISH"
    elif score <= th["bearish"]:
        signal, direction = "SELL", "BEARISH"
    else:
        signal, direction = "NEUTRAL", "NEUTRAL"

    covered = sum(p["max"] for p in params if p["bias"] is not None)
    pos_w = sum(p["max"] for p in params if (p["bias"] or 0) > 0.05)
    neg_w = sum(p["max"] for p in params if (p["bias"] or 0) < -0.05)
    coverage = round(covered / 100.0, 3)
    side = 1 if score >= 50 else -1
    agreement = round(((pos_w if side > 0 else neg_w) / covered), 3) if covered else 0.0
    conf = cfg.V3_CONFIDENCE
    low_conf = coverage < conf["min_coverage"] or agreement < conf["min_agreement"]
    # Uncertainty rises as coverage and agreement fall. Raw (uncalibrated) prob
    # is the score as a 0..1 directional confidence -- NOT a calibrated P(up).
    uncertainty = round((1 - (coverage * 0.5 + agreement * 0.5)) * 100, 1)
    prob_raw = round(score / 100.0, 3)

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
            "score": score, "signal": signal, "direction": direction, "params": params,
            "coverage": round(coverage * 100, 1), "agreement": round(agreement * 100, 1),
            "uncertainty": uncertainty, "probability_raw": prob_raw, "calibrated": False,
            "model_version": MODEL_VERSION, "low_confidence": low_conf}


def scan(days: int = 15) -> dict:
    """Score every name reporting in the next ~`days`, ranked by score."""
    import swr
    import uw_earnings_calendar as calmod

    rows = (calmod.calendar(days=days) or {}).get("rows") or []
    seen: set[str] = set()
    uniq: list[dict] = []
    for r in rows:
        s = (r.get("symbol") or "").upper()
        if s and s not in seen:
            seen.add(s)
            uniq.append(r)
    eqs = swr.peek_many([f"earn:equity:{s}" for s in seen])
    trades = swr.peek_many([f"earn:trade:default:{s}" for s in seen])
    try:
        import finviz_service as fv
        fmap = fv.earnings_map()
    except Exception:  # noqa: BLE001
        fmap = {}

    def _days_to(r: dict):
        d = r.get("date")
        if not d:
            return None
        try:
            return (datetime.fromisoformat(str(d)[:10]).date() - date.today()).days
        except Exception:  # noqa: BLE001
            return None

    out: list[dict] = []
    missing: list[str] = []
    scored = 0
    for r in uniq:
        s = r["symbol"].upper()
        eq = eqs.get(f"earn:equity:{s}")
        res = None
        if eq and eq.get("params"):
            try:
                res = score_one(s, eq, trades.get(f"earn:trade:default:{s}"), r,
                                with_analyst=False)
                scored += 1
            except Exception:  # noqa: BLE001
                res = None
        if res is None:
            missing.append(s)
            res = {"symbol": s, "company": r.get("company"), "date": r.get("date"),
                   "date_label": r.get("date_label"), "time_label": r.get("short_label"),
                   "report_timing": r.get("time_label") or r.get("timing_label"),
                   "days_to": _days_to(r), "prior_close": r.get("prior_close"),
                   "market_cap": r.get("market_cap"), "sector": r.get("sector"),
                   "score": None, "signal": "PENDING", "direction": "PENDING", "params": [],
                   "coverage": None, "agreement": None, "uncertainty": None,
                   "probability_raw": None, "low_confidence": None}
        fvi = fmap.get(s)
        if fvi and fvi.get("time") and str(fvi.get("date"))[:10] == str(res.get("date"))[:10]:
            res["report_clock"] = fvi["time"]
        out.append(res)
    out.sort(key=lambda x: (x["score"] if x["score"] is not None else -1), reverse=True)
    return {"status": "OK", "source": SOURCE, "model_version": MODEL_VERSION, "rows": out,
            "universe": len(seen), "scored": scored, "missing": missing[:60]}
