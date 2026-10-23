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
    import uw_priority
    t = uw_priority.Tracker()
    lw._force("k1", lambda: {"status": "LOADING"}, t)
    lw._force("k2", lambda: None, t)
    assert store == {}
    t.denied = 1                                                    # a refused request also blocks the store
    assert lw._force("k3", lambda: {"status": "OK", "params": [1]}, t) is False and store == {}




def test_production_mode_uses_the_production_state_row_and_daily_refresh_age():
    import subprocess
    import sys
    out = subprocess.run(
        [sys.executable, "-c", "import local_warmer as lw; print(lw.is_production(), lw.MAX_AGE/3600, lw._row_id())"],
        capture_output=True, text=True, env={**__import__("os").environ, "APP_ENV": "production"},
        cwd=str(__import__("pathlib").Path(lw.__file__).parent)).stdout.split()
    assert out == ["True", "20.0", "2"]


# ---- the warmer obeys the shared budget -----------------------------------------------------
import uw_budget as _bud  # noqa: E402
import uw_priority as _pri  # noqa: E402

_S = {"daily_limit": 100, "emergency_reserve": 10, "intraday_budget": 60, "other_budget": 15,
      "warmer_budget": 20, "warmer_hourly_cap": 8, "reset_hour_et": 20,
      "dashboard_reading_max_age_s": 6 * 3600, "provider_headers_max_age_s": 600,
      "cooldown_429_s": {3: 120, 4: 900}}


class _StopLoop(Exception):
    pass


def _run_paused(monkeypatch, controller):
    _bud.reset_for_tests(controller)
    monkeypatch.setattr(lw, "build_queue", lambda: (["AAA"], 1))
    monkeypatch.setattr(lw, "warm_ages", lambda syms: {})
    monkeypatch.setattr(lw, "save_state", lambda: None)
    monkeypatch.setattr(lw, "load_state", lambda: {})
    monkeypatch.setattr(lw, "_state", {"status": "IDLE"})
    calls = []
    monkeypatch.setattr(lw, "_warm_one", lambda sym: calls.append(sym) or True)
    sleeps = []

    def fake_sleep(s):
        sleeps.append(s)
        if len(sleeps) >= 2:
            raise _StopLoop

    monkeypatch.setattr(lw.time, "sleep", fake_sleep)
    try:
        lw._run_once()
    except _StopLoop:
        pass
    return calls, sleeps


def test_warmer_stays_paused_while_the_quota_is_unverified(monkeypatch):
    c = _bud.Controller(_S, persist=False)
    calls, sleeps = _run_paused(monkeypatch, c)
    assert calls == []                                          # it never warmed anything
    assert lw._state["status"] == "PAUSED" and "UNVERIFIED_QUOTA" in lw._state["pause_reason"]
    assert max(sleeps) <= 300                                   # re-checks soon; not asleep until midnight
    _bud.reset_for_tests()


def test_warmer_pauses_when_intraday_needs_the_capacity_and_resumes_when_permitted(monkeypatch):
    c = _bud.Controller(_S, persist=False)
    c.record_dashboard_usage(35)                                # 65 left <= reserve 10 + intraday still owed 60
    calls, _ = _run_paused(monkeypatch, c)
    assert calls == [] and "YIELD_TO_INTRADAY" in lw._state["pause_reason"]
    c2 = _bud.Controller(_S, persist=False)
    c2.record_dashboard_usage(0)                                # plenty of room: the warmer may run
    _bud.reset_for_tests(c2)
    monkeypatch.setattr(lw, "_warm_one", lambda sym: True)
    monkeypatch.setattr(lw, "build_queue", lambda: (["AAA", "BBB"], 2))
    monkeypatch.setattr(lw, "warm_ages", lambda syms: {})
    monkeypatch.setattr(lw, "save_state", lambda: None)
    monkeypatch.setattr(lw, "_state", {"status": "IDLE"})
    assert lw._run_once() == 0 and lw._state["status"] == "DONE" and lw._state["done"] == 2
    _bud.reset_for_tests()


def test_a_warm_that_was_partly_refused_is_discarded_not_stored(monkeypatch):
    import earnings_equity_service as eqsvc
    import earnings_trade_service as ets
    import swr
    import unusualwhales_service as uw
    swr.clear()
    c = _bud.reset_for_tests(_bud.Controller(_S, persist=False))        # unverified: warmer requests refused
    monkeypatch.setattr(uw, "configured", lambda: True)

    def build(sym):
        uw.get("/api/stock/AAA/options-volume")                          # refused for the warmer
        return {"status": "OK", "params": [{"name": "x"}]}

    monkeypatch.setattr(eqsvc, "get_analysis", build)
    monkeypatch.setattr(ets, "get_summary", lambda s, p: {"status": "OK"})
    assert lw._warm_one("AAA") is None                                  # deferred, not "done"
    assert swr.peek("earn:equity:AAA") is None                          # nothing half-built was stored
    assert c.snapshot()["modules"]["warmer"]["denied"] >= 1
