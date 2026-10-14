"""Pre-Earnings Direction -- v2 build spec (13 Oct 2026).

Scores a stock in the days before its earnings release and predicts the
direction of the pre-earnings drift (the position is opened before the result
and closed when it comes out). An all-positive 0..weight scale:

    points = weight * (0.5 + 0.5 * clamp(bias, -1, 1))
    neutral  -> half the weight
    missing  -> half the weight (never 0, so missing data can't push to SELL)

Nine parameters, weights sum to 100 (tab_settings.PED_WEIGHTS), so a no-signal
name lands at exactly 50. Signal BUY >= 65 / NEUTRAL / SELL <= 35
(PED_CUTOFFS). Options is a sub-split of disparity/skew 7 + flow/OI 6 +
unusual 3 (PED_OPTIONS_SPLIT). Dark pool is confirmation only: it moves off
neutral only when it agrees with options or price action.

Every parameter is its own function returning points 0..max; the score is the
sum, and each row carries the breakdown (points, max, raw bias) so every number
can be checked. Biases come from the already-warmed equity engine
(earn:equity:{SYM}); the price-reaction and relative-strength inputs come from
the warmed trade summary (earn:trade:default:{SYM}) when available.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import tab_settings as cfg

SOURCE = "Pre-Earnings Direction (v2)"

_LABEL = {
    "options": "Options Positioning",
    "price_action": "Price Action + Trend",
    "insider": "Insider Activity",
    "peers": "Peer Read-through",
    "eps_rev": "EPS Revisions",
    "hist_reaction": "Historical Reaction",
    "rev_rev": "Revenue Revisions",
    "prior_guidance": "Prior Guidance Delivery",
    "dark_pool": "Dark Pool (confirmation)",
}
_OPT_LABEL = {"disparity_skew": "Disparity / Skew", "flow_oi": "Flow + OI",
              "unusual": "Unusual Activity"}


def _clamp(b: float) -> float:
    return max(-1.0, min(1.0, b))


def _pts(weight: float, bias: Optional[float]) -> float:
    """All-positive point: half the weight at neutral/missing, full at bias +1."""
    if bias is None:
        return weight * 0.5
    return weight * (0.5 + 0.5 * _clamp(bias))


def _avg(vals: list[Optional[float]]) -> Optional[float]:
    xs = [v for v in vals if v is not None]
    return sum(xs) / len(xs) if xs else None


def _f(v) -> Optional[float]:
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


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


# Relative strength vs SPY, reused from the v3 engine so Price Action also
# reflects the stock's divergence from the market (per the spec).
def _rel_strength_bias(spark: Optional[list]) -> Optional[float]:
    try:
        import claude_last_service as v3
        return v3._rel_strength_bias(spark)
    except Exception:  # noqa: BLE001
        return None


def score_one(symbol: str, eq: dict, trade: Optional[dict] = None,
              row: Optional[dict] = None) -> dict:
    biases = {p.get("name"): p.get("bias") for p in (eq.get("params") or [])}
    sig = eq.get("signals") or {}
    trade = trade or {}
    row = row or {}
    W = cfg.PED_WEIGHTS
    OS = cfg.PED_OPTIONS_SPLIT
    params: list[dict] = []

    # 1) Options Positioning (16) = disparity/skew 7 + flow/OI 6 + unusual 3.
    opt_sub = [
        ("disparity_skew", OS["disparity_skew"], biases.get("disparity")),
        ("flow_oi", OS["flow_oi"], biases.get("options_positioning")),
        ("unusual", OS["unusual"], biases.get("unusual_options")),
    ]
    opt_pts = sum(_pts(w, b) for _, w, b in opt_sub)
    opt_bias = _avg([b for _, _, b in opt_sub])
    params.append({"name": "options", "label": _LABEL["options"],
                   "points": round(opt_pts, 2), "max": W["options"],
                   "bias": (round(opt_bias, 3) if opt_bias is not None else None),
                   "sub": [{"name": n, "label": _OPT_LABEL[n], "max": w,
                            "points": round(_pts(w, b), 2),
                            "bias": (round(b, 3) if b is not None else None)}
                           for n, w, b in opt_sub]})

    # 2) Price Action + Trend (14): the stock's own trend blended with its
    #    divergence from the market (relative strength vs SPY).
    pa = biases.get("price_action")
    rs = _rel_strength_bias(trade.get("spark"))
    pa_bias = _avg([pa, rs])
    params.append({"name": "price_action", "label": _LABEL["price_action"],
                   "points": round(_pts(W["price_action"], pa_bias), 2),
                   "max": W["price_action"],
                   "bias": (round(pa_bias, 3) if pa_bias is not None else None)})

    # 3)-8) single-bias parameters.
    insider_b = sig.get("insider_activity")
    if insider_b is None:
        insider_b = biases.get("short_insider")
    hist_bias = _hist_reaction_bias(trade.get("historical_moves"))
    if hist_bias is None:
        hist_bias = biases.get("historical_reaction")
    singles = [
        ("insider", insider_b),
        ("peers", biases.get("peer_readthrough")),
        ("eps_rev", biases.get("eps_estimates")),
        ("hist_reaction", hist_bias),
        ("rev_rev", biases.get("revenue_estimates")),
        ("prior_guidance", biases.get("guidance")),
    ]
    for name, b in singles:
        params.append({"name": name, "label": _LABEL[name],
                       "points": round(_pts(W[name], b), 2), "max": W[name],
                       "bias": (round(b, 3) if b is not None else None)})

    # 9) Dark pool (4): confirmation only -- off neutral only when it agrees with
    #    options OR price action.
    dp_bias = biases.get("dark_pool")
    agrees = dp_bias is not None and (
        (pa_bias is not None and pa_bias * dp_bias > 0)
        or (opt_bias is not None and opt_bias * dp_bias > 0))
    dp_pts = _pts(W["dark_pool"], dp_bias) if agrees else W["dark_pool"] * 0.5
    params.append({"name": "dark_pool", "label": _LABEL["dark_pool"],
                   "points": round(dp_pts, 2), "max": W["dark_pool"],
                   "bias": (round(dp_bias, 3) if dp_bias is not None else None),
                   "confirmed": agrees})

    # NOTE: the spec lists weights that sum to 90 (16+14+12+10+10+10+8+6+4) yet
    # states "sum = 100" and requires no-signal -> 50 with 65/35 cut-offs each 15
    # from neutral. To honour BOTH the listed per-parameter weights AND that
    # neutral=50 / symmetric-cut-off design, the row points keep the spec weights
    # and the headline score is the row total rescaled to 0..100.
    raw = round(sum(p["points"] for p in params), 2)
    max_total = sum(W.values()) or 100
    score = round(raw / max_total * 100, 1)
    cut = cfg.PED_CUTOFFS
    signal = "BUY" if score >= cut["buy"] else "SELL" if score <= cut["sell"] else "NEUTRAL"

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
            "prior_close": row.get("prior_close"), "market_cap": row.get("market_cap"),
            "sector": row.get("sector"),
            "score": score, "signal": signal, "params": params,
            "raw_total": raw, "max_total": max_total}


def scan(days: int = 15) -> dict:
    """Score every name reporting in the next ~`days`, ranked by score desc."""
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
                   "days_to": _days_to(r), "prior_close": r.get("prior_close"),
                   "market_cap": r.get("market_cap"), "sector": r.get("sector"),
                   "score": None, "signal": "PENDING", "params": []}
        out.append(res)
    out.sort(key=lambda x: (x["score"] if x["score"] is not None else -1), reverse=True)
    try:
        _log_scores([x for x in out if x["score"] is not None])
    except Exception:  # noqa: BLE001
        pass
    return {"status": "OK", "source": SOURCE, "rows": out,
            "universe": len(seen), "scored": scored, "missing": missing[:60]}


# --- Daily score log (compare with the actual move later) ------------------
_log_ready = False


def _log_scores(rows: list[dict]) -> int:
    """Upsert one (symbol, snapshot_date, score, signal) row per name per day."""
    global _log_ready
    if not rows:
        return 0
    try:
        from sqlalchemy import text
        from database import SessionLocal
    except Exception:  # noqa: BLE001
        return 0
    db = SessionLocal()
    try:
        if not _log_ready:
            db.execute(text(
                "CREATE TABLE IF NOT EXISTS ped_scores ("
                "  symbol VARCHAR(20) NOT NULL,"
                "  snapshot_date DATE NOT NULL,"
                "  score DOUBLE PRECISION,"
                "  signal VARCHAR(10),"
                "  earnings_date DATE,"
                "  PRIMARY KEY (symbol, snapshot_date))"))
            db.commit()
            _log_ready = True
        today = date.today().isoformat()
        for r in rows:
            db.execute(text(
                "INSERT INTO ped_scores (symbol, snapshot_date, score, signal, earnings_date)"
                " VALUES (:s, :d, :sc, :sg, :ed)"
                " ON CONFLICT (symbol, snapshot_date) DO UPDATE SET"
                " score = EXCLUDED.score, signal = EXCLUDED.signal,"
                " earnings_date = EXCLUDED.earnings_date"),
                {"s": r["symbol"], "d": today, "sc": r.get("score"),
                 "sg": r.get("signal"), "ed": str(r.get("date"))[:10] if r.get("date") else None})
        db.commit()
        return len(rows)
    except Exception:  # noqa: BLE001
        db.rollback()
        return 0
    finally:
        db.close()
