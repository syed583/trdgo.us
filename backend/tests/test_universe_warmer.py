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


def test_get_polls_a_loading_build_when_asked_to_wait(monkeypatch):
    import swr
    answers = iter([{"status": "LOADING"}, {"status": "LOADING"}, {"rows": [{"ticker": "AAPL"}]}])
    monkeypatch.setattr(swr, "serve", lambda k, fn, ttl: next(answers))
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert us.get(wait=60)["rows"][0]["ticker"] == "AAPL"


def test_get_does_not_block_without_wait(monkeypatch):
    import swr
    monkeypatch.setattr(swr, "serve", lambda k, fn, ttl: {"status": "LOADING"})
    assert us.get() is None


def test_build_raises_instead_of_returning_none(monkeypatch):
    import unusualwhales_service as uw
    import pytest
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr(uw, "get", lambda p, params=None: {"status": "RATE_LIMITED", "data": None})
    with pytest.raises(RuntimeError):
        us.build()


def test_cached_none_is_repaired_by_a_direct_rebuild(monkeypatch):
    import swr
    good = {"rows": [{"ticker": "AAPL"}], "count": 1}
    stored = {}
    monkeypatch.setattr(swr, "serve", lambda k, fn, ttl: None)          # the poisoned-cache behaviour
    monkeypatch.setattr(us, "build", lambda: good)
    monkeypatch.setattr(swr, "_run", lambda k, fn: stored.setdefault(k, fn()))
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert us.get(wait=30) == good and stored["universe:top1000"] == good


def test_warm_one_forces_a_recompute_even_if_a_value_is_stored(monkeypatch):
    import swr
    import earnings_equity_service as eq
    import earnings_trade_service as ets
    calls = []
    monkeypatch.setattr(eq, "get_analysis", lambda s: calls.append("eq") or {"params": [{"name": "x"}]})
    monkeypatch.setattr(ets, "get_summary", lambda s, pf: calls.append("tr") or {"ok": 1})
    store = {}
    monkeypatch.setattr(swr, "_run", lambda k, fn: store.setdefault(k, fn()) if False else store.__setitem__(k, fn()) or store[k])
    monkeypatch.setattr(swr, "peek", lambda k: store.get(k))
    import gemini_service as gm
    monkeypatch.setattr(gm, "hit_rate_bias", lambda *a, **k: None)
    assert lw._warm_one("ZZZ") is True and calls == ["eq", "tr"]


def test_force_ignores_a_loading_or_error_build(monkeypatch):
    import swr
    store = {}
    monkeypatch.setattr(swr, "_run", lambda k, fn: store.__setitem__(k, fn()))
    lw._force("k1", lambda: {"status": "LOADING"})
    lw._force("k2", lambda: None)
    assert store == {}


def test_sync_counter_counts_all_in_process_traffic_and_handles_day_rollover(monkeypatch):
    import unusualwhales_service as uw
    monkeypatch.setattr(lw, "_seen", {"day": None, "n": 0})
    monkeypatch.setattr(lw, "_state", {"day_id": lw.day_id(), "calls_day": 100})
    monkeypatch.setitem(uw._spent, "day", "D1")
    monkeypatch.setitem(uw._spent, "count", 50)
    lw.sync_counter()                                    # first sample adds nothing
    assert lw._state["calls_day"] == 100
    uw._spent["count"] = 80                              # 30 more calls, from anywhere in the process
    lw.sync_counter()
    assert lw._state["calls_day"] == 130
    uw._spent["day"], uw._spent["count"] = "D2", 12      # provider day rolled over: counter restarted
    lw.sync_counter()
    assert lw._state["calls_day"] == 142
