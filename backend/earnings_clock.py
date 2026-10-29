"""
US-Eastern earnings clock: the one place that answers "what is today", "how many days to the report" and "has it
reported yet" for the earnings screens.

Why it exists: the VPS runs on UTC+1, and several services computed the countdown with ``date.today()`` (the SERVER's
date). From 00:00 to about 05:00 UTC the server date is already the next day while New York is still on the previous
evening, so a report due "today" (ET) was stored as ``days_to = 0`` when the true countdown was 1. The value was also
computed once, when the cache entry was built, and then served unchanged for days.

Rules here:
  * "today" is always the America/New_York date (daylight saving included).
  * ``days_to`` is computed when it is READ (``refresh_countdown``), never trusted from a stored payload.
  * An event is REPORTED only when (a) a VERIFIED release timestamp says so, or (b) its report DATE is before today
    (ET). Nothing in this application stores a verified release timestamp today (``verified_release_utc`` returns None),
    so on the report day an event is UPCOMING, or POSSIBLY_REPORTED once the provisional window has passed. Provisional
    windows are estimates from the BMO / AMC label, never an exact release time.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Optional
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

UPCOMING = "UPCOMING"
POSSIBLY_REPORTED = "POSSIBLY_REPORTED"
REPORTED = "REPORTED"

BASIS_VERIFIED = "VERIFIED_RELEASE"
BASIS_DATE = "REPORT_DATE_PASSED"
BASIS_PROVISIONAL = "PROVISIONAL_WINDOW_UNVERIFIED"
BASIS_UNKNOWN_TIMING = "TIMING_UNKNOWN_UNVERIFIED"

# Provisional (conservative) windows, in step with earnings_scoreboard: a Before-Open report may land from 06:00 ET and has
# surely landed by 09:30 ET; an After-Close report lands from 16:00 ET.
BMO_EARLIEST = time(6, 0)
BMO_LATEST = time(9, 30)
AMC_RELEASE = time(16, 0)


def et_now(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(NY)


def et_today(now: Optional[datetime] = None) -> date:
    return et_now(now).date()


def as_date(value) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except (TypeError, ValueError):
        return None


def days_to(report_date, now: Optional[datetime] = None) -> Optional[int]:
    """Whole days from the US-Eastern date to the report date (negative once the date has passed)."""
    d = as_date(report_date)
    return None if d is None else (d - et_today(now)).days


def timing_kind(label: Optional[str]) -> Optional[str]:
    """'BMO' | 'AMC' | None (unknown). Delegates to earnings_outcomes so both modules read a label the same way."""
    from earnings_outcomes import timing_kind as _tk
    return _tk(label)


def verified_release_utc(symbol: str, earnings_date) -> Optional[datetime]:
    """The VERIFIED release timestamp of an event, or None.

    No source in this application provides one: Unusual Whales gives only a Before Open / After Close label, the SEC 8-K
    acceptance time can lag the press release by hours, and a provider's default clock time is not a release time. This is
    the single hook to wire a verified source into; until then every caller falls back to the provisional windows and
    labels the result unverified."""
    return None


def event_status(earnings_date, timing: Optional[str], now: Optional[datetime] = None,
                 verified_release: Optional[datetime] = None) -> dict:
    """{status, basis, days_to}. REPORTED only on a verified timestamp or a passed report date."""
    now = now or datetime.now(timezone.utc)
    d = as_date(earnings_date)
    out = {"days_to": days_to(d, now) if d else None, "timing": timing_kind(timing) or "UNKNOWN"}
    if d is None:
        return {**out, "status": UPCOMING, "basis": BASIS_UNKNOWN_TIMING}
    if verified_release is not None:
        rel = verified_release if verified_release.tzinfo else verified_release.replace(tzinfo=timezone.utc)
        return {**out, "status": REPORTED if now >= rel else UPCOMING, "basis": BASIS_VERIFIED}
    today = et_today(now)
    if d < today:
        return {**out, "status": REPORTED, "basis": BASIS_DATE}
    if d > today:
        return {**out, "status": UPCOMING, "basis": BASIS_PROVISIONAL}
    kind, t = timing_kind(timing), et_now(now).time()
    if kind == "BMO" and t >= BMO_LATEST:
        return {**out, "status": POSSIBLY_REPORTED, "basis": BASIS_PROVISIONAL}
    if kind == "AMC" and t >= AMC_RELEASE:
        return {**out, "status": POSSIBLY_REPORTED, "basis": BASIS_PROVISIONAL}
    if kind is None:
        return {**out, "status": UPCOMING, "basis": BASIS_UNKNOWN_TIMING}
    return {**out, "status": UPCOMING, "basis": BASIS_PROVISIONAL}


def is_upcoming(earnings_date, timing: Optional[str], now: Optional[datetime] = None,
                verified_release: Optional[datetime] = None) -> bool:
    """False only when the event has REPORTED on verified grounds (a verified timestamp, or a report date already past)."""
    return event_status(earnings_date, timing, now, verified_release)["status"] != REPORTED


def refresh_countdown(payload, now: Optional[datetime] = None):
    """A copy of an earn:trade style payload (or a scan row) with ``days_to`` recomputed from its report date at read time.

    Accepts {"earnings": {"date", "days_to"}} (earn:trade) or {"date", "days_to"} (a model scan row). Anything else is
    returned unchanged. The stored value is kept as ``days_to_cached`` so the difference stays visible."""
    if not isinstance(payload, dict):
        return payload
    out = dict(payload)
    e = out.get("earnings")
    if isinstance(e, dict) and e.get("date"):
        e2 = dict(e)
        e2["days_to_cached"] = e.get("days_to")
        e2["days_to"] = days_to(e.get("date"), now)
        out["earnings"] = e2
    elif out.get("date") and "days_to" in out:
        out["days_to_cached"] = out.get("days_to")
        out["days_to"] = days_to(out.get("date"), now)
    return out
