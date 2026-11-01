"""Smart Move V2: an EXPERIMENTAL, separately versioned 100-point model. V1 (the frozen 40-point engine) is never modified or re-scored here.

Everything below is a research hypothesis, not a validated improvement:
  * the ten factor weights come from the owner's proposal (sum 100);
  * the thresholds in CONFIG are documented guesses, hashed into the config hash, and unvalidated;
  * the decision policy is V1's, scaled by 2.5 (bull >= 35, bear <= 10, margin >= 25) with the same 0.60 minimum evidence ratio (60 of 100 points). It is an
    assumption, not a validated rule.
No missing factor is normalised away: a score is always "points out of 100 possible", with the available points shown next to it. A factor whose data is not saved
(or whose direction is not defined) contributes nothing and is listed as missing. Nothing here sends a request: adapters take saved bodies.
"""
from __future__ import annotations

import hashlib
import json
import statistics
from datetime import date, datetime, timezone
from typing import Optional

VERSION = "v2-exp-1"
STATUS = "EXPERIMENTAL"

WEIGHTS = {"F3": 15, "F5": 10, "F7": 10, "F8": 5, "ANALYST": 15, "OPTIONS_FLOW": 15, "IMPLIED_VS_HIST": 10, "SHORT_INST": 5, "REL_STRENGTH": 5, "NEWS_GUIDANCE": 10}
assert sum(WEIGHTS.values()) == 100

CONFIG = {
    "version": VERSION, "weights": WEIGHTS,
    "decision": {"bull_min": 35, "bear_max": 10, "margin_min": 25, "min_available": 60, "basis": "V1 thresholds x 2.5; unvalidated assumption"},
    "rel_strength": {"window_sessions": 10, "threshold_pct": 2.0, "basis": "hypothesis: 10-session return vs SPY and vs sector ETF, +-2 percentage points"},
    "analyst": {"lookback_days": 30, "net_actions_min": 2, "basis": "hypothesis: net upgrades minus downgrades (screener/analysts) in 30 days"},
    "options_flow": {"net_premium_min_usd": 1_000_000, "basis": "hypothesis: net call premium minus net put premium (net-prem-ticks), +-USD 1M"},
    "short_inst": {"si_float_high_pct": 15.0, "basis": "hypothesis: short interest above 15% of float is bearish; net 13F unit increase is bullish"},
    "implied_vs_hist": {"basis": "magnitude only: no direction is defined, so it adds evidence but no bull or bear points"},
    "news_guidance": {"basis": "BLOCKED: no verified structured guidance field; news sentiment semantics unverified"},
}
CONFIG_HASH = hashlib.sha256(json.dumps(CONFIG, sort_keys=True).encode()).hexdigest()

# status of each factor's DATA, from the availability audit (docs/smart_move/V2_DATA_AVAILABILITY.md)
FACTOR_STATUS = {"F3": "READY", "F5": "READY", "F7": "READY", "F8": "READY", "ANALYST": "PARTIAL", "OPTIONS_FLOW": "PARTIAL", "IMPLIED_VS_HIST": "PARTIAL",
                 "SHORT_INST": "PARTIAL", "REL_STRENGTH": "READY", "NEWS_GUIDANCE": "BLOCKED"}


def _f(x) -> Optional[float]:
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def _factor(name, bull, bear, available, **why):
    return {"factor": name, "max": WEIGHTS[name], "bull": bull if available else 0, "bear": bear if available else 0, "available": bool(available), **why}


# ---------------------------------------------------------------- the four V1 factors, taken from V1's own sub-signal breakdown (never recomputed)
def from_v1(subscores: dict) -> list:
    groups = {"F3": ("F3_mean", "F3_consistency"), "F5": ("F5_trend", "F5_relative_strength", "F5_extension"), "F7": ("F7_skew",), "F8": ("F8_market", "F8_sector")}
    out = []
    for fac, names in groups.items():
        parts = [subscores[n] for n in names if n in subscores]
        avail = [p for p in parts if p["available"]]
        if not avail:
            out.append(_factor(fac, 0, 0, False, reason="V1_FACTOR_MISSING", source="V1 engine"))
            continue
        out.append(_factor(fac, sum(p["bull"] for p in avail), sum(p["bear"] for p in avail), True, source="V1 engine sub-signals", available_points=sum(p["weight"] for p in avail)))
    return out


# ---------------------------------------------------------------- new factors (pure; inputs are saved bodies)
def implied_vs_hist(earnings_rows, event_date: str) -> dict:
    """Upcoming implied move (UW earnings row for the event date) divided by the mean absolute reported 1-day move of up to the last 8 reported quarters."""
    up = next((r for r in (earnings_rows or []) if str(r.get("report_date"))[:10] == event_date), None)
    im = _f((up or {}).get("expected_move_perc"))
    hist = [abs(_f(r.get("post_earnings_move_1d"))) for r in (earnings_rows or []) if str(r.get("report_date"))[:10] < event_date and _f(r.get("post_earnings_move_1d")) is not None][:8]
    if im is None or len(hist) < 4:
        return _factor("IMPLIED_VS_HIST", 0, 0, False, reason="NO_IMPLIED_MOVE_OR_FEWER_THAN_4_REPORTED_MOVES")
    ratio = im / statistics.mean(hist) if statistics.mean(hist) else None
    return _factor("IMPLIED_VS_HIST", 0, 0, True, ratio=round(ratio, 3) if ratio else None, implied_move=im, mean_abs_hist_move=round(statistics.mean(hist), 5), n_hist=len(hist),
                   note="magnitude only; contributes evidence but no directional points")


def rel_strength(stock_series: dict, spy_series: dict, sector_series: Optional[dict], last: date, variant: str) -> dict:
    """series: date -> close. 10-session return of the stock minus SPY and minus the sector ETF. Contrarian flips the sign (same convention as V1)."""
    cfg = CONFIG["rel_strength"]
    n, thr = cfg["window_sessions"], cfg["threshold_pct"]

    def ret(ser):
        ds = [d for d in sorted(ser) if d <= last]
        return (ser[ds[-1]] / ser[ds[-1 - n]] - 1) * 100 if len(ds) > n and ser[ds[-1 - n]] else None
    r, s, e = ret(stock_series or {}), ret(spy_series or {}), ret(sector_series or {}) if sector_series else None
    if r is None or s is None:
        return _factor("REL_STRENGTH", 0, 0, False, reason="NO_PRICE_OR_MARKET_HISTORY")
    half = WEIGHTS["REL_STRENGTH"] / 2
    bull = bear = 0.0
    vals = {"vs_spy": round(r - s, 2)}
    for key, base in (("vs_spy", s), ("vs_sector", e)):
        if base is None:
            continue
        d = r - base
        vals[key] = round(d, 2)
        sg = 1 if d >= thr else -1 if d <= -thr else 0
        sg = -sg if variant == "contrarian" else sg
        bull += half if sg > 0 else 0
        bear += half if sg < 0 else 0
    return _factor("REL_STRENGTH", bull, bear, True, values=vals, available_points=half * (1 + (e is not None)), sector_part=e is not None)


def analyst(rows, event_date: str, capture_ts: datetime, variant: str) -> dict:
    """rows: UW /api/screener/analysts rows for the ticker (action, timestamp). Only actions stamped before the capture time count."""
    cfg = CONFIG["analyst"]
    if rows is None:
        return _factor("ANALYST", 0, 0, False, reason="NOT_COLLECTED")
    up = dn = 0
    for r in rows:
        try:
            t = datetime.fromisoformat(str(r["timestamp"]).replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if t > capture_ts or (capture_ts - t).days > cfg["lookback_days"]:
            continue
        a = str(r.get("action", "")).lower()
        up += a in ("upgrade", "initiated", "raised")
        dn += a in ("downgrade", "lowered")
    net = up - dn
    sg = 1 if net >= cfg["net_actions_min"] else -1 if net <= -cfg["net_actions_min"] else 0
    sg = -sg if variant == "contrarian" else sg
    return _factor("ANALYST", WEIGHTS["ANALYST"] if sg > 0 else 0, WEIGHTS["ANALYST"] if sg < 0 else 0, True, upgrades=up, downgrades=dn,
                   note="rating actions only; EPS/revenue estimate revisions need dated snapshots that are not saved")


def options_flow(ticks, variant: str) -> dict:
    """ticks: UW /api/stock/{t}/net-prem-ticks rows (net_call_premium, net_put_premium per tick); the day's cumulative net premium decides."""
    cfg = CONFIG["options_flow"]
    if not ticks:
        return _factor("OPTIONS_FLOW", 0, 0, False, reason="NOT_COLLECTED")
    net = sum((_f(t.get("net_call_premium")) or 0) - (_f(t.get("net_put_premium")) or 0) for t in ticks)
    sg = 1 if net >= cfg["net_premium_min_usd"] else -1 if net <= -cfg["net_premium_min_usd"] else 0
    sg = -sg if variant == "contrarian" else sg
    return _factor("OPTIONS_FLOW", WEIGHTS["OPTIONS_FLOW"] if sg > 0 else 0, WEIGHTS["OPTIONS_FLOW"] if sg < 0 else 0, True, net_call_minus_put_premium=round(net), ticks=len(ticks))


def short_inst(short_row, own_rows, variant: str) -> dict:
    cfg = CONFIG["short_inst"]
    if short_row is None and not own_rows:
        return _factor("SHORT_INST", 0, 0, False, reason="NOT_COLLECTED")
    half = WEIGHTS["SHORT_INST"] / 2
    bull = bear = 0.0
    si = _f((short_row or {}).get("si_float"))
    if si is not None:
        pct = si * 100 if si <= 1 else si
        sg = -1 if pct >= cfg["si_float_high_pct"] else 0
        sg = -sg if variant == "contrarian" else sg
        bull += half if sg > 0 else 0
        bear += half if sg < 0 else 0
    if own_rows:
        net = sum(_f(r.get("units_change")) or 0 for r in own_rows)
        sg = 1 if net > 0 else -1 if net < 0 else 0
        sg = -sg if variant == "contrarian" else sg
        bull += half if sg > 0 else 0
        bear += half if sg < 0 else 0
    return _factor("SHORT_INST", bull, bear, si is not None or bool(own_rows), si_float=si, note="13F data is delayed (filing_date); FINRA short interest is delayed")


def news_guidance() -> dict:
    return _factor("NEWS_GUIDANCE", 0, 0, False, reason="BLOCKED_NO_VERIFIED_GUIDANCE_FIELD")


# ---------------------------------------------------------------- engine
def decide(bull: float, bear: float, available: float) -> tuple:
    d = CONFIG["decision"]
    codes = []
    if available < d["min_available"]:
        return "NO_TRADE", ["INSUFFICIENT_COVERAGE"]
    if bull >= d["bull_min"] and bear <= d["bear_max"] and bull - bear >= d["margin_min"]:
        return "BUY", ["RULE"]
    if bear >= d["bull_min"] and bull <= d["bear_max"] and bear - bull >= d["margin_min"]:       # mirror of the BUY rule
        return "SELL", ["RULE"]
    return "NO_TRADE", codes + ["BELOW_MARGIN"]


def score(factors: list, *, v1_gates: list, variant: str) -> dict:
    """factors: list from from_v1() plus the new factor dicts. v1_gates: V1's own eligibility/timing/liquidity gate codes, which V2 inherits and never relaxes."""
    # evidence points count a factor at its full weight when it has data, except a partial factor (e.g. F8 with only the market part) which counts what it covers
    avail = sum(f.get("available_points", f["max"]) for f in factors if f["available"])
    bull, bear = sum(f["bull"] for f in factors), sum(f["bear"] for f in factors)
    sig, codes = decide(bull, bear, avail)
    inherited = [g for g in v1_gates if g in ("OUT_OF_WINDOW", "UNKNOWN_TIMING", "TIMING_CONTRADICTED", "NOT_TRADABLE", "ILLIQUID_OR_UNKNOWN", "STALE_EVIDENCE", "BLOCKED_BY_GATE", "SUSPECT_WINDOW")]
    if sig in ("BUY", "SELL") and inherited:
        sig, codes = "NO_TRADE", ["BLOCKED_BY_V1_GATE"] + inherited
    missing = [{"factor": f["factor"], "max": f["max"], "reason": f.get("reason"), "data_status": FACTOR_STATUS[f["factor"]]} for f in factors if not f["available"]]
    return {"model": "V2", "version": VERSION, "status": STATUS, "config_hash": CONFIG_HASH, "variant": variant, "signal": sig, "reason_codes": codes if "BLOCKED_BY_V1_GATE" in codes else codes + inherited,
            "bull": round(bull, 2), "bear": round(bear, 2), "available_points": round(avail, 2), "max": 100, "missing": missing, "factors": factors,
            "usable_for_decisions": False, "note": "EXPERIMENTAL: weights and thresholds are unvalidated hypotheses; the displayed signal is research only"}
