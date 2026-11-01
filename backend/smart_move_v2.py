"""Smart Move V2: an EXPERIMENTAL, separately versioned 100-point model. V1 (the frozen 40-point engine) is never modified or re-scored here.

Everything below is a research hypothesis, not a validated improvement:
  * the ten factor weights come from the owner's proposal (sum 100);
  * every threshold lives in CONFIG, is hashed into CONFIG_HASH and is unvalidated;
  * the decision policy is V1's scaled by 2.5 (bull >= 35, bear <= 10, margin >= 25). The minimum evidence of 60 points is applied to DIRECTIONAL evidence only
    (factors able to emit a bull/bear point): magnitude-only evidence (implied move) is shown but never counts toward the minimum. Not lowered to create signals.
No missing factor is normalised away: a score is always "points out of 100 possible" next to the points that have data. Adapters take SAVED bodies and reject any
item stamped after the capture time. Nothing here sends a request.
"""
from __future__ import annotations

import hashlib
import json
import statistics
from datetime import date, datetime, timedelta, timezone
from typing import Optional

VERSION = "v2-exp-2"
STATUS = "EXPERIMENTAL"

WEIGHTS = {"F3": 15, "F5": 10, "F7": 10, "F8": 5, "ANALYST": 15, "OPTIONS_FLOW": 15, "IMPLIED_VS_HIST": 10, "SHORT_INST": 5, "REL_STRENGTH": 5, "NEWS_GUIDANCE": 10}
assert sum(WEIGHTS.values()) == 100
DIRECTIONAL = {"F3", "F5", "F7", "F8", "ANALYST", "OPTIONS_FLOW", "SHORT_INST", "REL_STRENGTH", "NEWS_GUIDANCE"}     # IMPLIED_VS_HIST is magnitude-only

CONFIG = {
    "version": VERSION, "weights": WEIGHTS,
    "decision": {"bull_min": 35, "bear_max": 10, "margin_min": 25, "min_directional_available": 60, "basis": "V1 thresholds x 2.5, same 0.60 evidence ratio; unvalidated, never lowered to create signals"},
    "analyst": {"estimate_points": 10, "action_points": 5, "revision_min_pct": 1.0, "snapshot_min_gap_days": 7, "snapshot_max_age_days": 60, "action_lookback_days": 30, "net_actions_min": 2,
                "basis": "current consensus vs the earliest saved consensus 7-60 days older (EPS and revenue); net rating upgrades minus downgrades"},
    "options_flow": {"lookback_days": 5, "min_alerts": 3, "min_attributed_premium_usd": 500_000, "sweep_weight": 1.5, "min_volume_oi_ratio": 1.0, "strong_net_ratio": 0.5, "moderate_net_ratio": 0.25,
                     "moderate_fraction": 0.5, "basis": "ask-side call + bid-side put premium vs bid-side call + ask-side put premium; multi-leg excluded; expiry on/after the earnings date"},
    "implied_vs_hist": {"min_reported_moves": 4, "rich_ratio": 1.25, "cheap_ratio": 0.8, "basis": "MAGNITUDE ONLY: risk/uncertainty evidence, no directional points, not counted toward the directional minimum"},
    "short_inst": {"si_float_high_pct": 15.0, "si_change_pct": 10.0, "snapshot_min_gap_days": 10, "inst_net_pct_of_shares": 0.5, "basis": "high short interest alone gives no direction; high AND rising = bearish crowding; falling = ambiguous (covering/squeeze) = no direction; institutional net 13F unit change"},
    "rel_strength": {"window_sessions": 5, "threshold_pct": 2.0, "basis": "5-session return vs SPY and vs sector ETF on the last common completed session; window differs from V1's 20-session F5/F8 to limit overlap"},
    "news_guidance": {"sentiment_semantics_verified": False, "lookback_days": 7, "net_major_min": 2, "points": 10, "basis": "BLOCKED until the meaning of the UW headline `sentiment` field is verified on a saved sample; only headlines stamped before BOTH the capture and the release time can count"},
}
CONFIG_HASH = hashlib.sha256(json.dumps(CONFIG, sort_keys=True).encode()).hexdigest()

FACTOR_STATUS = {"F3": "READY", "F5": "READY", "F7": "READY", "F8": "READY", "ANALYST": "PARTIAL", "OPTIONS_FLOW": "PARTIAL", "IMPLIED_VS_HIST": "PARTIAL",
                 "SHORT_INST": "PARTIAL", "REL_STRENGTH": "READY", "NEWS_GUIDANCE": "BLOCKED"}


def _f(x) -> Optional[float]:
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def _ts(x) -> Optional[datetime]:
    try:
        t = datetime.fromisoformat(str(x).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _factor(name, bull, bear, available, **why):
    return {"factor": name, "max": WEIGHTS[name], "bull": bull if available else 0, "bear": bear if available else 0, "available": bool(available), "directional": name in DIRECTIONAL, **why}


def _flip(sg, variant):
    return -sg if variant == "contrarian" else sg


# ---------------------------------------------------------------- the four V1 factors, taken from V1's own sub-signal breakdown (never recomputed)
def from_v1(subscores: dict) -> list:
    groups = {"F3": ("F3_mean", "F3_consistency"), "F5": ("F5_trend", "F5_relative_strength", "F5_extension"), "F7": ("F7_skew",), "F8": ("F8_market", "F8_sector")}
    out = []
    for fac, names in groups.items():
        avail = [subscores[n] for n in names if n in subscores and subscores[n]["available"]]
        if not avail:
            out.append(_factor(fac, 0, 0, False, reason="V1_FACTOR_MISSING", source="V1 engine"))
            continue
        out.append(_factor(fac, sum(p["bull"] for p in avail), sum(p["bear"] for p in avail), True, source="V1 engine sub-signals", available_points=sum(p["weight"] for p in avail)))
    return out


# ---------------------------------------------------------------- implied move: magnitude only
def implied_vs_hist(earnings_rows, event_date: str) -> dict:
    cfg = CONFIG["implied_vs_hist"]
    up = next((r for r in (earnings_rows or []) if str(r.get("report_date"))[:10] == event_date), None)
    im = _f((up or {}).get("expected_move_perc"))
    hist = [abs(_f(r.get("post_earnings_move_1d"))) for r in (earnings_rows or []) if str(r.get("report_date"))[:10] < event_date and _f(r.get("post_earnings_move_1d")) is not None][:8]
    if im is None or len(hist) < cfg["min_reported_moves"]:
        return _factor("IMPLIED_VS_HIST", 0, 0, False, reason="NO_IMPLIED_MOVE_OR_FEWER_THAN_%d_REPORTED_MOVES" % cfg["min_reported_moves"])
    m = statistics.mean(hist)
    ratio = im / m if m else None
    label = None if ratio is None else "options price a LARGER move than history (rich)" if ratio >= cfg["rich_ratio"] else "options price a SMALLER move than history (cheap)" if ratio <= cfg["cheap_ratio"] else "options price about the historical move"
    return _factor("IMPLIED_VS_HIST", 0, 0, True, ratio=round(ratio, 3) if ratio else None, implied_move=im, mean_abs_hist_move=round(m, 5), n_hist=len(hist), risk_label=label,
                   note="uncertainty/risk evidence only; no directional points")


# ---------------------------------------------------------------- relative strength (synchronised)
def rel_strength(stock_series: dict, spy_series: dict, sector_series: Optional[dict], last: date, variant: str) -> dict:
    cfg = CONFIG["rel_strength"]
    n, thr = cfg["window_sessions"], cfg["threshold_pct"]
    series = {"stock": stock_series or {}, "spy": spy_series or {}, "sector": sector_series or {}}
    heads = [max((d for d in s if d <= last), default=None) for k, s in series.items() if (k != "sector" or sector_series)]
    if any(h is None for h in heads):
        return _factor("REL_STRENGTH", 0, 0, False, reason="NO_PRICE_OR_MARKET_HISTORY")
    sync = min(heads)                                           # the last completed session ALL series share

    def ret(ser):
        ds = [d for d in sorted(ser) if d <= sync]
        return (ser[ds[-1]] / ser[ds[-1 - n]] - 1) * 100 if len(ds) > n and ser[ds[-1 - n]] else None
    r, s, e = ret(series["stock"]), ret(series["spy"]), (ret(series["sector"]) if sector_series else None)
    if r is None or s is None:
        return _factor("REL_STRENGTH", 0, 0, False, reason="NO_PRICE_OR_MARKET_HISTORY")
    half = WEIGHTS["REL_STRENGTH"] / 2
    bull = bear = 0.0
    vals = {}
    for key, base in (("vs_spy", s), ("vs_sector", e)):
        if base is None:
            continue
        d = r - base
        vals[key] = round(d, 2)
        sg = _flip(1 if d >= thr else -1 if d <= -thr else 0, variant)
        bull += half if sg > 0 else 0
        bear += half if sg < 0 else 0
    return _factor("REL_STRENGTH", bull, bear, True, values=vals, sync_session=sync.isoformat(), available_points=half * (1 + (e is not None)), sector_part=e is not None,
                   note="5-session window; V1 F5/F8 use 20 sessions")


# ---------------------------------------------------------------- analyst: estimate revisions + rating actions
def analyst(est_snapshots, actions, capture_ts: datetime, variant: str) -> dict:
    """est_snapshots: [{captured_at, eps, revenue}] consensus for the upcoming quarter saved at different times (None = never collected). actions: UW screener/analysts rows (action, timestamp)."""
    cfg = CONFIG["analyst"]
    if est_snapshots is None and actions is None:
        return _factor("ANALYST", 0, 0, False, reason="NOT_COLLECTED")
    bull = bear = 0.0
    avail_pts, detail = 0, {}
    snaps = sorted([s for s in (est_snapshots or []) if _ts(s.get("captured_at")) and _ts(s["captured_at"]) <= capture_ts], key=lambda s: _ts(s["captured_at"]))   # future snapshots never count
    if len(snaps) >= 2:
        latest = snaps[-1]
        prior = next((s for s in snaps if cfg["snapshot_min_gap_days"] <= (_ts(latest["captured_at"]) - _ts(s["captured_at"])).days <= cfg["snapshot_max_age_days"]), None)
        if prior:
            votes = []
            for k in ("eps", "revenue"):
                a, b = _f(latest.get(k)), _f(prior.get(k))
                if a is None or not b:
                    continue
                pct = (a - b) / abs(b) * 100
                detail[k + "_revision_pct"] = round(pct, 2)
                votes.append(1 if pct >= cfg["revision_min_pct"] else -1 if pct <= -cfg["revision_min_pct"] else 0)
            if votes:
                avail_pts += cfg["estimate_points"]
                tot = _flip(sum(votes), variant)
                full = cfg["estimate_points"]
                pts = full if abs(tot) >= 2 else full / 2 if abs(tot) == 1 else 0           # both measures agree = full, one = half, opposite = nothing
                bull += pts if tot > 0 else 0
                bear += pts if tot < 0 else 0
    if actions is not None:
        avail_pts += cfg["action_points"]
        up = dn = 0
        for r in actions:
            t = _ts(r.get("timestamp"))
            if not t or t > capture_ts or (capture_ts - t).days > cfg["action_lookback_days"]:
                continue
            a = str(r.get("action", "")).lower()
            up += a in ("upgrade", "initiated", "raised")
            dn += a in ("downgrade", "lowered")
        detail.update(upgrades=up, downgrades=dn)
        sg = _flip(1 if up - dn >= cfg["net_actions_min"] else -1 if up - dn <= -cfg["net_actions_min"] else 0, variant)
        bull += cfg["action_points"] if sg > 0 else 0
        bear += cfg["action_points"] if sg < 0 else 0
    if not avail_pts:
        return _factor("ANALYST", 0, 0, False, reason="NO_PRIOR_CONSENSUS_SNAPSHOT_AND_NO_ACTIONS", snapshots=len(snaps))
    return _factor("ANALYST", bull, bear, True, available_points=avail_pts, snapshots=len(snaps), **detail)


# ---------------------------------------------------------------- options flow with context
def options_flow(alerts, event_date: str, capture_ts: datetime, variant: str) -> dict:
    """alerts: UW /api/stock/{t}/flow-alerts rows. Direction comes from WHO was aggressive (ask vs bid side), not from call/put alone."""
    cfg = CONFIG["options_flow"]
    if alerts is None:
        return _factor("OPTIONS_FLOW", 0, 0, False, reason="NOT_COLLECTED")
    bullp = bearp = 0.0
    used = sweeps = skipped_future = skipped_ctx = 0
    for a in alerts:
        t = _ts(a.get("created_at"))
        if t is None or t > capture_ts:
            skipped_future += 1
            continue
        if (capture_ts - t).days > cfg["lookback_days"] or a.get("has_multileg") or str(a.get("expiry", ""))[:10] < event_date:
            skipped_ctx += 1                                                # multi-leg is directionally ambiguous; an expiry before the earnings date carries no earnings exposure
            continue
        voi = _f(a.get("volume_oi_ratio"))
        if voi is None or voi < cfg["min_volume_oi_ratio"]:
            skipped_ctx += 1                                                # volume below open interest is probably not new positioning
            continue
        ask, bid = _f(a.get("total_ask_side_prem")) or 0.0, _f(a.get("total_bid_side_prem")) or 0.0
        w = cfg["sweep_weight"] if a.get("has_sweep") else 1.0
        sweeps += 1 if a.get("has_sweep") else 0
        kind = str(a.get("type", "")).lower()
        if kind == "call":
            bullp += ask * w
            bearp += bid * w                                                # a call SOLD is not bullish
        elif kind == "put":
            bearp += ask * w
            bullp += bid * w                                                # a put SOLD is not bearish
        else:
            continue
        used += 1
    total = bullp + bearp
    if used < cfg["min_alerts"] or total < cfg["min_attributed_premium_usd"]:
        return _factor("OPTIONS_FLOW", 0, 0, False, reason="TOO_FEW_ATTRIBUTABLE_ALERTS_OR_PREMIUM", alerts_used=used, attributed_premium=round(total), skipped_future=skipped_future, skipped_context=skipped_ctx)
    ratio = (bullp - bearp) / total
    mag = abs(ratio)
    pts = WEIGHTS["OPTIONS_FLOW"] if mag >= cfg["strong_net_ratio"] else WEIGHTS["OPTIONS_FLOW"] * cfg["moderate_fraction"] if mag >= cfg["moderate_net_ratio"] else 0
    sg = _flip(1 if ratio > 0 else -1, variant) if pts else 0
    return _factor("OPTIONS_FLOW", pts if sg > 0 else 0, pts if sg < 0 else 0, True, net_ratio=round(ratio, 3), alerts_used=used, sweeps=sweeps, attributed_premium=round(total), skipped_future=skipped_future, skipped_context=skipped_ctx)


# ---------------------------------------------------------------- short interest + institutions
def short_inst(short_snaps, own_rows, capture_ts: datetime, variant: str) -> dict:
    """short_snaps: [{market_date, si_float, short_interest, days_to_cover, captured_at}] (several saved snapshots); own_rows: UW institution ownership rows (units_change, shares_outstanding, filing_date)."""
    cfg = CONFIG["short_inst"]
    if short_snaps is None and own_rows is None:
        return _factor("SHORT_INST", 0, 0, False, reason="NOT_COLLECTED")
    half = WEIGHTS["SHORT_INST"] / 2
    bull = bear = 0.0
    avail_pts, detail = 0, {}
    snaps = sorted([s for s in (short_snaps or []) if _ts(s.get("market_date")) and _ts(s["market_date"]) <= capture_ts], key=lambda s: str(s["market_date"]))
    if len(snaps) >= 2 and (_ts(snaps[-1]["market_date"]) - _ts(snaps[0]["market_date"])).days >= cfg["snapshot_min_gap_days"]:
        a, b = snaps[-1], snaps[0]
        si = _f(a.get("si_float"))
        pct = si * 100 if si is not None and si <= 1 else si
        s1, s0 = _f(a.get("short_interest")), _f(b.get("short_interest"))
        chg = (s1 - s0) / s0 * 100 if s1 is not None and s0 else None
        detail.update(si_float_pct=pct, short_interest_change_pct=round(chg, 2) if chg is not None else None, days_to_cover=_f(a.get("days_to_cover")))
        if pct is not None and chg is not None:
            avail_pts += half
            sg = -1 if (pct >= cfg["si_float_high_pct"] and chg >= cfg["si_change_pct"]) else 0          # high alone, or falling, gives no direction
            sg = _flip(sg, variant)
            bull += half if sg > 0 else 0
            bear += half if sg < 0 else 0
    if own_rows is not None:
        rows = [r for r in own_rows if (_ts(r.get("filing_date")) or capture_ts + timedelta(days=1)) <= capture_ts]
        sh = next((_f(r.get("shares_outstanding")) for r in rows if _f(r.get("shares_outstanding"))), None)
        if rows and sh:
            avail_pts += half
            net = sum(_f(r.get("units_change")) or 0 for r in rows) / sh * 100
            detail["institutional_net_pct_of_shares"] = round(net, 3)
            sg = _flip(1 if net >= cfg["inst_net_pct_of_shares"] else -1 if net <= -cfg["inst_net_pct_of_shares"] else 0, variant)
            bull += half if sg > 0 else 0
            bear += half if sg < 0 else 0
    if not avail_pts:
        return _factor("SHORT_INST", 0, 0, False, reason="NEEDS_TWO_SNAPSHOTS_10_DAYS_APART_OR_OWNERSHIP_DATA", snapshots=len(snaps))
    return _factor("SHORT_INST", bull, bear, True, available_points=avail_pts, note="FINRA short interest and 13F data are delayed; filing_date/market_date used as the stamp", **detail)


# ---------------------------------------------------------------- news / guidance (BLOCKED until sentiment semantics are verified)
def news_guidance(headlines, release_utc: Optional[datetime], capture_ts: datetime, variant: str) -> dict:
    cfg = CONFIG["news_guidance"]
    if not cfg["sentiment_semantics_verified"]:
        return _factor("NEWS_GUIDANCE", 0, 0, False, reason="BLOCKED_SENTIMENT_SEMANTICS_UNVERIFIED")
    if headlines is None or release_utc is None:
        return _factor("NEWS_GUIDANCE", 0, 0, False, reason="NOT_COLLECTED")
    cut = min(capture_ts, release_utc)                                      # nothing stamped at or after the earnings release can count
    net = 0
    for h in headlines:
        t = _ts(h.get("created_at"))
        if t is None or t >= cut or (cut - t).days > cfg["lookback_days"] or not h.get("is_major"):
            continue
        s = str(h.get("sentiment", "")).lower()
        net += 1 if s == "positive" else -1 if s == "negative" else 0
    sg = _flip(1 if net >= cfg["net_major_min"] else -1 if net <= -cfg["net_major_min"] else 0, variant)
    return _factor("NEWS_GUIDANCE", cfg["points"] if sg > 0 else 0, cfg["points"] if sg < 0 else 0, True, net_major_sentiment=net)


# ---------------------------------------------------------------- engine
def decide(bull: float, bear: float, directional_available: float) -> tuple:
    d = CONFIG["decision"]
    if directional_available < d["min_directional_available"]:
        return "NO_TRADE", ["INSUFFICIENT_COVERAGE"]
    if bull >= d["bull_min"] and bear <= d["bear_max"] and bull - bear >= d["margin_min"]:
        return "BUY", ["RULE"]
    if bear >= d["bull_min"] and bull <= d["bear_max"] and bear - bull >= d["margin_min"]:       # mirror of the BUY rule
        return "SELL", ["RULE"]
    return "NO_TRADE", ["BELOW_MARGIN"]


GATES = ("OUT_OF_WINDOW", "UNKNOWN_TIMING", "TIMING_CONTRADICTED", "NOT_TRADABLE", "ILLIQUID_OR_UNKNOWN", "STALE_EVIDENCE", "BLOCKED_BY_GATE", "SUSPECT_WINDOW")


def explain(r: dict) -> str:
    drivers = sorted((f for f in r["factors"] if f["available"]), key=lambda f: -(f["bull"] + f["bear"]))[:3]
    parts = ["%s %+g/%g" % (f["factor"], f["bull"] - f["bear"], f.get("available_points", f["max"])) for f in drivers]
    why = {"INSUFFICIENT_COVERAGE": "only %g of the required 60 DIRECTIONAL points have data" % r["directional_available_points"], "BELOW_MARGIN": "evidence does not clear the BUY/SELL margin",
           "RULE": "evidence clears the experimental rule", "BLOCKED_BY_V1_GATE": "blocked by V1 eligibility gate(s): " + ", ".join(r.get("inherited_gates", []))}
    return "; ".join(why.get(c, c) for c in r["reason_codes"][:2]) + ". Largest net factors: " + (", ".join(parts) if parts else "none") + ". Missing: " + (", ".join(m["factor"] for m in r["missing"]) or "none") + "."


def score(factors: list, *, v1_gates: list, variant: str) -> dict:
    """factors: from_v1() plus the new factor dicts. v1_gates: V1's own eligibility/timing/liquidity gate codes, which V2 inherits and never relaxes."""
    ev_avail = sum(f.get("available_points", f["max"]) for f in factors if f["available"])
    dir_avail = sum(f.get("available_points", f["max"]) for f in factors if f["available"] and f["directional"])
    bull, bear = sum(f["bull"] for f in factors), sum(f["bear"] for f in factors)
    sig, codes = decide(bull, bear, dir_avail)
    inherited = [g for g in v1_gates if g in GATES]
    if sig in ("BUY", "SELL") and inherited:
        sig, codes = "NO_TRADE", ["BLOCKED_BY_V1_GATE"]
    elif sig == "NO_TRADE" and inherited:
        codes = codes + inherited
    missing = [{"factor": f["factor"], "max": f["max"], "reason": f.get("reason"), "data_status": FACTOR_STATUS[f["factor"]]} for f in factors if not f["available"]]
    display = "DATA_INCOMPLETE" if "INSUFFICIENT_COVERAGE" in codes or "BLOCKED_BY_V1_GATE" in codes and dir_avail < CONFIG["decision"]["min_directional_available"] else sig
    r = {"model": "V2", "version": VERSION, "status": STATUS, "config_hash": CONFIG_HASH, "variant": variant, "signal": sig, "display_status": display, "reason_codes": codes, "inherited_gates": inherited,
         "bull": round(bull, 2), "bear": round(bear, 2), "available_points": round(ev_avail, 2), "directional_available_points": round(dir_avail, 2), "max": 100,
         "missing_points": round(100 - ev_avail, 2), "missing": missing, "factors": factors, "usable_for_decisions": False,
         "validated_real_data_signal": bool(sig in ("BUY", "SELL") and not missing),
         "note": "EXPERIMENTAL: weights and thresholds are unvalidated hypotheses; research only"}
    r["explanation"] = explain(r)
    return r
