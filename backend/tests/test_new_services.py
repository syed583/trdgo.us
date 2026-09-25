"""
Tests for the services added in the application phase.

Everything here is offline: the backtest's point-in-time guarantee, strategy
validation and the provider-status vocabulary. The network round trips
themselves are covered by the live verification run, not by unit tests that
would need one.

The news-text tests that used to open this file went with the IBKR news
wire: they covered the routing-code stripping and entity unescaping that its
payloads needed, and the feed that replaced it sends neither.
"""

import pytest

import backtest_service as bt
import provider_health as health
import workspace_service as ws


# ------------------------------------------------------------- backtesting


def _bars(closes):
    """
    Synthetic OHLCV with a deterministic intrabar range.

    Dates must increase monotonically across the whole series - an earlier
    version wrapped inside one month, which silently made every date
    comparison in these tests meaningless.
    """
    from datetime import date, timedelta

    start = date(2024, 1, 1)
    return [
        {
            "date": (start + timedelta(days=i)).isoformat(),
            "open": c, "high": c * 1.01, "low": c * 0.99,
            "close": c, "volume": 1_000_000,
        }
        for i, c in enumerate(closes)
    ]


def test_composite_needs_enough_history():
    assert bt._composite(_bars([100] * 30)) is None


def test_composite_is_high_in_a_clean_uptrend():
    closes = [50 + i * 0.5 for i in range(260)]
    score = bt._composite(_bars(closes))
    assert score is not None
    assert score > 70


def test_composite_is_low_in_a_downtrend():
    closes = [200 - i * 0.5 for i in range(260)]
    score = bt._composite(_bars(closes))
    assert score is not None
    assert score < 35


def test_composite_only_sees_the_bars_it_is_given():
    """
    The point-in-time guarantee: a later crash must not change an earlier
    signal. Same prefix in, same score out.
    """
    rising = [50 + i * 0.5 for i in range(260)]
    early = bt._composite(_bars(rising))

    crashed = rising + [10] * 40
    same_prefix = bt._composite(_bars(crashed[:260]))

    assert early == same_prefix


def test_trades_enter_on_the_bar_after_the_signal():
    closes = [50 + i * 0.5 for i in range(300)]
    bars = _bars(closes)
    trades = bt._run_symbol("TEST", bars, 60, "LONG", 10, 5.0, 10.0, None, None)
    assert trades
    for t in trades:
        # Entry is strictly after the signal bar, never on it.
        assert t["entry_date"] >= t["signal_date"]
        assert t["exit_date"] >= t["entry_date"]


def test_trades_do_not_overlap_in_one_symbol():
    closes = [50 + i * 0.5 for i in range(300)]
    trades = bt._run_symbol("TEST", _bars(closes), 60, "LONG", 10, 5.0, 10.0,
                            None, None)
    for a, b in zip(trades, trades[1:]):
        assert b["signal_date"] >= a["exit_date"]


def test_methodology_names_what_cannot_be_replayed():
    m = bt._methodology(0)
    assert "technicals" in m["replayable_components"]
    for field in ("fundamentals", "estimates", "options"):
        assert field in m["not_replayable"]


def test_backtest_rejects_an_empty_universe():
    assert bt.run_backtest([])["status"] == "INVALID"


# ------------------------------------------------------- strategy settings


def test_strategy_defaults_expose_every_documented_key():
    config = ws.get_strategy()
    for key in ("min_score_long", "min_confidence", "max_risk_per_trade_pct",
                "allow_long", "allow_short"):
        assert key in config["values"]
    assert config["status"] == "OK"


def test_strategy_component_weights_sum_to_one_hundred_by_default():
    values = ws.STRATEGY_DEFAULTS
    total = sum(v for k, v in values.items() if k.startswith("weight_"))
    assert total == 100


def test_strategy_rejects_out_of_range_and_unknown_keys():
    out = ws.update_strategy({"min_confidence": 500, "not_a_setting": 1})
    assert "min_confidence" in out["rejected"]
    assert "not_a_setting" in out["rejected"]
    assert not out["applied"]


def test_strategy_rejects_non_numeric_values():
    out = ws.update_strategy({"min_confidence": "abc"})
    assert out["rejected"]["min_confidence"] == "not a number"


def test_strategy_round_trips_a_valid_change():
    try:
        applied = ws.update_strategy({"min_confidence": 55})
        assert applied["applied"]["min_confidence"] == 55
        assert ws.get_strategy()["values"]["min_confidence"] == 55
    finally:
        ws.reset_strategy()


# ----------------------------------------------------------- alert config


def test_alert_kinds_are_the_ones_the_evaluator_handles():
    """Every offered kind must have a branch in evaluate_alerts."""
    import inspect
    source = inspect.getsource(ws.evaluate_alerts)
    for kind in ws.ALERT_KINDS:
        assert kind in source, f"{kind} is offered but never evaluated"


def test_alert_rejects_unknown_kind_and_comparator():
    assert ws.create_alert("NVDA", "NOPE", ">=", 1)["status"] == "INVALID"
    assert ws.create_alert("NVDA", "PRICE", "!!", 1)["status"] == "INVALID"


# ------------------------------------------------------ status vocabulary


def test_provider_status_constants_are_distinct():
    values = [health.OK, health.PROVIDER_OFFLINE, health.ENTITLEMENT_REQUIRED,
              health.DATA_UNAVAILABLE, health.TEST_DATA]
    assert len(set(values)) == len(values)


def test_database_probe_reports_a_status_either_way():
    out = health._database()
    assert out["status"] in (health.OK, health.PROVIDER_OFFLINE)
    assert "detail" in out


# --- market pulse: bounded, shared, and honest about a short read ----------

def test_pulse_quotes_are_bounded_by_a_deadline(monkeypatch):
    """
    Seventy provider round trips cannot hold a page open indefinitely. What
    answers inside the budget is what gets measured.
    """
    import time

    import live_market_service as market
    import market_pulse_service as pulse

    def slow(symbol):
        if symbol.startswith("SLOW"):
            time.sleep(5.0)
        return {"price": 10.0, "change": 0.1, "change_percent": 1.0,
                "source": "TEST"}

    monkeypatch.setattr(market, "_fallback_quote", slow)
    monkeypatch.setattr(pulse, "QUOTES_BUDGET", 0.6)

    started = time.time()
    out = pulse._quotes(["AAA", "BBB", "SLOW1", "SLOW2"])
    elapsed = time.time() - started

    assert elapsed < 3.0, "the deadline must actually end the wait"
    assert "AAA" in out and "BBB" in out
    assert "SLOW1" not in out


def test_provider_quotes_are_cached_briefly(monkeypatch):
    """
    The market overview asks for seventy quotes a minute. Going back to the
    network for a figure fetched seconds ago is most of why that page was
    slow.
    """
    import live_market_service as market

    calls = {"n": 0}

    class _P:
        def get_quote(self, symbol):
            calls["n"] += 1
            return {"price": 1.0}

    market.cache.clear()
    monkeypatch.setattr(market, "_quote_providers", lambda: [_P()])

    assert market._fallback_quote("ZZZZ")["price"] == 1.0
    assert market._fallback_quote("ZZZZ")["price"] == 1.0
    assert calls["n"] == 1, "the second ask must come from cache"


# --- a deliberate analysis re-reads the market -----------------------------

def test_purge_takes_one_symbol_and_only_that_symbol():
    """
    Purging "C" must not take "CRM" with it, and purging NVDA must not take
    NVDAX. The keys are matched on delimiters, not on substring.
    """
    import live_market_service as market

    market.cache.clear()
    for key in ("quote:NVDA", "chart:NVDA:6M", "trdgo:NVDA",
                "quote:NVDAX", "quote:CRM", "earnings_overview:C:6M"):
        market.cache.put(key, 1)

    assert market.cache.purge("NVDA") == 3
    assert market.cache.get("quote:NVDAX", 99) == 1
    assert market.cache.get("quote:NVDA", 99) is None

    assert market.cache.purge("C") == 1
    assert market.cache.get("quote:CRM", 99) == 1


def test_purge_of_nothing_is_harmless():
    import live_market_service as market

    market.cache.clear()
    market.cache.put("quote:AAPL", 1)
    assert market.cache.purge("") == 0
    assert market.cache.purge("ZZZZ") == 0
    assert market.cache.get("quote:AAPL", 99) == 1


def test_refresh_clears_the_symbol_before_streaming(monkeypatch):
    """
    Pressing Analyse means "go and look now". Without the purge the stream
    replays whatever was cached, and the screen showing sources arriving is
    then describing a read that already happened.
    """
    import analysis_stream_service as stream
    import live_market_service as market

    market.cache.clear()
    market.cache.put("quote:TSLA", 1)

    monkeypatch.setattr(
        stream, "STAGES", [], raising=False)

    gen = stream.stream_analysis("TSLA", refresh=True)
    next(gen)                      # the start event runs the purge
    assert market.cache.get("quote:TSLA", 99) is None
    gen.close()


def test_without_refresh_the_cache_is_left_alone():
    import analysis_stream_service as stream
    import live_market_service as market

    market.cache.clear()
    market.cache.put("quote:TSLA", 1)

    gen = stream.stream_analysis("TSLA")
    next(gen)
    assert market.cache.get("quote:TSLA", 99) == 1
    gen.close()


def test_refresh_keeps_what_cannot_have_changed():
    """
    A Form 4 filed yesterday is the same Form 4 two minutes later. Re-fetching
    the slow-moving families on every deliberate run cost about fifty seconds
    and could not change a figure on the screen.
    """
    import analysis_stream_service as stream
    import live_market_service as market

    market.cache.clear()
    market.cache.put("quote:AMD", 1)
    market.cache.put("flow:AMD", 1)
    market.cache.put("sec:form4:AMD:90", 1)
    market.cache.put("earnhist:AMD:8", 1)

    removed = market.cache.purge("AMD", keep=stream.SLOW_MOVING)

    assert removed == 2, "only the fast-moving families go"
    assert market.cache.get("sec:form4:AMD:90", 99) == 1
    assert market.cache.get("earnhist:AMD:8", 99) == 1
    assert market.cache.get("quote:AMD", 99) is None


# --- freshness badges ------------------------------------------------------

def test_a_live_badge_needs_a_real_print_and_an_open_market():
    """
    The flow page said "Live data" over a fifteen-minute-delayed tape. The
    badge has to come from the source that actually answered, or it is a
    decoration that is wrong exactly when it matters.

    The vocabulary is now the one get_quote actually writes: a live trade
    carries the feed's name, and a figure that is really the last completed
    session's close carries PROVIDER_SNAPSHOT.
    """
    import freshness as f

    assert f.for_quote("UNUSUAL_WHALES", "OPEN")["kind"] == f.LIVE
    # A snapshot during RTH is the last close, not a live price.
    assert f.for_quote("PROVIDER_SNAPSHOT", "OPEN")["kind"] == f.SNAPSHOT
    # And a real print outside RTH is still not the market moving.
    assert f.for_quote("UNUSUAL_WHALES", "CLOSED")["kind"] == f.SNAPSHOT


def test_the_chain_badge_reflects_the_real_age():
    """
    The chain badge is read from the newest contract's print, not a fixed
    "15 minutes". A fresh print during the session is live; an old one names
    the real gap; a closed market is the last session's close, not a delay.
    """
    import freshness as f
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    fresh = now.isoformat()
    stale = (now - timedelta(minutes=17)).isoformat()

    # Fresh print, market open -> live.
    assert f.for_chain("UNUSUAL_WHALES", fresh, "OPEN")["kind"] == f.LIVE
    # Old print, market open -> delayed by the real gap, not a hardcoded 15.
    old = f.for_chain("UNUSUAL_WHALES", stale, "OPEN")
    assert old["kind"] == f.DELAYED and old["delay_minutes"] >= 15
    # Market closed -> last close, not a delay.
    assert f.for_chain("UNUSUAL_WHALES", stale, "CLOSED")["kind"] == f.SNAPSHOT
    # No timestamp -> reported without claiming a specific delay.
    unknown = f.for_chain("UNUSUAL_WHALES")
    assert unknown["kind"] == f.DELAYED and unknown["delay_minutes"] is None


def test_the_tape_says_the_delay_is_a_licence_not_the_app():
    import freshness as f

    tape = f.for_tape(15.0, {"entitlement": "trialing"})
    assert tape["label"] == "15-MIN DELAYED"
    assert "licensing" in tape["detail"]
    assert "trialing" in tape["detail"]


def test_quarterly_names_its_quarter():
    import freshness as f

    q = f.for_quarterly("2026-Q1")
    assert q["kind"] == f.QUARTERLY
    assert "2026-Q1" in q["label"]
    assert "real time" in q["detail"]


def test_a_panel_is_only_as_live_as_its_slowest_feed():
    import freshness as f

    mixed = f.weakest(f.for_chain("IBKR"), f.for_tape(), f.for_quarterly("2026-Q1"))
    assert mixed["kind"] == f.QUARTERLY
    assert f.weakest(None, None) is None
