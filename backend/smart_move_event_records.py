"""Earnings EVENT RECORDS: one append-only, versioned, point-in-time record per earnings event, with the vendor confirmation verdict, the market session, how the release time is known, and the fiscal quarter.

Built OFFLINE from saved calendar observations (cal_obs) and saved earnings payloads. It sends no request and changes no score, prediction or outcome.

IDENTITY (how events are told apart and how a reschedule is linked)
  * quarter KNOWN     -> event_id = "SYMBOL|<ending_fiscal_quarter>"  (two different quarters of one company are two events, always, even if their dates were close).
  * quarter UNKNOWN   -> event_id = "SYMBOL|UNKNOWN|<first date seen>"; a later observation of the same symbol joins this event only if the event has not occurred yet and the new date is within
                         REBOOK_WINDOW_DAYS (21) of the event's latest date. Anything further away is a different (next) quarter and gets its own event. The window is far below the ~90-day spacing of quarters.
  * A reschedule (new date or new session, same event_id) appends a new CURRENT version and re-labels the previous one RESCHEDULED; other content changes label it SUPERSEDED; nothing is overwritten.
  * An event that vanishes from a complete calendar window before it occurred gets a REMOVED version; one whose date has passed gets OCCURRED. Both stay in the file.

FISCAL QUARTER
  The provider's earnings payload has a field `ending_fiscal_quarter`: the period-end DATE of the fiscal quarter the report covers (the company's fiscal quarter, not a calendar quarter). It supplies no fiscal-year or quarter
  number, so none is recorded. A quarter is KNOWN only when a saved payload, retrieved at or before the as-of time, has exactly one row for the event's report date with one valid quarter value that ends 1 to 200 days
  before the report. Otherwise QUARTER_UNKNOWN (no payload / no row / empty field) or AMBIGUOUS (conflicting or implausible values). The quarter is NEVER inferred from calendar dates.

RELEASE TIME
  Session is BMO / AMC / UNKNOWN from the vendors. The 06:00 and 16:00 ET clock times are NOMINAL planning times (release_basis NOMINAL_BMO / NOMINAL_AMC, release_time_verified false); only an explicitly supplied verified
  timestamp makes release_time_verified true.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "docs" / "smart_move" / "shadow_impl"))
from smvshadow import calendar_confirm as cc  # noqa: E402
from smvshadow import cutoff as co  # noqa: E402

UTC = timezone.utc
SCHEMA = "event-record-1"
QUARTER_UNKNOWN = "QUARTER_UNKNOWN"
REBOOK_WINDOW_DAYS = 21
MAX_QUARTER_AGE_DAYS = 200
CURRENT, RESCHEDULED, SUPERSEDED, REMOVED, OCCURRED = "CURRENT", "RESCHEDULED", "SUPERSEDED", "REMOVED", "OCCURRED"
FIELD_MEANING = ("provider field `ending_fiscal_quarter`: period-end date of the company's fiscal quarter covered by the report (not a calendar quarter); "
                 "no fiscal-year or quarter number is provided")
SESSIONS = ("BMO", "AMC", "UNKNOWN")


def _dt(x) -> datetime:
    t = x if isinstance(x, datetime) else datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _iso(x) -> str:
    return _dt(x).astimezone(UTC).isoformat()


# ------------------------------------------------------------------------------------------------ fiscal quarter
def extract_quarter(rows, event_date, *, retrieved_at, as_of, payload_sha256: Optional[str] = None) -> dict:
    """Quarter state for ONE event from a saved earnings payload. Never infers from dates; only accepts, rejects or reports unknown."""
    out = {"state": QUARTER_UNKNOWN, "ending_fiscal_quarter": QUARTER_UNKNOWN, "reason": None, "source": None}
    if rows is None:
        out["reason"] = "NO_EARNINGS_PAYLOAD"
        return out
    if not rows:
        out["reason"] = "EMPTY_EARNINGS_PAYLOAD"                                 # a payload was saved but lists no report at all (not the same as no payload)
        return out
    if retrieved_at is None or _dt(retrieved_at) > _dt(as_of):
        out["reason"] = "PAYLOAD_RETRIEVED_AFTER_AS_OF"                          # point in time: a later retrieval must not describe an earlier moment
        return out
    if event_date is None:
        out["reason"] = "NO_EVENT_DATE"
        return out
    ed = str(event_date)[:10]
    match = [r for r in rows if str(r.get("report_date") or "")[:10] == ed]
    if not match:
        out["reason"] = "NO_ROW_FOR_REPORT_DATE"
        return out
    values = {str(r.get("ending_fiscal_quarter") or "").strip() for r in match} - {""}
    if not values:
        out["reason"] = "QUARTER_FIELD_EMPTY"
        return out
    if len(values) > 1:
        out.update(state="AMBIGUOUS", reason="CONFLICTING_QUARTER_VALUES")
        return out
    q = next(iter(values))
    try:
        qd = date.fromisoformat(q[:10])
    except ValueError:
        out.update(state="AMBIGUOUS", reason="INVALID_QUARTER_VALUE")
        return out
    gap = (date.fromisoformat(ed) - qd).days
    if gap < 1:
        out.update(state="AMBIGUOUS", reason="QUARTER_NOT_BEFORE_REPORT")
        return out
    if gap > MAX_QUARTER_AGE_DAYS:
        out.update(state="AMBIGUOUS", reason="QUARTER_TOO_OLD_FOR_THIS_REPORT")
        return out
    out.update(state="KNOWN", ending_fiscal_quarter=qd.isoformat(), reason=None,
               source={"kind": "uw_earnings_payload", "provider_field": "ending_fiscal_quarter", "field_meaning": FIELD_MEANING, "report_date_row": ed,
                       "row_source": match[0].get("source"), "retrieved_at": _iso(retrieved_at), "payload_sha256": payload_sha256, "days_before_report": gap})
    return out


# ------------------------------------------------------------------------------------------------ release basis
def release_info(plan_basis: Optional[str], session: str, plan: Optional[dict], verified_release_utc=None) -> dict:
    if verified_release_utc is not None:
        return {"release_basis": "VERIFIED_TIMESTAMP", "release_time_verified": True, "release_utc": _iso(verified_release_utc), "nominal_release_utc": None}
    if session not in ("BMO", "AMC") or not plan:
        return {"release_basis": "UNKNOWN", "release_time_verified": False, "release_utc": None, "nominal_release_utc": None}
    basis = plan_basis or ("NOMINAL_" + session)
    if basis.startswith("SEC_ACCEPTANCE"):
        return {"release_basis": "SEC_ACCEPTANCE_UPPER_BOUND", "release_time_verified": False, "release_utc": None, "nominal_release_utc": plan["assumed_release_utc"].isoformat()}
    return {"release_basis": basis, "release_time_verified": False, "release_utc": None, "nominal_release_utc": plan["assumed_release_utc"].isoformat()}


# ------------------------------------------------------------------------------------------------ one observation
def calobs_from_rows(rows: Iterable[dict]) -> dict:
    """cal_obs.jsonl rows -> {symbol: [CalObs]} (the same shape the live scorer uses)."""
    out: dict = {}
    for r in rows:
        o = cc.CalObs(r["source"], r["symbol"], date.fromisoformat(r["date"]), r.get("label"), r.get("clock"), r.get("vendor_flag"), r["raw_sha256"], _dt(r["observed_at"]))
        out.setdefault(r["symbol"], []).append((o, r))
    return out


def build_observation(symbol: str, pairs: list, as_of, *, earnings_rows=None, earnings_retrieved_at=None, earnings_sha256=None, verified_release_utc=None, earnings_others=None) -> dict:
    """The content of one event record as it can be known at `as_of` (no id/version yet). pairs: [(CalObs, raw_row)]. Vendor evidence, including conflicting dates, is preserved verbatim."""
    as_of = _dt(as_of)
    seen = [(o, r) for o, r in pairs if o.observed_at <= as_of]
    obs = [o for o, _ in seen]
    ev = cc.evaluate(symbol, obs, as_of, as_of.astimezone(co.ET).date())
    latest = {}
    for o, r in sorted(seen, key=lambda p: p[0].observed_at):
        latest[o.source] = (o, r)
    vendors = [{"source": o.source, "date": o.date.isoformat(), "label": o.label, "clock": o.clock, "vendor_flag": o.vendor_flag, "observed_at": _iso(o.observed_at), "raw_sha256": o.raw_sha256,
                "request_id": r.get("request_id")} for o, r in sorted(latest.values(), key=lambda p: p[0].source)]
    dates = sorted({v["date"] for v in vendors})
    conflict = "DATE_CONFLICT_OVER_1_DAY" in ev["reasons"] or "DATE_DISAGREEMENT_1_DAY" in ev["reasons"] or "CONTRADICTED" in str(ev["reasons"])
    event_date = ev["event_date"].isoformat() if ev["event_date"] else None
    session = ev["kind"] if ev["kind"] in ("BMO", "AMC") else "UNKNOWN"
    plan = co.plan_event(ev["event_date"], session) if event_date and session in ("BMO", "AMC") else None
    rel = release_info(plan["basis"] if plan else None, session, plan, verified_release_utc)
    q = extract_quarter(earnings_rows, event_date, retrieved_at=earnings_retrieved_at, as_of=as_of, payload_sha256=earnings_sha256) if earnings_rows is not None else \
        extract_quarter(None, event_date, retrieved_at=None, as_of=as_of)
    for o_rows, o_ts, o_sha in (earnings_others or []):                          # older saved payloads for the same symbol: a different KNOWN quarter for the same report date is a conflict, never a silent pick
        q2 = extract_quarter(o_rows, event_date, retrieved_at=o_ts, as_of=as_of, payload_sha256=o_sha)
        if q["state"] == "KNOWN" and (q2["state"] == "AMBIGUOUS" or (q2["state"] == "KNOWN" and q2["ending_fiscal_quarter"] != q["ending_fiscal_quarter"])):
            q = dict(q, state="AMBIGUOUS", ending_fiscal_quarter=QUARTER_UNKNOWN, reason="CONFLICTING_EARNINGS_PAYLOADS", source=None)
    cutoff = plan["cutoff_utc"] if plan else None
    q_before_cutoff = None if cutoff is None or q["state"] != "KNOWN" else _dt(q["source"]["retrieved_at"]) <= cutoff
    after_cutoff = cutoff is not None and as_of > cutoff                          # observed once the 24 h cutoff had already passed: it cannot be established retroactively
    reasons = list(dict.fromkeys(ev["no_trade_reasons"] or ev["reasons"] if not ev["eligible_for_shadow"] else []))
    if session == "UNKNOWN" and "UNKNOWN_TIMING" not in reasons:
        reasons.append("UNKNOWN_TIMING")
    if after_cutoff and "OBSERVED_AFTER_CUTOFF" not in reasons:
        reasons.append("OBSERVED_AFTER_CUTOFF")
    eligible = bool(ev["eligible_for_shadow"]) and session in ("BMO", "AMC") and not conflict and event_date is not None and not after_cutoff
    pairing = eligible and q["state"] == "KNOWN" and q_before_cutoff is True
    blockers = ([] if eligible else ["EVENT_NOT_ELIGIBLE"]) + ([] if q["state"] == "KNOWN" else [q["state"] + ":" + str(q["reason"])]) + (["QUARTER_LEARNED_AFTER_CUTOFF"] if q["state"] == "KNOWN" and q_before_cutoff is not True else [])
    return {"schema": SCHEMA, "symbol": symbol, "event_date": event_date, "session": session, "vendor_status": ev["status"], "vendor_reasons": list(ev["reasons"]), "vendor_conflict": bool(conflict),
            "vendor_dates_seen": dates, "vendors": vendors, "stale_vendor_rows": ev.get("stale_vendor_rows") or [], "observed_at": _iso(as_of),
            "calendar_observed_at": _iso(ev["calendar_observed_at"]) if ev["calendar_observed_at"] else None, **rel,
            "ending_fiscal_quarter": q["ending_fiscal_quarter"], "quarter_state": q["state"], "quarter_reason": q["reason"], "quarter_source": q["source"], "quarter_known_before_cutoff": q_before_cutoff, "observed_after_cutoff": after_cutoff,
            "window_start_utc": plan["window_start_utc"].isoformat() if plan else None, "cutoff_utc": plan["cutoff_utc"].isoformat() if plan else None,
            "prediction_eligible": eligible, "ineligible_reasons": [] if eligible else (reasons or ["NOT_CONFIRMED"]), "analyst_pairing_eligible": pairing,
            "pairing_blockers": [] if pairing else blockers, "identity_state": "UNIQUE", "identity_basis": None, "identity_candidates": [], "prior_version_quarter": None}


def _signature(rec: dict) -> tuple:
    """What counts as a CHANGE of an event (timestamps, hashes and request ids do not)."""
    return (rec["event_date"], rec["session"], rec["vendor_status"], tuple(rec["vendor_reasons"]), rec["vendor_conflict"], tuple(rec["vendor_dates_seen"]), rec["ending_fiscal_quarter"], rec["quarter_state"],
            rec["quarter_reason"], rec["prediction_eligible"], tuple(rec["ineligible_reasons"]), rec["release_basis"], rec["release_time_verified"], rec["quarter_known_before_cutoff"], rec["identity_state"])


def record_hash(rec: dict) -> str:
    body = {k: v for k, v in rec.items() if k != "record_hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode("utf-8")).hexdigest()


# ------------------------------------------------------------------------------------------------ the store
class EventRecords:
    """Append-only versions. path=None keeps it in memory (derived views never write)."""

    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.versions: list = []
        if self.path and self.path.exists():
            self.versions = [json.loads(ln) for ln in self.path.read_text(encoding="utf-8").splitlines() if ln.strip()]

    # -- persistence
    def _append(self, rec: dict) -> dict:
        rec = dict(rec)
        rec["record_hash"] = record_hash(rec)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, sort_keys=True, default=str) + chr(10))
        self.versions.append(rec)
        return rec

    # -- views
    def history(self, event_id: str) -> list:
        return [v for v in self.versions if v["event_id"] == event_id]

    def latest(self, event_id: str) -> Optional[dict]:
        h = self.history(event_id)
        return h[-1] if h else None

    def event_ids(self, symbol: Optional[str] = None) -> list:
        ids = list(dict.fromkeys(v["event_id"] for v in self.versions))
        return [i for i in ids if symbol is None or i.split("|")[0] == symbol]

    def current(self) -> dict:
        out = {}
        for i in self.event_ids():
            v = self.latest(i)
            if v["status"] == CURRENT:
                out[i] = v
        return out

    def as_of(self, event_id: str, t) -> Optional[dict]:
        """The record in force at time t: the newest VERSION observed at or before t, with the status it had AT t (a later relabel such as RESCHEDULED / REMOVED is invisible before its own time). A quarter whose
        source payload was retrieved AFTER t is shown as QUARTER_UNKNOWN (no look-ahead)."""
        t = _dt(t)
        rows = [v for v in self.history(event_id) if _dt(v["observed_at"]) <= t]
        if not rows:
            return None
        ver = max(v["version"] for v in rows)
        same = [v for v in rows if v["version"] == ver]
        out, status = dict(same[0]), CURRENT
        for v in same[1:]:
            if v.get("superseded_at") and _dt(v["superseded_at"]) <= t:
                status = v["status"]
        out["status"] = status
        for k in ("superseded_at", "superseded_by_version"):
            out.pop(k, None)
        src = out.get("quarter_source")
        if out["quarter_state"] == "KNOWN" and src and _dt(src["retrieved_at"]) > t:
            out.update(ending_fiscal_quarter=QUARTER_UNKNOWN, quarter_state=QUARTER_UNKNOWN, quarter_reason="PAYLOAD_RETRIEVED_AFTER_AS_OF", quarter_source=None, analyst_pairing_eligible=False)
        return out

    def eligible(self, t, *, window_open: bool = False, pairing: bool = False) -> list:
        """Events that were prediction-eligible (and optionally pairing-eligible / inside their 48-to-24 h window) at time t."""
        t = _dt(t)
        out = []
        for i in self.event_ids():
            v = self.as_of(i, t)
            if not v or v["status"] != CURRENT or not v["prediction_eligible"]:
                continue
            if pairing and not v["analyst_pairing_eligible"]:
                continue
            if window_open and not (v["window_start_utc"] and _dt(v["window_start_utc"]) <= t <= _dt(v["cutoff_utc"])):
                continue
            out.append(v)
        return sorted(out, key=lambda v: (v["cutoff_utc"] or "", v["symbol"]))

    # -- identity
    def _open_events(self, symbol: str, observed: datetime) -> list:
        out = []
        for i in self.event_ids(symbol):
            v = self.latest(i)
            if v["status"] in (CURRENT,) and (v["event_date"] is None or date.fromisoformat(v["event_date"]) >= observed.astimezone(UTC).date() - timedelta(days=1)):
                out.append(v)
        return out

    def _identify(self, rec: dict) -> tuple:
        """-> (event_id, identity_state, identity_basis, candidates). A known fiscal quarter IS the identity. Without one, date proximity (REBOOK_WINDOW_DAYS, a heuristic) may link an observation to an open event
        of the same symbol only when exactly ONE such event qualifies; with several the identity is AMBIGUOUS and a separate, ineligible event is opened instead of forcing a merge."""
        sym, observed = rec["symbol"], _dt(rec["observed_at"])
        q, known = rec["ending_fiscal_quarter"], rec["quarter_state"] == "KNOWN"
        if known and "%s|%s" % (sym, q) in self.event_ids(sym):
            return "%s|%s" % (sym, q), "UNIQUE", "EXACT_QUARTER", []                    # same company, same fiscal quarter: the same event, whatever its date
        if known:
            for i in self.event_ids(sym):                                               # an event that LEARNED this quarter earlier keeps its original id (it was opened as SYMBOL|UNKNOWN|date)
                lv = self.latest(i)
                if lv["quarter_state"] == "KNOWN" and lv["ending_fiscal_quarter"] == q:
                    return i, "UNIQUE", "EXACT_QUARTER", []
        anchor = rec["event_date"] or (rec["vendor_dates_seen"][0] if rec["vendor_dates_seen"] else observed.date().isoformat())
        cands = []
        for v in self._open_events(sym, observed):
            if v.get("identity_state") == "AMBIGUOUS":
                continue
            if known and v["quarter_state"] == "KNOWN":
                continue                                                                # two known, different quarters are never the same event
            ref = v["event_date"]
            if rec["event_date"] is None or ref is None or abs((date.fromisoformat(rec["event_date"]) - date.fromisoformat(ref)).days) <= REBOOK_WINDOW_DAYS:
                cands.append(v["event_id"])
        new_id = "%s|%s" % (sym, q) if known else "%s|UNKNOWN|%s" % (sym, anchor)
        if len(cands) == 1:
            return cands[0], "UNIQUE", "DATE_PROXIMITY_HEURISTIC", cands
        if len(cands) > 1:
            return new_id, ("UNIQUE" if known else "AMBIGUOUS"), ("EXACT_QUARTER_NEW" if known else None), cands
        return new_id, "UNIQUE", "NEW", []

    # -- writes
    def observe(self, rec: dict) -> dict:
        """Add one observation. Returns {"action": NEW | UNCHANGED | RESCHEDULED | UPDATED | REVIVED, "event_id", "version"}."""
        eid, istate, ibasis, cands = self._identify(rec)
        rec = dict(rec, identity_state=istate, identity_basis=ibasis, identity_candidates=cands)
        if istate == "AMBIGUOUS":                                                       # unresolved identity is never eligible
            rec.update(prediction_eligible=False, analyst_pairing_eligible=False, ineligible_reasons=list(dict.fromkeys(list(rec["ineligible_reasons"]) + ["IDENTITY_AMBIGUOUS"])),
                       pairing_blockers=list(dict.fromkeys(list(rec["pairing_blockers"]) + ["EVENT_NOT_ELIGIBLE", "IDENTITY_AMBIGUOUS"])))
        prev = self.latest(eid)
        if prev is not None and rec["quarter_state"] != "KNOWN":                        # a quarter that was known for an earlier version is never silently dropped from the lineage
            rec["prior_version_quarter"] = prev["ending_fiscal_quarter"] if prev["quarter_state"] == "KNOWN" else prev.get("prior_version_quarter")
        if prev is not None and prev["status"] == CURRENT and _signature(prev) == _signature(rec):
            return {"action": "UNCHANGED", "event_id": eid, "version": prev["version"]}              # duplicate observation: no new version
        new = dict(rec, event_id=eid, status=CURRENT, version=(prev["version"] + 1) if prev else 1, change_reasons=[],
                   original_date=(prev or {}).get("original_date") or rec["event_date"], supersedes_version=prev["version"] if prev else None)
        action = "NEW"
        if prev is not None:
            reasons = [k for k, a, b in (("DATE", prev["event_date"], rec["event_date"]), ("SESSION", prev["session"], rec["session"])) if a != b]
            new["change_reasons"] = reasons or ["CONTENT"]
            if prev["quarter_state"] != "KNOWN" and rec["quarter_state"] == "KNOWN":
                new["change_reasons"].append("QUARTER_LEARNED")
            action = "RESCHEDULED" if reasons else "UPDATED"
            if prev["status"] == CURRENT:
                self._append(dict(prev, status=RESCHEDULED if reasons else SUPERSEDED, superseded_at=rec["observed_at"], superseded_by_version=new["version"]))
            else:
                action = "REVIVED"
        self._append(new)
        return {"action": action, "event_id": eid, "version": new["version"]}

    def observe_snapshot(self, recs: Iterable[dict], *, observed, window: Optional[tuple] = None, complete: bool = False) -> dict:
        """Observe every record of one calendar snapshot (duplicates of a symbol+date inside the snapshot are reported, first wins). complete=True with `window` (first_date, last_date) means the snapshot lists every
        event dated inside that window, so an open event in the window that is missing is REMOVED; events outside the window are never touched."""
        res = {"NEW": [], "UNCHANGED": [], "RESCHEDULED": [], "UPDATED": [], "REVIVED": [], "REMOVED": [], "OCCURRED": [], "duplicates": []}
        seen_keys, present = set(), set()
        for rec in recs:
            key = (rec["symbol"], rec["event_date"])
            if key in seen_keys:
                res["duplicates"].append(rec["symbol"])
                continue
            seen_keys.add(key)
            r = self.observe(rec)
            res[r["action"]].append(r["event_id"])
            present.add(r["event_id"])
        obs = _dt(observed)
        for i in self.event_ids():
            v = self.latest(i)
            if v["status"] != CURRENT or v["event_date"] is None:
                continue
            d = date.fromisoformat(v["event_date"])
            if d < obs.astimezone(UTC).date() - timedelta(days=1):
                self._append(dict(v, status=OCCURRED, superseded_at=_iso(obs)))                     # the date has passed: closed, kept
                res["OCCURRED"].append(i)
            elif complete and window and window[0] <= d <= window[1] and i not in present:
                self._append(dict(v, status=REMOVED, superseded_at=_iso(obs)))                      # vanished from a complete calendar window before it happened: kept, flagged
                res["REMOVED"].append(i)
        return res


# ------------------------------------------------------------------------------------------------ building from saved evidence
def earnings_payloads(evidence_rows: Iterable[dict], load: Callable[[str], Optional[object]], as_of) -> dict:
    """{symbol: (rows, retrieved_at, sha256, others)} from saved `earnings` evidence. The newest payload retrieved at or before as_of is chosen and `others` lists the older ones (conflict check). A symbol whose
    only payloads were retrieved AFTER as_of gets the earliest of them so the record says PAYLOAD_RETRIEVED_AFTER_AS_OF instead of pretending nothing was saved. load(sha) returns the decoded JSON body or None."""
    by_sym: dict = {}
    for r in evidence_rows:
        if r.get("kind") == "earnings" and r.get("status", "OK") == "OK":
            by_sym.setdefault(r["symbol"], []).append(r)
    out = {}
    for s, recs in by_sym.items():
        usable = []
        for r in sorted(recs, key=lambda r: r["retrieved_at"]):
            body = load(r["raw_sha256"])
            data = body.get("data") if isinstance(body, dict) else None
            if isinstance(data, list):
                usable.append((data, r["retrieved_at"], r["raw_sha256"]))
        before = [u for u in usable if _dt(u[1]) <= _dt(as_of)]
        if before:
            out[s] = before[-1] + (before[:-1],)
        elif usable:
            out[s] = usable[0] + ([],)
    return out


def records_from_saved(cal_rows: Iterable[dict], as_of, payloads: Optional[dict] = None) -> list:
    """One observation per symbol in the saved calendar, evaluated at as_of, with the quarter from saved payloads only."""
    payloads = payloads or {}
    out = []
    for sym, pairs in sorted(calobs_from_rows(cal_rows).items()):
        p = payloads.get(sym)
        er, ts, sha, others = (p[0], p[1], p[2], p[3] if len(p) > 3 else []) if p else (None, None, None, [])
        out.append(build_observation(sym, pairs, as_of, earnings_rows=er, earnings_retrieved_at=ts, earnings_sha256=sha, earnings_others=others))
    return out


def sync_registry(registry, records: EventRecords, observed) -> dict:
    """Feed the existing symbol-level EventRegistry (used by the board) from the event records: per symbol the nearest upcoming CURRENT event, `confirmed` = prediction-eligible, fiscal_period = the quarter
    (or None). The registry stays a derived symbol-level view; event_id is the authoritative identity."""
    best = {}
    for v in records.current().values():
        if v["event_date"] is None:
            continue
        if v["symbol"] not in best or v["event_date"] < best[v["symbol"]]["event_date"]:
            best[v["symbol"]] = v
    rows = [{"symbol": s, "earnings_date": v["event_date"], "kind": v["session"] if v["session"] in ("BMO", "AMC") else None, "confirmed": v["prediction_eligible"],
             "fiscal_period": v["ending_fiscal_quarter"] if v["quarter_state"] == "KNOWN" else None} for s, v in sorted(best.items())]
    return registry.apply_snapshot(rows, observed, complete=False)


def summarize(records: EventRecords) -> dict:
    cur = list(records.current().values())
    from collections import Counter
    latest = [records.latest(i) for i in records.event_ids()]                           # one row per event (NOT per stored line): the unit every count below reconciles to
    return {"events_total": len(latest), "events_by_status": dict(Counter(v["status"] for v in latest)), "quarter_reason_current": dict(Counter(str(v["quarter_reason"]) for v in cur)),
            "identity_ambiguous": sum(1 for v in cur if v.get("identity_state") == "AMBIGUOUS"), "events_current": len(cur), "by_vendor_status": dict(Counter(v["vendor_status"] for v in cur)), "by_session": dict(Counter(v["session"] for v in cur)),
            "prediction_eligible": sum(1 for v in cur if v["prediction_eligible"]), "pairing_eligible": sum(1 for v in cur if v["analyst_pairing_eligible"]),
            "quarter_state": dict(Counter(v["quarter_state"] for v in cur)), "vendor_conflicts": sum(1 for v in cur if v["vendor_conflict"]),
            "release_time_verified": sum(1 for v in cur if v["release_time_verified"]), "versions": len(records.versions)}
