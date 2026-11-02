"""Per-event outcome rows (PASS / FAIL / FLAT), Continuation vs Contrarian comparison and accuracy status, from SAVED predictions and outcomes only.

Reporting layer: it reads what smart_move_tracking already loaded, changes no score, threshold, gate, prediction or outcome, and never grades NO_TRADE or DATA_INCOMPLETE.
Grading follows the frozen grader: FLAT when -0.5 <= move <= +0.5 (boundaries included); the headline accuracy denominator and claim rules stay in smvshadow.grader.
"""
from __future__ import annotations

from collections import Counter
from typing import Optional

import smart_move_tracking as T
from smvshadow import grader

NOT_VERIFIED = "ACCURACY NOT VERIFIED - INSUFFICIENT DATA"
# The stored shadow variants are named for the reports. The historical baseline's full mirror is a different rule and is never mixed in.
STRATEGY = {"continuation": "continuation", "contrarian": "contrarian_partial_factor"}
STRATEGY_NOTE = {"continuation": "shadow scorer, primary direction",
                 "contrarian": "shadow scorer: reverses the F5 trend / relative-strength / extension and F8 market / sector signs only; F3 and F7 unchanged. Not the baseline contrarian_full_mirror (which reverses the final call)."}
CAL_FRESH_H = 6.0          # frozen rule inside D-4
CAL_WARN_H = 5.0           # collector refresh margin (smart_move_calendar_guard.MAX_AGE_H)


def pass_fail(signal: Optional[str], move_pct: Optional[float]) -> Optional[str]:
    """PASS / FAIL / FLAT for BUY and SELL only; None for NO_TRADE, DATA_INCOMPLETE, missing signal or missing move."""
    if signal not in ("BUY", "SELL") or move_pct is None:
        return None
    cls = grader.outcome_class(float(move_pct))
    if cls == "FLAT":
        return "FLAT"
    return "PASS" if (signal == "BUY") == (cls == "UP") else "FAIL"


def calendar_status(age_h: Optional[float]) -> dict:
    if age_h is None:
        return {"age_h": None, "status": "UNKNOWN", "note": "no calendar observation stored"}
    status = "STALE" if age_h > CAL_FRESH_H else "AGING" if age_h > CAL_WARN_H else "FRESH"
    return {"age_h": round(age_h, 2), "status": status, "note": "frozen limit %.0f h inside D-4" % CAL_FRESH_H}


def _latest(preds: list, mode: str) -> dict:
    last = {}
    for p in preds:
        if (p.get("mode") or "LIVE") != mode:
            continue
        k = (p["symbol"], str(p["earnings_date"])[:10])
        if k not in last or p.get("version", 1) >= last[k].get("version", 1):
            last[k] = p
    return last


def _status(e: dict, late: bool) -> str:
    if late:
        return "LATE_CAPTURE_NOT_GRADED"
    sig = e.get("signal")
    if sig in ("BUY", "SELL"):
        return sig
    codes = e.get("reason_codes") or []
    return "DATA_INCOMPLETE" if any(c.startswith(("INSUFFICIENT", "NO_", "MISSING", "STALE", "UNKNOWN", "RETRIEVED")) for c in codes) else "NO_TRADE"


def event_rows(preds: list, outcomes: list, *, mode: str, total_max: int = 40) -> list:
    """One row per latest prediction version per event and strategy. Outcome fields stay None when no stored outcome exists (nothing is manufactured)."""
    oc = {(o["symbol"], str(o["earnings_date"])[:10]): o for o in outcomes if o.get("mode") == mode}
    rows = []
    for (sym, ed), p in sorted(_latest(preds, mode).items(), key=lambda kv: (kv[0][1], kv[0][0])):
        late = T.is_late(p)
        o = oc.get((sym, ed))
        move = o.get("move_pct") if o else None
        for v in T.VARIANTS:
            e = p["engine"].get(v) or {}
            av = e.get("available")
            cls = grader.outcome_class(move) if move is not None else None
            rows.append({
                "symbol": sym, "earnings_date": ed, "kind": p.get("kind"), "mode": mode, "strategy": STRATEGY[v], "prediction_version": p.get("version", 1),
                "prediction_time": p.get("captured_at"), "decision": _status(e, late), "signal": e.get("signal"), "bull": e.get("bull"), "bear": e.get("bear"),
                "coverage": round(av / total_max, 3) if isinstance(av, (int, float)) and total_max else None, "late_capture": late,
                "reference_close": o.get("base_close") if o else None, "reaction_close": o.get("reaction_close") if o else None,
                "move_pct": move, "actual_direction": cls, "result": None if late else pass_fail(e.get("signal"), move),
                "missing_reason": ", ".join(e.get("reason_codes") or []) or None})
    return rows


def comparison(preds: list, *, mode: str) -> dict:
    """Continuation vs contrarian_partial_factor on the same stored predictions. Descriptive; no strategy is selected."""
    rows, agree = [], Counter()
    for (sym, ed), p in sorted(_latest(preds, mode).items(), key=lambda kv: (kv[0][1], kv[0][0])):
        c, k = (p["engine"].get("continuation") or {}), (p["engine"].get("contrarian") or {})
        cs, ks = c.get("signal") or "NO_TRADE", k.get("signal") or "NO_TRADE"
        rel = "AGREE" if cs == ks else "OPPOSITE" if {cs, ks} == {"BUY", "SELL"} else "ONE_ABSTAINS"
        agree[rel] += 1
        rows.append({"symbol": sym, "earnings_date": ed, "continuation": cs, "contrarian_partial_factor": ks, "relation": rel,
                     "continuation_available": c.get("available"), "contrarian_available": k.get("available"),
                     "missing_continuation": c.get("reason_codes") or [], "missing_contrarian": k.get("reason_codes") or []})
    return {"mode": mode, "events": len(rows), "relations": dict(agree), "rows": rows, "definitions": STRATEGY_NOTE,
            "note": "contrarian_partial_factor is not the baseline contrarian_full_mirror. Their results are never combined. No winner is selected."}


def accuracy_status(rows: list, grader_report: Optional[dict] = None) -> dict:
    """PASS/FAIL/FLAT tallies per strategy (descriptive) plus the frozen claim gate. Without verified outcomes the status is NOT_VERIFIED."""
    out = {}
    for s in STRATEGY.values():
        r = [x for x in rows if x["strategy"] == s and x["result"]]
        c = Counter(x["result"] for x in r)
        decided = c["PASS"] + c["FAIL"]
        out[s] = {"graded": len(r), "pass": c["PASS"], "fail": c["FAIL"], "flat": c["FLAT"], "decided": decided,
                  "no_trade_or_incomplete": sum(1 for x in rows if x["strategy"] == s and x["decision"] in ("NO_TRADE", "DATA_INCOMPLETE")),
                  "verified_outcomes": sum(1 for x in rows if x["strategy"] == s and x["move_pct"] is not None)}
    claim = bool(grader_report and grader_report.get("claim_allowed"))
    verified = sum(v["verified_outcomes"] for v in out.values())
    return {"strategies": out, "claim_allowed": claim,
            "headline": NOT_VERIFIED if (verified == 0 or not claim) else "claim gate satisfied: report with intervals",
            "min_decided_per_variant": grader.MIN_DECIDED_PER_VARIANT, "min_earnings_weeks": grader.MIN_EARNINGS_WEEKS,
            "separation": "SIMULATION and development results are labelled by mode and never shown as verified live performance"}


def build(preds: list, outcomes: list, *, mode: str, calendar_age_h: Optional[float] = None) -> dict:
    rows = event_rows(preds, outcomes, mode=mode)
    acc = T.accuracy(preds, outcomes, mode=mode)
    return {"mode": mode, "calendar": calendar_status(calendar_age_h), "rows": rows, "comparison": comparison(preds, mode=mode),
            "accuracy": accuracy_status(rows, acc), "no_trade_grading": "NO_TRADE and DATA_INCOMPLETE are never graded PASS or FAIL"}
