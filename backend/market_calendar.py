"""
US equity market calendar: trading days, holidays, early closes and session close times.

Used to decide WHEN an earnings reaction can be graded: a Before-Open report reacts in the
earnings-day session, an After-Close report in the next valid session, and neither can be graded
before that session has actually closed (13:00 ET on early-close days, 16:00 ET otherwise).

Pure functions, no network. All clock arithmetic goes through America/New_York so daylight saving
time is handled by the zoneinfo database, never by a fixed UTC offset.

Rules follow the NYSE holiday schedule: New Year's Day (a Saturday New Year is NOT observed on the
previous Friday), Martin Luther King Jr. Day, Washington's Birthday, Good Friday, Memorial Day,
Juneteenth, Independence Day, Labor Day, Thanksgiving and Christmas (other holidays that fall on a
weekend are observed on the nearest weekday). Early closes (13:00 ET): the day after Thanksgiving,
Christmas Eve and July 3 when they fall Monday-Thursday. Unscheduled closures are listed in
SPECIAL_CLOSURES.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)

# Closures nobody could schedule (add here when the exchange announces one).
SPECIAL_CLOSURES: dict[date, str] = {
    date(2025, 1, 9): "National Day of Mourning (J. Carter)",
}


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    """Western Easter Sunday (anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _observed(d: date) -> date:
    """Saturday -> Friday, Sunday -> Monday."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=64)
def holidays(year: int) -> dict[date, str]:
    """Market holidays that fall in `year`."""
    out: dict[date, str] = {}
    ny = date(year, 1, 1)
    if ny.weekday() == 6:                      # Sunday -> Monday; Saturday is not observed
        out[ny + timedelta(days=1)] = "New Year's Day"
    elif ny.weekday() < 5:
        out[ny] = "New Year's Day"
    out[_nth_weekday(year, 1, 0, 3)] = "Martin Luther King Jr. Day"
    out[_nth_weekday(year, 2, 0, 3)] = "Washington's Birthday"
    out[_easter(year) - timedelta(days=2)] = "Good Friday"
    out[_last_weekday(year, 5, 0)] = "Memorial Day"
    if year >= 2022:
        out[_observed(date(year, 6, 19))] = "Juneteenth"
    out[_observed(date(year, 7, 4))] = "Independence Day"
    out[_nth_weekday(year, 9, 0, 1)] = "Labor Day"
    out[_nth_weekday(year, 11, 3, 4)] = "Thanksgiving Day"
    out[_observed(date(year, 12, 25))] = "Christmas Day"
    return out


def is_trading_day(d: date) -> bool:
    if d.weekday() >= 5 or d in SPECIAL_CLOSURES:
        return False
    return d not in holidays(d.year)


def next_trading_day(d: date) -> date:
    """The first trading day strictly after `d`."""
    d += timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def prev_trading_day(d: date) -> date:
    """The last trading day strictly before `d`."""
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def on_or_next_trading_day(d: date) -> date:
    return d if is_trading_day(d) else next_trading_day(d)


def on_or_prev_trading_day(d: date) -> date:
    return d if is_trading_day(d) else prev_trading_day(d)


def is_early_close(d: date) -> bool:
    if not is_trading_day(d):
        return False
    thanksgiving = _nth_weekday(d.year, 11, 3, 4)
    if d == thanksgiving + timedelta(days=1):
        return True
    if d.weekday() < 4 and ((d.month == 12 and d.day == 24) or (d.month == 7 and d.day == 3)):
        return True
    return False


def session_close(d: date) -> datetime:
    """The regular-session close of trading day `d`, as an aware datetime in New York."""
    if not is_trading_day(d):
        raise ValueError(f"{d} is not a trading day")
    return datetime.combine(d, EARLY_CLOSE if is_early_close(d) else REGULAR_CLOSE, tzinfo=NY)


def session_close_utc(d: date) -> datetime:
    return session_close(d).astimezone(timezone.utc)
