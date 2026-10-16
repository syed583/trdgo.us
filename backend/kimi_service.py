"""Kimi -- Pre-Earnings Direction (14 factors, category-grouped).

From the owner's CSV, grouped Fundamentals / Options / Price-Volume / Context /
Confirmation. Adds price-target & rating changes, relative strength vs sector
(replacing the EMA factor) and a SPY-trend + VIX market-regime factor. All-
positive 5-step scale, score 0-100, BUY>=55 / NEUTRAL / SELL<=45.

Reads the warmed equity signals + trade cache; the market-regime factor is
computed once per scan (SPY trend + VIX) and shared across names. Price-target /
rating changes use Finnhub and so are computed on the detail page only (scored
neutral in the list, like analyst dispersion).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import tab_settings as cfg
import claude_last_service as cl
import meta_service as mt   # _implied_vs_hist_bias
import grok_service as gk   # _analyst_bias (Finnhub) as a rating-change proxy

SOURCE = "Kimi — Pre-Earnings Direction (14 factors)"

_LABEL = {
    "eps_rev": "EPS + Revenue Revisions",
    "guidance": "Guidance / Beat-Raise History",
    "price_target": "Price Target & Rating Changes",
    "peers": "Peer Read-through",
    "em_vs_hist": "Expected Move vs Historical",
    "unusual_flow": "Unusual Flow (sweeps/blocks)",
    "skew_oi": "Skew + OI Changes",
    "drift": "Pre-earnings Drift / Structure",
    "rel_strength": "Relative Strength vs Sector",
    "hist_reaction": "Historical Reaction Pattern",
    "spy_vix": "SPY Trend + VIX Regime",
    "short_interest": "Short Interest Change",
    "insider": "Insider (open-market buys)",
    "dark_pool": "Dark Pool",
}
_ORDER = list(_LABEL.keys())


def market_bias() -> Optional[float]:
    """SPY trend + VIX regime, one market-wide number cached briefly and shared
    across every name in a scan. SPY 15d drift sets the trend; VIX sets risk-on
    (low = bullish) / risk-off (high = bearish)."""
    import swr
    return swr.serve("kimi:market", _market_bias_calc, 600.0)


def _market_bias_calc() -> Optional[float]:
    spy = cl._spy_15d_return()                      # SPY 15-session return
    trend = cl._clamp(spy / 0.03) if spy is not None else None
    vix_reg = None
    try:
        import unusualwhales_service as uw
        q = uw.get_quote("VIX") or uw.get_quote("^VIX") or {}
        vix = cl._f(q.get("price"))
        if vix is not None:
            # <15 calm (+), 15-25 neutral, >25 fear (-); map to +-0.5.
            vix_reg = cl._clamp((18.0 - vix) / 10.0)
    except Exception:  # noqa: BLE001
        vix_reg = None
    parts = [p for p in (trend, vix_reg) if p is not None]
    if not parts:
        return None
    return cl._clamp(sum(parts) / len(parts))


def score_one(symbol: str, eq: dict, trade: Optional[dict] = None,
              row: Optional[dict] = None, with_analyst: bool = True,
              mkt: Optional[float] = "__compute__") -> dict:
    biases = {p.get("name"): p.get("bias") for p in (eq.get("params") or [])}
    sig = eq.get("signals") or {}
    trade = trade or {}
    row = row or {}
    W = cfg.KIMI_WEIGHTS
    if mkt == "__compute__":
        mkt = market_bias()

    pa = sig.get("price_action", biases.get("price_action"))
    raw: dict[str, Optional[float]] = {
        "eps_rev": cl._avg([biases.get("eps_estimates"), biases.get("revenue_estimates")]),
        "guidance": biases.get("guidance"),
        "price_target": (gk._analyst_bias(symbol) if with_analyst else None),
        "peers": biases.get("peer_readthrough"),
        "em_vs_hist": mt._implied_vs_hist_bias(trade),
        "unusual_flow": sig.get("unusual_activity", biases.get("unusual_options")),
        "skew_oi": cl._avg([sig.get("disparity", biases.get("disparity")),
                            sig.get("oi_positioning")]),
        "drift": pa,
        "rel_strength": cl._rel_strength_bias(trade.get("spark")),
        "hist_reaction": cl._hist_reaction_bias(trade.get("historical_moves")),
        "spy_vix": mkt,
        "short_interest": cl._short_interest_bias(eq.get("short_interest"), pa),
        "insider": sig.get("insider_activity", biases.get("short_insider")),
        "dark_pool": biases.get("dark_pool"),
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
    mkt = market_bias()
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
                                with_analyst=False, mkt=mkt)
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
