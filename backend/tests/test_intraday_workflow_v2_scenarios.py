"""Intraday workflow v2, scenarios B-T of the specification (A is in test_intraday_workflow_v2.py).

Rows come in with each strategy's own signal, score and parameters already computed: nothing here touches a score."""
import json
from datetime import timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

import intraday_execution as ex
import intraday_workflow as wf
import market_calendar as mc
import models_demo_trades as m
import tab_settings as cfg
from wf_harness import TUE, book, et, open_via_premarket, quote, rising_bars, row  # noqa: F401  (book is a fixture)

UTC = timezone.utc


def test_B_premarket_sell_opens_a_short_at_the_bid(book):
    now, t = open_via_premarket(book, et(*TUE, 7, 15), signal="SELL")
    assert t.side == "SHORT" and t.entry_session == "PREMARKET"
    assert t.entry_price == pytest.approx(99.98)                       # the bid
    assert t.stop == 101.0 and t.target == 98.0                        # the strategy's own levels, mirrored


# ============================================================ C  pre-market trade carries into the regular session ====
def test_C_a_premarket_position_is_not_closed_at_the_open_and_closes_at_the_bell(book):
    now, t = open_via_premarket(book, et(*TUE, 7, 15))
    for hh, mm, px in [(9, 29, 100.4), (9, 30, 100.6), (9, 31, 100.5), (12, 0, 100.9), (15, 59, 101.2)]:
        n = et(*TUE, hh, mm)
        book.set_board("chatgpt", row("chatgpt", "NVDA", n, signal="NEUTRAL", price=px, score=60.0))
        book.tick(n)
        assert book.trades()[0].status == "OPEN", f"closed too early at {hh}:{mm}"
    n = et(*TUE, 16, 0, 20)
    book.set_board("chatgpt", row("chatgpt", "NVDA", n, signal="NEUTRAL", price=101.3, score=60.0))
    book.tick(n)
    t = book.trades()[0]
    assert t.status == "MARKET_CLOSE" and t.exit_price == 101.3 and t.exit_price_source == "board"
    assert t.profit == pytest.approx((101.3 - 100.02) * 100, abs=0.01)
    assert "official close" in t.fill_assumption


# ===================================================== D / E / F  signals while a position is open ===================
def test_D_a_regular_signal_does_not_close_overwrite_or_double_the_premarket_position(book):
    now, t0 = open_via_premarket(book, et(*TUE, 7, 15))
    n = et(*TUE, 9, 45)
    book.set_board("chatgpt", row("chatgpt", "NVDA", n, signal="BUY", price=101.0))
    book.tick(n)
    trades = book.trades()
    assert len(trades) == 1 and trades[0].id == t0.id and trades[0].status == "OPEN"
    ev = book.events(session_type="REGULAR")
    assert len(ev) == 1 and ev[0].direction == "BUY" and ev[0].eligibility_status == "REJECTED"
    assert ev[0].validation_status == ex.WAIT_DUPLICATE
    assert {e.session_type for e in book.events()} == {"PREMARKET", "REGULAR"}      # separate events for the two sessions


def test_E_the_same_signal_read_again_never_opens_a_second_position(book):
    now, t = open_via_premarket(book, et(*TUE, 7, 15))
    for k in range(1, 6):
        n = now + timedelta(minutes=k)
        book.set_board("chatgpt", row("chatgpt", "NVDA", n, signal="BUY", price=100.0))
        book.tick(n)
    assert len(book.trades()) == 1
    with book.Session() as s:                                           # and the database itself refuses a second OPEN row
        s.add(m.DemoTrade(strategy="chatgpt", symbol="NVDA", side="LONG", shares=100, session_date=et(*TUE).date(), entry_price=1.0,
                          status="OPEN", entry_session="REGULAR"))
        with pytest.raises(IntegrityError):
            s.commit()


def test_F_an_opposite_regular_signal_does_not_flip_or_close_the_position(book):
    now, t0 = open_via_premarket(book, et(*TUE, 7, 15))
    n = et(*TUE, 9, 50)
    book.set_board("chatgpt", row("chatgpt", "NVDA", n, signal="SELL", price=100.0, score=45.0))
    book.tick(n)
    t = book.trades()
    assert len(t) == 1 and t[0].side == "LONG" and t[0].status == "OPEN"
    ev = [e for e in book.events() if e.direction == "SELL"]
    assert len(ev) == 1 and ev[0].validation_status == ex.WAIT_DUPLICATE


# ================================================================ G / H / I / J  entry validation ====================
def _rejection(book, strategy="chatgpt", symbol="NVDA", **row_kw):
    now0 = et(*TUE, 7, 15)
    for k in range(2):
        n = now0 + timedelta(minutes=3 * k)
        book.set_board(strategy, row(strategy, symbol, n, **row_kw))
        book.tick(n)
    return book.events(symbol=symbol)[0]


def test_G_a_stale_snapshot_is_refused_and_the_original_score_is_kept(book):
    book.quotes["NVDA"] = quote()
    ev = _rejection(book, meta_age=200000.0)
    assert not book.trades() and ev.eligibility_status == "REJECTED" and ev.validation_status == ex.WAIT_STALE
    assert ev.score is not None and ev.score >= 55.0                                 # the strategy's own score is still on record
    assert "eligible coverage" in json.loads(ev.reject_reasons)[0]["reasons"][0]


def test_H_missing_options_data_does_not_block_the_premarket_but_does_block_the_regular_session(book):
    book.quotes["NVDA"] = quote()
    opts = {"options_flow": None, "rvol": None, "unusual": None, "breadth_mtf": None}
    _rejection(book, overrides=opts)
    assert len(book.trades()) == 1                                                    # the options market is closed: not required
    n0 = et(*TUE, 11, 0)
    book.quotes["AMD"] = quote(100.0, 0.04)
    book.bars5["AMD"] = rising_bars(n0, 24, warmup=True)
    book.r15["AMD"] = {"status": "OK", "bias": 1, "last_bar_end_epoch": n0.timestamp() - 60, "detail": "15m bullish"}
    holes = {"options_flow": None, "gamma": None, "unusual": None, "rvol": None}
    for k in range(2):
        n = n0 + timedelta(minutes=3 * k)
        book.set_board("chatgpt", row("chatgpt", "AMD", n, signal="BUY", price=100.0, overrides=holes))
        book.tick(n)
    amd = book.events(symbol="AMD")[0]
    assert not book.trades(symbol="AMD") and amd.validation_status == ex.WAIT_INSUFFICIENT


def test_I_a_missing_or_invalid_price_is_refused(book):
    ev = _rejection(book)                                    # no quote at all
    assert not book.trades() and ev.validation_status == ex.WAIT_PRICE
    book.quotes["NVDA"] = {"price": 0.0, "bid": 0.0, "ask": 0.0, "age_s": 5.0}
    n = et(*TUE, 7, 21)
    book.set_board("chatgpt", row("chatgpt", "NVDA", n))
    book.tick(n)
    assert not book.trades() and book.events()[0].validation_status == ex.WAIT_PRICE


def test_J_low_liquidity_and_a_wide_premarket_spread_are_refused(book):
    book.quotes["NVDA"] = quote()
    book.avgvol["NVDA"] = 200_000.0
    ev = _rejection(book)
    assert not book.trades() and ev.validation_status == ex.WAIT_LIQUIDITY
    book.avgvol["NVDA"] = 9_000_000.0
    book.quotes["NVDA"] = quote(100.0, spread_pct=0.30)                               # the pre-market limit is 0.10%
    n = et(*TUE, 7, 21)
    book.set_board("chatgpt", row("chatgpt", "NVDA", n))
    book.tick(n)
    ev = book.events()[0]
    assert not book.trades() and ev.validation_status == ex.WAIT_PRICE
    assert "spread" in json.loads(ev.reject_reasons)[-1]["reasons"][0]


def test_an_unchanged_cached_snapshot_read_again_is_not_new_evidence(book):
    book.quotes["NVDA"] = quote()
    t0 = et(*TUE, 7, 15)
    for k in range(5):
        n = t0 + timedelta(minutes=k)
        book.set_board("chatgpt", row("chatgpt", "NVDA", n, gate_age=60.0 * k, meta_age=120.0))   # the board never rebuilt: only the clock moves
        book.tick(n)
    ev = book.events()[0]
    assert not book.trades() and ev.validation_status == ex.WAIT_UNCONFIRMED
    assert len(json.loads(ev.evidence_ids)) == 1 and ev.observations == 5


# =================================================================================== K / L / M  exits ===============
@pytest.mark.parametrize("price, score, status, exit_px", [
    (98.9, 70.0, "STOP", 99.0), (102.4, 70.0, "TARGET", 102.0), (100.5, 40.0, "SCORE_EXIT", 100.5)])
def test_KLM_the_existing_stop_target_and_score_exits_still_work(book, price, score, status, exit_px):
    now, t = open_via_premarket(book, et(*TUE, 7, 15))
    n = now + timedelta(minutes=5)
    book.set_board("chatgpt", row("chatgpt", "NVDA", n, signal="NEUTRAL", price=price, score=score))
    book.tick(n)
    t = book.trades()[0]
    assert t.status == status and t.exit_price == pytest.approx(exit_px)
    assert t.profit == pytest.approx((exit_px - t.entry_price) * 100, abs=0.01)


# ===================================================================================== N  end-of-day close ===========
def test_N_every_position_closes_at_the_bell_winners_and_losers_alike(book):
    for sym, px in (("NVDA", 100.0), ("AMD", 50.0)):
        open_via_premarket(book, et(*TUE, 7, 15), symbol=sym, price=px)
    n = et(*TUE, 16, 0, 30)
    book.set_board("chatgpt", row("chatgpt", "NVDA", n, signal="NEUTRAL", price=100.6, score=60.0),
                   row("chatgpt", "AMD", n, signal="NEUTRAL", price=49.6, score=60.0))
    book.tick(n)
    t = {x.symbol: x for x in book.trades()}
    assert t["NVDA"].status == t["AMD"].status == "MARKET_CLOSE"
    assert t["NVDA"].profit > 0 > t["AMD"].profit                                       # profit does not decide
    assert not [x for x in book.trades() if x.status == "OPEN"]


def test_a_position_inside_the_grace_window_waits_for_a_price_then_uses_the_final_candle(book):
    now, t = open_via_premarket(book, et(*TUE, 7, 15))
    book.boards["chatgpt"].clear()                                                     # no board price at all
    book.quotes.clear()
    book.tick(et(*TUE, 16, 1))
    assert book.trades()[0].status == "OPEN"                                          # inside the grace window: wait
    book.final_close[("NVDA", et(*TUE).date())] = 101.1
    book.tick(et(*TUE, 16, 6))                                                         # past the grace window
    t = book.trades()[0]
    assert t.status == "MARKET_CLOSE" and t.exit_price == 101.1 and t.exit_price_source == "regular_close_candle"
    ex_et = t.exit_time.replace(tzinfo=UTC).astimezone(mc.ET)
    assert (ex_et.hour, ex_et.minute) == (16, 0)                                       # booked at the bell, not at 16:06


def test_a_missing_closing_price_is_recorded_never_invented(book):
    now, t = open_via_premarket(book, et(*TUE, 7, 15))
    book.boards["chatgpt"].clear()
    book.quotes.clear()
    book.tick(et(*TUE, 16, 6))
    t = book.trades()[0]
    assert t.status == "PENDING_RECON" and t.exit_price is None and t.profit is None
    exc = book.exceptions()
    assert len(exc) == 1 and exc[0].kind == "CLOSE_PRICE_MISSING" and exc[0].trade_id == t.id and exc[0].resolved_at is None
    assert not [x for x in book.trades() if x.status == "OPEN"]                         # no longer an active position
    wf._last_recon_try.clear()
    book.final_close[("NVDA", et(*TUE).date())] = 100.9                                 # later, valid evidence arrives
    book.tick(et(*TUE, 17, 0))
    t = book.trades()[0]
    assert t.status == "MARKET_CLOSE" and t.exit_price == 100.9 and book.exceptions()[0].resolved_at is not None


# ===================================================================== O  early close, P  daylight saving ===========
EARLY = (2026, 11, 27)                      # the day after Thanksgiving: 13:00 ET close


def test_O_on_an_early_close_day_entries_stop_and_positions_close_at_one_oclock(book):
    assert mc.is_early_close(et(*EARLY).date())
    assert mc.entries_allowed(et(*EARLY, 12, 59, 59)) and not mc.entries_allowed(et(*EARLY, 13, 0))
    now, t = open_via_premarket(book, et(*EARLY, 7, 15))
    assert t.status == "OPEN"
    book.set_board("chatgpt", row("chatgpt", "NVDA", et(*EARLY, 12, 59), signal="NEUTRAL", price=100.4, score=60.0))
    book.tick(et(*EARLY, 12, 59))
    assert book.trades()[0].status == "OPEN"
    n = et(*EARLY, 13, 0, 30)
    book.set_board("chatgpt", row("chatgpt", "NVDA", n, signal="NEUTRAL", price=100.5, score=60.0))
    book.tick(n)
    assert book.trades()[0].status == "MARKET_CLOSE"


@pytest.mark.parametrize("day, close_utc_hour", [((2026, 10, 27), 20), ((2026, 11, 2), 21), ((2027, 3, 15), 20), ((2027, 3, 8), 21)])
def test_P_the_bell_follows_new_york_time_across_daylight_saving(day, close_utc_hour):
    c = mc.close_datetime(et(*day).date())
    assert c.astimezone(mc.ET).hour == 16
    assert c.utcoffset().total_seconds() / 3600 in (-4.0, -5.0)
    assert c.astimezone(UTC).hour == close_utc_hour
    assert mc.session_at(et(*day, 3, 59))["session"] == "CLOSED" and mc.session_at(et(*day, 4, 0))["session"] == "PREMARKET"


def test_P_a_premarket_position_closes_at_the_est_bell_after_the_november_change(book):
    now, t = open_via_premarket(book, et(2026, 11, 2, 7, 15))
    n = et(2026, 11, 2, 16, 0, 30)
    book.set_board("chatgpt", row("chatgpt", "NVDA", n, signal="NEUTRAL", price=100.5, score=60.0))
    book.tick(n)
    t = book.trades()[0]
    assert t.status == "MARKET_CLOSE" and t.exit_time.replace(tzinfo=UTC).hour == 21


# =========================================================================== Q  restart with open positions =========
def test_Q_after_a_restart_every_open_position_is_found_and_the_bell_still_closes_it(book):
    now, t = open_via_premarket(book, et(*TUE, 7, 15))
    wf._last_recon_try.clear()                                                         # a new process: nothing in memory
    book.boards.clear()
    book.final_close[("NVDA", et(*TUE).date())] = 100.8
    book.tick(et(*TUE, 17, 30))                                                        # the service was down at the close
    t = book.trades()[0]
    assert t.status == "MARKET_CLOSE" and t.exit_price == 100.8 and "final regular-session" in t.fill_assumption
    with book.Session() as s:                                                          # a survivor from yesterday, found next morning
        s.add(m.DemoTrade(strategy="grok", symbol="AAPL", side="LONG", shares=100, session_date=et(*TUE).date(), entry_price=50.0,
                          status="OPEN", entry_session="REGULAR", entry_time=et(*TUE, 15).astimezone(UTC)))
        s.commit()
    book.final_close[("AAPL", et(*TUE).date())] = 50.5
    book.tick(et(2026, 10, 28, 4, 1))
    assert not [x for x in book.trades() if x.status == "OPEN"]


# ============================================================================ R  database failure while closing =======
def test_R_a_database_failure_during_the_close_loses_nothing_and_the_next_tick_closes(book, monkeypatch):
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import Session, sessionmaker
    import database

    class Flaky(Session):
        fail = True

        def commit(self):
            if Flaky.fail:
                Flaky.fail = False
                raise OperationalError("COMMIT", {}, Exception("connection lost"))
            return super().commit()

    now, t = open_via_premarket(book, et(*TUE, 7, 15))
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=book.engine, class_=Flaky))
    n = et(*TUE, 16, 0, 30)
    book.set_board("chatgpt", row("chatgpt", "NVDA", n, signal="NEUTRAL", price=100.5, score=60.0))
    with pytest.raises(OperationalError):
        book.tick(n)
    assert book.trades()[0].status == "OPEN" and book.trades()[0].exit_price is None            # nothing half-written
    book.tick(n + timedelta(seconds=30))
    assert book.trades()[0].status == "MARKET_CLOSE"


# =========================================================================== S  several strategies, one symbol ======
def test_S_each_strategy_trades_independently_but_the_same_symbol_side_is_capped(book):
    book.quotes["NVDA"] = quote()
    order = ("chatgpt", "grok", "meta", "kimi")
    for k in range(2):
        n = et(*TUE, 7, 15) + timedelta(minutes=3 * k)
        for s in order:
            book.set_board(s, row(s, "NVDA", n))
        book.tick(n)
    t = book.trades()
    assert len(t) == int(cfg.INTRADAY_EXEC["max_strategies_same_symbol_side"]) == 3
    assert {x.strategy for x in t} == {"chatgpt", "grok", "meta"}                      # independent rows per strategy
    kimi = book.events(strategy="kimi")[0]
    assert kimi.eligibility_status == "REJECTED" and kimi.validation_status == ex.WAIT_LIMIT


# ================================================================================================ T  no overnight ====
def test_T_nothing_is_open_after_the_bell_and_nothing_opens_after_it(book):
    open_via_premarket(book, et(*TUE, 7, 15))
    open_via_premarket(book, et(*TUE, 7, 15), strategy="grok", symbol="AMD", price=50.0)
    n = et(*TUE, 16, 0, 30)
    book.boards.clear()
    book.final_close[("NVDA", et(*TUE).date())] = 100.9
    book.tick(n)
    book.tick(n + timedelta(minutes=10))
    for later in (et(*TUE, 16, 30), et(*TUE, 19, 59), et(*TUE, 23, 59), et(2026, 10, 28, 3, 59)):
        book.set_board("chatgpt", row("chatgpt", "MSFT", later, signal="BUY", price=100.0, session="REGULAR"))
        book.quotes["MSFT"] = quote()
        book.tick(later)
    assert not [x for x in book.trades() if x.status == "OPEN"]
    assert not book.trades(symbol="MSFT") and not book.events(symbol="MSFT")                # no entry, not even a candidate
    assert {x.status for x in book.trades()} <= {"MARKET_CLOSE", "PENDING_RECON"}
