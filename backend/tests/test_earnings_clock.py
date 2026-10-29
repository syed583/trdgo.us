"""US-Eastern earnings clock: countdown, date boundaries, daylight saving, reported/upcoming status (deterministic fixtures)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

import earnings_clock as ec


def utc(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def test_et_date_differs_from_server_date_overnight():
    # 01:06 UTC on 29 Oct is 21:06 on 28 Oct in New York: the TWLO cache build time from the audit.
    now = utc(2026, 10, 29, 1, 6)
    assert ec.et_today(now) == date(2026, 10, 28)
    assert ec.days_to("2026-10-29", now) == 1            # the server-local date (UTC+1) said 0


@pytest.mark.parametrize("now,report,expected", [
    (utc(2026, 10, 29, 3, 59), "2026-10-29", 1),          # 23:59 ET on the 28th
    (utc(2026, 10, 29, 4, 0), "2026-10-29", 0),           # 00:00 ET on the 29th (EDT = UTC-4)
    (utc(2026, 10, 30, 3, 59), "2026-10-29", 0),          # 23:59 ET on the 29th
    (utc(2026, 10, 30, 4, 0), "2026-10-29", -1),          # 00:00 ET on the 30th
    (utc(2026, 11, 2, 4, 59), "2026-11-02", 1),           # EST from 1 Nov: 23:59 ET on the 1st is 04:59 UTC
    (utc(2026, 11, 2, 5, 0), "2026-11-02", 0),            # 00:00 ET on 2 Nov (EST = UTC-5)
    (utc(2026, 3, 8, 6, 59), "2026-03-08", 0),            # 01:59 EST on 8 Mar, one minute before the spring change
    (utc(2026, 3, 8, 7, 0), "2026-03-08", 0),             # 03:00 EDT on 8 Mar: the clock jumped, the date did not
    (utc(2026, 3, 9, 3, 59), "2026-03-08", 0),            # 23:59 EDT on 8 Mar
    (utc(2026, 3, 9, 4, 0), "2026-03-08", -1),            # 00:00 EDT on 9 Mar
])
def test_days_to_across_midnight_and_daylight_saving(now, report, expected):
    assert ec.days_to(report, now) == expected


def test_days_to_handles_bad_input():
    assert ec.days_to(None) is None
    assert ec.days_to("") is None
    assert ec.days_to("not a date") is None
    assert ec.days_to("2026-10-29T16:30:00Z", utc(2026, 10, 29, 12)) == 0


def test_stored_countdown_is_recomputed_at_read_time():
    stale = {"symbol": "AA", "earnings": {"date": "2026-10-15", "days_to": 0, "label": "Thu Oct 15"}}
    out = ec.refresh_countdown(stale, utc(2026, 10, 29, 18))
    assert out["earnings"]["days_to"] == -14
    assert out["earnings"]["days_to_cached"] == 0
    assert stale["earnings"]["days_to"] == 0            # the input is not mutated
    row = {"symbol": "X", "date": "2026-10-30", "days_to": 0}
    assert ec.refresh_countdown(row, utc(2026, 10, 29, 18))["days_to"] == 1
    assert ec.refresh_countdown("not a dict", utc(2026, 10, 29, 0)) == "not a dict"
    assert ec.refresh_countdown({"x": 1}) == {"x": 1}


def test_a_passed_report_date_is_reported_and_leaves_the_upcoming_view():
    now = utc(2026, 10, 29, 18)
    st = ec.event_status("2026-10-28", "After Close", now)
    assert st["status"] == ec.REPORTED and st["basis"] == ec.BASIS_DATE
    assert not ec.is_upcoming("2026-10-28", "After Close", now)


def test_report_day_is_never_called_reported_without_a_verified_timestamp():
    # 17:28 ET on 29 Oct: a BMO name has surely reported, but nothing VERIFIES it, so it is only POSSIBLY_REPORTED.
    now = utc(2026, 10, 29, 21, 28)
    bmo = ec.event_status("2026-10-29", "Before Open", now)
    assert bmo["status"] == ec.POSSIBLY_REPORTED and bmo["basis"] == ec.BASIS_PROVISIONAL
    assert ec.is_upcoming("2026-10-29", "Before Open", now)       # still shown: unverified
    amc = ec.event_status("2026-10-29", "After Close", now)
    assert amc["status"] == ec.POSSIBLY_REPORTED
    early = ec.event_status("2026-10-29", "After Close", utc(2026, 10, 29, 15, 0))   # 11:00 ET
    assert early["status"] == ec.UPCOMING


def test_unknown_timing_is_kept_separate_and_labelled_unverified():
    st = ec.event_status("2026-10-29", None, utc(2026, 10, 29, 15))
    assert st["status"] == ec.UPCOMING and st["basis"] == ec.BASIS_UNKNOWN_TIMING and st["timing"] == "UNKNOWN"
    assert ec.event_status("2026-10-29", "Before Open", utc(2026, 10, 29, 3))["timing"] == "BMO"
    assert ec.event_status("2026-10-29", "After Close", utc(2026, 10, 29, 3))["timing"] == "AMC"


def test_a_verified_release_timestamp_decides_reported_exactly():
    rel = utc(2026, 10, 29, 20, 5)
    assert ec.event_status("2026-10-29", "After Close", utc(2026, 10, 29, 20, 4), rel)["status"] == ec.UPCOMING
    st = ec.event_status("2026-10-29", "After Close", utc(2026, 10, 29, 20, 6), rel)
    assert st["status"] == ec.REPORTED and st["basis"] == ec.BASIS_VERIFIED


def test_no_verified_release_source_is_wired_in_yet():
    assert ec.verified_release_utc("TWLO", date(2026, 10, 29)) is None


def test_the_all_models_matrix_counts_days_on_the_eastern_date(monkeypatch):
    import all_models_service as am
    fixed = utc(2026, 10, 29, 1, 6)                      # 21:06 ET on the 28th: the server-local date is already the 29th
    real = ec.days_to
    monkeypatch.setattr(ec, "days_to", lambda d, now=None: real(d, fixed))
    assert am._row_days_to({"date": "2026-10-29", "days_to": 0}) == 1
    assert am._row_days_to({"date": None, "days_to": 7}) == 7          # no usable date: keep the model's own value


def test_the_earnings_trade_endpoint_returns_a_countdown_recomputed_at_read_time(monkeypatch):
    import api_routes
    import swr
    stored = {"symbol": "AA", "earnings": {"date": "2026-10-15", "days_to": 0}}
    monkeypatch.setattr(swr, "serve", lambda key, fn, ttl: stored)
    out = api_routes.earnings_trade("AA")
    assert out["earnings"]["days_to"] < 0 and out["earnings"]["days_to_cached"] == 0
    assert stored["earnings"]["days_to"] == 0                       # the cached payload itself is never mutated
