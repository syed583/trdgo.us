"""Freshness and point-in-time evidence rules (component 8). Evidence is usable only if (a) everything it contains was available before the cutoff, (b) its
availability time is independently supportable (our capture clock, or a provider stamp that is not later than the capture), and (c) it is not stale.
Missing or stale evidence is never valid evidence: it becomes MISSING with a reason code and earns no points.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from . import cutoff as co

UTC = timezone.utc
FRESH, WEEKEND, HOLIDAY, STALE, UNSTAMPED = "FRESH", "LAST_SESSION_WEEKEND", "LAST_SESSION_HOLIDAY", "STALE_AGE", "UNSTAMPED"
MAX_CAPTURE_AGE_HOURS = {"price": None, "volatility": None, "headlines": 6, "consensus": 24, "flow": 24}   # None: must reflect the latest completed session


class LeakageError(RuntimeError):
    """Evidence stamped after the cutoff (or after the assumed release) tried to enter a pre-announcement prediction."""


@dataclass
class FactorEvidence:
    factor: int
    source_name: str
    status: str = "OK"                         # OK | MISSING
    missing_reason: str | None = None
    extracted: dict = field(default_factory=dict)
    provider_as_of: datetime | None = None
    capture_ts_utc: datetime | None = None
    session_reflected: date | None = None
    as_of_quality: str = "FETCH_TIME_ONLY"     # PER_ROW_STAMPED | DATED_NOT_STAMPED | FETCH_TIME_ONLY | DERIVED
    freshness_class: str = UNSTAMPED
    payload_sha256: str | None = None
    notes: list = field(default_factory=list)

    @property
    def usable(self):
        return self.status == "OK" and self.freshness_class != STALE


def missing(factor, source, reason, capture_ts=None, **kw):
    return FactorEvidence(factor=factor, source_name=source, status="MISSING", missing_reason=reason, capture_ts_utc=capture_ts, **kw)


def availability_time(capture_ts_utc: datetime, provider_as_of: datetime | None) -> datetime:
    """Independently supportable availability: our own capture clock. A provider stamp may only move it EARLIER (the value existed before we fetched it);
    a provider stamp later than the capture is impossible and treated as an error."""
    if provider_as_of is None:
        return capture_ts_utc
    if provider_as_of > capture_ts_utc + timedelta(minutes=5):
        raise LeakageError("provider stamp %s is later than the capture time %s" % (provider_as_of, capture_ts_utc))
    return min(provider_as_of, capture_ts_utc)


def assert_point_in_time(evidence_time_utc: datetime, cutoff_utc: datetime, what="evidence"):
    if evidence_time_utc.tzinfo is None:
        raise LeakageError("%s has a naive timestamp" % what)
    if evidence_time_utc > cutoff_utc:
        raise LeakageError("%s dated %s is after the cutoff %s" % (what, evidence_time_utc.isoformat(), cutoff_utc.isoformat()))


def classify(capture_ts_utc: datetime, session_reflected: date | None, provider_as_of: datetime | None, kind: str = "price") -> str:
    """FRESH | LAST_SESSION_WEEKEND | LAST_SESSION_HOLIDAY | STALE_AGE | UNSTAMPED relative to the capture time."""
    mc = co._mc()
    if session_reflected is None:
        return UNSTAMPED
    expected = co.last_completed_session_at(capture_ts_utc)
    if session_reflected < expected:
        return STALE                                           # older than the latest completed session: not usable
    if session_reflected > expected:
        return STALE                                           # claims a session that has not completed yet: treated as invalid, not fresh
    limit = MAX_CAPTURE_AGE_HOURS.get(kind)
    if limit is not None and provider_as_of is not None and capture_ts_utc - provider_as_of > timedelta(hours=limit):
        return STALE
    d = capture_ts_utc.astimezone(co.ET).date()
    gap = [expected + timedelta(n) for n in range(1, (d - expected).days + 1)]
    closed = [x for x in gap if not mc.is_trading_day(x)]            # non-trading days between the evidence and the capture
    if not closed:
        return FRESH
    return HOLIDAY if any(x.weekday() < 5 for x in closed) else WEEKEND
