"""Release assumptions, prediction windows and cutoffs (component 8). Pure functions; every clock calculation goes through America/New_York.

A verified release timestamp does not exist for any event. The assumed release is the EARLIEST plausible one: the nominal session time of the event's own label
(BMO 06:00, AMC 16:00 on the event date), or the SEC filing acceptance time when that is earlier (a filing time is only an upper bound on a release time, never
proof of it). Cutoff = assumed release minus exactly 24 absolute hours; the capture window opens 48 hours before the assumed release.
"""
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = timezone.utc
NOMINAL = {"BMO": time(6, 0), "AMC": time(16, 0)}
BUFFER_MIN = 45            # a daily close is usable 45 minutes after the bell
ORDER_DEADLINE_MIN = 10    # market-on-close orders must be placed 10 minutes before the close
_BACKEND = Path(__file__).resolve().parents[4] / "backend"


def _mc():
    if str(_BACKEND) not in sys.path:
        sys.path.insert(0, str(_BACKEND))
    import market_calendar as mc
    return mc


class UnknownTiming(ValueError):
    pass


def assumed_release(event_date: date, kind: str, sec_acceptance_utc: datetime | None = None):
    """(assumed release UTC, basis). The basis says why; it is never 'verified'."""
    if kind not in NOMINAL:
        raise UnknownTiming("timing %r has no nominal release" % (kind,))
    nominal = datetime.combine(event_date, NOMINAL[kind], ET).astimezone(UTC)
    if sec_acceptance_utc is not None and sec_acceptance_utc.tzinfo is None:
        raise ValueError("SEC acceptance time must be timezone-aware")
    if sec_acceptance_utc is not None and sec_acceptance_utc < nominal:
        return sec_acceptance_utc.astimezone(UTC), "SEC_ACCEPTANCE_UPPER_BOUND_EARLIER_THAN_NOMINAL"
    return nominal, "NOMINAL_" + kind


def conservative_release_unknown_timing(event_date: date):
    """Unknown timing: the earliest of the two nominal times, so the window can only open early (the event is evidence-only and scored NO TRADE)."""
    return datetime.combine(event_date, NOMINAL["BMO"], ET).astimezone(UTC), "UNKNOWN_TIMING_USES_EARLIEST_NOMINAL"


def window(release_utc: datetime):
    return release_utc - timedelta(hours=48), release_utc - timedelta(hours=24)


def last_usable_session(cutoff_utc: datetime) -> date:
    """Last trading session whose close plus the availability buffer is not after the cutoff."""
    mc = _mc()
    day = cutoff_utc.astimezone(ET).date()
    while not (mc.is_trading_day(day) and mc.session_close_utc(day) + timedelta(minutes=BUFFER_MIN) <= cutoff_utc):
        day -= timedelta(days=1)
    return day


def last_completed_session_at(ts_utc: datetime) -> date:
    """Session whose data is complete at ts (same rule as above, applied to any instant such as a capture time)."""
    return last_usable_session(ts_utc)


def base_session(event_date: date, kind: str) -> date:
    """Session whose close is the entry price (BMO: previous trading day, AMC: the event date or the last trading day before it)."""
    mc = _mc()
    if kind == "BMO":
        return mc.prev_trading_day(mc.on_or_next_trading_day(event_date))
    if kind == "AMC":
        return mc.on_or_prev_trading_day(event_date)
    raise UnknownTiming(kind)


def reaction_session(event_date: date, kind: str) -> date:
    mc = _mc()
    if kind == "BMO":
        return mc.on_or_next_trading_day(event_date)
    if kind == "AMC":
        return mc.next_trading_day(event_date)
    raise UnknownTiming(kind)


def order_deadline_utc(base: date) -> datetime:
    return _mc().session_close_utc(base) - timedelta(minutes=ORDER_DEADLINE_MIN)


def tradable(cutoff_utc: datetime, base: date) -> bool:
    """The entry is a market-on-close order on `base`: it must be a session and its order deadline must come after the cutoff (no earlier close is ever assumed tradable)."""
    return _mc().is_trading_day(base) and cutoff_utc < order_deadline_utc(base)


def plan_event(event_date: date, kind: str, sec_acceptance_utc: datetime | None = None) -> dict:
    """Everything the scheduler and scorer need about one event version's timing."""
    if kind in NOMINAL:
        release, basis = assumed_release(event_date, kind, sec_acceptance_utc)
        evidence_only = False
    else:
        release, basis = conservative_release_unknown_timing(event_date)
        evidence_only = True
    start, deadline = window(release)
    cutoff = deadline
    out = {"assumed_release_utc": release, "assumed_release_et": release.astimezone(ET), "basis": basis, "window_start_utc": start, "deadline_utc": deadline,
           "cutoff_utc": cutoff, "cutoff_et": cutoff.astimezone(ET), "evidence_only": evidence_only, "last_usable_session": last_usable_session(cutoff),
           "dst_between_cutoff_and_release": release.astimezone(ET).utcoffset() != cutoff.astimezone(ET).utcoffset()}
    if kind in NOMINAL:
        base = base_session(event_date, kind)
        out.update(base_session=base, reaction_session=reaction_session(event_date, kind), tradable=tradable(cutoff, base))
    else:
        out.update(base_session=None, reaction_session=None, tradable=False)
    return out
