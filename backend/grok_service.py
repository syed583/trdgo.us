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
import universe_service
import claude_last_service as cl   # reuse _quant / _pts / _f / helpers

SOURCE = "Grok — Pre-Earnings Direction (9 factors)"

# Grok = "Anas Earnings Prediction Model v1" — 11 parameters / 100 pts.
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
_STRENGTH = {
    "eps_estimates": "High", "price_action_rs": "High", "options_combined": "High",
    "revenue_estimates": "Moderate", "sector_market": "Moderate",
    "forward_guidance": "Moderate", "peer_readthrough": "Moderate",
    "implied_vs_realized": "Low-Moderate", "dark_pool": "Low",
    "short_insider": "Low", "calendar_timing": "Low",
}


def _analyst_bias(symbol: str) -> Optional[float]:
    """Cached wrapper -- the recommendation trend changes slowly, so persist it
    6h so the scan never re-hits Finnhub for the same name within a session.

    Keep-last-good: if a refresh comes back empty (Finnhub down, rate-limited,
    or a transient miss) it must NOT replace a previously good reading with a
    missing value. When the fresh calc is None but a prior numeric value is
    cached, we re-serve the prior value, so the cache never regresses to None
    over real data. A genuinely never-seen name (no prior) still caches None."""
    import swr
    key = f"grok:analyst:{symbol}"

    def _calc():
        v = _analyst_bias_calc(symbol)
        if v is None:
            prev = swr.peek(key)
            if isinstance(prev, (int, float)):
                return prev
        return v

    v = swr.serve(key, _calc, 6 * 3600.0)
    # swr returns a status dict ({"status": "LOADING"/"DATA_UNAVAILABLE"}) when a
    # cold build yields None. The scorer must only ever see a float or None, so
    # coerce anything non-numeric to None (no-data) -- never a dict.
    return v if isinstance(v, (int, float)) else None


def warm_analyst(symbols: list, per_minute: int = 55) -> int:
    """Pre-populate the 6h analyst cache for `symbols` so the All Models scan
    serves from cache instead of per-name live calls. Pacing is handled centrally
    by finnhub_service._rate_gate (shared across every Finnhub caller), so this
    just loops. A rate-limited/failed fetch keeps the last good value (never
    overwrites with None, never fabricates). Returns how many names now have a
    non-None analyst reading. Safe to run in the background; non-blocking by
    design when the caller runs it on its own thread."""
    import swr
    import finnhub_service as fh
    if not fh.configured():
        return 0
    got = 0
    for s in [str(x).upper() for x in (symbols or []) if x]:
        try:
            if _analyst_bias(s) is not None:
                got += 1
        except Exception:  # noqa: BLE001
            pass
    return got


_POS_REC = ("buy", "overweight", "outperform", "positive", "accumulate", "add")
_NEG_REC = ("sell", "underweight", "underperform", "negative", "reduce")


def _uw_analyst_bias(symbol: str, days: int = 90) -> Optional[float]:
    """Analyst lean from Unusual Whales /api/screener/analysts: each recent rating
    scores +1 (buy-type) / -1 (sell-type) / 0 (hold); an upgrade adds +0.5 and a
    downgrade -0.5. Average over the last `days`, clamped to -1..+1. None when UW
    has no rating in the window."""
    try:
        import unusualwhales_service as uw
        from datetime import datetime, timedelta, timezone
        out = uw.analyst_actions(symbol, 50)
        rows = uw._rows(out) if out.get("status") == "OK" else []
    except Exception:  # noqa: BLE001
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    vals: list[float] = []
    for r in rows:
        try:
            ts = datetime.fromisoformat(str(r.get("timestamp")).replace("Z", "+00:00"))
        except Exception:  # noqa: BLE001
            continue
        if ts < cutoff or str(r.get("ticker") or symbol).upper() != symbol.upper():
            continue
        rec = str(r.get("recommendation") or "").lower()
        v = 1.0 if any(k in rec for k in _POS_REC) else -1.0 if any(k in rec for k in _NEG_REC) else 0.0
        act = str(r.get("action") or "").lower()
        v += 0.5 if "upgrad" in act else -0.5 if "downgrad" in act else 0.0
        vals.append(v)
    return cl._clamp(sum(vals) / len(vals)) if vals else None


def _analyst_bias_calc(symbol: str) -> Optional[float]:
    """Analyst lean: Unusual Whales analyst ratings first; Finnhub recommendation
    counts (net (strongBuy+buy - sell-strongSell)/total) only as a fallback when
    UW has nothing. Needs FINNHUB_API_KEY for the fallback, else None."""
    uwb = _uw_analyst_bias(symbol)
    if uwb is not None:
        return uwb
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
        "eps_estimates":      biases.get("eps_estimates"),
        "price_action_rs":    pa_bias,
        "options_combined":   options_lean,   # flow + unusual + skew, merged
        "revenue_estimates":  biases.get("revenue_estimates"),
        "sector_market":      biases.get("sector_market"),
        "forward_guidance":   biases.get("guidance"),
        "peer_readthrough":   biases.get("peer_readthrough"),
        "implied_vs_realized": cl._realized_vs_implied_bias(trade.get("historical_moves"), options_lean),
        "dark_pool":          biases.get("dark_pool"),
        "short_insider":      cl._avg([cl._short_interest_bias(eq.get("short_interest"), pa_bias), insider_b]),
        "calendar_timing":    None,            # no directional bias available -> neutral
    }

    params: list[dict] = []
    for name in _ORDER:
        qb = cl._quant(raw.get(name))
        params.append({"name": name, "label": _LABEL[name], "max": W[name],
                       "points": round(cl._pts(W[name], qb), 2), "bias": qb,
                       "strength": _STRENGTH[name]})

    timing_p = next((p for p in params if p["name"] == "calendar_timing"), None)
    score, cov_pct = cl.finalize(params, timing_p, row)
    cut = cfg.V3_CUTOFFS
    signal = "BUY" if score >= cut["buy"] else "SELL" if score <= cut["sell"] else "NEUTRAL"

    tier = None
    if cl.low_data(cov_pct):           # below the minimum data coverage: no BUY/SELL
        signal, tier = "LOW_DATA", "Low data"
    covered = sum(p["max"] for p in params if p["bias"] is not None)
    pos_w = sum(p["max"] for p in params if (p["bias"] or 0) > 0.05)
    neg_w = sum(p["max"] for p in params if (p["bias"] or 0) < -0.05)
    coverage = round(cov_pct / 100.0, 3)
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
            "score": score, "signal": signal, "tier": tier, "params": params,
            "coverage": round(coverage * 100, 1), "agreement": round(agreement * 100, 1),
            "low_confidence": low_conf}


def scan(days: int = 15) -> dict:
    """Score every name reporting in the next ~`days`, ranked by score."""
    import swr
    import uw_earnings_calendar as calmod

    rows = (calmod.calendar(days=days) or {}).get("rows") or []
    rows = universe_service.restrict(rows)      # top-1000 liquid names only
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
