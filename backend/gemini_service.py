"""Gemini -- Anas Engine v2 (11 parameters, 100 pts; bands 68/58/43/33/32).

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
import universe_service
import claude_last_service as cl
import meta_service as mt   # _implied_vs_hist_bias

SOURCE = "Gemini — Anas Engine v2 (11 params)"

_LABEL = {
    "eps_revision": "EPS Revision Momentum",
    "revenue_revision": "Revenue Revisions & Beat Trend",
    "price_rs": "Price Action & Rel. Strength",
    "sector_market": "Sector & Market Regime",
    "options_flow": "Options Flow & Gamma Skew",
    "iv_crush": "IV Crush & Expected Move",
    "forward_guidance": "Forward Guidance Score",
    "guidance_hit_rate": "Guidance Hit-Rate (8Q beats + reaction)",
    "short_squeeze": "Short Interest Squeeze",
    "insider_buyback": "Insider / Buyback Shield",
}
_ORDER = list(_LABEL.keys())


_UNSET = object()      # score_one(hit_rate=...) not supplied -> read/fetch the cache itself


def _hit_rate_calc(symbol: str, moves: Optional[list]) -> Optional[float]:
    """Management hit-rate: share of the last 8 quarters that beat consensus EPS
    (Finnhub /stock/earnings) blended with the direction of the post-result move
    over the same window. None when Finnhub has <4 usable quarters."""
    try:
        import finnhub_service as fh
        rows = fh.earnings_history(symbol, 8)
    except Exception:  # noqa: BLE001
        return None
    beats = [1 if r["actual"] > r["estimate"] else 0 for r in rows
             if cl._f(r.get("actual")) is not None and cl._f(r.get("estimate")) is not None]
    if len(beats) < 4:
        return None
    parts = [cl._clamp((sum(beats) / len(beats) - 0.5) * 2.0)]
    mv = [cl._f(m.get("move_pct")) for m in (moves or [])[:8]]
    mv = [v for v in mv if v is not None and abs(v) > 0.2]
    if len(mv) >= 4:
        parts.append(cl._clamp((sum(1 for v in mv if v > 0) / len(mv) - 0.5) * 2.0))
    return sum(parts) / len(parts)


def hit_rate_bias(symbol: str, trade: Optional[dict] = None, fetch: bool = False) -> Optional[float]:
    """Cached (6h). fetch=False only reads the cache -- the list scan must not make
    1,000+ paced Finnhub calls; the warmer (warm_hit_rate) and detail page fetch."""
    import swr
    key = f"gemini:hitrate:{symbol}"
    moves = (trade or {}).get("historical_moves")
    if not fetch:
        v = swr.peek(key)
        return v if isinstance(v, (int, float)) else None
    v = swr.serve(key, lambda: _hit_rate_calc(symbol, moves), 6 * 3600.0)
    return v if isinstance(v, (int, float)) else None


def warm_hit_rate(symbols: list) -> int:
    """Pre-populate the hit-rate cache (Finnhub pacing is central). Returns the
    number of names that now have a value."""
    n = 0
    for s in symbols or []:
        try:
            if hit_rate_bias(str(s).upper(), None, fetch=True) is not None:
                n += 1
        except Exception:  # noqa: BLE001
            continue
    return n


def score_one(symbol: str, eq: dict, trade: Optional[dict] = None,
              row: Optional[dict] = None, fetch_hit_rate: bool = False,
              hit_rate=_UNSET) -> dict:
    biases = {p.get("name"): p.get("bias") for p in (eq.get("params") or [])}
    sig = eq.get("signals") or {}
    trade = trade or {}
    row = row or {}
    W = cfg.GEMINI_WEIGHTS

    pa = sig.get("price_action", biases.get("price_action"))
    raw: dict[str, Optional[float]] = {
        "eps_revision": biases.get("eps_estimates"),
        "revenue_revision": biases.get("revenue_estimates"),
        "price_rs": cl._avg([pa, sig.get("ema_trend"),
                             cl._rel_strength_bias(trade.get("spark"))]),
        "sector_market": biases.get("sector_market"),
        "options_flow": cl._avg([sig.get("unusual_activity", biases.get("unusual_options")),
                                 sig.get("options_flow", biases.get("options_positioning")),
                                 sig.get("disparity", biases.get("disparity"))]),
        "iv_crush": mt._implied_vs_hist_bias(trade),
        "forward_guidance": biases.get("guidance"),
        "guidance_hit_rate": (hit_rate if hit_rate is not _UNSET
                              else hit_rate_bias(symbol, trade, fetch=fetch_hit_rate)),
        "short_squeeze": cl._short_interest_bias(eq.get("short_interest"), pa),
        "insider_buyback": sig.get("insider_activity", biases.get("short_insider")),
    }

    params: list[dict] = []
    for name in _ORDER:
        qb = cl._quant(raw.get(name))
        params.append({"name": name, "label": _LABEL[name], "max": W[name],
                       "points": round(cl._pts(W[name], qb), 2), "bias": qb})

    timing_p = None
    score, cov_pct = cl.finalize(params, timing_p, row)
    cut = cfg.GEMINI_CUTOFFS
    if score >= cut["strong_buy"]:
        tier, signal = "STRONG BUY", "BUY"
    elif score >= cut["buy"]:
        tier, signal = "SPECULATIVE BUY", "BUY"
    elif score <= cut["strong_sell"]:
        tier, signal = "STRONG SHORT", "SELL"
    elif score <= cut["sell"]:
        tier, signal = "SPECULATIVE SHORT", "SELL"
    else:
        tier, signal = "NEUTRAL", "NEUTRAL"
    # Speculative buys need the IV filter (implied move below realized) to pass.
    bq = {p["name"]: p["bias"] for p in params}
    if tier == "SPECULATIVE BUY" and bq.get("iv_crush") is not None and bq["iv_crush"] <= 0:
        tier, signal = "NEUTRAL", "NEUTRAL"

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
            "score": score, "signal": signal, "tier": tier, "direction": tier,
            "params": params, "coverage": round(coverage * 100, 1), "agreement": round(agreement * 100, 1),
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
    # One batched read for the hit-rate cache (a per-name peek is a DB round trip each).
    hrs = swr.peek_many([f"gemini:hitrate:{s}" for s in seen])
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
                hr = hrs.get(f"gemini:hitrate:{s}")
                res = score_one(s, eq, trades.get(f"earn:trade:default:{s}"), r,
                                hit_rate=hr if isinstance(hr, (int, float)) else None)
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
