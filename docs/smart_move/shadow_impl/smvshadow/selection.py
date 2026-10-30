"""Deterministic event selection and earliest-deadline scheduling (component 2).

Every event has a capture window (assumed release - 48 h .. - 24 h). Capacity is applied per Eastern calendar day: on each day the events whose window touches that
day are ranked by (deadline, date/timing status, SHA-256(seed|symbol|date)) and the first `core_cap` are scheduled; spare capacity on earlier days is therefore used
before an event's deadline day. Events that reach their deadline unscheduled are written to the ledger as MISSED_CAPACITY (never dropped silently). The
choice uses no price and no outcome information, and the same inputs always give the same plan (the plan hash proves it).
"""
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import calendar_validation as cv
from .cutoff import ET, UTC

DATE_RANK = {cv.CONFIRMED: 0, cv.ESTIMATED: 1}
TIMING_RANK = {cv.T_CONFIRMED: 0, cv.T_SINGLE: 1, cv.T_CONFLICT: 2, cv.T_UNKNOWN: 2}


@dataclass
class Candidate:
    symbol: str
    event_date: object
    version: int
    kind: str
    date_status: str
    timing_status: str
    window_start: datetime
    deadline: datetime
    liquid: bool | None = None          # None: not known yet (candles not captured), allowed
    evidence_only: bool = False


def tie_break(seed: str, symbol: str, event_date) -> str:
    return hashlib.sha256(("%s|%s|%s" % (seed, symbol, event_date)).encode("ascii")).hexdigest()


def priority_key(c: Candidate, seed: str):
    return (c.deadline, DATE_RANK.get(c.date_status, 3) * 10 + TIMING_RANK.get(c.timing_status, 2), tie_break(seed, c.symbol, c.event_date))


def eligibility(c: Candidate):
    """-> None if eligible, else the ledger reason."""
    if c.date_status == cv.UNKNOWN or c.event_date is None:
        return "SKIPPED_UNCERTAIN_OR_UNKNOWN_DATE"
    if c.date_status == cv.STALE:
        return "SKIPPED_STALE_DATE_REFRESH_FIRST"
    if c.liquid is False:
        return "SKIPPED_ILLIQUID"
    return None


def _day_bounds(day):
    start = datetime.combine(day, datetime.min.time(), ET).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), ET).astimezone(UTC)
    return start, end


def schedule(cands, *, seed: str, core_cap: int = 220, full_cap: int = 30):
    ledger, eligible = [], []
    for c in cands:
        r = eligibility(c)
        (ledger.append({"type": r, "symbol": c.symbol, "event_date": str(c.event_date), "version": c.version}) if r else eligible.append(c))
    if not eligible:
        return {"assignments": [], "ledger": ledger, "demand_by_day": {}, "plan_hash": _hash([], ledger)}
    remaining = sorted(eligible, key=lambda c: priority_key(c, seed))
    first = min(c.window_start for c in eligible).astimezone(ET).date()
    last = max(c.deadline for c in eligible).astimezone(ET).date()
    assignments, demand = [], {}
    day = first
    while day <= last:
        ds, de = _day_bounds(day)
        avail = [c for c in remaining if c.window_start < de and c.deadline > ds]
        demand[str(day)] = len(avail)
        take = avail[:core_cap]
        taken = {id(c) for c in take}
        full = _pick_full(take, seed, full_cap)
        for c in take:
            planned = min(max(c.window_start, ds), c.deadline - timedelta(minutes=1))
            assignments.append({"symbol": c.symbol, "event_date": str(c.event_date), "version": c.version, "kind": c.kind, "capture_day": str(day),
                                "planned_capture_utc": planned.isoformat(), "tier": "FULL" if id(c) in full else "CORE", "evidence_only": c.evidence_only,
                                "deadline_utc": c.deadline.isoformat()})
        remaining = [c for c in remaining if id(c) not in taken]
        expired = [c for c in remaining if c.deadline <= de]
        for rank, c in enumerate(expired):
            ledger.append({"type": "MISSED_CAPACITY", "symbol": c.symbol, "event_date": str(c.event_date), "version": c.version, "capture_day": str(day),
                           "day_demand": len(avail), "core_cap": core_cap, "deadline_utc": c.deadline.isoformat(), "priority_hash": tie_break(seed, c.symbol, c.event_date)})
        remaining = [c for c in remaining if c.deadline > de]
        day += timedelta(days=1)
    return {"assignments": assignments, "ledger": ledger, "demand_by_day": demand, "plan_hash": _hash(assignments, ledger)}


def _pick_full(take, seed, full_cap):
    """30 full-capture slots among the events scheduled that day: CONFIRMED date and timing, liquid, stratified half BMO / half AMC by hash order."""
    pool = [c for c in take if c.date_status == cv.CONFIRMED and c.timing_status == cv.T_CONFIRMED and c.liquid is not False and c.kind in ("BMO", "AMC") and not c.evidence_only]
    by = {k: sorted([c for c in pool if c.kind == k], key=lambda c: tie_break(seed, c.symbol, c.event_date)) for k in ("BMO", "AMC")}
    half = full_cap // 2
    chosen = by["BMO"][:half] + by["AMC"][:half]
    rest = by["BMO"][half:] + by["AMC"][half:]
    rest.sort(key=lambda c: tie_break(seed, c.symbol, c.event_date))
    chosen += rest[:full_cap - len(chosen)]
    return {id(c) for c in chosen[:full_cap]}


def _hash(assignments, ledger):
    body = json.dumps({"a": assignments, "l": ledger}, sort_keys=True, default=str).encode("ascii")
    return hashlib.sha256(body).hexdigest()
