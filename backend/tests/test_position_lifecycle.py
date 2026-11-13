"""The pure-intraday paper tracker's position lifecycle.

Every open position is closed before the bell (EOD_CLOSE at close - 15 min: 15:45 ET, 12:45 on an early-close day,
since A.22);
a position still OPEN from an earlier date is a leftover and is closed as CLEANUP at the first reliable price, never
carried and never managed; STOP / TARGET / SCORE_EXIT are booked at the OBSERVED price; every close happens exactly
once; a DB failure mid-tick loses nothing; the entry lock blocks new entries and never monitoring or exits; a restart
recovers every open position of any date. Mocked: no API calls, no production data (SQLite databases built here)."""
import types
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import demo_trade_service as dts
import entry_lock
import intraday_signal_v2 as eng
import market_calendar as mc
import paper_fills as pf
import release_tool as rt
from models_demo_trades import DemoTrade

ET = ZoneInfo("America/New_York")


def et(y, m, d, hh=10, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


MON, TUE = (2026, 10, 19), (2026, 10, 20)           # Monday, Tuesday
FRI, SAT, NEXT_MON = (2026, 10, 23), (2026, 10, 24), (2026, 10, 26)
BLACK_FRI = (2026, 11, 27)                           # the day after Thanksgiving: 13:00 close


def _utc(v):
    """SQLite hands back naive datetimes; they were stored as UTC."""
    return v.replace(tzinfo=timezone.utc) if isinstance(v, datetime) and v.tzinfo is None else v


# ======================================================================= calendar ===
def test_the_market_calendar_knows_weekends_and_exchange_holidays():
    assert mc.is_trading_day(date(2026, 10, 23)) and not mc.is_trading_day(date(2026, 10, 24))
    assert not mc.is_trading_day(date(2026, 11, 26))                       # Thanksgiving
    assert not mc.is_trading_day(date(2026, 7, 3))                         # Independence Day observed (Jul 4 is a Saturday)
    assert not mc.is_trading_day(date(2026, 4, 3))                         # Good Friday
    assert not mc.is_trading_day(date(2026, 12, 25)) and not mc.is_trading_day(date(2027, 12, 24))
    assert mc.is_trading_day(date(2026, 12, 31)) and mc.is_trading_day(date(2027, 12, 31))
    assert mc.first_close_after(et(2026, 11, 25, 10)) == et(2026, 11, 25, 16)
    assert mc.first_close_after(et(2026, 11, 25, 17)) == et(2026, 11, 27, 13)      # Thursday is a holiday; Friday closes at 13:00
    assert mc.first_close_after(et(2026, 10, 23, 17)) == et(2026, 10, 26, 16)      # Friday evening -> Monday close


# ================================================================ the EOD cutoff ===
def trade(entry, **kw):
    return types.SimpleNamespace(entry_time=entry.astimezone(timezone.utc) if entry else None,
                                 session_date=entry.date() if entry else None, **kw)


@pytest.mark.parametrize("entered, now, expect", [
    (et(*TUE, 10), et(*TUE, 15, 44), False),                  # not before close - 15 min
    (et(*TUE, 10), et(*TUE, 15, 45), True),                   # ...from 15:45 (A.22)
    (et(*TUE, 10), et(*TUE, 16, 0), True),
    (et(*TUE, 10), et(*TUE, 16, 30), True),
    (et(*MON, 10), et(*TUE, 9, 31), True),                    # a leftover from an EARLIER date is always due
    (et(*MON, 10), et(*TUE, 10), True),
    (et(*FRI, 10), et(*SAT, 10), True),                       # ...even on a Saturday (it is cleaned up, not carried)
    (et(2026, 11, 25, 10), et(2026, 11, 26, 10), True),       # ...even on Thanksgiving
    (et(*BLACK_FRI, 10), et(*BLACK_FRI, 12, 44), False),      # early close 13:00: the cutoff is 12:45
    (et(*BLACK_FRI, 10), et(*BLACK_FRI, 12, 45), True),
    (et(2026, 12, 24, 10), et(2026, 12, 24, 12, 45), True),   # Christmas Eve: 13:00 close
    (et(*TUE, 10), et(*TUE, 12, 45), False),                  # a regular day is not an early-close day
    (None, et(*TUE, 16, 5), False),                           # no session date: nothing to be due
])
def test_the_eod_close_is_due_fifteen_minutes_before_the_sessions_close_and_a_leftover_is_always_due(entered, now, expect):
    assert dts._eod_due(trade(entered), now) is expect
    assert dts._exit_timed(trade(entered), now) is expect                  # the older name is the same rule


def test_the_eod_cutoff_follows_the_calendar():
    assert dts.eod_cutoff(date(*TUE)) == et(*TUE, 15, 45)
    assert dts.eod_cutoff(date(*BLACK_FRI)) == et(*BLACK_FRI, 12, 45)
    assert dts.eod_cutoff(date(*SAT)) is None and dts.eod_cutoff(date(2026, 11, 26)) is None


# ============================================================ the paper tracker ===
FRESH_Q = {"price": 100.0, "bid": 99.98, "ask": 100.02, "spread": 0.04, "spread_pct": 0.04, "quote_age_s": 2.0, "quote_epoch": 1.0}


@pytest.fixture
def book(monkeypatch):
    engine = create_engine("sqlite://")
    DemoTrade.__table__.create(bind=engine)
    Session = sessionmaker(bind=engine)
    import database
    monkeypatch.setattr(database, "SessionLocal", Session)
    monkeypatch.setattr(dts, "ensure_schema", lambda: None)
    monkeypatch.setattr(dts, "MODELS", {"grok": ("Grok Intraday", "x:y")})
    monkeypatch.setattr(dts, "_quote_price", lambda sym: None)
    monkeypatch.setattr(dts, "_fresh_quote", lambda sym, now=None: (None, "no live quote"))
    monkeypatch.setattr(dts, "_last_tick", 0.0)
    monkeypatch.setattr(dts, "_entry_allowed", lambda *a, **k: False)       # these tests are about EXITS and cleanup
    return types.SimpleNamespace(engine=engine, Session=Session, board={})


def put(book, symbol="AAA", side="LONG", entered=None, entry=100.0, stop=99.0, target=102.0, score=70.0, strategy="grok",
        last=None, status="OPEN"):
    """A position in the book. Default: opened TODAY (Tuesday 10:00); pass ``entered`` on an earlier date for a leftover."""
    entered = entered or et(*TUE, 10)
    with book.Session() as s:
        t = DemoTrade(strategy=strategy, symbol=symbol, side=side, shares=100, session_date=entered.date(),
                      entry_time=entered.astimezone(timezone.utc), entry_price=entry, stop=stop, target=target,
                      score_at_entry=score, last_price=last, status=status)
        s.add(t)
        s.commit()
        return t.id


def brow(symbol, price, score=68, age=5):
    return {"symbol": symbol, "signal": "NEUTRAL", "score": score, "now": price, "data": {"ok": True, "price_age_s": age}}


def run(book, monkeypatch, now, price=None, score=None, symbol="AAA", extra=None):
    """One tracker tick at ``now`` (ET) with the boards showing ``price`` / ``score`` for ``symbol``."""
    rows = []
    if price is not None:
        rows.append(brow(symbol, price, score))
    rows += list(extra or [])
    monkeypatch.setattr(dts, "_et_now", lambda: now)
    monkeypatch.setattr(dts, "_board", lambda spec: {"neutral": rows})
    dts.tick(force=True)


def row(book, tid):
    with book.Session() as s:
        t = s.get(DemoTrade, tid)
        return types.SimpleNamespace(status=t.status, exit_price=t.exit_price, last=t.last_price, profit=t.profit,
                                     exit_time=_utc(t.exit_time), session=t.session_date, stop=t.stop, target=t.target,
                                     be_moved=bool(t.be_moved))


def count(book, **flt):
    with book.Session() as s:
        return s.query(DemoTrade).filter_by(**flt).count()


# ------------------------------------------------------------------ the EOD close ---
def test_every_open_position_is_closed_at_15_45_at_the_observed_price_and_not_a_minute_earlier(book, monkeypatch):
    long_, short = put(book, "AAA", "LONG"), put(book, "BBB", "SHORT", stop=101.0, target=98.0)
    run(book, monkeypatch, et(*TUE, 15, 44), price=100.6, score=69, extra=[brow("BBB", 99.6)])
    assert row(book, long_).status == "OPEN" and row(book, short).status == "OPEN"
    run(book, monkeypatch, et(*TUE, 15, 45), price=100.8, score=69, extra=[brow("BBB", 99.5)])
    a, b = row(book, long_), row(book, short)
    # A.22 (owner decision 2026-11-13): the close runs BEFORE the boards are rebuilt and prices from a live quote,
    # falling back to the position's last known price when no quote arrives in time. There is no quote in this
    # harness, so both book at the last price they saw (the 15:44 tick), not at the board built in this same tick.
    assert (a.status, a.exit_price, a.profit) == ("EOD_CLOSE", 100.6, 60.0)
    assert (b.status, b.exit_price, b.profit) == ("EOD_CLOSE", 99.6, 40.0)
    assert a.exit_time.astimezone(ET) == et(*TUE, 15, 45)
    assert count(book, status="OPEN") == 0


def test_nothing_happens_at_16_00_to_a_position_already_closed_at_15_50(book, monkeypatch):
    tid = put(book)
    run(book, monkeypatch, et(*TUE, 15, 45), price=100.8, score=69)
    first = row(book, tid)
    assert first.status == "EOD_CLOSE"
    for hh, mm in ((16, 0), (16, 1), (16, 30)):
        run(book, monkeypatch, et(*TUE, hh, mm), price=95.0, score=40)           # the bell / after hours: a closed row is never touched
        r = row(book, tid)
        assert (r.status, r.exit_price, r.profit, r.exit_time, r.last) == \
            ("EOD_CLOSE", 100.8, 80.0, first.exit_time, 100.8)
    assert count(book, status="TIMED_EXIT") == 0 and count(book, status="MARKET_CLOSE") == 0


def test_an_early_close_day_closes_at_12_45(book, monkeypatch):
    tid = put(book, entered=et(*BLACK_FRI, 10))
    run(book, monkeypatch, et(*BLACK_FRI, 12, 44), price=100.5, score=69)
    assert row(book, tid).status == "OPEN"
    run(book, monkeypatch, et(*BLACK_FRI, 12, 45), price=100.6, score=69)
    r = row(book, tid)
    assert (r.status, r.exit_price) == ("EOD_CLOSE", 100.5)      # last known price: no quote in this harness (A.22)
    assert r.exit_time.astimezone(ET) == et(*BLACK_FRI, 12, 45)


def test_a_stop_takes_precedence_over_the_eod_close_at_the_cutoff(book, monkeypatch):
    tid = put(book, stop=99.0)
    run(book, monkeypatch, et(*TUE, 15, 45), price=98.7, score=69)
    r = row(book, tid)
    assert (r.status, r.exit_price) == ("STOP", 98.7)


# ------------------------------------------------------------- prior-day leftovers ---
def test_a_prior_day_open_row_is_closed_as_cleanup_at_the_first_tick_with_a_reliable_price(book, monkeypatch):
    tid = put(book, entered=et(*MON, 14))                                          # opened Monday, still OPEN on Tuesday
    run(book, monkeypatch, et(*TUE, 9, 31), price=97.3, score=68)                  # the very first tick, even before 10:00
    r = row(book, tid)
    assert (r.status, r.exit_price, r.profit) == ("CLEANUP", 97.3, -270.0)
    assert r.exit_time.astimezone(ET) == et(*TUE, 9, 31) and r.session == date(*MON)


def test_a_leftover_is_cleanup_even_when_its_levels_are_breached_and_is_never_managed(book, monkeypatch):
    breached, trailing = put(book, "AAA", entered=et(*MON, 14), stop=99.0), put(book, "BBB", entered=et(*MON, 14), stop=99.0, target=102.0)
    run(book, monkeypatch, et(*TUE, 11), price=94.0, score=30, extra=[brow("BBB", 101.5)])
    a, b = row(book, breached), row(book, trailing)
    assert (a.status, a.exit_price, a.profit) == ("CLEANUP", 94.0, -600.0)         # not STOP, not SCORE_EXIT: a leftover is cleaned up
    assert (b.status, b.exit_price, b.profit, b.be_moved, b.stop) == ("CLEANUP", 101.5, 150.0, False, 99.0)   # no breakeven trail on a leftover


def test_a_leftover_with_no_reliable_price_stays_open_and_no_fill_is_invented(book, monkeypatch):
    tid = put(book, entered=et(*MON, 14), last=None)
    run(book, monkeypatch, et(*TUE, 11))                                           # no board row, no quote at all
    r = row(book, tid)
    assert r.status == "OPEN" and r.exit_price is None and r.last is None          # not even a refresh from the entry price
    run(book, monkeypatch, et(*TUE, 16, 30))                                       # the day ends and STILL no price
    r = row(book, tid)
    assert r.status == "OPEN" and r.exit_price is None
    run(book, monkeypatch, et(*TUE, 16, 45), price=100.8, score=69)                # a reliable price arrives: cleaned up at it
    r = row(book, tid)
    assert (r.status, r.exit_price) == ("CLEANUP", 100.8)


def test_a_leftover_with_no_reliable_price_for_the_configured_trading_days_ends_as_no_price_without_pnl(book, monkeypatch):
    import tab_settings
    assert tab_settings.PAPER_EXECUTION["max_unpriced_trading_days"] == 5
    tid = put(book, entered=et(*MON, 10), last=100.2)
    run(book, monkeypatch, et(2026, 10, 23, 16, 30))                               # the 4th trading day after the Monday
    assert row(book, tid).status == "OPEN"
    run(book, monkeypatch, et(*NEXT_MON, 16, 30))                                  # the 5th: given up
    r = row(book, tid)
    assert r.status == "NO_PRICE" and r.exit_price is None and r.profit is None
    with book.Session() as s_:
        t = s_.get(DemoTrade, tid)
        assert t.profit is None and t.profit_pct is None and t.exit_time is not None


def test_a_weekend_or_holiday_tick_cleans_up_a_leftover_rather_than_carrying_it(book, monkeypatch):
    fri = put(book, entered=et(*FRI, 10))
    run(book, monkeypatch, et(*SAT, 10, 30), price=100.3, score=69)                # Saturday: a reliable price, nothing to carry
    r = row(book, fri)
    assert (r.status, r.exit_price) == ("CLEANUP", 100.3)
    wed = put(book, "HHH", entered=et(2026, 11, 25, 10))
    run(book, monkeypatch, et(2026, 11, 26, 10, 30), price=100.3, score=69, symbol="HHH")   # Thanksgiving
    assert row(book, wed).status == "CLEANUP"


# ---------------------------------------------------------------- observed fills ---
@pytest.mark.parametrize("side, entry, stop, target, adverse, favourable, lose, win", [
    ("LONG", 100.0, 99.0, 102.0, 98.5, 103.0, -150.0, 300.0),
    ("SHORT", 100.0, 101.0, 98.0, 101.5, 97.0, -150.0, 300.0),                     # the mirror image
])
def test_a_stop_or_target_is_booked_at_the_observed_crossing_price_symmetrically(book, monkeypatch, side, entry, stop, target,
                                                                                 adverse, favourable, lose, win):
    stopped, won = put(book, "AAA", side, entry=entry, stop=stop, target=target), put(book, "BBB", side, entry=entry, stop=stop, target=target)
    run(book, monkeypatch, et(*TUE, 11), price=adverse, score=None, extra=[brow("BBB", favourable)])
    a, b = row(book, stopped), row(book, won)
    assert (a.status, a.exit_price, a.profit) == ("STOP", adverse, lose)           # the price that crossed, not the level
    assert (b.status, b.exit_price, b.profit) == ("TARGET", favourable, win)
    first = a.exit_time
    run(book, monkeypatch, et(*TUE, 11, 5), price=95.0 if side == "LONG" else 105.0, score=40)   # later ticks never touch a closed trade
    again = row(book, stopped)
    assert (again.status, again.exit_price, again.profit, again.exit_time) == ("STOP", adverse, lose, first)


def test_the_explicit_level_model_still_books_at_the_level_for_comparison(book, monkeypatch):
    import tab_settings
    monkeypatch.setitem(tab_settings.PAPER_EXECUTION, "fill_model", "level")
    tid = put(book, stop=99.0)
    run(book, monkeypatch, et(*TUE, 11), price=94.0, score=68)
    r = row(book, tid)
    assert (r.status, r.exit_price, r.profit) == ("STOP", 99.0, -100.0)


@pytest.mark.parametrize("model, gap, booked", [
    ("observed", False, 94.0), ("observed", True, 94.0),
    ("level", False, 99.0), ("level", True, 99.0),
    ("gap_aware", False, 99.0), ("gap_aware", True, 94.0),
])
def test_the_three_fill_models_are_distinct_and_the_shipped_one_is_observed(model, gap, booked):
    import tab_settings
    assert tab_settings.PAPER_EXECUTION["fill_model"] == "observed"
    assert pf.fill_price("LONG", "STOP", 99.0, 102.0, 94.0, gap, model=model) == booked
    assert pf.fill_price("LONG", "EOD_CLOSE", 99.0, 102.0, 94.0, gap, model=model) == 94.0   # a non-level exit: always observed


@pytest.mark.parametrize("last_seen, now, gap", [
    (et(*MON, 15, 55), et(*TUE, 9, 35), True),
    (et(*FRI, 15, 55), et(*NEXT_MON, 9, 35), True),
    (et(*MON, 10, 0), et(*MON, 13, 0), False),
    (et(*MON, 16, 5), et(*MON, 17, 0), False),
])
def test_a_gap_is_a_closed_period_between_two_observations(last_seen, now, gap):
    assert pf.observation_gap(last_seen, now) is gap


@pytest.mark.parametrize("side, now_score, expect", [
    ("LONG", 54.0, "SCORE_EXIT"), ("LONG", 60.0, "OPEN"), ("SHORT", 86.0, "SCORE_EXIT"), ("SHORT", 80.0, "OPEN")])
def test_a_score_exit_fires_on_a_15_point_drop_and_is_booked_at_the_observed_price(book, monkeypatch, side, now_score, expect):
    tid = put(book, side=side, score=70.0, entry=100.0, stop=90.0 if side == "LONG" else 110.0,
              target=110.0 if side == "LONG" else 90.0)
    run(book, monkeypatch, et(*TUE, 11), price=96.0, score=now_score)
    r = row(book, tid)
    assert r.status == expect
    assert r.exit_price == (96.0 if expect == "SCORE_EXIT" else None)


# ------------------------------------------------------------- restart / recovery ---
def test_a_restart_recovers_every_open_position_of_every_date_and_closes_leftovers_exactly_once(book, monkeypatch):
    old, older, today = put(book, "AAA", entered=et(*MON, 14)), put(book, "CCC", entered=et(2026, 10, 16, 11)), put(book, "BBB", entered=et(*TUE, 10))
    before = count(book)
    extra = [brow("BBB", 100.7), brow("CCC", 100.7)]
    monkeypatch.setattr(dts, "_last_tick", 0.0)                                    # a fresh process
    run(book, monkeypatch, et(*TUE, 10, 30), price=100.7, score=69, extra=extra)
    a, c, b = row(book, old), row(book, older), row(book, today)
    assert (a.status, a.exit_price) == ("CLEANUP", 100.7) and (c.status, c.exit_price) == ("CLEANUP", 100.7)
    assert b.status == "OPEN" and b.last == 100.7 and b.profit == 70.0             # today's: picked up and managed, not closed
    assert count(book) == before                                                   # no duplicated position
    first_a = a.exit_time
    monkeypatch.setattr(dts, "_last_tick", 0.0)                                    # a second restart
    run(book, monkeypatch, et(*TUE, 10, 31), price=99.1, score=69, extra=[brow("BBB", 100.9), brow("CCC", 99.1)])
    a2, b2 = row(book, old), row(book, today)
    assert (a2.status, a2.exit_price, a2.exit_time) == ("CLEANUP", 100.7, first_a)  # the leftover's close is not redone
    assert b2.status == "OPEN" and b2.last == 100.9
    assert count(book) == before and count(book, status="OPEN") == 1


def test_a_new_signal_never_doubles_a_position_already_open_or_traded_today(book, monkeypatch):
    put(book, "AAA", entered=et(*TUE, 10))                                         # open today
    put(book, "DDD", entered=et(*TUE, 10), status="STOP")                          # already traded (and closed) today
    monkeypatch.setattr(dts, "_entry_allowed", lambda *a, **k: True)               # the gate would allow it
    monkeypatch.setattr(dts, "_fresh_quote", lambda sym, now=None: (dict(FRESH_Q), ""))
    import intraday_safety
    monkeypatch.setattr(intraday_safety, "tradable", lambda r: True)

    def sig(sym):
        return {"symbol": sym, "signal": "BUY", "score": 72.0, "entry": 100.2, "now": 100.2, "sl": 99.0, "tp": 102.0,
                "tradable": True, "data": {"ok": True, "price_age_s": 5}}
    monkeypatch.setattr(dts, "_et_now", lambda: et(*TUE, 11))
    monkeypatch.setattr(dts, "_board", lambda spec: {"buyers": [sig("AAA"), sig("DDD"), sig("YYY")]})
    dts.tick(force=True)
    assert count(book, symbol="AAA") == 1 and count(book, symbol="DDD") == 1       # one per strategy + symbol + day
    assert count(book, symbol="YYY", status="OPEN") == 1                           # but a new symbol may open
    with book.Session() as s:
        y = s.query(DemoTrade).filter_by(symbol="YYY").one()
        assert y.entry_price == 100.0 and y.shares == 100 and y.session_date == date(*TUE)        # priced at the quote, $10k
        assert _utc(y.entry_time).astimezone(ET) == et(*TUE, 11)                   # stamped from the tracker's own clock
    dts.tick(force=True)                                                           # the same tick again: still one each
    assert count(book, symbol="YYY") == 1 and count(book, symbol="AAA") == 1


def test_a_leftover_is_cleaned_up_and_never_blocks_or_doubles_todays_entry_for_the_same_symbol(book, monkeypatch):
    tid = put(book, "AAA", entered=et(*MON, 14))
    monkeypatch.setattr(dts, "_entry_allowed", lambda *a, **k: True)
    monkeypatch.setattr(dts, "_fresh_quote", lambda sym, now=None: (dict(FRESH_Q), ""))
    import intraday_safety
    monkeypatch.setattr(intraday_safety, "tradable", lambda r: True)
    sig = {"symbol": "AAA", "signal": "BUY", "score": 72.0, "entry": 100.2, "now": 100.2, "sl": 99.0, "tp": 102.0,
           "tradable": True, "data": {"ok": True, "price_age_s": 5}}
    monkeypatch.setattr(dts, "_et_now", lambda: et(*TUE, 11))
    monkeypatch.setattr(dts, "_board", lambda spec: {"buyers": [sig]})
    dts.tick(force=True)
    l = row(book, tid)
    assert (l.status, l.exit_price) == ("CLEANUP", 100.2)                          # the leftover is closed (the cleanup is written first)...
    assert count(book, symbol="AAA") == 2 and count(book, symbol="AAA", status="OPEN") == 1   # ...and today's entry opens: never two OPEN
    first = l.exit_time
    dts.tick(force=True)                                                           # again: nothing doubles, the cleanup is not redone
    assert count(book, symbol="AAA") == 2 and count(book, symbol="AAA", status="OPEN") == 1
    assert row(book, tid).exit_time == first


# -------------------------------------------------------------- executable prices ---
def test_a_stale_board_price_or_a_wait_row_is_not_an_executable_price(book, monkeypatch):
    tid = put(book)
    stale = brow("AAA", 98.0, age=900)
    wait = {"symbol": "AAA", "signal": "WAIT", "score": None, "score_raw": 68, "now": 98.0, "wait": True,
            "data": {"ok": False, "price_age_s": 5}}
    for bad in (stale, wait):
        run(book, monkeypatch, et(*TUE, 11), extra=[bad])
        r = row(book, tid)
        assert r.status == "OPEN" and r.exit_price is None                         # a breached stop at a stale price: no fill
    run(book, monkeypatch, et(*TUE, 11, 2), price=98.0, score=68)                  # the same price, fresh: now it executes
    r = row(book, tid)
    assert (r.status, r.exit_price) == ("STOP", 98.0)


def test_a_live_quote_is_an_executable_price_when_no_board_has_one(book, monkeypatch):
    tid = put(book)
    monkeypatch.setattr(dts, "_quote_price", lambda sym: 98.0)
    run(book, monkeypatch, et(*TUE, 11))
    r = row(book, tid)
    assert (r.status, r.exit_price) == ("STOP", 98.0)


def test_todays_position_with_no_price_at_the_cutoff_waits_and_is_closed_at_the_first_reliable_price(book, monkeypatch):
    tid = put(book, last=None)
    run(book, monkeypatch, et(*TUE, 15, 45))                                       # the close is due and there is no price
    r = row(book, tid)
    assert r.status == "OPEN" and r.exit_price is None and r.last is None
    run(book, monkeypatch, et(*TUE, 16, 10), price=100.8, score=69)                # a reliable price arrives: closed at it
    r = row(book, tid)
    assert (r.status, r.exit_price) == ("EOD_CLOSE", 100.8)


def test_an_unexpected_closure_the_calendar_does_not_know_never_invents_a_fill(book, monkeypatch):
    tid = put(book, stop=99.0, entered=et(*MON, 14))
    stale = brow("AAA", 94.0, age=61000)
    for hh, mm in ((9, 35), (12, 0), (16, 5)):
        run(book, monkeypatch, et(*TUE, hh, mm), extra=[stale])
        assert row(book, tid).status == "OPEN"


# -------------------------------------------------------------- exactly-once closes ---
def test_a_database_failure_during_the_tick_loses_nothing_and_the_next_tick_closes_once(book, monkeypatch):
    tid, left = put(book), put(book, "OLD", entered=et(*MON, 14))
    real = book.Session
    calls = {"n": 0}

    class Flaky:
        def __call__(self):
            s = real()
            orig = s.commit

            def commit():
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("connection lost")
                return orig()
            s.commit = commit
            return s
    import database
    monkeypatch.setattr(database, "SessionLocal", Flaky())
    run(book, monkeypatch, et(*TUE, 11), price=98.0, score=68, extra=[brow("OLD", 97.0)])   # the commit fails: nothing persisted
    assert row(book, tid).status == "OPEN" and row(book, left).status == "OPEN"
    run(book, monkeypatch, et(*TUE, 11, 1), price=98.0, score=68, extra=[brow("OLD", 97.0)])  # recovered: closed exactly once
    r, l = row(book, tid), row(book, left)
    assert (r.status, r.exit_price, r.profit) == ("STOP", 98.0, -200.0)
    assert (l.status, l.exit_price, l.profit) == ("CLEANUP", 97.0, -300.0)
    assert count(book) == 2


def test_a_competing_close_is_never_overwritten_or_duplicated(book, monkeypatch):
    tid = put(book)
    real_reason = dts._exit_reason

    def competing(t, last, score, after_close):
        with book.engine.begin() as c:                                             # another process closes it first
            c.execute(text("UPDATE demo_trades SET status='TARGET', exit_price=102.0, last_price=102.0, "
                           "profit=200.0, profit_pct=2.0 WHERE id = :i AND status = 'OPEN'"), {"i": tid})
        return real_reason(t, last, score, after_close)
    monkeypatch.setattr(dts, "_exit_reason", competing)
    run(book, monkeypatch, et(*TUE, 11), price=98.0, score=68)                     # this tick wants a STOP at 98
    r = row(book, tid)
    assert (r.status, r.exit_price, r.profit) == ("TARGET", 102.0, 200.0)          # the first close stands


def test_a_competing_close_of_a_leftover_is_never_overwritten_by_the_cleanup(book, monkeypatch):
    tid = put(book, entered=et(*MON, 14))
    real_pnl = dts._pnl

    def competing(side, entry, last, shares):
        with book.engine.begin() as c:
            c.execute(text("UPDATE demo_trades SET status='STOP', exit_price=99.0, last_price=99.0, profit=-100.0 "
                           "WHERE id = :i AND status = 'OPEN'"), {"i": tid})
        return real_pnl(side, entry, last, shares)
    monkeypatch.setattr(dts, "_pnl", competing)
    run(book, monkeypatch, et(*TUE, 11), price=97.0, score=68)                     # this tick wants CLEANUP at 97
    r = row(book, tid)
    assert (r.status, r.exit_price, r.profit) == ("STOP", 99.0, -100.0)


def test_a_closed_trades_pnl_is_never_refreshed_by_a_stale_snapshot(book, monkeypatch):
    tid = put(book)
    with book.engine.begin() as c:
        c.execute(text("UPDATE demo_trades SET status='STOP', exit_price=99.0, last_price=99.0, profit=-100.0 "
                       "WHERE id = :i"), {"i": tid})
    run(book, monkeypatch, et(*TUE, 11), price=101.0, score=68)
    r = row(book, tid)
    assert (r.status, r.exit_price, r.last, r.profit) == ("STOP", 99.0, 99.0, -100.0)


# ----------------------------------------------------- entry lock, reporting, rollback ---
def put_unlocked(book, **kw):
    """A position that exists from before the lock (inserted with the lock briefly off)."""
    entry_lock.set_locked(False, "seed", book.engine)
    tid = put(book, **kw)
    entry_lock.set_locked(True, "release", book.engine)
    return tid


def test_the_entry_lock_blocks_new_entries_but_never_monitoring_or_exits(book, monkeypatch):
    entry_lock.install(book.engine)
    entry_lock.set_locked(True, "release", book.engine)
    tid, left = put_unlocked(book), put_unlocked(book, symbol="OLD", entered=et(*MON, 14))
    assert entry_lock.is_locked(book.engine, fresh=True) is True                  # (read once: an in-memory SQLite shares ONE connection)
    monkeypatch.setattr(dts, "_entry_allowed", lambda *a, **k: False)             # what the real gate answers while locked
    monkeypatch.setattr(dts, "_fresh_quote", lambda sym, now=None: (dict(FRESH_Q), ""))   # the quote alone would allow it
    sig = {"symbol": "NEW", "signal": "BUY", "score": 72.0, "entry": 50.0, "now": 50.0, "tradable": True,
           "data": {"ok": True, "price_age_s": 5}}
    monkeypatch.setattr(dts, "_et_now", lambda: et(*TUE, 11))                     # inside the entry window
    monkeypatch.setattr(dts, "_board", lambda spec: {"buyers": [sig], "neutral": [brow("AAA", 98.0), brow("OLD", 97.0)]})
    import intraday_safety
    monkeypatch.setattr(intraday_safety, "tradable", lambda r: True)
    dts.tick(force=True)
    r, l = row(book, tid), row(book, left)
    assert (r.status, r.exit_price) == ("STOP", 98.0)                              # the exit went through the triggers
    assert (l.status, l.exit_price) == ("CLEANUP", 97.0)                           # and so did the cleanup
    assert count(book, symbol="NEW") == 0                                          # and the entry did not happen
    with book.engine.begin() as c:                                                 # the trigger itself, for any build
        with pytest.raises(Exception):
            c.execute(text("INSERT INTO demo_trades (strategy, symbol, side, shares, session_date, entry_price, status) "
                           "VALUES ('grok','X','LONG',100,'2026-10-20',1.0,'OPEN')"))


def test_a_rollback_is_refused_while_any_position_is_open_and_allowed_when_flat(book, monkeypatch):
    entry_lock.install(book.engine)
    entry_lock.set_locked(False, "live", book.engine)
    left, tid = put(book, "OLD", entered=et(*MON, 14)), put(book, "AAA")           # a leftover and today's position
    assert entry_lock.open_positions(book.engine)["demo_trades"] == 2              # counted whatever their dates
    refused = rt.rollback_prepare(engine=book.engine)
    assert refused["ok"] is False and refused["open_positions"]["demo_trades"] == 2
    entry_lock.set_locked(True, "release", book.engine)
    run(book, monkeypatch, et(*TUE, 11), price=100.5, score=69, extra=[brow("OLD", 97.0)])   # monitoring continues while locked
    assert row(book, left).status == "CLEANUP" and row(book, tid).status == "OPEN"
    assert rt.rollback_prepare(engine=book.engine)["ok"] is False                  # today's position still blocks it
    run(book, monkeypatch, et(*TUE, 15, 45), price=100.5, score=69)
    assert row(book, tid).status == "EOD_CLOSE"
    assert rt.rollback_prepare(engine=book.engine)["ok"] is True                   # flat after the bell: allowed


def test_the_blotter_lists_leftovers_apart_from_todays_rows_and_totals(book, monkeypatch):
    put(book, "AAA", entered=et(*MON, 14))                                         # a leftover, not yet cleaned up
    put(book, "TDY", entered=et(*TUE, 10), last=101.0)                             # today's
    with book.engine.begin() as c:
        c.execute(text("UPDATE demo_trades SET profit = 100.0 WHERE symbol = 'TDY'"))
        c.execute(text("UPDATE demo_trades SET profit = -40.0 WHERE symbol = 'AAA'"))
    monkeypatch.setattr(dts, "_et_now", lambda: et(*TUE, 12))
    monkeypatch.setattr(dts, "tick", lambda force=False: None)
    out = dts.blotter()
    assert [r["symbol"] for r in out["rows"]] == ["TDY"] and out["total_profit"] == 100.0 and out["open"] == 1
    assert out["carried_open_count"] == 1 and out["carried_open"][0]["symbol"] == "AAA"
    assert out["carried_open"][0]["opened_on"] == "2026-10-19"
    assert out["rules"]["eod_close_et"] == "15:45" and out["rules"]["entry_window_et"] == ["10:00", "15:00"]
    past = dts.blotter(date="2026-10-19")                                          # a past date's blotter is its own day
    assert [r["symbol"] for r in past["rows"]] == ["AAA"] and past["carried_open"] == []


def test_the_gap_effect_and_realized_pnl_are_reported_without_distorting_the_entry_date_view(book, monkeypatch):
    left = put(book, "AAA", stop=99.0, entered=et(*MON, 14))
    put(book, "TDY", entered=et(*TUE, 10), stop=99.0, last=100.0)
    run(book, monkeypatch, et(*TUE, 10, 5), price=97.0, score=68, extra=[brow("TDY", 94.0)])
    assert row(book, left).status == "CLEANUP"
    monkeypatch.setattr(dts, "tick", lambda force=False: None)
    tue = dts.blotter(date="2026-10-20")
    assert [r["symbol"] for r in tue["rows"]] == ["TDY"]
    r = tue["rows"][0]
    assert (r["status"], r["exit_price"], r["exit_basis"], r["gap_effect"]) == ("STOP", 94.0, "GAP", -500.0)   # 94 vs the 99 level
    assert tue["total_profit"] == -600.0 and tue["gap_effect_total"] == -500.0
    assert tue["realized_on_date"] == -900.0 and tue["closed_on_date"] == 2       # both closed today (the cleanup included)
    mon = dts.blotter(date="2026-10-19")
    assert [r["symbol"] for r in mon["rows"]] == ["AAA"] and mon["total_profit"] == -300.0
    assert (mon["rows"][0]["exit_basis"], mon["rows"][0]["exit_date"], mon["rows"][0]["exit_reason_label"]) == \
        (None, "2026-10-20", "Cleanup (prior-day leftover)")
    assert mon["gap_effect_total"] == 0.0


def test_a_no_price_close_is_in_no_total_win_rate_or_realized_pnl(book, monkeypatch):
    tid = put(book, entered=et(*MON, 10), last=100.2)
    run(book, monkeypatch, et(*NEXT_MON, 16, 30))
    assert row(book, tid).status == "NO_PRICE"
    monkeypatch.setattr(dts, "tick", lambda force=False: None)
    out = dts.blotter(date="2026-10-19")
    assert out["total_profit"] == 0 and out["wins"] == 0
    assert out["rows"][0]["profit"] is None and out["rows"][0]["exit_basis"] is None
    assert dts.blotter(date="2026-10-26")["realized_on_date"] == 0.0


# ====================================================================== live engine ===
@pytest.fixture
def sigdb(monkeypatch):
    engine = create_engine("sqlite://")
    with engine.begin() as c:
        c.execute(text("CREATE TABLE intraday_sig_v2 (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, session_date TEXT,"
                       " state TEXT, side TEXT, is_open BOOLEAN DEFAULT 1, entry REAL, now_price REAL, exit_price REAL,"
                       " tp REAL, sl REAL, best_price REAL, atr REAL, be_moved BOOLEAN DEFAULT 0, score_in REAL,"
                       " score_now REAL, signal_ts TEXT, status_ts TEXT, exit_reason TEXT, was_state TEXT)"))
        c.execute(text("CREATE TABLE intraday_sig_v2_changes (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT,"
                       " session_date TEXT, ts TEXT, from_state TEXT, to_state TEXT, pl_pct REAL)"))
        c.execute(text("CREATE TABLE intraday_sig_v2_pending (symbol TEXT, session_date TEXT, side TEXT, count INTEGER,"
                       " first_ts TEXT, last_ts TEXT, PRIMARY KEY (symbol, session_date))"))
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(eng, "_db", lambda: Session())
    monkeypatch.setattr(eng, "_ready", True)
    return types.SimpleNamespace(engine=engine, Session=Session)


def sig_row(db, symbol="AAA", session="2026-10-19", side="BUY", entry=100.0, sl=99.0, tp=102.0):
    with db.engine.begin() as c:
        c.execute(text("INSERT INTO intraday_sig_v2 (symbol, session_date, state, side, is_open, entry, now_price, tp, sl, "
                       "best_price, atr, score_in, score_now, signal_ts, status_ts) VALUES (:s,:d,:st,:sd,1,:e,:e,:tp,:sl,:e,"
                       "2.0,72,72,'2026-10-19T14:00:00-04:00','2026-10-19T14:00:00-04:00')"),
                  {"s": symbol, "d": session, "st": side, "sd": side, "e": entry, "tp": tp, "sl": sl})


def step(db, symbol, now, price, score=72.0, spy_above=None):
    return eng.step(symbol, {"score": score, "spot": price, "call": "NO EDGE", "side": None, "status": "OK",
                             "spy_above_vwap": spy_above}, now)


def changes(db):
    with db.engine.connect() as c:
        return c.execute(text("SELECT to_state FROM intraday_sig_v2_changes")).scalars().all()


def test_a_signal_opened_on_an_earlier_session_is_found_and_managed(sigdb):
    sig_row(sigdb)
    with sigdb.Session() as s:
        assert eng._open_row(s, "AAA", "2026-10-20")["session_date"] == "2026-10-19"
    out = step(sigdb, "AAA", et(*TUE, 10), 100.4)
    assert out["is_open"] is True and out["now"] == 100.4 and out["session"] == "2026-10-19"


def test_a_carried_signal_exits_on_its_rules_and_is_closed_and_logged_exactly_once(sigdb):
    sig_row(sigdb)
    out = step(sigdb, "AAA", et(*TUE, 10), 98.5)                                   # stop
    assert out["is_open"] is False and out["exit_reason"] == "SL"
    assert changes(sigdb) == ["CLOSED"]
    again = step(sigdb, "AAA", et(*TUE, 10, 1), 97.0)                              # a second scan: nothing to close
    assert changes(sigdb) == ["CLOSED"]
    with sigdb.engine.connect() as c:
        assert c.execute(text("SELECT exit_price FROM intraday_sig_v2")).scalar() == 98.5


def test_two_scans_racing_to_close_one_signal_write_one_close(sigdb, monkeypatch):
    sig_row(sigdb)
    with sigdb.Session() as a, sigdb.Session() as b:
        row_a, row_b = dict(eng._open_row(a, "AAA", "2026-10-20")), dict(eng._open_row(b, "AAA", "2026-10-20"))
        sc = {"score": 72.0, "spot": 98.0, "spy_above_vwap": None}
        eng._manage_open(a, row_a, sc, et(*TUE, 10))
        a.commit()
        out = eng._manage_open(b, row_b, dict(sc, spot=97.0), et(*TUE, 10, 1))      # the slower scan holds a stale open row
        b.commit()
    assert changes(sigdb) == ["CLOSED"] and out["is_open"] is False
    with sigdb.engine.connect() as c:
        assert c.execute(text("SELECT exit_price FROM intraday_sig_v2")).scalar() == 98.0     # the first close stands


def test_the_end_of_day_exit_fires_at_a_trading_close_not_on_a_date_change_weekend_or_holiday(sigdb):
    sig_row(sigdb, "AAA", session="2026-10-19")
    assert step(sigdb, "AAA", et(*TUE, 10), 100.5)["is_open"] is True               # next morning: still open
    assert step(sigdb, "AAA", et(*TUE, 15, 54), 100.5)["is_open"] is True
    assert step(sigdb, "AAA", et(*TUE, 15, 55), 100.5)["exit_reason"] == "EOD"
    sig_row(sigdb, "BBB", session="2026-10-23")
    assert step(sigdb, "BBB", et(*SAT, 15, 56), 100.5)["is_open"] is True           # Saturday: no close
    sig_row(sigdb, "CCC", session="2026-11-25")
    assert step(sigdb, "CCC", et(2026, 11, 26, 15, 56), 100.5)["is_open"] is True   # Thanksgiving: no close
    assert step(sigdb, "CCC", et(2026, 11, 27, 15, 56), 100.5)["exit_reason"] == "EOD"


@pytest.mark.parametrize("side, entry, sl, tp, spot, expect", [
    ("BUY", 100.0, 99.0, 102.0, 98.5, "SL"), ("SELL", 100.0, 101.0, 98.0, 101.5, "SL"),       # symmetric stops
    ("BUY", 100.0, 99.0, 102.0, 102.5, "TP"), ("SELL", 100.0, 101.0, 98.0, 97.5, "TP")])      # symmetric targets
def test_carried_buy_and_sell_signals_exit_symmetrically(sigdb, side, entry, sl, tp, spot, expect):
    sig_row(sigdb, side=side, entry=entry, sl=sl, tp=tp)
    assert step(sigdb, "AAA", et(*TUE, 11), spot)["exit_reason"] == expect


def test_a_restart_recovers_open_signals_of_every_session_and_the_scan_covers_symbols_outside_the_universe(sigdb, monkeypatch):
    sig_row(sigdb, "AAA", session="2026-10-20")                                     # opened the day before: carried
    sig_row(sigdb, "ZZZ", session="2026-10-20")                                     # no longer in the universe
    sig_row(sigdb, "OLD", session="2026-10-16")                                     # older than the carry window: stale
    assert eng._active_symbols("2026-10-21") == {"AAA", "ZZZ"}
    scored = []
    monkeypatch.setattr(eng, "universe", lambda: ["AAA", "BBB"])
    monkeypatch.setattr(eng.im, "bars_meta", lambda s, critical=False: ([], False))
    monkeypatch.setattr(eng.im, "score", lambda s: scored.append(s) or {"score": 55.0, "spot": 100.0, "status": "OK"})
    monkeypatch.setattr(eng, "step", lambda s, sc, now: None)
    monkeypatch.setattr(eng, "_compose", lambda rows, now, spy: {"rows": rows})
    monkeypatch.setattr(eng, "_last_scored", {})
    out = eng.scan_once(et(2026, 10, 21, 11))
    assert sorted(scored) == ["AAA", "BBB", "ZZZ"] and sorted(r["symbol"] for r in out["rows"]) == ["AAA", "BBB", "ZZZ"]


def test_the_log_and_the_summary_show_carried_signals_without_changing_a_past_sessions_numbers(sigdb, monkeypatch):
    sig_row(sigdb, "AAA", session="2026-10-19")
    monkeypatch.setattr(eng, "_session", lambda now=None: "2026-10-20")
    rows = eng.log_rows("2026-10-20")
    assert [r["symbol"] for r in rows] == ["AAA"] and rows[0]["carried"] is True and rows[0]["session"] == "2026-10-19"
    assert eng.summary("2026-10-20")["carried_open"] == 1
    past = eng.summary("2026-10-19")
    assert past["issued"] == 1 and past["open"] == 1 and past["carried_open"] == 0
    assert eng.log_rows("2026-10-19")[0]["carried"] is False                        # its own session: not "carried"


def test_the_engine_ends_the_day_five_minutes_before_an_early_close(sigdb):
    sig_row(sigdb, "AAA", session="2026-11-27")
    assert step(sigdb, "AAA", et(2026, 11, 27, 12, 54), 100.5)["is_open"] is True
    assert step(sigdb, "AAA", et(2026, 11, 27, 12, 55), 100.5)["exit_reason"] == "EOD"


@pytest.mark.parametrize("stale", [True, False])
def test_the_engine_books_no_exit_at_a_stale_price(sigdb, stale):
    sig_row(sigdb)
    out = eng.step("AAA", {"score": 72.0, "spot": 98.0, "call": "NO EDGE", "side": None,
                           "status": "STOCK_STALE" if stale else "OK", "stock_stale": stale,
                           "spy_above_vwap": None}, et(*TUE, 11))
    assert out["is_open"] is stale
    assert (out["exit_reason"] == "SL") is (not stale)


# ============================================================================== early closes ===
def test_the_calendar_knows_early_closes_and_operator_closures(monkeypatch):
    assert mc.is_early_close(date(2026, 11, 27)) and mc.is_early_close(date(2026, 12, 24))
    assert mc.is_early_close(date(2025, 7, 3)) and mc.is_early_close(date(2024, 7, 3))
    assert not mc.is_early_close(date(2026, 7, 3)) and not mc.is_early_close(date(2026, 10, 23))
    assert not mc.is_early_close(date(2027, 12, 24))
    assert str(mc.close_time(date(2026, 11, 27))) == "13:00:00" and str(mc.close_time(date(2026, 10, 23))) == "16:00:00"
    assert str(mc.eod_time(date(2026, 11, 27))) == "12:55:00" and str(mc.eod_time(date(2026, 10, 23))) == "15:55:00"
    monkeypatch.setenv("MARKET_EXTRA_CLOSURES", "2027-01-09, not-a-date, 2026-10-22")
    assert not mc.is_trading_day(date(2026, 10, 22)) and mc.is_trading_day(date(2026, 10, 21))
    monkeypatch.setenv("MARKET_EXTRA_EARLY_CLOSES", "2026-10-23")
    assert mc.is_early_close(date(2026, 10, 23))


# ================================================================================ instruments ===
def test_only_stocks_and_etfs_are_modelled():
    got = [mc.instrument_kind(x) for x in ("AAPL", "SPY", "QQQ", "BRK.B", "ES", "/ES", "ES=F", "SPX", "^VIX", "VIX", "")]
    assert got == ["equity", "equity", "equity", "equity", "equity", "future", "future", "index", "index", "index", "unknown"]
