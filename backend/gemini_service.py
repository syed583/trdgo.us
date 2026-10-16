"""Gemini -- 90-Day US Market Earnings Model (6 weighted categories).

Category-level scoring matrix from the spec: each category is the average of its
sub-components (each a normalised -1..+1 directional bias), then weighted:

  Fundamental Momentum   25  EPS revisions + prior guidance / beat history
  Derivatives & Flow     20  unusual options flow + skew & sentiment disparity
  Volatility Edge        15  implied move vs historical realized move
  Technical Structure    15  EMA 20/50/200 alignment + price-action structure
  Institutional Footprint15  dark-pool blocks + heavy-volume node clustering
  Cross-Asset Read-through10 sector peer performance (early reporters)

All-positive 5-step scale, composite 0-100, BUY>=55 / NEUTRAL / SELL<=45. Every
input is read from the warmed equity signals + trade cache -> no per-name calls.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import tab_settings as cfg
import claude_last_service as cl
import meta_service as mt   # _implied_vs_hist_bias

SOURCE = "Gemini — 90-Day Earnings Model (6 categories)"

_LABEL = {
    "fundamental_momentum": "Fundamental Momentum",
    "derivatives_flow": "Derivatives & Flow",
    "volatility_edge": "Volatility Edge",
    "technical_structure": "Technical Structure",
    "institutional": "Institutional Footprint",
    "peer_readthrough": "Cross-Asset Read-through",
}
_ORDER = list(_LABEL.keys())


def score_one(symbol: str, eq: dict, trade: Optional[dict] = None,
              row: Optional[dict] = None) -> dict:
    biases = {p.get("name"): p.get("bias") for p in (eq.get("params") or [])}
    sig = eq.get("signals") or {}
    trade = trade or {}
    row = row or {}
    W = cfg.GEMINI_WEIGHTS

    # Each category = average of its normalised sub-components.
    raw: dict[str, Optional[float]] = {
        "fundamental_momentum": cl._avg([biases.get("eps_estimates"), biases.get("guidance")]),
        "derivatives_flow": cl._avg([sig.get("unusual_activity", biases.get("unusual_options")),
                                     sig.get("options_flow", biases.get("options_positioning")),
                                     sig.get("disparity", biases.get("disparity"))]),
        "volatility_edge": mt._implied_vs_hist_bias(trade),
        "technical_structure": cl._avg([sig.get("ema_trend"),
                                        sig.get("price_action", biases.get("price_action"))]),
        "institutional": cl._avg([biases.get("dark_pool"), sig.get("oi_positioning")]),
        "peer_readthrough": biases.get("peer_readthrough"),
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
                res = score_one(s, eq, trades.get(f"earn:trade:default:{s}"), r)
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
                   "score": None, "signal": "PENDING", "params": [],
                   "coverage": None, "agreement": None, "low_confidence": None}
        fvi = fmap.get(s)
        if fvi and fvi.get("time") and str(fvi.get("date"))[:10] == str(res.get("date"))[:10]:
            res["report_clock"] = fvi["time"]
        out.append(res)
    out.sort(key=lambda x: (x["score"] if x["score"] is not None else -1), reverse=True)
    return {"status": "OK", "source": SOURCE, "rows": out,
            "universe": len(seen), "scored": scored, "missing": missing[:60]}
