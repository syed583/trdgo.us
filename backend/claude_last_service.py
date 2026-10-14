"""Claude Last -- Pre-Earnings Direction Model v3 (two-sided, SELL-capable).

Replaces Tab A v2. Same all-positive point rule as v2
(points = weight * (0.5 + 0.5 * bias), neutral/missing = half weight), but the
weights and the emphasis change so the model can genuinely say SELL:

  * Price Action is the heaviest input (22) and carries real relative strength
    vs SPY, so a lagging stock scores below neutral on its own.
  * Historical reaction now uses the actual share-price move over the last ~8
    reports (from the corrected earnings-move history), not EPS beat-rate.
  * Revenue estimates are dropped (they duplicate EPS and are almost always
    positive, which quietly biased every stock upward).
  * Cut-offs tighten to BUY >= 55 / SELL <= 45, with a confidence meter
    (coverage + agreement) that flags thin or conflicted signals.

Biases come from the already-warmed equity engine (earn:equity:{SYM}); the
price-reaction and relative-strength inputs are derived from the warmed trade
summary (earn:trade:default:{SYM}) when available.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import tab_settings as cfg

SOURCE = "Claude Last — Direction v3.1"

_LABEL = {
    "price_action": "Price Action + EMA + Rel. Strength",
    "options_flow_unusual": "Options Flow + Unusual",
    "skew_disparity": "Put-Call Skew + Disparity",
    "eps_rev_dir": "EPS Revision Direction",
    "hist_price_reaction": "Historical Price Reaction",
    "insider": "Insider Activity",
    "peers": "Peer Read-through",
    "short_interest": "Short Interest",
    "prior_guidance": "Prior Guidance Delivery",
    "dark_pool": "Dark Pool (confirmation)",
}


def _clamp(b: float) -> float:
    return max(-1.0, min(1.0, b))


def _pts(weight: float, bias: Optional[float]) -> float:
    if bias is None:
        return weight * 0.5
    return weight * (0.5 + 0.5 * _clamp(bias))


def _avg(vals: list[Optional[float]]) -> Optional[float]:
    xs = [v for v in vals if v is not None]
    return sum(xs) / len(xs) if xs else None


def _f(v) -> Optional[float]:
    try:
        f = float(v)
        return f if f == f else None  # drop NaN
    except (TypeError, ValueError):
        return None


# --- Relative strength vs SPY ------------------------------------------------
def _spy_15d_return() -> Optional[float]:
    """SPY's 15-session return, cached briefly (shared across the scan)."""
    import swr

    def _calc() -> Optional[float]:
        try:
            import live_market_service as market
            chart = market.get_chart("SPY", "3M") or {}
            closes = [b["close"] for b in (chart.get("bars") or []) if b.get("close")]
            if len(closes) >= 16:
                return (closes[-1] - closes[-16]) / closes[-16]
        except Exception:  # noqa: BLE001
            return None
        return None

    return swr.serve("v3:spy15d", _calc, 600.0)


def _rel_strength_bias(spark: Optional[list]) -> Optional[float]:
    """(stock 15d return - SPY 15d return) / 5%, clamped to +/-1."""
    closes = [c for c in (spark or []) if _f(c) is not None]
    if len(closes) < 16:
        return None
    stock = (closes[-1] - closes[-16]) / closes[-16]
    spy = _spy_15d_return()
    if spy is None:
        return None
    return _clamp((stock - spy) / 0.05)


def _short_interest_bias(meta: Optional[dict], price_dir: Optional[float]) -> Optional[float]:
    """Two-sided, read WITH price: a crowded short into a rising stock is a
    bullish squeeze setup; a crowded/rising short into a falling stock is
    bearish. Low short interest = no signal."""
    if not meta:
        return None
    si = cfg.V3_SHORT_INT
    pof = _f(meta.get("pct_float"))
    dtc = _f(meta.get("days_to_cover"))
    mags = []
    if pof is not None:
        mags.append(min(1.0, pof / si["high_pct_float"]))
    if dtc is not None:
        mags.append(min(1.0, dtc / si["high_days_to_cover"]))
    if not mags:
        return None
    mag = sum(mags) / len(mags)            # 0..1 how crowded the short is
    if mag < 0.4:
        return 0.0                         # low/stable short interest -> no signal
    if price_dir is None or abs(price_dir) < 0.1:
        return 0.0                         # high short but price flat -> wait
    # High short + rising price => squeeze (bullish); high short + falling price
    # => short pressure confirmed (bearish).
    return _clamp(mag * _clamp(price_dir))


def _hist_reaction_bias(moves: Optional[list]) -> Optional[float]:
    """Direction of the actual price move over the last reports: (up-down)/n."""
    vals = [_f(m.get("move_pct")) for m in (moves or []) if _f(m.get("move_pct")) is not None]
    if not vals:
        return None
    ups = sum(1 for v in vals if v > 0.2)
    downs = sum(1 for v in vals if v < -0.2)
    n = ups + downs
    if n == 0:
        return 0.0
    return _clamp((ups - downs) / n)


def score_one(symbol: str, eq: dict, trade: Optional[dict] = None,
              row: Optional[dict] = None) -> dict:
    biases = {p.get("name"): p.get("bias") for p in (eq.get("params") or [])}
    sig = eq.get("signals") or {}        # raw directional biases (pure insider etc.)
    trade = trade or {}
    row = row or {}
    W = cfg.V3_WEIGHTS
    PS = cfg.V3_PRICE_SPLIT
    FS = cfg.V3_FLOW_SPLIT
    SS = cfg.V3_SKEW_SPLIT
    params: list[dict] = []

    # 1) Price Action (20) = EMA trend 7 + 15d drift 7 + relative strength 6.
    pa = biases.get("price_action")
    rs = _rel_strength_bias(trade.get("spark"))
    pa_sub = [
        ("ema_trend", PS["ema_trend"], pa),
        ("drift_15d", PS["drift_15d"], pa),
        ("rel_strength", PS["rel_strength"], rs),
    ]
    pa_pts = sum(_pts(w, b) for _, w, b in pa_sub)
    pa_bias = _avg([pa, pa, rs])
    params.append({"name": "price_action", "label": _LABEL["price_action"],
                   "points": round(pa_pts, 2), "max": W["price_action"],
                   "bias": (round(pa_bias, 3) if pa_bias is not None else None),
                   "sub": [{"name": n, "max": w, "points": round(_pts(w, b), 2),
                            "bias": (round(b, 3) if b is not None else None)}
                           for n, w, b in pa_sub]})

    # 2) Options Flow + Unusual (13) = flow/OI 8 + unusual 5.
    flow_sub = [
        ("flow_oi", FS["flow_oi"], biases.get("options_positioning")),
        ("unusual", FS["unusual"], biases.get("unusual_options")),
    ]
    flow_pts = sum(_pts(w, b) for _, w, b in flow_sub)
    flow_bias = _avg([b for _, _, b in flow_sub])
    params.append({"name": "options_flow_unusual", "label": _LABEL["options_flow_unusual"],
                   "points": round(flow_pts, 2), "max": W["options_flow_unusual"],
                   "bias": (round(flow_bias, 3) if flow_bias is not None else None),
                   "sub": [{"name": n, "max": w, "points": round(_pts(w, b), 2),
                            "bias": (round(b, 3) if b is not None else None)}
                           for n, w, b in flow_sub]})

    # 3) Put-Call Skew + Disparity (9) -- its own parameter so the earliest
    #    bearish options signal is not diluted by the rest of the tape.
    skew_b = biases.get("disparity")
    skew_sub = [
        ("atm_disparity", SS["atm_disparity"], skew_b),
        ("skew_trend", SS["skew_trend"], skew_b),
    ]
    skew_pts = sum(_pts(w, b) for _, w, b in skew_sub)
    params.append({"name": "skew_disparity", "label": _LABEL["skew_disparity"],
                   "points": round(skew_pts, 2), "max": W["skew_disparity"],
                   "bias": (round(skew_b, 3) if skew_b is not None else None),
                   "sub": [{"name": n, "max": w, "points": round(_pts(w, b), 2),
                            "bias": (round(b, 3) if b is not None else None)}
                           for n, w, b in skew_sub]})

    # 4)-? single-bias parameters. Insider is now PURE open-market insider
    #    (raw signal), separate from short interest.
    hist_bias = _hist_reaction_bias(trade.get("historical_moves"))
    insider_b = sig.get("insider_activity")
    if insider_b is None:
        insider_b = biases.get("short_insider")   # fallback for un-refreshed caches
    short_b = _short_interest_bias(eq.get("short_interest"), pa_bias)
    singles = [
        ("eps_rev_dir", biases.get("eps_estimates")),
        ("hist_price_reaction", hist_bias),
        ("insider", insider_b),
        ("peers", biases.get("peer_readthrough")),
        ("short_interest", short_b),
        ("prior_guidance", biases.get("guidance")),
    ]
    for name, b in singles:
        params.append({"name": name, "label": _LABEL[name],
                       "points": round(_pts(W[name], b), 2), "max": W[name],
                       "bias": (round(b, 3) if b is not None else None)})

    # Dark pool (4): confirmation only -- moves off neutral only when it agrees
    # with price action OR options flow.
    dp_bias = biases.get("dark_pool")
    agrees = dp_bias is not None and (
        (pa_bias is not None and pa_bias * dp_bias > 0)
        or (flow_bias is not None and flow_bias * dp_bias > 0))
    dp_pts = _pts(W["dark_pool"], dp_bias) if agrees else W["dark_pool"] * 0.5
    params.append({"name": "dark_pool", "label": _LABEL["dark_pool"],
                   "points": round(dp_pts, 2), "max": W["dark_pool"],
                   "bias": (round(dp_bias, 3) if dp_bias is not None else None),
                   "confirmed": agrees})

    score = round(sum(p["points"] for p in params), 1)
    cut = cfg.V3_CUTOFFS
    signal = "BUY" if score >= cut["buy"] else "SELL" if score <= cut["sell"] else "NEUTRAL"

    # Confidence meter: coverage = weight with real data / 100; agreement =
    # covered weight leaning the signal's side / covered weight.
    covered = 0.0
    pos_w = 0.0
    neg_w = 0.0
    for p in params:
        b = p.get("bias")
        if b is None:
            continue
        covered += p["max"]
        if b > 0.05:
            pos_w += p["max"]
        elif b < -0.05:
            neg_w += p["max"]
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
    import uw_earnings_calendar as cal

    rows = (cal.calendar(days=days) or {}).get("rows") or []
    seen: set[str] = set()
    uniq: list[dict] = []
    for r in rows:
        s = (r.get("symbol") or "").upper()
        if s and s not in seen:
            seen.add(s)
            uniq.append(r)
    eqs = swr.peek_many([f"earn:equity:{s}" for s in seen])
    trades = swr.peek_many([f"earn:trade:default:{s}" for s in seen])
    # Finviz gives the earnings release time (e.g. "4:30 PM") that UW lacks.
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
        # Attach the Finviz clock time when its date matches this report.
        fvi = fmap.get(s)
        if fvi and fvi.get("time") and str(fvi.get("date"))[:10] == str(res.get("date"))[:10]:
            res["report_clock"] = fvi["time"]
        out.append(res)
    out.sort(key=lambda x: (x["score"] if x["score"] is not None else -1), reverse=True)
    return {"status": "OK", "source": SOURCE, "rows": out,
            "universe": len(seen), "scored": scored, "missing": missing[:60]}
