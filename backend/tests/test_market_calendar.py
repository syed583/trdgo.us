"""Trading calendar: holidays, weekends, early closes and daylight saving time."""
from datetime import date, datetime, timezone

import pytest

import market_calendar as mc


def test_2026_holidays_match_the_exchange_schedule():
    h = mc.holidays(2026)
    assert h[date(2026, 1, 1)] == "New Year's Day"
    assert date(2026, 1, 19) in h          # Martin Luther King Jr. Day
    assert date(2026, 2, 16) in h          # Washington's Birthday
    assert date(2026, 4, 3) in h           # Good Friday
    assert date(2026, 5, 25) in h          # Memorial Day
    assert date(2026, 6, 19) in h          # Juneteenth (Friday)
    assert date(2026, 7, 3) in h           # Independence Day observed (July 4 is a Saturday)
    assert date(2026, 7, 4) not in h
    assert date(2026, 9, 7) in h           # Labor Day
    assert date(2026, 11, 26) in h         # Thanksgiving
    assert date(2026, 12, 25) in h         # Christmas (Friday)
    assert len(h) == 10


def test_2025_holidays_and_special_closure():
    h = mc.holidays(2025)
    assert date(2025, 4, 18) in h          # Good Friday
    assert date(2025, 7, 4) in h
    assert date(2025, 6, 19) in h
    assert not mc.is_trading_day(date(2025, 1, 9))     # National Day of Mourning


def test_saturday_new_year_is_not_observed_on_friday():
    assert date(2022, 1, 1).weekday() == 5
    assert mc.is_trading_day(date(2021, 12, 31))        # the market was open


def test_sunday_holiday_moves_to_monday():
    assert date(2027, 12, 25).weekday() == 5            # Christmas on a Saturday -> Friday 24th
    assert not mc.is_trading_day(date(2027, 12, 24))
    assert date(2023, 1, 1).weekday() == 6              # New Year on Sunday -> Monday
    assert not mc.is_trading_day(date(2023, 1, 2))


def test_weekends_are_not_trading_days():
    assert not mc.is_trading_day(date(2026, 10, 31))    # Saturday
    assert not mc.is_trading_day(date(2026, 11, 1))     # Sunday
    assert mc.is_trading_day(date(2026, 11, 2))


def test_next_and_previous_trading_day_skip_weekends_and_holidays():
    assert mc.next_trading_day(date(2026, 10, 30)) == date(2026, 11, 2)       # Fri -> Mon
    assert mc.next_trading_day(date(2026, 11, 25)) == date(2026, 11, 27)      # Wed -> Fri (Thanksgiving)
    assert mc.next_trading_day(date(2026, 1, 16)) == date(2026, 1, 20)        # Fri -> Tue (MLK Monday)
    assert mc.prev_trading_day(date(2026, 11, 27)) == date(2026, 11, 25)
    assert mc.prev_trading_day(date(2026, 4, 6)) == date(2026, 4, 2)          # Mon after Good Friday
    assert mc.on_or_next_trading_day(date(2026, 11, 1)) == date(2026, 11, 2)
    assert mc.on_or_prev_trading_day(date(2026, 11, 1)) == date(2026, 10, 30)


def test_early_closes():
    assert mc.is_early_close(date(2026, 11, 27))        # day after Thanksgiving
    assert mc.is_early_close(date(2026, 12, 24))        # Christmas Eve (Thursday)
    assert mc.is_early_close(date(2025, 7, 3))          # July 3 (Thursday)
    assert not mc.is_early_close(date(2026, 7, 3))      # observed holiday, not an early close
    assert not mc.is_early_close(date(2026, 10, 28))
    assert not mc.is_early_close(date(2026, 11, 26))    # holiday


def test_session_close_is_utc_correct_across_daylight_saving():
    # EDT (UTC-4) until Sunday 2026-11-01 02:00 ET, EST (UTC-5) after.
    assert mc.session_close_utc(date(2026, 10, 28)) == datetime(2026, 10, 28, 20, 0, tzinfo=timezone.utc)
    assert mc.session_close_utc(date(2026, 10, 30)) == datetime(2026, 10, 30, 20, 0, tzinfo=timezone.utc)
    assert mc.session_close_utc(date(2026, 11, 2)) == datetime(2026, 11, 2, 21, 0, tzinfo=timezone.utc)
    # spring forward: 2026-03-08; Friday before is EST, Monday after is EDT
    assert mc.session_close_utc(date(2026, 3, 6)) == datetime(2026, 3, 6, 21, 0, tzinfo=timezone.utc)
    assert mc.session_close_utc(date(2026, 3, 9)) == datetime(2026, 3, 9, 20, 0, tzinfo=timezone.utc)
    # early close 13:00 EST = 18:00 UTC
    assert mc.session_close_utc(date(2026, 11, 27)) == datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc)


def test_session_close_rejects_non_trading_days():
    with pytest.raises(ValueError):
        mc.session_close(date(2026, 11, 26))
