"""Experimental options-flow analysis (flowx-1): the SAME qualification rule as V2 (>= 3 attributable alerts, >= $500k attributed premium, |net ratio| >= 0.25), made fully transparent.

What it adds (nothing weakens a threshold):
  * a waterfall that says, for one stock, exactly how many alerts were dropped at each step and why (outside lookback / multi-leg / expiry before earnings / volume < open interest /
    side not attributable / call-put unknown), so "why does this stock not qualify" is answerable;
  * the observed LEAN even when the stock does not qualify (direction of the attributable premium) labelled `lean_only`; it earns no points;
  * directional consistency (share of attributed premium on the winning side), sweep share, unattributed-premium share (mid-price prints), expiry proximity;
  * the lookback is a parameter (5/10/15/30) so the windows can be compared on the same saved alerts. All alerts are required to be stamped at or before the capture time (future-stamped alerts are counted and never used).
Verified against the saved data (1 Nov): 5,863 alerts, zero null fields, zero future stamps; premium split ask 44.3 % / bid 46.1 % / unattributed 9.6 %; `all_opening_trades` is true for 39 alerts and 0 inside the 5-day window (so it
cannot replace the volume/open-interest test); no 100-row truncation falls inside the 5-day window. The low qualifying count is genuine thin activity, not a parsing error.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Optional

VERSION = "flowx-1"
RULE = {"min_alerts": 3, "min_attributed_premium_usd": 500_000.0, "strong_net_ratio": 0.5, "moderate_net_ratio": 0.25, "sweep_weight": 1.5, "min_volume_oi_ratio": 1.0}      # identical to smart_move_v2.CONFIG["options_flow"]
LOOKBACKS = (5, 10, 15, 30)


def _t(x) -> Optional[datetime]:
    try:
        t = datetime.fromisoformat(str(x).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _f(x) -> Optional[float]:
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def analyse(alerts, event_date: str, capture_ts: datetime, lookback_days: int = 5) -> dict:
    if alerts is None:
        return {"version": VERSION, "state": "NOT_COLLECTED", "qualifies": False, "direction": None, "lookback_days": lookback_days}
    w = Counter()
    bull = bear = attributed = total_prem = sweep_prem = 0.0
    used = 0
    for a in alerts:
        w["returned"] += 1
        t = _t(a.get("created_at"))
        prem = _f(a.get("total_premium")) or 0.0
        if t is None:
            w["bad_timestamp"] += 1
            continue
        if t > capture_ts:
            w["future_stamped_never_used"] += 1
            continue
        if (capture_ts - t).total_seconds() > lookback_days * 86400:
            w["outside_lookback"] += 1
            continue
        w["inside_lookback"] += 1
        if a.get("has_multileg"):
            w["multi_leg"] += 1
            continue
        if str(a.get("expiry", ""))[:10] < event_date:
            w["expiry_before_earnings"] += 1
            continue
        voi = _f(a.get("volume_oi_ratio"))
        if voi is None:
            w["volume_oi_missing"] += 1
            continue
        if voi < RULE["min_volume_oi_ratio"]:
            w["volume_below_open_interest"] += 1
            continue
        kind = str(a.get("type", "")).lower()
        if kind not in ("call", "put"):
            w["type_unknown"] += 1
            continue
        ask, bid = _f(a.get("total_ask_side_prem")) or 0.0, _f(a.get("total_bid_side_prem")) or 0.0
        total_prem += prem
        if ask + bid == 0:
            w["side_not_attributable"] += 1
            continue
        wt = RULE["sweep_weight"] if a.get("has_sweep") else 1.0
        up, dn = (ask, bid) if kind == "call" else (bid, ask)                    # call bought / put sold = bullish; call sold / put bought = bearish
        bull += up * wt
        bear += dn * wt
        attributed += (ask + bid)
        sweep_prem += (ask + bid) if a.get("has_sweep") else 0.0
        used += 1
        w["used"] += 1
    tot = bull + bear
    out = {"version": VERSION, "lookback_days": lookback_days, "waterfall": dict(w), "alerts_used": used, "attributed_premium": round(attributed), "net_ratio": None, "consistency": None, "sweep_share": None,
           "unattributed_share": round(1 - attributed / total_prem, 3) if total_prem else None, "direction": None, "qualifies": False, "points_tier": None}
    if w["returned"] == 0:
        return dict(out, state="NO_ALERTS")
    if w["inside_lookback"] == 0:
        return dict(out, state="NONE_IN_LOOKBACK")
    if tot == 0:
        return dict(out, state="NOTHING_ATTRIBUTABLE")
    ratio = (bull - bear) / tot
    lean = "BULLISH" if ratio > 0 else "BEARISH" if ratio < 0 else "NEUTRAL"
    out.update(net_ratio=round(ratio, 3), consistency=round(max(bull, bear) / tot, 3), sweep_share=round(sweep_prem / attributed, 3) if attributed else None, direction=lean)
    if used >= RULE["min_alerts"] and attributed >= RULE["min_attributed_premium_usd"]:
        mag = abs(ratio)
        tier = "STRONG" if mag >= RULE["strong_net_ratio"] else "MODERATE" if mag >= RULE["moderate_net_ratio"] else None
        if tier:
            return dict(out, state="QUALIFIES", qualifies=True, points_tier=tier)
        return dict(out, state="QUALIFIED_BUT_BALANCED", direction="BALANCED")                  # enough activity, no directional edge: a valid observation, no points
    return dict(out, state="LEAN_ONLY_BELOW_MINIMUM", direction=lean, lean_only=True,
                shortfall={"alerts_needed": max(0, RULE["min_alerts"] - used), "premium_needed_usd": max(0, round(RULE["min_attributed_premium_usd"] - attributed))})


def compare_lookbacks(alerts, event_date: str, capture_ts: datetime) -> dict:
    """The same alerts under each window: qualification and direction per lookback (no future data: every alert is stamped <= capture_ts)."""
    return {str(L): {k: v for k, v in analyse(alerts, event_date, capture_ts, L).items() if k in ("state", "qualifies", "direction", "net_ratio", "alerts_used", "attributed_premium")} for L in LOOKBACKS}
