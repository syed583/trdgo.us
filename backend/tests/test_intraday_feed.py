"""Phase 2: fresh inputs for the six AI boards, judged per kind of data; dashboard health fields.
Everything is mocked: no API calls, no production database."""
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import intraday_feed as feed
import intraday_safety as safety
import swr
import tab_settings as cfg
import uw_budget as bud
import uw_priority as pri

NOW = 1_760_000_000.0
FAST_ALL = {n: 0.8 for n in feed.FAST_SIGNALS}


def snapshot(**sig):
    return {"params": [{"name": "eps_estimates", "bias": 0.5}], "signals": dict(sig)}


@pytest.fixture(autouse=True)
def _clean():
    swr.clear()
    yield
    swr.clear()


def put_feed(symbol, signals, age_s, now=NOW):
    swr._values[feed.KEY.format(symbol)] = (now, {"symbol": symbol, "signals": dict(signals), "ts": now - age_s})


# ------------------------------------------------------ freshness by kind of data ---
def test_limits_are_per_kind_of_data_and_session_phase():
    live, mx = feed.limits_for("options_flow", "rth")
    assert (live, mx) == cfg.INTRADAY_FRESHNESS["options_flow"]
    assert feed.limits_for("options_flow", "closed")[1] == mx * 36          # relaxed when the market is shut
    ev = feed.limits_for("event_radar", "closed")
    assert ev == cfg.INTRADAY_FRESHNESS["earnings_fundamentals"]            # earnings data: not scaled, not 10 min
    assert feed.limits_for("insider_activity", "rth")[1] == 7 * 86400


def test_slow_data_is_not_held_to_a_ten_minute_rule():
    snap = snapshot(event_radar=0.3, insider_activity=0.2, options_flow=0.9)
    out = feed.effective_inputs("AAA", snap, snapshot_age_s=5 * 3600, phase="rth", now=NOW)
    assert out["signals"]["event_radar"] == 0.3 and out["signals"]["insider_activity"] == 0.2
    assert out["meta"]["event_radar"]["status"] == "LIVE"                   # 5 h is fine for earnings data
    assert "options_flow" in out["dropped"]                                 # 5 h is stale for options flow


def test_ancient_slow_data_is_dropped_too_not_neutralised():
    snap = snapshot(event_radar=0.3, insider_activity=0.2)
    out = feed.effective_inputs("AAA", snap, snapshot_age_s=60 * 3600, phase="rth", now=NOW)
    assert "event_radar" not in out["signals"] and "event_radar" in out["dropped"]    # past 48 h
    assert out["signals"].get("insider_activity") == 0.2                             # 7 days limit


def test_a_fresh_feed_replaces_a_stale_snapshot_for_time_sensitive_signals():
    put_feed("AAA", FAST_ALL, age_s=60)
    snap = snapshot(event_radar=0.3, options_flow=-0.9, rsi=-0.9)           # old, opposite readings
    out = feed.effective_inputs("AAA", snap, snapshot_age_s=30 * 3600, phase="rth", now=NOW)
    assert out["signals"]["options_flow"] == 0.8 and out["signals"]["rsi"] == 0.8      # the feed wins
    assert out["meta"]["options_flow"]["source"] == "intraday feed"
    assert out["missing_critical"] == []
    assert out["signals"]["event_radar"] == 0.3                              # slow signal still from the snapshot


def test_feed_readings_age_out_by_their_own_limit():
    live, mx = cfg.INTRADAY_FRESHNESS["options_flow"]
    put_feed("AAA", {"options_flow": 0.8, "ema_trend": 0.8}, age_s=live + 60)
    out = feed.effective_inputs("AAA", snapshot(), None, "rth", now=NOW)
    assert out["meta"]["options_flow"]["status"] == "DELAYED" and "options_flow" in out["signals"]
    put_feed("AAA", {"options_flow": 0.8, "ema_trend": 0.8}, age_s=mx + 60)
    out = feed.effective_inputs("AAA", snapshot(), None, "rth", now=NOW)
    assert out["meta"]["options_flow"]["status"] == "STALE" and "options_flow" not in out["signals"]
    assert "options flow" in out["missing_critical"]


def test_missing_critical_data_is_reported_and_nothing_is_invented():
    put_feed("AAA", {"unusual_activity": 0.8}, age_s=30)                    # flow yes, price action no
    out = feed.effective_inputs("AAA", snapshot(), None, "rth", now=NOW)
    assert out["missing_critical"] == ["price action (daily bars + live bar)"]
    assert "rsi" not in out["signals"] and "ema_trend" not in out["signals"]   # absent, not 0 / bullish / bearish


def test_newest_reading_of_each_signal_wins_across_sources():
    put_feed("AAA", {"options_flow": 0.1}, age_s=500)
    out = feed.effective_inputs("AAA", snapshot(), None, "rth", now=NOW,
                                live=({"options_flow": 0.7}, 20.0))
    assert out["signals"]["options_flow"] == 0.7 and out["meta"]["options_flow"]["source"] == "live directional"


# ---------------------------------------------------------------- the feeder ----
def test_feed_cycle_refreshes_stalest_first_within_its_cap_and_skips_fresh_symbols():
    put_feed("FRESH", FAST_ALL, age_s=10)
    put_feed("OLD", FAST_ALL, age_s=900)
    put_feed("OLDER", FAST_ALL, age_s=3000)
    calls = []

    def fake(sym):
        calls.append(sym)
        return {"symbol": sym, "signals": dict(FAST_ALL), "ts": NOW}

    c = bud.reset_for_tests(bud.Controller(persist=False))
    out = feed.feed_cycle(["FRESH", "OLD", "OLDER", "NEVER"], "rth", max_symbols=2, now=NOW, compute=fake)
    assert calls == ["NEVER", "OLDER"]                                      # never-built, then the stalest; cap 2
    assert out["refreshed"] == ["NEVER", "OLDER"] and "FRESH" not in calls and out["due"] == 3


def test_feed_cycle_stops_when_the_shared_budget_says_no():
    calls = []
    c = bud.Controller({"daily_limit": 100, "emergency_reserve": 10, "intraday_budget": 60, "other_budget": 15,
                        "warmer_budget": 20, "warmer_hourly_cap": 8, "reset_hour_et": 20,
                        "dashboard_reading_max_age_s": 6 * 3600, "provider_headers_max_age_s": 600,
                        "cooldown_429_s": {3: 120, 4: 900}}, persist=False)
    c.record_dashboard_usage(95)                                            # inside the emergency reserve
    bud.reset_for_tests(c)
    out = feed.feed_cycle(["A", "B", "C"], "rth", now=NOW, compute=lambda s: calls.append(s))
    assert calls == [] and out["skipped"] and out["skipped"][0][1] == "RESERVE"


def test_a_thin_or_failed_refresh_is_not_stored():
    c = bud.reset_for_tests(bud.Controller(persist=False))
    out = feed.feed_cycle(["A", "B"], "rth", now=NOW, compute=lambda s: None if s == "A" else
                          {"symbol": s, "signals": dict(FAST_ALL), "ts": NOW})
    assert out["failed"] == ["A"] and out["refreshed"] == ["B"]
    assert feed.read("A") is None and feed.read("B") is not None


def test_compute_fast_uses_the_same_signal_functions_and_rejects_thin_answers(monkeypatch):
    import directional_score_service as dss
    import disparity_service
    import live_market_service as market
    import live_options_analytics as options
    import uw_flow_service as odflow
    monkeypatch.setattr(options, "get_overview", lambda s: {})
    monkeypatch.setattr(odflow, "daily_oi_change", lambda s: {})
    monkeypatch.setattr(disparity_service, "get_disparity", lambda s: {})
    monkeypatch.setattr(market, "_fallback_bars", lambda s, d: ([], None))
    seen = []
    monkeypatch.setattr(dss, "build_signals", lambda data: seen.append(data) or [
        {"name": n, "bias": 0.4, "available": True} for n in feed.FAST_SIGNALS[:2]] + [
        {"name": "event_radar", "bias": 0.9, "available": True}])
    assert feed.compute_fast("AAA") is None                                 # 2 of 13 fast signals: too thin
    monkeypatch.setattr(dss, "build_signals", lambda data: [
        {"name": n, "bias": 0.4, "available": True} for n in feed.FAST_SIGNALS] + [
        {"name": "event_radar", "bias": 0.9, "available": True}])
    got = feed.compute_fast("AAA")
    assert set(got["signals"]) == set(feed.FAST_SIGNALS) and "event_radar" not in got["signals"]


def test_the_feed_runs_at_essential_priority(monkeypatch):
    import directional_score_service as dss
    import disparity_service
    import live_market_service as market
    import live_options_analytics as options
    import uw_flow_service as odflow
    seen = []
    monkeypatch.setattr(options, "get_overview", lambda s: seen.append(pri.current()[:2]) or {})
    monkeypatch.setattr(odflow, "daily_oi_change", lambda s: {})
    monkeypatch.setattr(disparity_service, "get_disparity", lambda s: {})
    monkeypatch.setattr(market, "_fallback_bars", lambda s, d: ([], None))
    monkeypatch.setattr(dss, "build_signals", lambda data: [])
    feed.compute_fast("AAA")
    assert seen == [(pri.ESSENTIAL, "ai_boards")]


# ------------------------------------------------- the six boards on the feed -----
def _boards():
    import gemini_intraday_service as gi
    import intraday_grok_service as ig
    import kimi_intraday_service as kin
    import meta_intraday_service as mi
    return [("grok", ig.board), ("meta", mi.board), ("kimi", kin.board), ("gemini", gi.board)]


def _prep(monkeypatch, bias, snapshot_age, with_feed, phase="rth"):
    import ai_trade_service as ats
    import intraday_grok_service as ig
    import intraday_indicators as ind
    import kimi_intraday_service as kin
    swr.clear()
    monkeypatch.setattr(ats, "UNIVERSE", ["AAA", "BBB"])
    monkeypatch.setattr(ind, "market_phase", lambda *a, **k: phase)
    monkeypatch.setattr(safety, "live_signals", lambda s: None)
    monkeypatch.setattr(safety, "age_s", lambda key: 20.0 if key.startswith("grokintra:px") else snapshot_age)
    names = sorted(set(cfg.SIGNAL_DATA_TYPE) | {"oi_positioning"})
    e = {"params": [{"name": n, "bias": bias} for n in (
        "eps_estimates", "revenue_estimates", "guidance", "price_action", "options_positioning",
        "unusual_options", "disparity", "dark_pool", "peer_readthrough", "sector_market")],
        "signals": {n: bias for n in names}}
    for mod in (ig, kin):
        monkeypatch.setattr(mod, "_eq_cached", lambda s, e=e: e)
        monkeypatch.setattr(mod, "_price_atr", lambda s: (100.0, 2.0))
    if with_feed:
        real = time.time()
        for s in ("AAA", "BBB"):
            swr._values[feed.KEY.format(s)] = (real, {"symbol": s, "ts": real - 60,
                                                      "signals": {n: bias for n in feed.FAST_SIGNALS}})


@pytest.mark.parametrize("idx", [0, 1, 2, 3])
def test_stale_snapshot_plus_fresh_feed_gives_signals_on_both_sides(monkeypatch, idx):
    name, board = _boards()[idx]
    _prep(monkeypatch, +0.9, 40 * 3600.0, with_feed=True)
    up = board()
    _prep(monkeypatch, -0.9, 40 * 3600.0, with_feed=True)
    down = board()
    assert up["counts"]["buy"] == 2 and up["counts"]["wait"] == 0, name
    assert down["counts"]["sell"] == 2 and down["counts"]["wait"] == 0, name
    h = up["buyers"][0]["health"]
    assert h["eligible_for_new_entries"] is True and h["missing_critical"] == []


@pytest.mark.parametrize("idx", [0, 1, 2, 3])
def test_without_a_fresh_feed_the_same_stale_snapshot_gives_wait(monkeypatch, idx):
    name, board = _boards()[idx]
    for bias in (+0.9, -0.9):
        _prep(monkeypatch, bias, 40 * 3600.0, with_feed=False)
        out = board()
        assert out["counts"]["buy"] == out["counts"]["sell"] == 0 and out["counts"]["wait"] == 2, (name, bias)
        w = out["wait"][0]
        assert w["signal"] == "WAIT" and w["score"] is None
        assert any("options flow" in r for r in w["wait_reasons"]), w["wait_reasons"]
        assert w["health"]["eligible_for_new_entries"] is False and w["health"]["missing_critical"]


def test_six_boards_share_one_feed_and_make_no_api_calls_themselves(monkeypatch):
    """Before: each model had to refresh its own inputs. Now one cycle serves all six."""
    import chatgpt_intraday_service as cg
    import claude_pro_intraday_service as cp
    import intraday_grok_service as ig
    _prep(monkeypatch, +0.9, 40 * 3600.0, with_feed=True, phase="closed")
    monkeypatch.setattr(ig, "_eq_cached", ig._eq_cached)
    fetches = []
    import unusualwhales_service as uw
    monkeypatch.setattr(uw, "get", lambda *a, **k: fetches.append(a) or {"status": "OK", "data": []})
    for _name, board in _boards():
        board()
    cg.board()
    cp.board()
    # The boards read the feed (a cache read): no per-symbol request at all. The only requests are the
    # market-wide breadth reads (one tape for every symbol and every board, shared by the memo).
    assert all("/api/stock/" not in f[0] and "AAA" not in str(f) and "BBB" not in str(f) for f in fetches), fetches
    assert len(fetches) <= 6
    computed = []
    put = lambda s: computed.append(s) or {"symbol": s, "signals": dict(FAST_ALL), "ts": time.time()}  # noqa: E731
    for s in ("AAA", "BBB"):
        swr._values.pop(feed.KEY.format(s), None)
    bud.reset_for_tests(bud.Controller(persist=False))
    feed.feed_cycle(["AAA", "BBB"], "rth", compute=put)
    assert sorted(computed) == ["AAA", "BBB"]                               # one refresh per symbol, not six


# ------------------------------------------------------------- dashboard health ---
def _row(now=100.0):
    return {"symbol": "AAA", "signal": "BUY", "score": 70.0, "coverage": 100.0, "now": now, "entry": now,
            "params": []}


def test_health_is_live_only_when_the_data_is_young_and_the_api_is_healthy(monkeypatch):
    c = bud.reset_for_tests(bud.Controller(persist=False))
    inputs = feed.effective_inputs("AAA", snapshot(), None, "rth", now=time.time(),
                                   live=({n: 0.5 for n in feed.FAST_SIGNALS}, 30.0))
    r = safety.gate(_row(), phase="rth", signals_age_s=30.0, price_age=20.0, meta=inputs)
    h = r["health"]
    assert h["status"] == "LIVE" and h["api_health"] == "OK" and h["eligible_for_new_entries"]
    assert h["display_tz"] == "Asia/Dubai" and h["signal_generated_dubai"].endswith("GST")
    assert abs(h["market_data_at"] - (time.time() - 30)) < 5               # the data's own timestamp
    c.record_call(pri.LIVE, "intraday_engine", "429")                      # the API is rate limiting ...
    r2 = safety.gate(_row(), phase="rth", signals_age_s=30.0, price_age=20.0, meta=inputs)
    assert r2["health"]["status"] == "DELAYED" and r2["health"]["api_health"] == "RATE_LIMITED"   # ... so not LIVE


def test_an_http_200_does_not_make_old_data_live():
    live, mx = cfg.INTRADAY_FRESHNESS["options_flow"]
    inputs = feed.effective_inputs("AAA", snapshot(), None, "rth", now=time.time(),
                                   live=({"options_flow": 0.5, "ema_trend": 0.5}, mx + 300))
    r = safety.gate(_row(), phase="rth", signals_age_s=mx + 300, price_age=20.0, meta=inputs)
    assert r["health"]["status"] == "STALE" and r["signal"] == "WAIT"
    assert any("options flow" in m for m in r["health"]["missing_critical"])


def test_health_statuses_delayed_and_missing(monkeypatch):
    bud.reset_for_tests(bud.Controller(persist=False))
    live, mx = cfg.INTRADAY_FRESHNESS["options_flow"]
    mid = feed.effective_inputs("AAA", snapshot(), None, "rth", now=time.time(),
                                live=({"options_flow": 0.5, "ema_trend": 0.5}, live + 100))
    r = safety.gate(_row(), phase="rth", signals_age_s=live + 100, price_age=20.0, meta=mid)
    assert r["health"]["status"] == "DELAYED" and r["signal"] == "BUY"      # usable, but not called LIVE
    gone = safety.gate(_row(now=None), phase="rth", signals_age_s=30.0, price_age=None, meta=mid)
    assert gone["health"]["status"] == "MISSING" and "live price" in gone["health"]["missing_critical"]
    assert gone["signal"] == "WAIT"


def test_the_live_engine_path_reports_its_actual_timeframe():
    r = safety.gate(_row(), phase="rth", signals_age_s=40.0, price_age=40.0, model_status="OK")
    assert r["health"]["status"] == "LIVE" and "5-minute" in r["health"]["timeframe"]
    assert "no 15-minute" in r["health"]["timeframe"]                        # never claims a 15-minute engine
    stale = safety.gate(_row(), phase="rth", signals_age_s=900.0, price_age=40.0, model_status="OK")
    assert stale["health"]["status"] == "STALE" and stale["signal"] == "WAIT"
    unhealthy = safety.gate(_row(), phase="rth", signals_age_s=40.0, price_age=40.0, model_status="MARKET_STALE")
    assert unhealthy["health"]["status"] == "STALE" and unhealthy["signal"] == "WAIT"


def test_timestamps_are_shown_in_dubai_time():
    assert safety.dubai(NOW) == datetime.fromtimestamp(NOW, ZoneInfo("Asia/Dubai")).strftime("%d %b %H:%M:%S GST")
    assert safety.dubai(1_760_000_000.0) == "09 Oct 12:53:20 GST"           # 08:53:20 UTC + 4 h, no DST
    assert safety.dubai(None) is None


# ---------------------------------------------------------- operator readings -----
def test_the_running_service_picks_up_a_dashboard_reading_and_the_pause_switch(monkeypatch):
    import db_cache
    clk = [1_760_000_000.0]
    c = bud.Controller({"daily_limit": 100, "emergency_reserve": 10, "intraday_budget": 60, "other_budget": 15,
                        "warmer_budget": 20, "warmer_hourly_cap": 8, "reset_hour_et": 20,
                        "dashboard_reading_max_age_s": 6 * 3600, "provider_headers_max_age_s": 600,
                        "cooldown_429_s": {3: 120, 4: 900}}, now=lambda: clk[0], persist=True)
    monkeypatch.setattr(c, "_save", lambda: None)
    store = {"uwbudget:dashboard": (clk[0], {"used": 5, "at": clk[0] - 10, "day": c.state["day"]})}
    monkeypatch.setattr(db_cache, "get", lambda k: store.get(k))
    assert c.admit(pri.WARMER, "warmer").ok is True                        # reading found -> VERIFIED, warmer may run
    assert c.quota_state() == "VERIFIED_DASHBOARD" and c.used_total() == 5
    clk[0] += 60
    store["uwbudget:control"] = (clk[0], {"paused": True})
    assert c.admit(pri.WARMER, "warmer").code == "PAUSED"                  # operator pause applies within 30 s
    store["uwbudget:dashboard"] = (clk[0], {"used": 9, "at": clk[0], "day": "1999-01-01"})   # an old day's reading
    clk[0] += 60
    store["uwbudget:control"] = (clk[0], {"paused": False})
    c.state["dashboard"] = None
    assert c.admit(pri.WARMER, "warmer").code == "UNVERIFIED_QUOTA"        # a previous day's reading is ignored
