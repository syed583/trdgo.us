from datetime import datetime
from zoneinfo import ZoneInfo

import local_warmer as lw
import universe_service as us

ET = ZoneInfo("America/New_York")


def test_restrict_keeps_only_universe(monkeypatch):
    monkeypatch.setattr(us, "symbols", lambda: {"AAPL", "MSFT"})
    rows = [{"symbol": "aapl"}, {"symbol": "ZZZZ"}, {"symbol": "MSFT"}]
    assert [r["symbol"] for r in us.restrict(rows)] == ["aapl", "MSFT"]


def test_restrict_fails_open_when_universe_unavailable(monkeypatch):
    monkeypatch.setattr(us, "symbols", lambda: None)
    rows = [{"symbol": "AAPL"}, {"symbol": "ZZZZ"}]
    assert us.restrict(rows) == rows


def test_build_ranks_by_dollar_volume(monkeypatch):
    import unusualwhales_service as uw
    rows = [{"ticker": f"T{i}", "avg30_volume": str(1000 * (i + 1)), "close": "10"} for i in range(150)]
    rows.append({"ticker": "T0", "avg30_volume": "1", "close": "1"})            # duplicate, smaller
    monkeypatch.setattr(uw, "get", lambda p, params=None: {"status": "OK", "data": rows if params["offset"] == 0 else []})
    monkeypatch.setattr(uw, "_rows", lambda out: out["data"])
    u = us.build()
    assert u["count"] == 150 and u["rows"][0]["ticker"] == "T149" and u["rows"][0]["rank"] == 1


def test_next_reset_is_8pm_eastern():
    assert datetime.fromtimestamp(lw.next_reset_epoch(datetime(2026, 10, 22, 19, 0, tzinfo=ET)), ET).strftime("%d %H") == "22 20"
    assert datetime.fromtimestamp(lw.next_reset_epoch(datetime(2026, 10, 22, 21, 0, tzinfo=ET)), ET).strftime("%d %H") == "23 20"


def test_ten_trading_day_window_skips_weekends():
    from datetime import datetime as dt
    assert lw.trading_days_window(10, dt(2026, 10, 19)) == 14      # Monday -> two calendar weeks
    assert lw.trading_days_window(10, dt(2026, 10, 22)) == 14      # Thursday
    assert lw.trading_days_window(1, dt(2026, 10, 23)) == 3        # Friday -> next Monday


def test_production_mode_limits(monkeypatch):
    import subprocess
    import sys
    out = subprocess.run(
        [sys.executable, "-c",
         "import local_warmer as lw; print(lw.is_production(), lw.DAILY_LIMIT, lw.RESERVE, lw.MAX_AGE/3600, lw._row_id())"],
        capture_output=True, text=True, env={**__import__("os").environ, "APP_ENV": "production"},
        cwd=str(__import__("pathlib").Path(lw.__file__).parent)).stdout.split()
    assert out == ["True", "13000", "1500", "20.0", "2"]


def test_provider_counter_is_capped_at_13000_in_production(monkeypatch):
    import unusualwhales_service as uw
    monkeypatch.setattr(lw, "DAILY_LIMIT", 13000)
    monkeypatch.setattr(uw, "budget", lambda: {"daily_limit_known": True, "app_budget": 15000,
                                               "used_by_this_app": 12000, "app_left": 3000,
                                               "blocked": False, "blocked_for_seconds": 0})
    b = lw._budget()
    assert b["app_budget"] == 13000 and b["app_left"] == 1000        # not the plan's 15,000
