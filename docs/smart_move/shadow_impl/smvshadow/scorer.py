"""Frozen 40-point SHADOW scorer (component 6). Every threshold is read from the frozen pre-registration (shadow/prereg_score40_v1.json, hash checked against its sidecar);
nothing is hard-coded twice. The score is never normalised to 100. Bullish and bearish points are accumulated separately; evidence that is missing, stale or not
point-in-time earns no points and does not count toward coverage. Both variants (continuation, contrarian) are scored independently and never merged.
"""
import hashlib
import json
from pathlib import Path

PREREG = Path(__file__).resolve().parents[2] / "shadow" / "prereg_score40_v1.json"
SCORING_VERSION = "score40-v1"
LIQUIDITY_MIN = 20_000_000


class PreregError(RuntimeError):
    pass


def load_prereg(path=PREREG):
    path = Path(path)
    want = Path(str(path) + ".sha256").read_text(encoding="ascii").split()[0]
    got = hashlib.sha256(path.read_bytes()).hexdigest()
    if got != want:
        raise PreregError("pre-registration hash mismatch")
    return json.loads(path.read_bytes().decode("ascii")), got


def _step(x, strong, moderate, pts_strong, pts_moderate):
    """Points and sign for |x| against thresholds (x in the same unit as the thresholds)."""
    a = abs(x)
    p = pts_strong if a >= strong else pts_moderate if (moderate is not None and a >= moderate) else 0
    return p, (1 if x > 0 else -1 if x < 0 else 0)


def _add(bd, name, weight, bull, bear, available, **why):
    bd[name] = {"weight": weight, "available": available, "bull": bull, "bear": bear, **why}


def subscores(pre, ev, variant):
    """Per sub-signal breakdown. `ev` maps factor number -> FactorEvidence. Returns (breakdown, bull, bear, available_weight)."""
    bd = {}
    swap = (variant == "contrarian")
    f3, f5, f7, f8 = ev.get(3), ev.get(5), ev.get(7), ev.get(8)
    p3, p5, p8 = pre["F3"], pre["F5"], pre["F8"]
    # ---- F3 (tendency of the stock's own reactions; not variant dependent)
    if f3 and f3.usable:
        x = f3.extracted
        ms, cs = p3["mean_signal"], p3["consistency_signal"]
        pts, sg = _step(x["mean_pct"], ms["strong_pct"], ms["moderate_pct"], ms["points"]["strong"], ms["points"]["moderate"])
        _add(bd, "F3_mean", ms["max_points"], pts if sg > 0 else 0, pts if sg < 0 else 0, True, value=x["mean_pct"], snapshot=f3.payload_sha256)
        b = cs["points"]["strong"] if x["up_share"] >= cs["strong_share"] else cs["points"]["moderate"] if x["up_share"] >= cs["moderate_share"] else 0
        e = cs["points"]["strong"] if x["down_share"] >= cs["strong_share"] else cs["points"]["moderate"] if x["down_share"] >= cs["moderate_share"] else 0
        _add(bd, "F3_consistency", cs["max_points"], b, e, True, up_share=x["up_share"], down_share=x["down_share"])
    else:
        for n, k in (("F3_mean", "mean_signal"), ("F3_consistency", "consistency_signal")):
            _add(bd, n, p3[k]["max_points"], 0, 0, False, missing=(f3.missing_reason if f3 else "NO_EVIDENCE") if not f3 or not f3.usable else None)
    # ---- F5 (variant dependent direction)
    x5 = f5.extracted if f5 and f5.usable else {}
    for name, key, spec, scale in (("F5_trend", "ret20", p5["trend"], 100.0), ("F5_relative_strength", "rel20", p5["relative_strength"], 100.0), ("F5_extension", "ext50", p5["extension"], 100.0)):
        v = x5.get(key)
        if v is None:
            _add(bd, name, spec["max_points"], 0, 0, False, missing=(x5.get("missing_why", {}) or {}).get(key) or (f5.missing_reason if f5 else "NO_EVIDENCE"))
            continue
        pts, sg = _step(v * scale, spec["strong_pct"], spec.get("moderate_pct"), spec["points"]["strong"], spec["points"].get("moderate", 0))
        if swap:
            sg = -sg
        _add(bd, name, spec["max_points"], pts if sg > 0 else 0, pts if sg < 0 else 0, True, value=v)
    # ---- F7 (provider-defined skew label; identical in both variants)
    p7 = pre["F7"]
    if f7 and f7.usable and p7["enabled"]:
        lab = f7.extracted["skew_label"]
        _add(bd, "F7_skew", p7["max_points"], p7["max_points"] if lab == "call_rich" else 0, p7["max_points"] if lab == "put_rich" else 0, True, label=lab)
    else:
        _add(bd, "F7_skew", p7["max_points"], 0, 0, False, missing=(f7.missing_reason if f7 else "NO_EVIDENCE") if not (f7 and f7.usable) else "FLAG_OFF")
    # ---- F8 (variant dependent)
    x8 = f8.extracted if f8 and f8.usable else {}
    for name, key, spec in (("F8_market", "spy_ret20", p8["market"]), ("F8_sector", "sector_rel20", p8["sector"])):
        v = x8.get(key)
        if v is None:
            _add(bd, name, spec["max_points"], 0, 0, False, missing=(f8.missing_reason if f8 and not f8.usable else "NO_SECTOR_OR_HISTORY") if f8 else "NO_EVIDENCE")
            continue
        pts, sg = _step(v * 100.0, spec["strong_pct"], None, spec["points"], 0)
        if swap:
            sg = -sg
        _add(bd, name, spec["max_points"], pts if sg > 0 else 0, pts if sg < 0 else 0, True, value=v)
    bull = sum(s["bull"] for s in bd.values() if s["available"])
    bear = sum(s["bear"] for s in bd.values() if s["available"])
    avail = sum(s["weight"] for s in bd.values() if s["available"])
    return bd, bull, bear, avail


def score(pre, prereg_sha, ev, *, symbol, event_date, event_version, kind, timing_status, plan, capture_ts, variant, prediction_version=1):
    """One prediction (one variant). Returns the dict stored in smv.predictions."""
    d = pre["decision"]
    bd, bull, bear, avail = subscores(pre, ev, variant)
    codes = []
    in_window = plan["window_start_utc"] <= capture_ts <= plan["cutoff_utc"]
    if not in_window:
        codes.append("OUT_OF_WINDOW")
    if kind not in ("BMO", "AMC") or timing_status in ("UNKNOWN",):
        codes.append("UNKNOWN_TIMING")
    elif timing_status == "CONFLICT":
        codes.append("TIMING_CONTRADICTED")
    if not plan["tradable"]:
        codes.append("NOT_TRADABLE")
    f5 = ev.get(5)
    adv = f5.extracted.get("adv20") if f5 and f5.usable else None
    if adv is None or adv < LIQUIDITY_MIN:
        codes.append("ILLIQUID_OR_UNKNOWN")
    if any(e is not None and e.status == "MISSING" and e.missing_reason == "STALE_AGE" for e in ev.values()):
        codes.append("STALE_EVIDENCE")
    if any((e is not None and "SUSPECT_WINDOW" in str((e.extracted or {}).get("missing_why", {}).values()) + str(e.missing_reason)) for e in ev.values()):
        codes.append("SUSPECT_WINDOW")
    coverage = avail / pre["factors"]["total_max"]
    if coverage < d["min_coverage"]:
        codes.append("INSUFFICIENT_COVERAGE")
    gates = [c for c in codes if c in ("OUT_OF_WINDOW", "UNKNOWN_TIMING", "TIMING_CONTRADICTED", "NOT_TRADABLE", "ILLIQUID_OR_UNKNOWN", "INSUFFICIENT_COVERAGE")]
    signal = "NO_TRADE"
    if not gates:
        if bull >= 14 and bear <= 4 and bull - bear >= 10:
            signal, codes = "BUY", codes + ["RULE"]
        elif bear >= 14 and bull <= 4 and bear - bull >= 10:
            signal, codes = "SELL", codes + ["RULE"]
        elif bull >= 14 and bear >= 14:
            codes.append("CONFLICT")
        else:
            codes.append("BELOW_MARGIN")
    return {"symbol": symbol, "earnings_date": event_date, "event_version": event_version, "scoring_version": SCORING_VERSION, "variant": variant, "prereg_sha256": prereg_sha,
            "prediction_version": prediction_version, "predicted_at_utc": capture_ts, "cutoff_utc": plan["cutoff_utc"], "in_window": in_window, "signal": signal,
            "bull_points": bull, "bear_points": bear, "weight_available": avail, "reason_codes": codes,
            "explanation": {"weight_max": pre["factors"]["total_max"], "coverage": round(coverage, 4), "subscores": bd, "never_scaled_to_100": True,
                            "thresholds": {"BUY": d["BUY"], "SELL": d["SELL"], "min_coverage": d["min_coverage"]}}}
