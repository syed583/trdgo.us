"""
Unusual Whales standing in for the bar feeds, daily and intraday.

The daily side has to survive a shape the free providers never sent: one
calendar date arrives as several rows -- pre-market, regular session,
post -- and counting those as separate days would double-count volume and
shorten every moving average. Asking for as many rows as days wanted
returned a third of the history for the same reason.

The intraday side is the part that had no substitute at all. TWS refuses a
request now and then, and while it refuses, VWAP, the opening range and
relative volume have nothing to be measured from.
"""

import unusualwhales_service as uw


def _stub(monkeypatch, rows):
    monkeypatch.setattr(uw, "candles",
                        lambda s, size="1d", limit=300, date="": {
                            "status": "OK", "data": rows, "source": uw.SOURCE})


def test_one_date_in_several_sessions_is_one_trading_day(monkeypatch):
    _stub(monkeypatch, [
        {"date": "2026-09-23", "open": "228.8", "high": "229.4",
         "low": "227.3", "close": "228.0", "volume": 2617823,
         "total_volume": 2617823, "market_time": "pr"},
        {"date": "2026-09-23", "open": "228.1", "high": "228.9",
         "low": "224.0", "close": "224.6", "volume": 45085361,
         "total_volume": 47703184, "market_time": "r"},
        {"date": "2026-09-22", "open": "220.0", "high": "221.0",
         "low": "219.0", "close": "220.5", "volume": 30000000,
         "total_volume": 30000000, "market_time": "r"},
    ])

    bars = uw.get_daily_bars("NVDA", "1 Y")
    assert [b["date"] for b in bars] == ["2026-09-22", "2026-09-23"]
    assert bars[-1]["close"] == 224.6, "the regular session is the day's close"
    assert bars[-1]["volume"] == 47703184, "and its full volume"


def test_bars_come_back_oldest_first():
    """Every technical in this app assumes it, and would invert without it."""
    rows = [{"date": "2026-09-2%d" % d, "close": "1", "open": "1",
             "high": "1", "low": "1", "volume": 1} for d in (3, 1, 2)]

    class _Fake:
        status = "OK"

    import types
    fake = types.SimpleNamespace()
    out = {"status": "OK", "data": rows, "source": uw.SOURCE}
    original = uw.candles
    uw.candles = lambda *a, **k: out
    try:
        bars = uw.get_daily_bars("NVDA", "1 M")
    finally:
        uw.candles = original
    assert [b["date"] for b in bars] == sorted(b["date"] for b in bars)


def test_a_malformed_row_does_not_discard_the_series(monkeypatch):
    _stub(monkeypatch, [
        {"date": "2026-09-22", "close": "220.5", "open": "220",
         "high": "221", "low": "219", "volume": 1},
        {"date": "", "close": "1"},
        {"date": "2026-09-23", "close": None},
    ])
    bars = uw.get_daily_bars("NVDA", "1 M")
    assert len(bars) == 1 and bars[0]["date"] == "2026-09-22"


def test_a_failed_call_is_no_market_data_not_zero(monkeypatch):
    monkeypatch.setattr(uw, "candles", lambda *a, **k: {
        "status": "RATE_LIMITED", "data": None, "source": uw.SOURCE})
    assert uw.get_daily_bars("NVDA", "1 Y") == []
    assert uw.get_intraday_bars("NVDA", "5m") == []


def test_enough_rows_are_asked_for_to_fill_the_days_wanted(monkeypatch):
    asked = {}

    def candles(s, size="1d", limit=300, date=""):
        asked["limit"] = limit
        return {"status": "OK", "data": [], "source": uw.SOURCE}

    monkeypatch.setattr(uw, "candles", candles)
    uw.get_daily_bars("NVDA", "1 Y")
    assert asked["limit"] >= 252 * 2, \
        "one date can send three rows; ask for room or lose the history"


def test_intraday_bars_keep_their_timestamps(monkeypatch):
    _stub(monkeypatch, [
        {"start_time": "2026-09-23T17:35:00Z", "open": "224.7", "high": "224.72",
         "low": "224.45", "close": "224.615", "volume": 274683},
        {"start_time": "2026-09-23T17:30:00Z", "open": "224.44", "high": "224.75",
         "low": "224.37", "close": "224.68", "volume": 333534},
    ])
    bars = uw.get_intraday_bars("NVDA", "5m", 1)
    assert [b["t"] for b in bars] == ["2026-09-23T17:30:00Z",
                                      "2026-09-23T17:35:00Z"]
    assert bars[0]["volume"] == 333534
