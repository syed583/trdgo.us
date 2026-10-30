"""Event-level earnings calendar validation and versioning (component 1).

Observations come from independent sources (UW calendar / UW per-ticker row / Finviz export). Each event version carries a DATE status
(CONFIRMED | ESTIMATED | STALE | UNKNOWN), a TIMING status (CONFIRMED_TIMING | SINGLE_SOURCE | CONFLICT | UNKNOWN) and flags. A moved date opens a new version (the old one is
closed, never edited). Two dates of one symbol within 45 days that are observed at the same time are an UNCERTAIN pair: never merged, never silently counted twice.
Nothing here makes a request; callers pass observations.
"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

CONFIRMED, ESTIMATED, STALE, UNKNOWN = "CONFIRMED", "ESTIMATED", "STALE", "UNKNOWN"
T_CONFIRMED, T_SINGLE, T_CONFLICT, T_UNKNOWN = "CONFIRMED_TIMING", "SINGLE_SOURCE", "CONFLICT", "UNKNOWN"
CONFIRM_MAX_AGE = timedelta(hours=48)
CLOSE_DAYS = 45
_BMO = re.compile(r"^(pre|before|bmo)", re.I)
_AMC = re.compile(r"^(post|after|amc)", re.I)
_CLOCK = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*(AM|PM)?\s*$", re.I)


@dataclass
class Observation:
    source: str                      # UW_CALENDAR | UW_ROW | FINVIZ
    symbol: str
    date: date
    session: str | None              # provider label ('premarket', 'After Close', '4:30 PM', None)
    provider_status: str | None      # 'company' | 'estimation' | None
    observed_at: datetime


def freshness_limit(days_to_event: int) -> timedelta:
    """Maximum age of the newest observation for a date to count as fresh: 24 h from D-10 to D-4, 6 h inside D-4, and at most 24 h beyond D-10."""
    return timedelta(hours=6) if days_to_event <= 4 else timedelta(hours=24)


def normalise_session(label) -> str:
    """BMO | AMC | UNKNOWN from a label or a clock string. A clock time inside regular trading hours is UNKNOWN (not guessed)."""
    if label is None or str(label).strip() == "":
        return "UNKNOWN"
    s = str(label).strip()
    if _BMO.match(s):
        return "BMO"
    if _AMC.match(s):
        return "AMC"
    m = _CLOCK.match(s)
    if m:
        h, mi, ap = int(m.group(1)), int(m.group(2)), (m.group(3) or "").upper()
        if ap == "PM" and h < 12:
            h += 12
        if ap == "AM" and h == 12:
            h = 0
        t = time(h, mi)
        if t < time(9, 30):
            return "BMO"
        if t >= time(16, 0):
            return "AMC"
    return "UNKNOWN"


def _latest_by_source(obs):
    out = {}
    for o in obs:
        if o.source not in out or o.observed_at > out[o.source].observed_at:
            out[o.source] = o
    return out


def classify_date(obs: list, now: datetime, today: date):
    """-> (status, date or None, flags). `obs` are observations of ONE symbol for ONE candidate event (they may disagree)."""
    flags = []
    if not obs:
        return UNKNOWN, None, ["NO_OBSERVATION"]
    latest = _latest_by_source(obs)
    dates = sorted({o.date for o in latest.values()})
    chosen = max(latest.values(), key=lambda o: o.observed_at).date
    if len(dates) > 1:
        spread = (dates[-1] - dates[0]).days
        if spread > 1:
            return UNKNOWN, None, ["DATE_CONFLICT_OVER_1_DAY"]
        flags.append("DATE_DISAGREEMENT_1_DAY")
    newest = max(o.observed_at for o in latest.values())
    days_to = (chosen - today).days
    if now - newest > freshness_limit(max(days_to, 0)):
        return STALE, chosen, flags + ["REFRESH_TOO_OLD"]
    company = any(o.provider_status == "company" for o in latest.values() if o.date == chosen)
    agree = sum(1 for o in latest.values() if o.date == chosen)
    if company and agree >= 2 and now - newest <= CONFIRM_MAX_AGE and not flags:
        return CONFIRMED, chosen, flags
    return ESTIMATED, chosen, flags


def classify_timing(obs: list, chosen_date: date | None):
    if chosen_date is None:
        return T_UNKNOWN, "UNKNOWN"
    labels = {}
    for o in _latest_by_source(obs).values():
        if o.date == chosen_date:
            labels[o.source] = normalise_session(o.session)
    known = {k: v for k, v in labels.items() if v != "UNKNOWN"}
    if not known:
        return T_UNKNOWN, "UNKNOWN"
    values = set(known.values())
    if len(values) > 1:
        return T_CONFLICT, "UNKNOWN"
    kind = values.pop()
    return (T_CONFIRMED if len(known) >= 2 else T_SINGLE), kind


@dataclass
class EventVersion:
    symbol: str
    event_date: date | None
    version: int
    date_status: str
    timing_status: str
    kind: str
    flags: list
    recorded_at: datetime
    supersedes: int | None = None
    reason: str = "NEW"


class EventRegistry:
    """In-memory model of smv.event_versions: append-only list of versions per symbol."""

    def __init__(self):
        self.versions: list[EventVersion] = []
        self.pairs: list[dict] = []

    def current(self, symbol):
        vs = [v for v in self.versions if v.symbol == symbol]
        return vs[-1] if vs else None

    def observe(self, symbol: str, obs: list, now: datetime, today: date):
        """Fold new observations of one symbol into the registry; returns the (possibly new) current version."""
        status, d, flags = classify_date(obs, now, today)
        tstatus, kind = classify_timing(obs, d)
        cur = self.current(symbol)
        key = (d, status, tstatus, kind, tuple(flags))
        if cur and (cur.event_date, cur.date_status, cur.timing_status, cur.kind, tuple(cur.flags)) == key:
            return cur                                           # nothing changed: no new version (idempotent)
        reason = "NEW"
        if cur:
            if d is not None and cur.event_date is not None and d != cur.event_date:
                reason = "MOVED" if abs((d - cur.event_date).days) <= CLOSE_DAYS else "NEW_EVENT_SAME_SYMBOL"
            else:
                reason = "STATUS_CHANGE"
        v = EventVersion(symbol, d, (cur.version + 1) if cur else 1, status, tstatus, kind, flags, now, supersedes=cur.version if cur else None, reason=reason)
        self.versions.append(v)
        return v

    def register_concurrent_dates(self, symbol: str, obs_by_date: dict, now: datetime, today: date):
        """Two dates of one symbol up to 45 days apart observed at the same time: kept as an UNCERTAIN pair, both dates UNKNOWN for scheduling, nothing merged."""
        dates = sorted(obs_by_date)
        out = []
        for a, b in zip(dates, dates[1:]):
            if (b - a).days <= CLOSE_DAYS:
                rec = {"symbol": symbol, "date_1": a, "date_2": b, "code": "UNCERTAIN_CONCURRENT_OBSERVATIONS", "decision": "KEPT_SEPARATE",
                       "reason": "both dates are observed at the same time: may be two events or a date correction", "at": now}
                self.pairs.append(rec)
                out.append(rec)
        for dt, o in obs_by_date.items():
            v = EventVersion(symbol, dt, 1 + sum(1 for x in self.versions if x.symbol == symbol), UNKNOWN, T_UNKNOWN, "UNKNOWN", ["UNCERTAIN_PAIR"], now, reason="UNCERTAIN_PAIR")
            self.versions.append(v)
        return out
