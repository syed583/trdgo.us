"""Workflow v2: regular-session entries (candle confirmation), second positions, the entry lock, instrument and calendar rules,
the schema migration and the tracker's scheduling helpers."""
import json
from datetime import timedelta, timezone

import pytest
from sqlalchemy import create_engine, text

import demo_trade_service as dts
import intraday_execution as ex
import intraday_workflow as wf
import market_calendar as mc
import models_demo_trades as m
import tab_settings as cfg
from wf_harness import TUE, bar, book, et, falling_bars, open_via_premarket, quote, rising_bars, row  # noqa: F401

UTC = timezone.utc


def history(now, today_bars=24):
    """Yesterday's closing hours plus today's closed bars: what the provider returns (several sessions)."""
    return rising_bars(et(2026, 10, 26, 16, 0), 24) + [b for b in rising_bars(now, today_bars) if True]


def regular_candidate(book, now, symbol="NVDA", signal="BUY", **kw):
    book.quotes[symbol] = quote(100.0, 0.04)
    book.bars5[symbol] = kw.pop("bars", rising_bars(now, 24, warmup=True) if signal == "BUY" else falling_bars(now, 24, warmup=True))
    book.r15[symbol] = kw.pop("r15", {"status": "OK", "bias": 1 if signal == "BUY" else -1, "last_bar_end_epoch": now.timestamp() - 60, "detail": "15m"})
    return row("grok", symbol, now, signal=signal, session="REGULAR", price=100.0, score=kw.pop("score", 56.0 if signal == "BUY" else 44.0), **kw)


def test_a_regular_session_buy_opens_after_the_candles_confirm(book):
    for k in range(2):
        n = et(*TUE, 11, 0) + timedelta(minutes=3 * k)
        book.set_board("grok", regular_candidate(book, n))
        book.tick(n)
    t = book.trades()
    assert len(t) == 1 and t[0].entry_session == "REGULAR" and t[0].side == "LONG"
    v = json.loads(t[0].validation)
    assert v["confirmation"]["applies"] and v["confirmation"]["five"]["status"] == "OK" and v["confirmation"]["fifteen"]["bias"] == 1


def test_candles_that_disagree_with_the_signal_block_the_entry(book):
    for k in range(2):
        n = et(*TUE, 11, 0) + timedelta(minutes=3 * k)
        book.set_board("grok", regular_candidate(book, n, bars=falling_bars(n, 24, warmup=True), r15={"status": "OK", "bias": -1, "last_bar_end_epoch": n.timestamp() - 60, "detail": "15m bearish"}))
        book.tick(n)
    ev = book.events()[0]
    assert not book.trades() and ev.validation_status == ex.WAIT_UNCONFIRMED
    assert "5-minute candles do not confirm" in json.loads(ev.reject_reasons)[-1]["reasons"][0]


def test_in_the_first_half_hour_the_five_minute_candles_alone_decide(book):
    sym = "NVDA"
    for k in range(2):
        n = et(*TUE, 9, 41) + timedelta(minutes=3 * k)
        book.quotes[sym] = quote(100.0, 0.04)
        prior = rising_bars(et(2026, 10, 26, 16, 0), 24)                               # yesterday's closing hours (EMA / RSI warm-up)
        top = prior[-1]["close"]
        today = [bar(et(*TUE, 9, 30), top, top + 0.3, top - 0.05, top + 0.2), bar(et(*TUE, 9, 35), top + 0.2, top + 0.5, top + 0.15, top + 0.45)]
        book.bars5[sym] = prior + today
        book.r15[sym] = None                                                           # the first 15-minute candles have not closed
        book.set_board("grok", row("grok", sym, n, signal="BUY", session="REGULAR", price=100.0, score=56.0))
        book.tick(n)
    t = book.trades()
    assert len(t) == 1 and json.loads(t[0].validation)["confirmation"]["fifteen"]["status"] == "WARMUP"


def test_a_stale_fifteen_minute_reading_after_warmup_blocks_the_entry(book):
    for k in range(2):
        n = et(*TUE, 11, 0) + timedelta(minutes=3 * k)
        book.set_board("grok", regular_candidate(book, n, r15={"status": "OK", "bias": 1, "last_bar_end_epoch": n.timestamp() - 7200, "detail": "old"}))
        book.tick(n)
    assert not book.trades() and book.events()[0].validation_status == ex.WAIT_UNCONFIRMED


# ============================================================================== a second, genuinely new position =====
def test_a_new_regular_session_signal_may_open_a_second_position_once_the_first_has_exited(book):
    now, t0 = open_via_premarket(book, et(*TUE, 7, 15))
    n = et(*TUE, 7, 30)
    book.set_board("grok", row("grok", "NVDA", n, signal="NEUTRAL", price=98.9, score=60.0))
    book.tick(n)
    assert book.trades()[0].status == "STOP"
    for k in range(2):
        n = et(*TUE, 11, 30) + timedelta(minutes=3 * k)
        book.set_board("grok", regular_candidate(book, n))
        book.tick(n)
    t = book.trades()
    assert [x.entry_session for x in t] == ["PREMARKET", "REGULAR"] and t[1].status == "OPEN"
    ev = {e.session_type: e for e in book.events()}
    assert ev["PREMARKET"].eligibility_status == "TRADED" and ev["REGULAR"].eligibility_status == "TRADED"
    assert ev["PREMARKET"].trade_id == t[0].id and ev["REGULAR"].trade_id == t[1].id


def test_a_stopped_out_premarket_signal_does_not_re_enter_the_same_session(book):
    now, t0 = open_via_premarket(book, et(*TUE, 7, 15))
    book.set_board("grok", row("grok", "NVDA", et(*TUE, 7, 30), signal="NEUTRAL", price=98.9, score=60.0))
    book.tick(et(*TUE, 7, 30))
    for k in range(6):
        n = et(*TUE, 7, 40) + timedelta(minutes=3 * k)
        book.set_board("grok", row("grok", "NVDA", n, signal="BUY", price=100.0, score=57.0))
        book.tick(n)
    assert len(book.trades()) == 1


# ========================================================================================== lock, instrument, clock =====
def test_the_entry_lock_blocks_new_entries_but_never_the_exits(book):
    now, t0 = open_via_premarket(book, et(*TUE, 7, 15))
    book.locked = True
    book.quotes["AMD"] = quote(50.0)
    for k in range(2):
        n = now + timedelta(minutes=3 * (k + 1))
        book.set_board("grok", row("grok", "AMD", n, signal="BUY", price=50.0, score=57.0),
                       row("grok", "NVDA", n, signal="NEUTRAL", price=98.8, score=60.0))
        book.tick(n)
    assert [x.symbol for x in book.trades()] == ["NVDA"] and book.trades()[0].status == "STOP"       # the exit still ran
    assert book.events(symbol="AMD")[0].validation_status == ex.WAIT_LOCKED


def test_an_index_or_future_is_never_a_candidate(book):
    for k in range(2):
        n = et(*TUE, 7, 15) + timedelta(minutes=3 * k)
        book.set_board("grok", row("grok", "SPX", n, signal="BUY", price=100.0))
        book.tick(n)
    assert not book.trades() and book.events()[0].validation_status == ex.WAIT_INSTRUMENT


@pytest.mark.parametrize("when", [et(2026, 10, 24, 10, 0), et(2026, 10, 25, 10, 0), et(2026, 11, 26, 10, 0), et(*TUE, 3, 50),
                                  et(*TUE, 16, 0), et(*TUE, 18, 0), et(*TUE, 20, 0)])
def test_no_entry_is_considered_outside_the_two_sessions(book, when):
    book.quotes["NVDA"] = quote()
    for k in range(2):
        n = when + timedelta(minutes=3 * k)
        book.set_board("grok", row("grok", "NVDA", n, signal="BUY", price=100.0, session="REGULAR"))
        book.tick(n)
    assert not book.trades() and not book.events()


def test_the_tracker_sleeps_until_the_bell_and_keeps_ticking_while_anything_is_open():
    assert wf.sleep_seconds(et(*TUE, 12, 0)) == 60.0
    assert 30.0 <= wf.sleep_seconds(et(*TUE, 15, 59, 29)) <= 33.0
    assert wf.sleep_seconds(et(2026, 11, 27, 12, 59, 40)) < 25.0                       # the early close
    assert wf.should_tick(et(*TUE, 10, 0), lambda: False) and wf.should_tick(et(*TUE, 7, 0), lambda: False)
    assert not wf.should_tick(et(*TUE, 21, 0), lambda: False) and wf.should_tick(et(*TUE, 21, 0), lambda: True)
    assert not wf.should_tick(et(2026, 10, 24, 10, 0), lambda: False)


def test_the_close_helpers_know_trade_dates():
    d = et(*TUE).date()
    assert not mc.close_due(d, et(*TUE, 15, 59, 59)) and mc.close_due(d, et(*TUE, 16, 0))
    assert mc.close_due(d, et(2026, 10, 28, 4, 0)) and not mc.close_due(et(2026, 10, 28).date(), et(*TUE, 17, 0))
    assert mc.seconds_to_close(et(*TUE, 15, 59, 0)) == 60.0 and mc.seconds_to_close(et(*TUE, 16, 0)) is None


# ================================================================================================== evidence ==========
def test_every_snapshot_keeps_each_parameters_reading_contribution_and_category(book):
    now, t = open_via_premarket(book, et(*TUE, 7, 15))
    snaps = book.snapshots()
    assert len(snaps) == 2                                                             # two distinct pieces of evidence were kept
    s = [x for x in snaps if x.id == t.snapshot_id][0]
    params = {p["name"]: p for p in json.loads(s.params)}
    assert params["trend_price"]["category"] == ex.AVAILABLE and params["trend_price"]["points"] == 24.0     # grok: 24 x (0.5 + 0.5 x 1)
    assert params["options_flow"]["category"] == ex.UNAVAILABLE_PREMARKET and params["volatility_em"]["category"] == ex.ZERO_WEIGHT
    assert s.coverage_eligible_pct == 100.0 and s.coverage_total_pct == pytest.approx(42.0) and s.agreement_pct == 100.0   # 24+8+5+3+2 of 100
    assert json.loads(s.sources)["ema_trend"]["age_s"] >= 120.0 and s.fingerprint


def test_the_legacy_low_confidence_flag_is_recorded_for_audit(book):
    now, t = open_via_premarket(book, et(*TUE, 7, 15), strategy="grok", symbol="AAPL")
    book.set_board("grok", row("grok", "AMD", now + timedelta(minutes=1), signal="BUY", price=100.0, low_confidence=True))
    book.quotes["AMD"] = quote()
    book.tick(now + timedelta(minutes=1))
    snap = [s for s in book.snapshots() if s.symbol == "AMD"][0]
    assert snap.low_confidence is True and json.loads(snap.validation)["legacy_low_confidence"] is True


# ==================================================================================================== migration ========
def test_the_migration_is_additive_idempotent_and_keeps_every_historical_row():
    engine = create_engine("sqlite://")
    with engine.begin() as c:                                                           # a database as the release left it
        c.execute(text("""CREATE TABLE demo_trades (id INTEGER PRIMARY KEY, strategy VARCHAR(24) NOT NULL, symbol VARCHAR(16) NOT NULL,
            side VARCHAR(5) NOT NULL, shares INTEGER NOT NULL, session_date DATE NOT NULL, entry_time DATETIME, entry_price FLOAT NOT NULL,
            stop FLOAT, target FLOAT, score_at_entry FLOAT, last_price FLOAT, profit FLOAT, profit_pct FLOAT, status VARCHAR(16) NOT NULL,
            exit_time DATETIME, exit_price FLOAT, updated_at DATETIME, CONSTRAINT uq_demo_strategy_symbol_day UNIQUE (strategy, symbol, session_date))"""))
        c.execute(text("INSERT INTO demo_trades (strategy, symbol, side, shares, session_date, entry_price, status, score_at_entry) "
                       "VALUES ('grok', 'AAPL', 'LONG', 100, '2026-10-26', 150.0, 'TIMED_EXIT', 61.0)"))
    first = wf.migrate(engine)
    assert set(first["columns"]) == set(wf._NEW_COLUMNS) and set(first["tables"]) >= {"intraday_snapshots", "intraday_signal_events", "intraday_data_exceptions"}
    second = wf.migrate(engine)
    assert second["columns"] == [] and second["tables"] == []
    with engine.connect() as c:
        row_ = c.execute(text("SELECT strategy, symbol, status, score_at_entry, entry_session FROM demo_trades")).one()
    assert tuple(row_) == ("grok", "AAPL", "TIMED_EXIT", 61.0, None)                  # untouched; new columns are NULL, never invented
