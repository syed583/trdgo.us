"""US equity market calendar: which days the exchange trades, and when a position's first close arrives.

The app had no holiday calendar ("the clock has no holiday calendar"). Paper positions are now carried across dates
until a rule closes them, so "the first 16:00 ET close after this entry" must skip weekends AND exchange holidays.
Full-day NYSE closures, plus the 13:00 ET early closes (day after Thanksgiving, Christmas Eve, July 3 when July 4 is
a Tue-Fri weekday):

    New Year's Day, Martin Luther King Jr. Day, Presidents' Day, Good Friday, Memorial Day, Juneteenth,
    Independence Day, Labor Day, Thanksgiving, Christmas -- with the exchange's weekend observance rules.

Computed, not tabled, so it never runs out of years. A special one-off closure (a national day of mourning) is not
knowable here: ``EXTRA_CLOSURES`` / ``EXTRA_EARLY_CLOSES`` (or the environment variables ``MARKET_EXTRA_CLOSURES`` /
``MARKET_EXTRA_EARLY_CLOSES``, comma-separated ISO dates) let an operator add one. A day the calendar believes is open
but the exchange shut is still safe: nothing here invents a price, and the paper tracker defers every exit while no
reliable price arrives (see paper_fills.py).

Instruments: US STOCKS and ETFs share one session (extended hours 04:00-20:00 ET, regular 09:30-16:00 ET, close at
13:00 ET on early-close days). Cash INDEXES cannot be traded and FUTURES follow a different (Globex) calendar that is
NOT modelled here, so ``instrument_kind`` separates them and the entry gate refuses anything that is not a stock / ETF.
"""
from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from typing import Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
CLOSE = time(16, 0)
EXTRA_CLOSURES: set[date] = set()
EXTRA_EARLY_CLOSES: set[date] = set()
EARLY_CLOSE = time(13, 0)
EXT_OPEN, EXT_CLOSE = time(4, 0), time(20, 0)         # extended-hours session (stocks / ETFs)
_ENV_CACHE: dict = {}


def _env_dates(name: str) -> set:
    raw = os.environ.get(name, "")
    if _ENV_CACHE.get(name, (None, None))[0] != raw:
        out = set()
        for part in raw.split(","):
            try:
                out.add(date.fromisoformat(part.strip()))
            except ValueError:
                continue
        _ENV_CACHE[name] = (raw, out)
    return _ENV_CACHE[name][1]


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _observed(d: date) -> Optional[date]:
    """A fixed-date holiday: Saturday -> the Friday before, Sunday -> the Monday after."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=64)
def holidays(year: int) -> dict:
    """{date: name} of the full-day exchange closures that fall IN ``year``."""
    out: dict = {}
    # New Year's Day: when it falls on a Saturday the exchange stays open the Friday (Dec 31 of the prior year).
    ny = date(year, 1, 1)
    if ny.weekday() != 5:
        out[_observed(ny)] = "New Year's Day"
    out[_nth_weekday(year, 1, 0, 3)] = "Martin Luther King Jr. Day"
    out[_nth_weekday(year, 2, 0, 3)] = "Presidents' Day"
    out[_easter(year) - timedelta(days=2)] = "Good Friday"
    out[_last_weekday(year, 5, 0)] = "Memorial Day"
    if year >= 2022:
        out[_observed(date(year, 6, 19))] = "Juneteenth"
    out[_observed(date(year, 7, 4))] = "Independence Day"
    out[_nth_weekday(year, 9, 0, 1)] = "Labor Day"
    out[_nth_weekday(year, 11, 3, 4)] = "Thanksgiving"
    out[_observed(date(year, 12, 25))] = "Christmas"
    return {d: n for d, n in out.items() if d.year == year}


def is_trading_day(d: date) -> bool:
    if d.weekday() >= 5 or d in EXTRA_CLOSURES or d in SPECIAL_CLOSURES or d in _env_dates("MARKET_EXTRA_CLOSURES"):
        return False
    # a holiday observed in the NEXT year can fall on Dec 31 (a Friday): not for NYSE, but check both years anyway
    return d not in holidays(d.year) and d not in holidays(d.year + 1)


def next_trading_day(d: date) -> date:
    d += timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def is_early_close(d: date) -> bool:
    """A 13:00 ET close: the day after Thanksgiving, Christmas Eve, and July 3 when July 4 is a weekday (Tue-Fri)."""
    if not is_trading_day(d):
        return False
    if d in EXTRA_EARLY_CLOSES or d in _env_dates("MARKET_EXTRA_EARLY_CLOSES"):
        return True
    if d == _nth_weekday(d.year, 11, 3, 4) + timedelta(days=1):
        return True
    if d.month == 12 and d.day == 24:
        return True
    return d.month == 7 and d.day == 3 and date(d.year, 7, 4).weekday() < 5 and date(d.year, 7, 4).weekday() >= 1


def close_time(d: date) -> time:
    """The regular-session close of a trading day: 16:00 ET, or 13:00 ET on an early-close day."""
    return EARLY_CLOSE if is_early_close(d) else CLOSE


def close_datetime(d: date) -> datetime:
    return datetime.combine(d, close_time(d), tzinfo=ET)


def eod_time(d: date) -> time:
    """The live engine's end-of-day exit: five minutes before the session's close (15:55 ET, 12:55 on early closes)."""
    c = close_datetime(d) - timedelta(minutes=5)
    return c.time()


def first_close_after(moment: datetime) -> datetime:
    """The first regular-session close of a TRADING day that comes after ``moment`` (13:00 on early-close days)."""
    et = moment.astimezone(ET)
    day = et.date()
    if is_trading_day(day) and et < close_datetime(day):
        return close_datetime(day)
    return close_datetime(next_trading_day(day))


def trading_days_back(d: date, n: int) -> date:
    """The trading day ``n`` trading days before ``d`` (``d`` itself need not be one)."""
    out, done = d, 0
    while done < n:
        out -= timedelta(days=1)
        if is_trading_day(out):
            done += 1
    return out


def extended_session_open(d: date) -> bool:
    return is_trading_day(d)


def closed_between(prev: datetime, now: datetime) -> bool:
    """Did a period in which these stocks / ETFs do not trade lie between two observations? (the overnight break
    20:00 -> 04:00 ET, a weekend, a holiday). The extended-hours session of a trading day is 04:00-20:00 ET."""
    a, b = prev.astimezone(ET), now.astimezone(ET)
    if b <= a:
        return False
    d = a.date() - timedelta(days=1)
    while d <= b.date():
        if is_trading_day(d):
            start = datetime.combine(d, EXT_CLOSE, tzinfo=ET)                      # this day's session ends
            end = datetime.combine(next_trading_day(d), EXT_OPEN, tzinfo=ET)       # the next one begins
            if a < end and b > start:
                return True
        d += timedelta(days=1)
    return False


_INDEX_ROOTS = frozenset({"SPX", "SPXW", "NDX", "NDXP", "RUT", "RUTW", "INDU", "DJX", "VIX", "VIXW", "COMP", "OEX", "XSP"})


def instrument_kind(symbol: str) -> str:
    """'equity' (a stock or ETF), 'index' (a cash index: not tradable) or 'future' (Globex: not modelled).

    Futures are recognised only by an unambiguous marker ('/ES', 'ES=F'): 'ES' alone is a stock (Eversource)."""
    s = (symbol or "").strip().upper()
    if not s:
        return "unknown"
    if s.startswith("/") or s.endswith("=F"):
        return "future"
    if s.startswith("^") or s in _INDEX_ROOTS:
        return "index"
    return "equity" if s.replace(".", "").replace("-", "").isalnum() else "unknown"


# ------------------------------------------------------------------ trading sessions (paper-trade workflow) ----------
# The paper-trade workflow works in two sessions of a TRADING day, in America/New_York (so daylight saving is the zone's
# business, never a fixed UTC offset and never Dubai time):
#
#     PREMARKET   04:00 - 09:30 ET
#     REGULAR     09:30 - close      (16:00 ET, or 13:00 ET on an early-close day)
#
# New entries are allowed in both. Nothing opens at or after the close, on a weekend, or on an exchange holiday.
# Post-market (close - 20:00 ET) is an extended session the workflow does not trade.
PREMARKET, REGULAR, POSTMARKET, CLOSED = "PREMARKET", "REGULAR", "POSTMARKET", "CLOSED"
PREMARKET_OPEN, REGULAR_OPEN = time(4, 0), time(9, 30)


def session_bounds(d: date) -> Optional[dict]:
    """{'premarket_open', 'regular_open', 'close', 'early_close'} (aware ET datetimes) of a trading day, else None."""
    if not is_trading_day(d):
        return None
    return {"premarket_open": datetime.combine(d, PREMARKET_OPEN, tzinfo=ET),
            "regular_open": datetime.combine(d, REGULAR_OPEN, tzinfo=ET),
            "close": close_datetime(d), "early_close": is_early_close(d)}


def session_at(moment: datetime) -> dict:
    """The session ``moment`` falls in: {'session', 'trading_date', 'bounds'}. ``trading_date`` is the ET calendar date;
    ``session`` is PREMARKET / REGULAR / POSTMARKET / CLOSED (a weekend, a holiday, or outside 04:00-20:00 ET)."""
    et = moment.astimezone(ET)
    d = et.date()
    b = session_bounds(d)
    if b is None:
        return {"session": CLOSED, "trading_date": d, "bounds": None}
    if et < b["premarket_open"]:
        s = CLOSED
    elif et < b["regular_open"]:
        s = PREMARKET
    elif et < b["close"]:
        s = REGULAR
    elif et < datetime.combine(d, EXT_CLOSE, tzinfo=ET):
        s = POSTMARKET
    else:
        s = CLOSED
    return {"session": s, "trading_date": d, "bounds": b}


def entries_allowed(moment: datetime) -> bool:
    """May a NEW paper entry open at ``moment``? Only in PREMARKET or REGULAR, strictly before the official close."""
    return session_at(moment)["session"] in (PREMARKET, REGULAR)


def seconds_to_close(moment: datetime) -> Optional[float]:
    """Seconds until today's official close (None when today is not a trading day or the close has passed)."""
    et = moment.astimezone(ET)
    b = session_bounds(et.date())
    if b is None or et >= b["close"]:
        return None
    return (b["close"] - et).total_seconds()


def close_due(trade_date: date, moment: datetime) -> bool:
    """Has the official close of ``trade_date`` arrived (or passed) at ``moment``? A trade of an earlier date is always due."""
    et = moment.astimezone(ET)
    if trade_date < et.date():
        return True
    if trade_date > et.date():
        return False
    return et >= close_datetime(trade_date) if is_trading_day(trade_date) else True

# ---------------------------------------------------------------- earnings-evaluation helpers (Phase 2A, production) ----------
# The earnings outcome / scoreboard modules (earnings_outcomes, earnings_scoreboard, tools/outcome_*) use these names; they
# were the whole of the production calendar before the intraday calendar above absorbed it. Same holiday rules, same zone.
NY = ET
REGULAR_CLOSE = CLOSE
# Closures nobody could schedule (add here when the exchange announces one).
SPECIAL_CLOSURES: dict = {
    date(2025, 1, 9): "National Day of Mourning (J. Carter)",
}


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


def session_close(d: date) -> datetime:
    """The regular-session close of trading day `d`, as an aware datetime in New York (13:00 on an early close)."""
    if not is_trading_day(d):
        raise ValueError(f"{d} is not a trading day")
    return close_datetime(d)


def session_close_utc(d: date) -> datetime:
    return session_close(d).astimezone(timezone.utc)
