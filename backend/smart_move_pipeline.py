"""Earnings data pipeline glue (Phase 1, offline): point-in-time prediction validation and capture planning, per-event completeness with reason codes, event-level Snapshot A/B scheduling, and the
cutoff / reschedule rules that tie a saved prediction to ONE event version. Reads saved records only; it sends nothing, scores nothing and never writes an outcome. The prediction DEFINITIONS (engines, scorer,
digest) stay frozen: this module only judges whether a saved prediction is admissible evidence and what is still missing around it."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional

import smart_move_collection_plan as CP
import smart_move_event_records as ER

UTC = timezone.utc

# every reason code the pipeline can emit (a test asserts nothing outside this set is produced)
REASONS = {
    "EVENT_NOT_RECORDED_AT_CAPTURE", "EVENT_NOT_ELIGIBLE_AT_CAPTURE", "CAPTURED_BEFORE_WINDOW", "CAPTURED_AFTER_CUTOFF", "EVIDENCE_RETRIEVED_AFTER_CAPTURE", "NO_DIGEST", "NO_EVIDENCE_LISTED",
    "EVENT_DATE_CHANGED_AFTER_CAPTURE", "EVENT_REMOVED_AFTER_CAPTURE", "IDENTITY_AMBIGUOUS_AT_CAPTURE", "NO_CAPTURED_AT",
    "CALENDAR_NOT_CONFIRMED", "TIMING_UNKNOWN", "CALENDAR_CONFLICT", "QUARTER_UNKNOWN", "QUARTER_AMBIGUOUS", "QUARTER_LEARNED_AFTER_CUTOFF", "SNAPSHOT_PAIR_MISSING", "NEWS_NOT_COLLECTED",
    "NEWS_FAILED", "NEWS_EMPTY_RESPONSE", "NEWS_NO_VALID_HEADLINE", "PRICE_BARS_NOT_SAVED", "FLOW_NOT_COLLECTED", "RELEASE_TIME_NOT_VERIFIED",
}


def _dt(x) -> datetime:
    d = x if isinstance(x, datetime) else datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


# ------------------------------------------------------------------ point-in-time prediction validation
def event_for_prediction(records: "ER.EventRecords", symbol: str, earnings_date: str, at) -> Optional[dict]:
    """The event record for (symbol, event date) as it was KNOWN at time `at`. A date that matches no version known then returns None."""
    at = _dt(at)
    hits = []
    for eid in records.event_ids(symbol):
        v = records.as_of(eid, at)
        if v and v["event_date"] == str(earnings_date)[:10]:
            hits.append(v)
    return max(hits, key=lambda v: v["version"]) if hits else None


def validate_prediction(pred: dict, records: "ER.EventRecords") -> dict:
    """Is a SAVED prediction admissible point-in-time evidence for its event? Checks (each failure is a reason code, none is repaired):
    captured inside the event's 48 h to 24 h window and not after the cutoff; the event was recorded AND eligible when the prediction was captured; every listed evidence record was retrieved at or before
    the capture; a digest exists; the event was not rescheduled or removed afterwards (then the outcome sessions would differ). Binds the prediction to the event id and version."""
    reasons = []
    out = {"symbol": pred.get("symbol"), "earnings_date": str(pred.get("earnings_date"))[:10], "prediction_version": pred.get("version"), "event_id": None, "event_version": None, "valid": False, "reasons": reasons,
           "signals": {k: (v or {}).get("signal") for k, v in (pred.get("engine") or {}).items()}}
    if not pred.get("captured_at"):
        reasons.append("NO_CAPTURED_AT")
        return out
    cap = _dt(pred["captured_at"])
    if not pred.get("digest"):
        reasons.append("NO_DIGEST")
    ev = pred.get("evidence")
    if not ev:
        reasons.append("NO_EVIDENCE_LISTED")
    elif any(_dt(e["retrieved_at"]) > cap for e in ev if e.get("retrieved_at")):
        reasons.append("EVIDENCE_RETRIEVED_AFTER_CAPTURE")
    head = event_for_prediction(records, pred["symbol"], pred["earnings_date"], cap)
    if head is None:
        reasons.append("EVENT_NOT_RECORDED_AT_CAPTURE")
        return out
    out.update(event_id=head["event_id"], event_version=head["version"])
    if head.get("identity_state") == "AMBIGUOUS":
        reasons.append("IDENTITY_AMBIGUOUS_AT_CAPTURE")
    if not head["prediction_eligible"] or head["status"] != ER.CURRENT:
        reasons.append("EVENT_NOT_ELIGIBLE_AT_CAPTURE")
    if head["window_start_utc"] and cap < _dt(head["window_start_utc"]):
        reasons.append("CAPTURED_BEFORE_WINDOW")
    if head["cutoff_utc"] and cap > _dt(head["cutoff_utc"]):
        reasons.append("CAPTURED_AFTER_CUTOFF")
    now_state = records.latest(head["event_id"])                                           # what became known afterwards
    if now_state and now_state["event_date"] != head["event_date"]:
        reasons.append("EVENT_DATE_CHANGED_AFTER_CAPTURE")
    if now_state and now_state["status"] == ER.REMOVED:
        reasons.append("EVENT_REMOVED_AFTER_CAPTURE")
    out["reasons"] = list(dict.fromkeys(reasons))
    out["valid"] = not out["reasons"]
    return out


def validate_all(preds: Iterable[dict], records: "ER.EventRecords") -> dict:
    rows = [validate_prediction(p, records) for p in preds]
    return {"rows": rows, "total": len(rows), "valid": sum(1 for r in rows if r["valid"]), "reasons": dict(Counter(c for r in rows for c in r["reasons"]))}


# ------------------------------------------------------------------ capture planning (what should be captured NOW, what was missed)
def capture_plan(records: "ER.EventRecords", now, preds: Iterable[dict] = ()) -> dict:
    """{capture_now, missed, waiting}. capture_now: eligible events whose window is open and that have no admissible prediction yet. missed: eligible events whose cutoff passed without one. Never executes a capture."""
    now = _dt(now)
    have = {(r["symbol"], r["earnings_date"]) for r in (validate_prediction(p, records) for p in preds) if r["valid"]}
    now_list, missed, waiting = [], [], []
    for eid in sorted(records.current()):
        v = records.as_of(eid, now)
        if not v or v["status"] != ER.CURRENT or not v["cutoff_utc"] or not v["prediction_eligible"] and not v.get("observed_after_cutoff"):
            continue
        key = (v["symbol"], v["event_date"])
        ws, co_ = _dt(v["window_start_utc"]), _dt(v["cutoff_utc"])
        row = {"event_id": eid, "symbol": v["symbol"], "event_date": v["event_date"], "session": v["session"], "window_start_utc": v["window_start_utc"], "cutoff_utc": v["cutoff_utc"], "event_version": v["version"]}
        if key in have:
            continue
        if now > co_:
            missed.append(dict(row, reason="CUTOFF_PASSED_WITHOUT_ADMISSIBLE_PREDICTION"))
        elif now >= ws:
            if v["prediction_eligible"]:
                now_list.append(dict(row, minutes_to_cutoff=int((co_ - now).total_seconds() // 60)))
        else:
            waiting.append(dict(row, window_opens_in_hours=round((ws - now).total_seconds() / 3600, 1)))
    now_list.sort(key=lambda r: r["minutes_to_cutoff"])
    return {"as_of": now.isoformat(), "capture_now": now_list, "missed": missed, "waiting": waiting}


# ------------------------------------------------------------------ completeness with reason codes
def event_completeness(head: dict, *, pair: Optional[dict] = None, news: Optional[dict] = None, bars: Optional[list] = None, flow_records: Optional[list] = None) -> dict:
    """What is present and what is missing around ONE event version, as reason codes. `pair`: snapshot_pairs verdict; `news`: news_audit.audit_event result; `bars`/`flow_records`: saved records or None."""
    miss, warn = [], []
    if not head["prediction_eligible"]:
        miss.append("CALENDAR_CONFLICT" if head.get("vendor_conflict") else "CALENDAR_NOT_CONFIRMED")
    if head["session"] not in ("BMO", "AMC"):
        miss.append("TIMING_UNKNOWN")
    if head["quarter_state"] == "AMBIGUOUS":
        miss.append("QUARTER_AMBIGUOUS")
    elif head["quarter_state"] != "KNOWN":
        miss.append("QUARTER_UNKNOWN")
    elif head.get("quarter_known_before_cutoff") is False:
        miss.append("QUARTER_LEARNED_AFTER_CUTOFF")
    if pair is None or pair.get("state") != "PAIRED":
        miss.append("SNAPSHOT_PAIR_MISSING")
    ns = (news or {}).get("news_state", "NOT_COLLECTED")
    if ns != "HAS_VALID_HEADLINES":
        miss.append({"NOT_COLLECTED": "NEWS_NOT_COLLECTED", "FAILED_RESPONSES_ONLY": "NEWS_FAILED", "EMPTY_PROVIDER_RESPONSE": "NEWS_EMPTY_RESPONSE", "NO_VALID_HEADLINES": "NEWS_NO_VALID_HEADLINE"}.get(ns, "NEWS_NOT_COLLECTED"))
    if not bars:
        miss.append("PRICE_BARS_NOT_SAVED")
    if not flow_records:
        miss.append("FLOW_NOT_COLLECTED")
    if not head["release_time_verified"]:
        warn.append("RELEASE_TIME_NOT_VERIFIED")
    unknown = [c for c in miss + warn if c not in REASONS]
    assert not unknown, unknown
    return {"event_id": head["event_id"], "event_version": head["version"], "symbol": head["symbol"], "event_date": head["event_date"], "missing": list(dict.fromkeys(miss)), "warnings": warn,
            "complete": not miss, "prediction_ready": bool(head["prediction_eligible"]) and head["session"] in ("BMO", "AMC"), "pairing_ready": bool(head["analyst_pairing_eligible"])}


def completeness_summary(rows: list) -> dict:
    return {"events": len(rows), "complete": sum(1 for r in rows if r["complete"]), "prediction_ready": sum(1 for r in rows if r["prediction_ready"]), "pairing_ready": sum(1 for r in rows if r["pairing_ready"]),
            "missing_by_code": dict(Counter(c for r in rows for c in r["missing"])), "warnings_by_code": dict(Counter(c for r in rows for c in r["warnings"]))}


# ------------------------------------------------------------------ Snapshot A / B scheduling bound to events
def schedule_snapshots(records: "ER.EventRecords", estimate_recs: Iterable[dict], now) -> dict:
    """Event-level A/B tasks. A task is due when the existing stage geometry says so (CP.snapshot_stage) and no OK estimate record already satisfies that stage. Each task carries event_id, version and the quarter
    (or QUARTER_NEEDED_BEFORE_PAIRING). Events that cannot be paired are listed with the reason instead of being silently skipped. Dedup is per event + stage, so a reschedule that keeps the event id never
    requests the same history twice."""
    now = _dt(now)
    ok = [(r["symbol"], _dt(r["retrieved_at"])) for r in estimate_recs if r.get("kind", "uw_estimates") == "uw_estimates" and r.get("status") == "OK"]
    tasks, skipped = [], []
    for eid in sorted(records.current()):
        v = records.as_of(eid, now)
        if not v or v["status"] != ER.CURRENT:
            continue
        if not v["prediction_eligible"] or not v["cutoff_utc"]:
            skipped.append({"event_id": eid, "reason": "EVENT_NOT_ELIGIBLE_FOR_SNAPSHOTS"})
            continue
        rel = _dt(v["release_utc"] or v["nominal_release_utc"])
        ws, co_ = _dt(v["window_start_utc"]), _dt(v["cutoff_utc"])
        stage = CP.snapshot_stage(rel, ws, co_, now)
        if stage is None:
            continue
        if stage == "B":
            satisfied = any(s == v["symbol"] and ws <= t <= now for s, t in ok)
        else:
            lo, hi = rel - timedelta(days=CP.SNAP_A_TO_DAYS + CP.SNAP_A_LOOKBACK_DAYS), rel - timedelta(days=CP.SNAP_A_TO_DAYS)
            satisfied = any(s == v["symbol"] and lo <= t <= hi for s, t in ok)
        if satisfied:
            continue
        tasks.append({"kind": "uw_estimates", "symbol": v["symbol"], "stage": stage, "event_id": eid, "event_version": v["version"], "event_date": v["event_date"], "cutoff_utc": v["cutoff_utc"],
                      "ending_fiscal_quarter": v["ending_fiscal_quarter"] if v["quarter_state"] == "KNOWN" else "QUARTER_NEEDED_BEFORE_PAIRING", "pairing_possible": bool(v["analyst_pairing_eligible"]),
                      "id": "uw_estimates-%s-%s-%s" % (v["symbol"], v["event_date"], stage)})
    tasks.sort(key=lambda t: (t["cutoff_utc"], t["symbol"], t["stage"]))
    return {"as_of": now.isoformat(), "tasks": tasks, "not_scheduled": skipped}
