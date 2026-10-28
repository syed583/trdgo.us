"""Proposed fix (NOT applied to the branch): the grader must use regular-session closes.

Fixtures follow the provider's documented candle shape (market_time pr / r / po, string prices) and the app's own note that
one date can carry several rows. They are synthetic: no stored production bars exist (candles are cached in memory only)."""
import pathlib
import sys
from datetime import date, datetime, timezone

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[0]))
BACKEND = None
for parent in pathlib.Path(__file__).resolve().parents:
    if (parent / "backend" / "earnings_outcomes.py").exists():
        BACKEND = parent / "backend"
        break
sys.path.insert(0, str(BACKEND))

import earnings_outcomes as eo   # noqa: E402

UTC = timezone.utc


def row(day, mt, close, vol, **extra):
    r = {"date": day, "close": str(close), "total_volume": vol, "volume": vol}
    if mt is not None:
        r["market_time"] = mt
    r.update(extra)
    return r


# AMC report on Tue 27 Oct after the close. Regular close 100.0, after-hours 108.0 on the earnings, next regular close 106.0.
MULTI_ROW = [
    row("2026-10-26", "pr", 98.0, 400), row("2026-10-26", "r", 99.0, 7000), row("2026-10-26", "po", 99.2, 7100),
    row("2026-10-27", "pr", 99.5, 1000), row("2026-10-27", "r", 100.0, 9000), row("2026-10-27", "po", 108.0, 9500),
    row("2026-10-28", "pr", 107.5, 500), row("2026-10-28", "r", 106.0, 8000), row("2026-10-28", "po", 106.4, 8300)]
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def test_regular_row_wins_on_dates_with_pre_regular_and_post_rows():
    closes = eo.regular_session_closes(MULTI_ROW)
    assert closes == {date(2026, 10, 26): 99.0, date(2026, 10, 27): 100.0, date(2026, 10, 28): 106.0}


def test_after_close_move_is_measured_regular_close_to_regular_close():
    o = eo.evaluate("X", date(2026, 10, 27), "AMC", NOW, eo.regular_session_closes(MULTI_ROW))
    assert o["status"] == eo.GRADED and (o["base_close"], o["reaction_close"], o["move_pct"]) == (100.0, 106.0, 6.0)


def test_last_row_wins_logic_would_have_been_wrong():
    """What the previous fetcher did (last row per date): the after-hours row contaminates the base."""
    last_wins = {}
    for r in MULTI_ROW:
        last_wins[date.fromisoformat(r["date"])] = float(r["close"])
    bad = eo.evaluate("X", date(2026, 10, 27), "AMC", NOW, last_wins)
    assert (bad["base_close"], bad["move_pct"], bad["direction"]) == (108.0, -1.48, "SELL")     # the wrong sign


def test_before_open_uses_the_regular_closes_too():
    o = eo.evaluate("X", date(2026, 10, 27), "BMO", NOW, eo.regular_session_closes(MULTI_ROW))
    assert (o["base_close"], o["reaction_close"], o["move_pct"]) == (99.0, 100.0, 1.01)


def test_date_with_only_extended_hours_rows_has_no_regular_close_yet():
    rows = MULTI_ROW[:6] + [row("2026-10-28", "pr", 107.5, 500), row("2026-10-28", "po", 106.4, 8300)]
    closes = eo.regular_session_closes(rows)
    assert date(2026, 10, 28) not in closes
    o = eo.evaluate("X", date(2026, 10, 27), "AMC", NOW, closes)
    assert o["status"] == eo.PENDING_PRICE and "2026-10-28" in o["note"]


def test_unlabelled_single_rows_and_duplicates_without_market_time():
    rows = [row("2026-10-27", None, 100.0, 9000), row("2026-10-28", None, 104.0, 500), row("2026-10-28", None, 105.0, 8000)]
    assert eo.regular_session_closes(rows) == {date(2026, 10, 27): 100.0, date(2026, 10, 28): 105.0}   # highest volume


def test_several_regular_rows_last_wins_and_bad_rows_are_ignored():
    rows = [row("2026-10-27", "r", 100.0, 1), row("2026-10-27", "r", 100.5, 2), {"date": None, "close": "1"},
            {"date": "2026-10-28", "close": None, "market_time": "r"}, {"date": "garbage", "close": "5", "market_time": "r"},
            {"start_time": "2026-10-29T00:00:00Z", "close": "7.5", "market_time": "R"}]
    assert eo.regular_session_closes(rows) == {date(2026, 10, 27): 100.5, date(2026, 10, 29): 7.5}


def test_default_fetcher_uses_regular_closes(monkeypatch):
    import unusualwhales_service as uw
    monkeypatch.setattr(uw, "candles", lambda s, size, limit=0, date="": {"status": "OK", "data": MULTI_ROW})
    assert eo.default_bars_fetcher("X")[date(2026, 10, 27)] == 100.0
