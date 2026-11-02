"""Analyst Snapshot A / Snapshot B pairing bound to one earnings event (Phase 13, Priority 2). OFFLINE: reads saved estimate payloads and the event records; sends nothing, scores nothing.

A pair is built for ONE event version and ONE fiscal quarter (the event's `ending_fiscal_quarter`, matched exactly against the provider's `fiscal quarter` row; never by nearest date):
  Snapshot B  an OK `uw_estimates` payload retrieved inside the event's prediction window (window_start .. cutoff).
  Snapshot A  an earlier OK payload retrieved between release-(9+58) d and release-9 d, 7..60 days before B.
Only payloads retrieved at or before the cutoff AND at or before `as_of` count (no look-ahead). The event must be analyst_pairing_eligible at B's retrieval time (point-in-time view), its quarter known before the cutoff,
and its identity not ambiguous. Provider values are copied as saved; the revision percentages are plain arithmetic on them. Nothing here changes a prediction, a score or an outcome."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

import smart_move_event_records as ER
import smart_move_v2_snapshots as SN

UTC = timezone.utc
SCHEMA = "smart_move.snapshot_pair.v1"
MIN_GAP_DAYS, MAX_GAP_DAYS = 7, 60
A_TO_DAYS, A_LOOKBACK_DAYS = 9, 58                      # the same geometry the collection plan uses (smart_move_collection_plan.SNAP_A_*)
PAIRED, UNPAIRED, NOT_PAIRABLE = "PAIRED", "UNPAIRED", "NOT_PAIRABLE"


def _dt(x) -> datetime:
    d = x if isinstance(x, datetime) else datetime.fromisoformat(str(x))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _pct(new, old):
    return round((new - old) / abs(old) * 100, 3) if new is not None and old else None


def pair_hash(rec: dict) -> str:
    return hashlib.sha256(json.dumps({k: v for k, v in rec.items() if k != "pair_hash"}, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _snapshots(estimate_recs: Iterable[dict], load_rows: Callable[[str], Optional[list]], symbol: str, quarter: str, upto: datetime) -> tuple:
    """-> (usable, excluded). usable: [{retrieved_at, raw_sha256, row, eps, revenue}] sorted by time; excluded: [{raw_sha256, retrieved_at, reason}] (kept so the audit can show why)."""
    usable, excluded, seen = [], [], set()
    for r in sorted((r for r in estimate_recs if r.get("kind") == "uw_estimates" and r.get("symbol") == symbol), key=lambda r: r["retrieved_at"]):
        if r.get("status") != "OK":
            excluded.append({"raw_sha256": r.get("raw_sha256"), "retrieved_at": r["retrieved_at"], "reason": "STATUS_" + str(r.get("status"))})
            continue
        if _dt(r["retrieved_at"]) > upto:
            excluded.append({"raw_sha256": r["raw_sha256"], "retrieved_at": r["retrieved_at"], "reason": "RETRIEVED_AFTER_CUTOFF_OR_AS_OF"})
            continue
        if r["raw_sha256"] in seen:
            excluded.append({"raw_sha256": r["raw_sha256"], "retrieved_at": r["retrieved_at"], "reason": "DUPLICATE_PAYLOAD"})
            continue
        seen.add(r["raw_sha256"])
        row = SN.quarter_row(load_rows(r["raw_sha256"]), quarter)
        if row is None:
            excluded.append({"raw_sha256": r["raw_sha256"], "retrieved_at": r["retrieved_at"], "reason": "NO_ROW_FOR_EVENT_QUARTER"})
            continue
        usable.append({"retrieved_at": r["retrieved_at"], "raw_sha256": r["raw_sha256"], "row": row, "eps": SN._num(row.get("eps_estimate_average")), "revenue": SN._num(row.get("revenue_estimate_average"))})
    return usable, excluded


def pair_event(records: "ER.EventRecords", event_id: str, estimate_recs: Iterable[dict], load_rows: Callable[[str], Optional[list]], as_of) -> dict:
    """One pairing verdict for the event as known at as_of. Always returns a dict with `state` PAIRED / UNPAIRED / NOT_PAIRABLE and the reasons."""
    as_of = _dt(as_of)
    head = records.as_of(event_id, as_of)
    out = {"schema": SCHEMA, "event_id": event_id, "as_of": as_of.isoformat(), "state": NOT_PAIRABLE, "reasons": [], "event_version": None, "symbol": event_id.split("|")[0], "ending_fiscal_quarter": None}
    if head is None:
        out["reasons"] = ["EVENT_UNKNOWN_AT_AS_OF"]
        return out
    out.update(event_version=head["version"], symbol=head["symbol"], ending_fiscal_quarter=head["ending_fiscal_quarter"], event_date=head["event_date"], session=head["session"])
    why = []
    if head["status"] != ER.CURRENT:
        why.append("EVENT_" + head["status"])
    if head.get("identity_state") == "AMBIGUOUS":
        why.append("IDENTITY_AMBIGUOUS")
    if not head["prediction_eligible"]:
        why.append("EVENT_NOT_PREDICTION_ELIGIBLE")
    if head["quarter_state"] != "KNOWN":
        why.append("QUARTER_NOT_KNOWN:" + head["quarter_state"])
    elif head.get("quarter_known_before_cutoff") is not True:
        why.append("QUARTER_LEARNED_AFTER_CUTOFF")
    if not head["analyst_pairing_eligible"] and not why:
        why.append("EVENT_NOT_PAIRING_ELIGIBLE")
    if why:
        out["reasons"] = why
        return out
    cutoff, ws = _dt(head["cutoff_utc"]), _dt(head["window_start_utc"])
    release = _dt(head["release_utc"] or head["nominal_release_utc"])
    out.update(cutoff_utc=cutoff.isoformat(), window_start_utc=ws.isoformat(), release_utc=release.isoformat(), release_time_verified=head["release_time_verified"], release_basis=head["release_basis"])
    q = head["ending_fiscal_quarter"]
    usable, excluded = _snapshots(estimate_recs, load_rows, head["symbol"], q, min(as_of, cutoff))
    out["excluded_payloads"] = excluded
    bs = [s for s in usable if ws <= _dt(s["retrieved_at"]) <= cutoff]
    if not bs:
        out.update(state=UNPAIRED, reasons=["NO_SNAPSHOT_B_IN_WINDOW"])
        return out
    b = bs[-1]
    t_b = _dt(b["retrieved_at"])
    at_b = records.as_of(event_id, t_b)                                                      # the event as it was KNOWN when B was taken, never as it is now
    if at_b is None or at_b["status"] != ER.CURRENT or not at_b["analyst_pairing_eligible"] or at_b["ending_fiscal_quarter"] != q:
        out.update(state=UNPAIRED, reasons=["EVENT_NOT_PAIRING_ELIGIBLE_WHEN_B_WAS_TAKEN"])
        return out
    a_lo, a_hi = release - timedelta(days=A_TO_DAYS + A_LOOKBACK_DAYS), release - timedelta(days=A_TO_DAYS)
    in_range = [s for s in usable if s is not b and a_lo <= _dt(s["retrieved_at"]) <= a_hi]
    if not in_range:
        out.update(state=UNPAIRED, reasons=["NO_SNAPSHOT_A_IN_RANGE"], snapshot_b=_view(b))
        return out
    ok = [s for s in in_range if MIN_GAP_DAYS <= (t_b - _dt(s["retrieved_at"])).days <= MAX_GAP_DAYS]
    if not ok:
        out.update(state=UNPAIRED, reasons=["GAP_A_TO_B_NOT_7_TO_60_DAYS"], snapshot_b=_view(b))
        return out
    a = ok[-1]                                                                                # the most recent A that still leaves a measurable gap
    at_a = records.as_of(event_id, _dt(a["retrieved_at"]))
    out.update(state=PAIRED, reasons=[], snapshot_a=_view(a), snapshot_b=_view(b), gap_days=(t_b - _dt(a["retrieved_at"])).days, event_version_at_a=at_a["version"] if at_a else None,
               event_version_at_b=at_b["version"], eps_revision_pct=_pct(b["eps"], a["eps"]), revenue_revision_pct=_pct(b["revenue"], a["revenue"]),
               provider_fields={"quarter_row": "horizon == 'fiscal quarter' and date == ending_fiscal_quarter", "eps": "eps_estimate_average", "revenue": "revenue_estimate_average"})
    return out


def _view(s: dict) -> dict:
    return {"retrieved_at": s["retrieved_at"], "raw_sha256": s["raw_sha256"], "period": str(s["row"].get("date"))[:10], "eps": s["eps"], "revenue": s["revenue"]}


class PairStore:
    """Append-only JSONL of verdicts (only PAIRED / UNPAIRED / NOT_PAIRABLE changes are appended; an identical verdict is not repeated)."""

    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.rows = [json.loads(ln) for ln in self.path.read_text(encoding="utf-8").splitlines() if ln.strip()] if self.path and self.path.exists() else []

    def add(self, v: dict) -> bool:
        key = lambda r: (r["event_id"], r["event_version"], r["state"], tuple(r["reasons"]), (r.get("snapshot_a") or {}).get("raw_sha256"), (r.get("snapshot_b") or {}).get("raw_sha256"))  # noqa: E731
        if any(key(r) == key(v) for r in self.rows):
            return False
        v = dict(v)
        v["pair_hash"] = pair_hash(v)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(v, sort_keys=True, default=str) + chr(10))
        self.rows.append(v)
        return True


def pair_all(records: "ER.EventRecords", estimate_recs: list, load_rows: Callable[[str], Optional[list]], as_of, store: Optional[PairStore] = None) -> dict:
    """Verdict for every CURRENT event at as_of. Returns {"verdicts": [...], "counts": {state: n}, "reasons": {reason: n}}."""
    from collections import Counter
    verdicts = [pair_event(records, i, estimate_recs, load_rows, as_of) for i in sorted(records.current())]
    if store is not None:
        for v in verdicts:
            store.add(v)
    return {"verdicts": verdicts, "counts": dict(Counter(v["state"] for v in verdicts)), "reasons": dict(Counter(r for v in verdicts for r in v["reasons"]))}
