"""Prospective outcome grader and audit report (component 7). Reads predictions and graded outcomes (earnings_event_outcomes rows: next-session close-to-close, flat band
0.5 %), never edits a prediction, and states plainly that no accuracy claim is allowed until the evidence audit and the pre-registered minimum sample are met.
Only in-window predictions of the FINAL event version, latest prediction version per variant, are graded; everything else is counted and explained in the audit.
"""
import math
import statistics
from collections import Counter, defaultdict
from datetime import date

FLAT_BAND = 0.5
MIN_DECIDED_PER_VARIANT, MIN_EARNINGS_WEEKS = 100, 8
LIQ_SIDE_BPS = ((200e6, 3), (20e6, 8), (0, 20))


def side_bps(adv):
    if adv is None:
        return 20
    return next(b for lim, b in LIQ_SIDE_BPS if adv >= lim)


def net_return(sign, move_pct, adv, mult=1.0):
    return sign * move_pct - mult * (2 * (side_bps(adv) + 1) + (1 if sign < 0 else 0)) / 100.0


def wilson(k, n, z=1.96):
    if n == 0:
        return None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(c - h, 3), round(c + h, 3)]


def outcome_class(move):
    return "UP" if move > FLAT_BAND else "DOWN" if move < -FLAT_BAND else "FLAT"


def grade(predictions, outcomes, final_versions, *, audit_passed=False):
    """predictions: list of dicts as stored (symbol, earnings_date, event_version, variant, prediction_version, signal, in_window, bull_points, ..., adv20).
    outcomes: {(symbol, earnings_date): {'status': 'GRADED'|'PENDING_*', 'move_pct': float}}. final_versions: {(symbol, earnings_date): final event version}."""
    audit = Counter()
    latest = {}
    for p in predictions:
        k = (p["symbol"], p["earnings_date"], p["variant"])
        if k not in latest or p["prediction_version"] > latest[k]["prediction_version"]:
            latest[k] = p
    audit["predictions_total"] = len(predictions)
    audit["superseded_prediction_versions"] = len(predictions) - len(latest)
    by_variant = defaultdict(list)
    for (sym, ed, var), p in sorted(latest.items(), key=lambda kv: (kv[0][2], str(kv[0][1]), kv[0][0])):
        fv = final_versions.get((sym, ed))
        if fv is not None and p["event_version"] != fv:
            audit["not_final_event_version"] += 1
            continue
        if not p["in_window"]:
            audit["out_of_window_excluded"] += 1
            continue
        o = outcomes.get((sym, ed))
        if not o or o.get("status") != "GRADED" or o.get("move_pct") is None:
            audit["ungraded_" + ((o or {}).get("status") or "no_outcome_row")] += 1
            continue
        by_variant[var].append((p, o["move_pct"]))
    report = {"variants": {}, "audit": dict(audit), "claim_allowed": False, "claim_text": "NO ACCURACY CLAIM: shadow descriptive statistics only"}
    for var, rows in by_variant.items():
        mat = {s: Counter() for s in ("BUY", "SELL", "NO_TRADE")}
        dec, weeks = [], set()
        for p, mv in rows:
            c = outcome_class(mv)
            mat[p["signal"]][c] += 1
            weeks.add(p["earnings_date"].isocalendar()[:2] if isinstance(p["earnings_date"], date) else str(p["earnings_date"])[:7])
            if p["signal"] in ("BUY", "SELL"):
                dec.append((p, mv, c))
        hit = sum(1 for p, mv, c in dec if (p["signal"] == "BUY" and c == "UP") or (p["signal"] == "SELL" and c == "DOWN"))
        nonflat = sum(1 for _, _, c in dec if c != "FLAT")
        nets = {m: [net_return(1 if p["signal"] == "BUY" else -1, mv, p.get("adv20"), m) for p, mv, _ in dec] for m in (0, 1, 2, 3)}
        report["variants"][var] = {
            "graded_events": len(rows), "decided": len(dec), "coverage": round(len(dec) / len(rows), 3) if rows else None,
            "confusion": {s: {k: mat[s].get(k, 0) for k in ("UP", "FLAT", "DOWN")} for s in mat},
            "strict_hit_rate": round(hit / len(dec), 3) if dec else None, "strict_hit_ci95": wilson(hit, len(dec)),
            "direction_only_hit_rate": round(hit / nonflat, 3) if nonflat else None, "direction_only_n": nonflat,
            "flat_among_decided": sum(1 for _, _, c in dec if c == "FLAT"),
            "net_mean_pct_cost_multiplier": {str(m): (round(statistics.mean(v), 3) if v else None) for m, v in nets.items()},
            "earnings_weeks": len(weeks)}
        enough = len(dec) >= MIN_DECIDED_PER_VARIANT and len(weeks) >= MIN_EARNINGS_WEEKS
        report["variants"][var]["minimum_sample_met"] = enough
    all_enough = bool(report["variants"]) and all(v["minimum_sample_met"] for v in report["variants"].values())
    report["claim_allowed"] = bool(all_enough and audit_passed)
    if report["claim_allowed"]:
        report["claim_text"] = "minimum sample and evidence audit satisfied: results may be reported with their intervals; still no performance guarantee"
    return report


def audit_report(ledger_entries, report):
    """Counts of everything that was skipped, refused, retried, failed or missed, next to the grading audit."""
    c = Counter(e["type"] for e in ledger_entries)
    return {"ledger_counts": dict(c), "grading_audit": report["audit"], "claim_allowed": report["claim_allowed"]}
