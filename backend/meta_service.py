"""Meta -- Pre-Earnings Direction (owner's 11-factor / 100-pt model).

Extends the direction models with three expectation/structure factors -- Implied
Move vs Historical Move, IV Rank and Gamma/OI Concentration -- on top of the
fundamental and flow factors. Every input is read from the already-warmed equity
signals (earn:equity) + trade summary (earn:trade), so the whole-universe scan
needs no extra per-name API calls and stays fast.

Factors / weights (sum = 100):
  EPS Revisions                 13
  Implied Move vs Historical    13
  Options Flow                  11
  IV Rank                       11
  Unusual Skew                   9
  Prior Guidance / Beat          9
  Revenue Revisions / Whisper    9
  Historical Price Reaction      8
  Gamma / OI Concentration       8
  Peer Read-through              7
  Short Interest                 2

Same all-positive 5-step scale as Claude New (neutral/missing = half), BUY >= 55
/ NEUTRAL / SELL <= 45. Reuses the Claude New helpers.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import tab_settings as cfg
import claude_last_service as cl

SOURCE = "Meta — Pre-Earnings Direction (11 factors)"

# Meta = owner's 11-parameter / 100-pt model (same spec as Grok "Anas v1").
_LABEL = {
    "eps_estimates":      "EPS Estimates & Revisions",
    "price_action_rs":    "Price Action & Relative Strength",
    "options_combined":   "Options Flow + Unusual + Put/Call Skew",
    "revenue_estimates":  "Revenue Estimates & Revisions",
    "sector_market":      "Sector & Market Regime",
    "forward_guidance":   "Forward Guidance & Analyst Outlook",
    "peer_readthrough":   "Peer Earnings Read-through",
    "implied_vs_realized": "Implied vs Realized Move",
    "dark_pool":          "Dark Pool Confirmation",
    "short_insider":      "Short Interest & Insider Activity",
    "calendar_timing":    "Earnings Calendar & Timing",
}
_ORDER = list(_LABEL.keys())


def _implied_vs_hist_bias(trade: dict) -> Optional[float]:
    """IM ratio = implied move % / avg abs historical move %. Cheap options
    (ratio < 0.9) favour a move -> bullish lean; richly priced (> 1.5) -> 0/short."""
    em = (trade or {}).get("expected_move") or {}
    im_pct = cl._f(em.get("percent"))
    mags = [abs(x) for x in (cl._f(m.get("move_pct")) for m in (trade or {}).get("historical_moves") or [])
            if x is not None]
    hist = (sum(mags) / len(mags)) if mags else None
    if im_pct is None or not hist:        # None/empty/zero -> no usable ratio
        return None
    ratio = im_pct / hist
    if ratio < 0.9:
        return 1.0
    if ratio < 1.1:
        return 0.5
    if ratio < 1.3:
        return 0.0
    if ratio < 1.5:
        return -0.5
    return -1.0


def score_one(symbol: str, eq: dict, trade: Optional[dict] = None,
              row: Optional[dict] = None) -> dict:
    biases = {p.get("name"): p.get("bias") for p in (eq.get("params") or [])}
    sig = eq.get("signals") or {}
    trade = trade or {}
    row = row or {}
    W = cfg.META_WEIGHTS

    pa_bias = sig.get("price_action", biases.get("price_action"))
    options_lean = cl._avg([sig.get("options_flow", biases.get("options_positioning")),
                            sig.get("unusual_activity", biases.get("unusual_options")),
                            sig.get("disparity", biases.get("disparity"))])
    insider_b = sig.get("insider_activity", biases.get("short_insider"))
    raw: dict[str, Optional[float]] = {
        "eps_estimates":      biases.get("eps_estimates"),
        "price_action_rs":    pa_bias,
        "options_combined":   options_lean,   # flow + unusual + skew, merged
        "revenue_estimates":  biases.get("revenue_estimates"),
        "sector_market":      biases.get("sector_market"),
        "forward_guidance":   biases.get("guidance"),
        "peer_readthrough":   biases.get("peer_readthrough"),
        "implied_vs_realized": _implied_vs_hist_bias(trade),
        "dark_pool":          biases.get("dark_pool"),
        "short_insider":      cl._avg([cl._short_interest_bias(eq.get("short_interest"), pa_bias), insider_b]),
        "calendar_timing":    None,            # no directional bias available -> neutral
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
