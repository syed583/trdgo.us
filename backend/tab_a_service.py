"""Tab A -- Pre-Earnings Stock Direction (v2, all-positive scale).

Reuses the biases already computed by the equity earnings engine (cached as
earn:equity:{SYM}) and re-scores them on the spec's ALL-POSITIVE 0..weight
scale: half the weight is neutral, a bullish lean goes above half and a bearish
lean below, and missing data stays at half (never 0, so a thin-data name is not
pushed toward SELL). Score 0-100; BUY >= 65, SELL <= 35, else NEUTRAL.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import tab_settings as cfg

SOURCE = "Tab A — Direction v2"

# Which equity-engine bias feeds each Tab A parameter.
_BIAS_KEY = {
    "price_action": "price_action",
    "insider": "short_insider",
    "peers": "peer_readthrough",
    "eps_rev": "eps_estimates",
    "hist_reaction": "historical_reaction",
    "rev_rev": "revenue_estimates",
    "prior_guidance": "guidance",
}
_LABEL = {
    "options": "Options Positioning", "price_action": "Price Action + Trend",
    "insider": "Insider Activity", "peers": "Peer Read-through",
    "eps_rev": "EPS Revisions", "hist_reaction": "Historical Reaction",
    "rev_rev": "Revenue Revisions", "prior_guidance": "Prior Guidance",
    "dark_pool": "Dark Pool",
}


# The spec's 9 weights sum to 90; normalize to 100 so a no-signal stock lands
# at exactly 50 and the 65/35 cuts behave as the spec intends.
_SCALE = 100.0 / float(sum(cfg.TAB_A_WEIGHTS.values()))


def _clamp(b: float) -> float:
    return max(-1.0, min(1.0, b))


def _pts(weight: float, bias: Optional[float]) -> float:
    """All-positive: neutral/missing = half weight; bias in -1..+1 scales it."""
    if bias is None:
        return weight * 0.5
    return weight * (0.5 + 0.5 * _clamp(bias))


def _avg(vals: list[Optional[float]]) -> Optional[float]:
    xs = [v for v in vals if v is not None]
    return sum(xs) / len(xs) if xs else None


def score_one(symbol: str, eq: dict, row: Optional[dict] = None) -> dict:
    biases = {p.get("name"): p.get("bias") for p in (eq.get("params") or [])}
    W = cfg.TAB_A_WEIGHTS
    OS = cfg.TAB_A_OPTIONS_SPLIT
    params: list[dict] = []

    # 1) Options (16) = disparity 7 + flow/OI 6 + unusual 3.
    sub = [
        ("disparity", OS["disparity"], biases.get("disparity")),
        ("flow_oi", OS["flow_oi"], biases.get("options_positioning")),
        ("unusual", OS["unusual"], biases.get("unusual_options")),
    ]
    opt_pts = sum(_pts(w, b) for _, w, b in sub)
    opt_bias = _avg([b for _, _, b in sub])
    params.append({"name": "options", "label": _LABEL["options"],
                   "points": round(opt_pts, 2), "max": W["options"],
                   "bias": (round(opt_bias, 3) if opt_bias is not None else None),
                   "sub": [{"name": n, "max": w,
                            "points": round(_pts(w, b), 2),
                            "bias": (round(b, 3) if b is not None else None)}
                           for n, w, b in sub]})

    pa_bias = biases.get("price_action")
    # 2)-8) straightforward single-bias parameters.
    for name in ("price_action", "insider", "peers", "eps_rev",
                 "hist_reaction", "rev_rev", "prior_guidance"):
        b = biases.get(_BIAS_KEY[name])
        params.append({"name": name, "label": _LABEL[name],
                       "points": round(_pts(W[name], b), 2), "max": W[name],
                       "bias": (round(b, 3) if b is not None else None)})

    # 9) Dark pool: side is unknown, so it only moves off neutral when options
    #    OR price action lean the same direction; otherwise it stays at half.
    dp_bias = biases.get("dark_pool")
    agrees = dp_bias is not None and (
        (opt_bias is not None and opt_bias * dp_bias > 0)
        or (pa_bias is not None and pa_bias * dp_bias > 0))
    dp_pts = _pts(W["dark_pool"], dp_bias) if agrees else W["dark_pool"] * 0.5
    params.append({"name": "dark_pool", "label": _LABEL["dark_pool"],
                   "points": round(dp_pts, 2), "max": W["dark_pool"],
                   "bias": (round(dp_bias, 3) if dp_bias is not None else None),
                   "confirmed": agrees})

    # Normalize every row (and its sub-rows) onto the 0-100 scale; the total is
    # the sum of the displayed rows.
    for p in params:
        p["points"] = round(p["points"] * _SCALE, 2)
        p["max"] = round(p["max"] * _SCALE, 1)
        for sp in p.get("sub") or []:
            sp["points"] = round(sp["points"] * _SCALE, 2)
            sp["max"] = round(sp["max"] * _SCALE, 1)
    score = round(sum(p["points"] for p in params), 1)
    cut = cfg.TAB_A_CUTOFFS
    signal = "BUY" if score >= cut["buy"] else "SELL" if score <= cut["sell"] else "NEUTRAL"

    days_to = None
    d = (row or {}).get("date")
    if d:
        try:
            days_to = (datetime.fromisoformat(str(d)[:10]).date() - date.today()).days
        except Exception:  # noqa: BLE001
            days_to = None
    return {"symbol": symbol, "company": (row or {}).get("company"),
            "date": d, "date_label": (row or {}).get("short_label")
            or (row or {}).get("date_label"), "days_to": days_to,
            "score": score, "signal": signal, "params": params}


def scan(days: int = 15) -> dict:
    """Score every name reporting in the next ~`days`, ranked by score."""
    import swr
    import uw_earnings_calendar as cal

    rows = (cal.calendar(days=days) or {}).get("rows") or []
    # One row per symbol (nearest report first; calendar is date-ordered).
    seen: set[str] = set()
    uniq: list[dict] = []
    for r in rows:
        s = (r.get("symbol") or "").upper()
        if s and s not in seen:
            seen.add(s)
            uniq.append(r)
    eqs = swr.peek_many([f"earn:equity:{s}" for s in seen])

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
        row = None
        if eq and eq.get("params"):
            try:
                row = score_one(s, eq, r)
                scored += 1
            except Exception:  # noqa: BLE001
                row = None
        if row is None:
            # Every upcoming name shows; score fills in as the engine warms it.
            missing.append(s)
            row = {"symbol": s, "company": r.get("company"), "date": r.get("date"),
                   "date_label": r.get("short_label") or r.get("date_label"),
                   "days_to": _days_to(r), "score": None, "signal": "PENDING",
                   "params": []}
        out.append(row)
    # Scored first (highest score), pending at the bottom.
    out.sort(key=lambda x: (x["score"] if x["score"] is not None else -1), reverse=True)
    return {"status": "OK", "source": SOURCE, "rows": out,
            "universe": len(seen), "scored": scored, "missing": missing[:60]}
