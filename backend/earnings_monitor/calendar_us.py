"""US equity-market calendar (NYSE): holidays, early closes, trading days and the reaction session of an earnings report. Computed from the exchange's rules, not from a table that can run out.

Rules used (NYSE): New Year's Day, Martin Luther King Jr. Day (3rd Mon Jan), Washington's Birthday (3rd Mon Feb), Good Friday, Memorial Day (last Mon May), Juneteenth (Jun 19, from 2022), Independence Day, Labor Day
(1st Mon Sep), Thanksgiving (4th Thu Nov), Christmas. A holiday on a Saturday is observed the Friday before, on a Sunday the Monday after; New Year's Day on a Saturday is NOT observed on the Friday.
Early close (13:00 ET): the day after Thanksgiving, Christmas Eve when it is a weekday and not itself the observed Christmas holiday, and July 3 when it is a weekday and July 4 is a weekday (Tue-Fri).
Special closures (for example a national day of mourning) cannot be predicted and are not included; add them to SPECIAL_CLOSURES when announced."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = timezone.utc
SPECIAL_CLOSURES: set = set()          # dates the exchange announced as closed outside the rules above


def _nth_weekday(y: int, m: int, weekday: int, n: int) -> date:
    d = date(y, m, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(y: int, m: int, weekday: int) -> date:
    d = date(y, m + 1, 1) - timedelta(days=1) if m < 12 else date(y, 12, 31)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _easter(y: int) -> date:
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(y, month, day + 1)


def _observed(d: date, new_year: bool = False) -> Optional[date]:
    if d.weekday() == 5:
        return None if new_year else d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def holidays(y: int) -> dict:
    h = {}
    for d, name, ny in ((date(y, 1, 1), "New Year's Day", True), (date(y, 7, 4), "Independence Day", False), (date(y, 12, 25), "Christmas Day", False)):
        o = _observed(d, ny)
        if o:
            h[o] = name
    if y >= 2022:
        o = _observed(date(y, 6, 19))
        h[o] = "Juneteenth"
    # New Year's Day of the NEXT year falling on a Saturday is not observed on Dec 31, so nothing to add here.
    h[_nth_weekday(y, 1, 0, 3)] = "Martin Luther King Jr. Day"
    h[_nth_weekday(y, 2, 0, 3)] = "Washington's Birthday"
    h[_easter(y) - timedelta(days=2)] = "Good Friday"
    h[_last_weekday(y, 5, 0)] = "Memorial Day"
    h[_nth_weekday(y, 9, 0, 1)] = "Labor Day"
    h[_nth_weekday(y, 11, 3, 4)] = "Thanksgiving Day"
    return h


def is_holiday(d: date) -> bool:
    return d in holidays(d.year) or d in SPECIAL_CLOSURES


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and not is_holiday(d)


def is_early_close(d: date) -> bool:
    if not is_trading_day(d):
        return False
    tg = _nth_weekday(d.year, 11, 3, 4)
    if d == tg + timedelta(days=1):
        return True
    if d == date(d.year, 12, 24):
        return True                                    # a weekday Dec 24 that is open (not the observed Christmas) closes at 13:00
    if d == date(d.year, 7, 3) and date(d.year, 7, 4).weekday() in (1, 2, 3, 4):
        return True
    return False


def next_trading_day(d: date) -> date:
    d += timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def prev_trading_day(d: date) -> date:
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def close_et(d: date) -> time:
    return time(13, 0) if is_early_close(d) else time(16, 0)


def close_utc(d: date) -> datetime:
    return datetime.combine(d, close_et(d), tzinfo=ET).astimezone(UTC)


def reaction_session(report_date: date, session: str) -> date:
    """The session whose close first reflects the report: a before-open report reacts the same session, an after-close report the next one. A report dated on a non-trading day reacts on the next trading day."""
    base = report_date if is_trading_day(report_date) else next_trading_day(report_date)
    return next_trading_day(base) if session == "AMC" else base


def window_et(d: date, session: str, cfg_window: tuple) -> tuple:
    """The earnings-day check window for a session; an after-close window is pulled in to the early close when the day closes at 13:00."""
    lo, hi = cfg_window
    if session == "AMC" and is_early_close(d):
        return ("12:00", "17:00")
    return (lo, hi)
