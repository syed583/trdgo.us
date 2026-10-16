"""Grok -- Pre-Earnings Direction from 9 research-strength factors.

A second opinion alongside Claude New, weighted by each factor's evidence
strength (the owner's research table) rather than the v2 spec:

  EPS Revisions            18  High
  Prior Beat History       15  Moderate-High
  Options Skew / Flow      12  Moderate
  Realized vs Implied      11  Moderate
  Peer Read-through        11  Moderate (timing dependent)
  Short Interest Changes   10  Moderate
  Analyst Dispersion       10  Moderate
  Pre-earnings Momentum     8  Low-Moderate
  Dark Pool / Insiders      5  Low

Same all-positive 5-step scale as Claude New (points = weight*(0.5+0.5*bias),
neutral/missing = half), sum = 100, BUY >= 55 / NEUTRAL / SELL <= 45. Reuses the
warmed equity engine (earn:equity) + trade summary (earn:trade) and the shared
Claude New helpers, so the two models read the same data.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import tab_settings as cfg
import claude_last_service as cl   # reuse _quant / _pts / _f / helpers

SOURCE = "Grok — Pre-Earnings Direction (9 factors)"

_LABEL = {
    "eps_revisions": "EPS Revisions",
    "prior_beat_history": "Prior Beat History",
    "options_skew_flow": "Options Skew / Flow",
    "realized_vs_implied": "Realized vs Implied Move",
    "peer_readthrough": "Peer Read-through",
    "short_interest": "Short Interest Changes",
    "analyst_dispersion": "Analyst Dispersion",
    "premarket_momentum": "Pre-earnings Momentum",
    "dark_pool_insiders": "Dark Pool / Insiders",
}
_ORDER = list(_LABEL.keys())
_STRENGTH = {
    "eps_revisions": "High", "prior_beat_history": "Moderate-High",
    "options_skew_flow": "Moderate", "realized_vs_implied": "Moderate",
    "peer_readthrough": "Moderate", "short_interest": "Moderate",
    "analyst_dispersion": "Moderate", "premarket_momentum": "Low-Moderate",
    "dark_pool_insiders": "Low",
}


def _analyst_bias(symbol: str) -> Optional[float]:
    """Cached wrapper -- the recommendation trend changes slowly, so persist it
    6h so the scan never re-hits Finnhub for the same name within a session."""
    import swr
    return swr.serve(f"grok:analyst:{symbol}", lambda: _analyst_bias_calc(symbol), 6 * 3600.0)


def _analyst_bias_calc(symbol: str) -> Optional[float]:
    """Analyst consensus/dispersion from Finnhub recommendation counts: net
    (strongBuy+buy - sell-strongSell)/total. Needs FINNHUB_API_KEY, else None."""
    try:
        import finnhub_service as fh
        if not fh.configured():
            return None
        rec = fh.recommendation(symbol) or {}
    except Exception:  # noqa: BLE001
        return None
    sb = cl._f(rec.get("strongBuy")) or 0
    b = cl._f(rec.get("buy")) or 0
    s = cl._f(rec.get("sell")) or 0
    ss = cl._f(rec.get("strongSell")) or 0
    h = cl._f(rec.get("hold")) or 0
    tot = sb + b + h + s + ss
    if tot <= 0:
        return None
    return cl._clamp((sb + b - s - ss) / tot)


def _momentum_bias(spark: Optional[list]) -> Optional[float]:
    """Pre-earnings momentum: price drift over the last ~10 sessions, +-5% = full."""
    closes = [c for c in (cl._f(c) for c in (spark or [])) if c is not None]
    if len(closes) < 11:
        return None
    drift = (closes[-1] - closes[-11]) / closes[-11]
    return cl._clamp(drift / 0.05)


def score_one(symbol: str, eq: dict, trade: Optional[dict] = None,
              row: Optional[dict] = None, with_analyst: bool = True) -> dict:
    # with_analyst=False in the list scan: the analyst-dispersion Finnhub call is
    # one request per name, too slow for the whole universe, so it is scored
    # neutral in the list and computed for real on the detail page.
    biases = {p.get("name"): p.get("bias") for p in (eq.get("params") or [])}
    sig = eq.get("signals") or {}
    trade = trade or {}
    row = row or {}
    W = cfg.GROK_WEIGHTS

    pa_bias = sig.get("price_action", biases.get("price_action"))
    options_lean = cl._avg([sig.get("options_flow", biases.get("options_positioning")),
                            sig.get("unusual_activity", biases.get("unusual_options")),
                            sig.get("disparity", biases.get("disparity"))])
    insider_b = sig.get("insider_activity", biases.get("short_insider"))

    raw: dict[str, Optional[float]] = {
        "eps_revisions": biases.get("eps_estimates"),
        "prior_beat_history": biases.get("guidance"),
        "options_skew_flow": options_lean,
        "realized_vs_implied": cl._realized_vs_implied_bias(trade.get("historical_moves"), options_lean),
        "peer_readthrough": biases.get("peer_readthrough"),
        "short_interest": cl._short_interest_bias(eq.get("short_interest"), pa_bias),
        "analyst_dispersion": (_analyst_bias(symbol) if with_analyst else None),
        "premarket_momentum": _momentum_bias(trade.get("spark")),
        "dark_pool_insiders": cl._avg([biases.get("dark_pool"), insider_b]),
    }

    params: list[dict] = []
    for name in _ORDER:
        qb = cl._quant(raw.get(name))
        params.append({"name": name, "label": _LABEL[name], "max": W[name],
                       "points": round(cl._pts(W[name], qb), 2), "bias": qb,
                       "strength": _STRENGTH[name]})

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
                   "score": None, "signal": "PENDING", "params": [],
                   "coverage": None, "agreement": None, "low_confidence": None}
        fvi = fmap.get(s)
        if fvi and fvi.get("time") and str(fvi.get("date"))[:10] == str(res.get("date"))[:10]:
            res["report_clock"] = fvi["time"]
        out.append(res)
    out.sort(key=lambda x: (x["score"] if x["score"] is not None else -1), reverse=True)
    return {"status": "OK", "source": SOURCE, "rows": out,
            "universe": len(seen), "scored": scored, "missing": missing[:60]}
