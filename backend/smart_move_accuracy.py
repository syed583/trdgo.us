"""Prediction-versus-outcome comparison and accuracy report (Phase 3, offline). It joins ADMISSIBLE saved predictions (smart_move_pipeline.validate_prediction) with VERIFIED outcomes (the outcome audit) and reports
counts, hit rates and Wilson intervals WITH their sample sizes. Verified and unverified outcomes are kept apart and unverified ones never enter a rate. The frozen grader supplies the FLAT band and the interval
formula (read only); prediction definitions and outcome definitions are untouched. No accuracy claim is allowed unless the grader's own minimum-sample rule is met, and even then the text says it is no guarantee."""
from __future__ import annotations

import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docs" / "smart_move" / "shadow_impl"))
from smvshadow import grader as gr  # noqa: E402

DIRECTION = {"BUY": "UP", "SELL": "DOWN"}


def verified_outcome(row: dict) -> bool:
    """An outcome counts as VERIFIED only when: computed from saved regular-session bars retrieved after the reaction close, timing from the event record (not a vendor label alone), no corporate-action suspicion."""
    return row.get("state") == "OUTCOME_COMPUTABLE" and row.get("timing_basis") == "EVENT_RECORD" and row.get("outcome_class") is not None


def compare(validated: Iterable[dict], outcome_rows: Iterable[dict]) -> dict:
    """validated: pipeline.validate_prediction results (with `signals`, `valid`, `reasons`). outcome_rows: outcome_audit.audit(...)['events']. Join key (symbol, event date).
    -> {rows, exclusions}: rows are the comparable pairs, exclusions count why predictions did not make it (never silently dropped)."""
    out_by = {(o["symbol"], str(o["event_date"])[:10]): o for o in outcome_rows}
    rows, excl = [], Counter()
    for v in validated:
        key = (v["symbol"], v["earnings_date"])
        if not v["valid"]:
            excl["PREDICTION_NOT_ADMISSIBLE:" + (v["reasons"][0] if v["reasons"] else "?")] += 1
            continue
        o = out_by.get(key)
        if o is None:
            excl["NO_OUTCOME_ROW"] += 1
        elif not verified_outcome(o):
            excl["OUTCOME_NOT_VERIFIED:" + o["state"] + ("" if o.get("timing_basis", "EVENT_RECORD") == "EVENT_RECORD" else ":" + o["timing_basis"])] += 1
        else:
            rows.append({"symbol": v["symbol"], "event_date": v["earnings_date"], "event_id": v["event_id"], "event_version": v["event_version"], "signals": v["signals"], "outcome_class": o["outcome_class"],
                         "move_pct": o["move_pct"], "bars_sha256": o.get("bars_sha256")})
    return {"rows": rows, "exclusions": dict(excl)}


def report(validated: list, outcome_rows: list) -> dict:
    """Accuracy per engine variant with sample sizes. `decided` = BUY or SELL; a hit is BUY->UP or SELL->DOWN (strict: FLAT counts as a miss); `direction_only` drops FLAT outcomes."""
    cmp_ = compare(validated, outcome_rows)
    out_states = Counter(("VERIFIED" if verified_outcome(o) else "UNVERIFIED:" + o["state"]) for o in outcome_rows)
    variants = sorted({k for r in cmp_["rows"] for k in r["signals"]})
    rep = {}
    for var in variants:
        rows = [r for r in cmp_["rows"] if r["signals"].get(var)]
        dec = [r for r in rows if r["signals"][var] in DIRECTION]
        hits = sum(1 for r in dec if DIRECTION[r["signals"][var]] == r["outcome_class"])
        nonflat = [r for r in dec if r["outcome_class"] != "FLAT"]
        dhits = sum(1 for r in nonflat if DIRECTION[r["signals"][var]] == r["outcome_class"])
        weeks = {date.fromisoformat(r["event_date"]).isocalendar()[:2] for r in dec}
        conf = {s: Counter(r["outcome_class"] for r in rows if r["signals"][var] == s) for s in ("BUY", "SELL", "NO_TRADE")}
        enough = len(dec) >= gr.MIN_DECIDED_PER_VARIANT and len(weeks) >= gr.MIN_EARNINGS_WEEKS
        rep[var] = {"compared_events": len(rows), "decided_n": len(dec), "abstained_no_trade_n": len(rows) - len(dec), "strict_hits": hits, "strict_hit_rate": round(hits / len(dec), 3) if dec else None,
                    "strict_hit_ci95": gr.wilson(hits, len(dec)) if dec else None, "direction_only_n": len(nonflat), "direction_only_hits": dhits,
                    "direction_only_hit_rate": round(dhits / len(nonflat), 3) if nonflat else None, "confusion": {s: {k: c.get(k, 0) for k in ("UP", "FLAT", "DOWN")} for s, c in conf.items()},
                    "minimum_sample_met": enough, "minimum_decided_required": gr.MIN_DECIDED_PER_VARIANT, "earnings_weeks": len(weeks), "minimum_weeks_required": gr.MIN_EARNINGS_WEEKS}
    allowed = bool(rep) and all(v["minimum_sample_met"] for v in rep.values())
    return {"variants": rep, "predictions_total": len(validated), "predictions_admissible": sum(1 for v in validated if v["valid"]), "comparable_pairs": len(cmp_["rows"]), "exclusions": cmp_["exclusions"],
            "outcomes_by_verification": dict(out_states), "claim_allowed": allowed,
            "claim_text": ("minimum sample met: report with intervals; still no performance guarantee" if allowed else "NO ACCURACY CLAIM: sample too small or no verified comparable pairs"),
            "flat_band_pct": gr.FLAT_BAND}
