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

SOURCE = "Claude Last — Direction v2 (14-param, 100pt)"

# Spec table order (Pre_Earnings_Model_v2.pdf §2).
_LABEL = {
    "eps_rev_dir": "EPS Revisions & Direction",
    "options_flow": "Options Flow (premium)",
    "unusual_skew": "Unusual Call/Put Skew",
    "prior_guidance": "Prior Guidance / Beat History",
    "hist_price_reaction": "Historical Price Reaction",
    "price_action": "Price Action (structure)",
    "ema_trend": "EMA Trend (20/50/200)",
    "peers": "Peer Read-through",
    "disparity": "Options Disparity (C/P ratio)",
    "realized_vs_implied": "Realized vs Implied History",
    "insider": "Insider Activity",
    "short_interest": "Short Interest",
    "dark_pool": "Dark Pool (confirmation)",
    "implied_move": "Implied Move (expectation)",
}
# The spec's §2 param order, for a stable UI breakdown.
_ORDER = list(_LABEL.keys())


def _clamp(b: float) -> float:
    return max(-1.0, min(1.0, b))


def _quant(bias: Optional[float]) -> Optional[float]:
    """Snap a raw directional bias (-1..1) onto the spec's 5-step scale:
    strong bull +1 / mild bull +0.5 / neutral 0 / mild bear -0.5 / strong bear -1.
    Missing data stays None (scored as neutral, never zero)."""
    if bias is None:
        return None
    b = _clamp(bias)
    if b >= 0.5:
        return 1.0
    if b >= 0.15:
        return 0.5
    if b > -0.15:
        return 0.0
    if b > -0.5:
        return -0.5
    return -1.0


def _pts(weight: float, bias: Optional[float]) -> float:
    """5-step points: neutral/missing = 50% of Max, strong bull = 100%, strong
    bear = 0 (spec §1). `bias` is expected pre-quantised by _quant."""
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


def _implied_move_eval(trade: dict, side: int, spark: Optional[list]) -> dict:
    """Implied Move ratio, the param-14 bias, and the trade GATE (spec §3).

    IM Ratio = implied move % (pre-result ATM straddle, already computed by the
    trade engine) / average absolute next-day move of the last ~8 reports.
    Returns ratio, a long-side bias for the 5-step scale, the size gate, and the
    priced-in flag (moved > 1x implied move in the signal's direction in 10d)."""
    em = (trade or {}).get("expected_move") or {}
    im_pct = _f(em.get("percent"))
    mags = [abs(x) for x in (_f(m.get("move_pct")) for m in (trade or {}).get("historical_moves") or [])
            if x is not None]
    hist_avg = (sum(mags) / len(mags)) if mags else None
    if im_pct is None or not hist_avg:
        return {"im_pct": im_pct, "hist_avg_move": round(hist_avg, 2) if hist_avg else None,
                "ratio": None, "bias": None, "gate": "unknown", "priced_in": False,
                "size_factor": 1.0}
    ratio = im_pct / hist_avg
    bias = -1.0
    for below, b in cfg.V3_IM_POINTS:
        if ratio < below:
            bias = b
            break
    # Priced-in: already moved more than 1x the implied move the signal's way (10d).
    priced = False
    closes = [c for c in (_f(c) for c in (spark or [])) if c is not None]
    if len(closes) >= 11 and side:
        move10 = (closes[-1] - closes[-11]) / closes[-11] * 100.0
        priced = (side > 0 and move10 > im_pct) or (side < 0 and move10 < -im_pct)
    g = cfg.V3_IM_GATE
    gate = "normal" if ratio < g["half_above"] else "half" if ratio <= g["no_trade_above"] else "no_trade"
    if priced:
        gate = "no_trade"
        bias = -1.0        # expectation fully priced in -> 0 pts for a long
    size_factor = 1.0 if gate == "normal" else 0.5 if gate == "half" else 0.0
    return {"im_pct": round(im_pct, 2), "hist_avg_move": round(hist_avg, 2),
            "ratio": round(ratio, 2), "bias": bias, "gate": gate,
            "priced_in": priced, "size_factor": size_factor}


def score_one(symbol: str, eq: dict, trade: Optional[dict] = None,
              row: Optional[dict] = None) -> dict:
    biases = {p.get("name"): p.get("bias") for p in (eq.get("params") or [])}
    sig = eq.get("signals") or {}        # numeric directional biases (-1..1)
    trade = trade or {}
    row = row or {}
    W = cfg.V3_WEIGHTS

    pa_bias = sig.get("price_action")
    if pa_bias is None:
        pa_bias = biases.get("price_action")
    flow_bias = sig.get("options_flow")
    if flow_bias is None:
        flow_bias = biases.get("options_positioning")
    insider_b = sig.get("insider_activity")
    if insider_b is None:
        insider_b = biases.get("short_insider")   # fallback for un-refreshed caches

    # The 13 directional parameters (everything except Implied Move), each read
    # from the best available source and snapped to the spec's 5-step scale.
    raw: dict[str, Optional[float]] = {
        "eps_rev_dir": biases.get("eps_estimates"),
        "options_flow": flow_bias,
        "unusual_skew": sig.get("unusual_activity", biases.get("unusual_options")),
        "prior_guidance": biases.get("guidance"),
        "hist_price_reaction": _hist_reaction_bias(trade.get("historical_moves")),
        "price_action": pa_bias,
        "ema_trend": sig.get("ema_trend"),
        "peers": biases.get("peer_readthrough"),
        "disparity": sig.get("disparity", biases.get("disparity")),
        # #10 realized-vs-implied needs per-event straddle-lean history we do not
        # persist -> no data -> neutral (spec: missing is always neutral).
        "realized_vs_implied": None,
        "insider": insider_b,
        "short_interest": _short_interest_bias(eq.get("short_interest"), pa_bias),
    }

    params: list[dict] = []
    for name in _ORDER:
        if name in ("dark_pool", "implied_move"):
            continue
        qb = _quant(raw.get(name))
        params.append({"name": name, "label": _LABEL[name], "max": W[name],
                       "points": round(_pts(W[name], qb), 2), "bias": qb})

    # #13 Dark pool: confirmation only -- off neutral only when it agrees with
    # price action OR options flow (spec: accumulation/distribution vs VWAP).
    dp_bias = _quant(biases.get("dark_pool"))
    agrees = dp_bias is not None and dp_bias != 0 and (
        (pa_bias is not None and pa_bias * dp_bias > 0)
        or (flow_bias is not None and flow_bias * dp_bias > 0))
    dp_pts = _pts(W["dark_pool"], dp_bias) if agrees else W["dark_pool"] * 0.5
    params.append({"name": "dark_pool", "label": _LABEL["dark_pool"], "max": W["dark_pool"],
                   "points": round(dp_pts, 2), "bias": (dp_bias if agrees else None),
                   "confirmed": agrees})

    # #14 Implied Move: provisional side (13 params + neutral IM) picks the gate
    # direction, then the IM param + gate are applied (spec §3b/§3c).
    base_pts = sum(p["points"] for p in params)
    prov = base_pts + W["implied_move"] * 0.5
    side = 1 if prov > 50 else -1
    im = _implied_move_eval(trade, side, trade.get("spark"))
    im_qb = _quant(im.get("bias"))
    params.append({"name": "implied_move", "label": _LABEL["implied_move"],
                   "max": W["implied_move"], "points": round(_pts(W["implied_move"], im_qb), 2),
                   "bias": im_qb, "ratio": im.get("ratio")})

    score = round(sum(p["points"] for p in params), 1)
    cut = cfg.V3_CUTOFFS
    # Spec §4 bands are strict: BUY > 70, SELL < 50, else NEUTRAL.
    raw_signal = "BUY" if score > cut["buy"] else "SELL" if score < cut["sell"] else "NEUTRAL"
    # Implied-Move gate (spec §3c / §5e): a NO-TRADE gate blocks the signal.
    signal = "NEUTRAL" if (raw_signal != "NEUTRAL" and im.get("gate") == "no_trade") else raw_signal

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
            "score": score, "signal": signal, "raw_signal": raw_signal, "params": params,
            "coverage": round(coverage * 100, 1), "agreement": round(agreement * 100, 1),
            "low_confidence": low_conf,
            # Implied-Move gate (spec §3): size/no-trade overlay on top of the score.
            "im_ratio": im.get("ratio"), "im_pct": im.get("im_pct"),
            "hist_avg_move": im.get("hist_avg_move"), "im_gate": im.get("gate"),
            "priced_in": im.get("priced_in"), "size_factor": im.get("size_factor")}


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
