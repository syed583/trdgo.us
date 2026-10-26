"""Open paper positions are carried across midnight, weekends and holidays until a RULE closes them.

Nothing is closed merely because the date changed; every close happens exactly once; the entry lock blocks new entries
and never position monitoring; a restart recovers every open position of any date. Mocked: no API calls, no production
data (SQLite databases built here)."""
import types
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import demo_trade_service as dts
import entry_lock
import intraday_signal_v2 as eng
import market_calendar as mc
import release_tool as rt
from models_demo_trades import DemoTrade

ET = ZoneInfo("America/New_York")


def et(y, m, d, hh=10, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


MON, TUE = (2026, 10, 19), (2026, 10, 20)           # Monday, Tuesday
FRI, SAT, NEXT_MON = (2026, 10, 23), (2026, 10, 24), (2026, 10, 26)


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


# ================================================================ the timed exit ===
def trade(entry, **kw):
    return types.SimpleNamespace(entry_time=entry.astimezone(timezone.utc) if entry else None,
                                 session_date=entry.date() if entry else None, **kw)


@pytest.mark.parametrize("entered, now, expect", [
    (et(*MON, 10), et(*MON, 15, 59), False),                  # the original rule: not before the close
    (et(*MON, 10), et(*MON, 16, 0), True),                    # ...at the close
    (et(*MON, 5), et(*MON, 16, 5), True),                     # a pre-market entry exits at that day's close
    (et(*MON, 10), et(*TUE, 10), False),                      # THE DATE CHANGED: nothing closes by itself
    (et(*MON, 10), et(*TUE, 15, 59), False),
    (et(*MON, 10), et(*TUE, 16, 0), True),                    # a missed close fires at the next close
    (et(*MON, 17), et(*MON, 18), False),                      # an after-hours entry is not closed the same evening
    (et(*MON, 17), et(*TUE, 10), False),
    (et(*MON, 17), et(*TUE, 16, 0), True),                    # ...it exits at the next trading day's close
    (et(*FRI, 10), et(*SAT, 16, 30), False),                  # a Saturday close does not exist
    (et(*FRI, 17), et(2026, 10, 25, 16, 30), False),          # nor a Sunday one
    (et(*FRI, 10), et(*NEXT_MON, 15, 0), False),
    (et(*FRI, 10), et(*NEXT_MON, 16, 0), True),               # the first close after the weekend
    (et(2026, 11, 25, 10), et(2026, 11, 26, 16, 0), False),   # Thanksgiving: the exchange is shut
    (et(2026, 11, 25, 10), et(2026, 11, 27, 16, 0), True),    # the next trading day's close
    (None, et(*MON, 16, 5), False),
])
def test_the_timed_exit_fires_at_a_close_never_because_the_date_changed(entered, now, expect):
    assert dts._exit_timed(trade(entered), now) is expect


# ============================================================ the paper tracker ===
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
    monkeypatch.setattr(dts, "_last_tick", 0.0)
    monkeypatch.setattr(dts, "_entry_allowed", lambda *a, **k: False)       # these tests are about EXITS and carry-over
    return types.SimpleNamespace(engine=engine, Session=Session, board={})


def put(book, symbol="AAA", side="LONG", entered=None, entry=100.0, stop=99.0, target=102.0, score=70.0, strategy="grok",
        last=None):
    entered = entered or et(*MON, 14)
    with book.Session() as s:
        t = DemoTrade(strategy=strategy, symbol=symbol, side=side, shares=100, session_date=entered.date(),
                      entry_time=entered.astimezone(timezone.utc), entry_price=entry, stop=stop, target=target,
                      score_at_entry=score, last_price=last, status="OPEN")
        s.add(t)
        s.commit()
        return t.id


def run(book, monkeypatch, now, price=None, score=None, symbol="AAA", extra=None):
    """One tracker tick at ``now`` (ET) with the boards showing ``price`` / ``score`` for ``symbol``."""
    rows = []
    if price is not None:
        rows.append({"symbol": symbol, "signal": "NEUTRAL", "score": score, "now": price, "data": {"ok": True, "price_age_s": 5}})
    rows += list(extra or [])
    monkeypatch.setattr(dts, "_et_now", lambda: now)
    monkeypatch.setattr(dts, "_board", lambda spec: {"neutral": rows})
    dts.tick(force=True)


def row(book, tid):
    with book.Session() as s:
        t = s.get(DemoTrade, tid)
        return types.SimpleNamespace(status=t.status, exit_price=t.exit_price, last=t.last_price, profit=t.profit,
                                     exit_time=t.exit_time, session=t.session_date)


def test_a_position_is_still_monitored_after_midnight_and_not_closed_by_the_date(book, monkeypatch):
    tid = put(book)                                                     # opened Monday 14:00, still open
    run(book, monkeypatch, et(*TUE, 10, 0), price=100.5, score=68)
    r = row(book, tid)
    assert r.status == "OPEN" and r.last == 100.5 and r.profit == 50.0 and r.exit_time is None


def test_a_stop_or_target_hit_the_next_day_closes_it_once_at_the_level(book, monkeypatch):
    stopped, won = put(book, "AAA"), put(book, "BBB")
    run(book, monkeypatch, et(*TUE, 10), price=98.5, score=68, extra=[
        {"symbol": "BBB", "signal": "NEUTRAL", "score": 68, "now": 103.0, "data": {"ok": True, "price_age_s": 5}}])
    a, b = row(book, stopped), row(book, won)
    assert (a.status, a.exit_price, a.profit) == ("STOP", 99.0, -100.0)            # clean fill AT the stop
    assert (b.status, b.exit_price, b.profit) == ("TARGET", 102.0, 200.0)
    first = a.exit_time
    run(book, monkeypatch, et(*TUE, 10, 5), price=95.0, score=40)                  # later ticks never touch a closed trade
    again = row(book, stopped)
    assert (again.status, again.exit_price, again.profit, again.exit_time) == ("STOP", 99.0, -100.0, first)


def test_a_missed_close_fires_the_timed_exit_at_the_next_close_not_at_restart(book, monkeypatch):
    tid = put(book, entered=et(*MON, 10))
    run(book, monkeypatch, et(*TUE, 9, 45), price=100.4, score=69)                 # restart the next morning
    assert row(book, tid).status == "OPEN"
    run(book, monkeypatch, et(*TUE, 16, 1), price=100.8, score=69)
    r = row(book, tid)
    assert (r.status, r.exit_price) == ("TIMED_EXIT", 100.8)


@pytest.mark.parametrize("side, entry, stop, target, up, down", [
    ("LONG", 100.0, 99.0, 102.0, 98.5, 103.0),
    ("SHORT", 100.0, 101.0, 98.0, 101.5, 97.0),                                    # the mirror image
])
def test_buy_and_sell_positions_carry_and_exit_symmetrically(book, monkeypatch, side, entry, stop, target, up, down):
    adverse, favourable = put(book, "AAA", side, entry=entry, stop=stop, target=target), \
        put(book, "BBB", side, entry=entry, stop=stop, target=target)
    run(book, monkeypatch, et(*TUE, 11), price=up, score=None, extra=[
        {"symbol": "BBB", "signal": "NEUTRAL", "score": None, "now": down, "data": {"ok": True, "price_age_s": 5}}])
    assert row(book, adverse).status == "STOP" and row(book, adverse).profit == -100.0
    assert row(book, favourable).status == "TARGET" and row(book, favourable).profit == 200.0


@pytest.mark.parametrize("side, now_score, expect", [
    ("LONG", 54.0, "SCORE_EXIT"), ("LONG", 60.0, "OPEN"), ("SHORT", 86.0, "SCORE_EXIT"), ("SHORT", 80.0, "OPEN")])
def test_the_score_exit_still_applies_to_a_carried_position(book, monkeypatch, side, now_score, expect):
    tid = put(book, side=side, score=70.0, entry=100.0, stop=90.0 if side == "LONG" else 110.0,
              target=110.0 if side == "LONG" else 90.0)
    run(book, monkeypatch, et(*TUE, 11), price=100.0, score=now_score)
    assert row(book, tid).status == expect


def test_a_restart_recovers_every_open_position_of_every_date_exactly_once(book, monkeypatch):
    ids = [put(book, "AAA", entered=et(*MON, 14)), put(book, "BBB", entered=et(*TUE, 11)),
           put(book, "CCC", entered=et(2026, 10, 21, 9, 45))]
    with book.Session() as s:
        before = s.query(DemoTrade).count()
    boards_extra = [{"symbol": sym, "signal": "NEUTRAL", "score": 69, "now": 100.7, "data": {"ok": True, "price_age_s": 5}}
                    for sym in ("BBB", "CCC")]
    monkeypatch.setattr(dts, "_last_tick", 0.0)                                    # a fresh process
    run(book, monkeypatch, et(2026, 10, 21, 10, 30), price=100.7, score=69, extra=boards_extra)
    for i in ids:
        r = row(book, i)
        assert r.status == "OPEN" and r.last == 100.7                              # every one picked up, none closed
    with book.Session() as s:
        assert s.query(DemoTrade).count() == before                                # no duplicated position
    run(book, monkeypatch, et(2026, 10, 21, 10, 31), price=100.7, score=69, extra=boards_extra)   # a second restart
    with book.Session() as s:
        assert s.query(DemoTrade).count() == before and s.query(DemoTrade).filter_by(status="OPEN").count() == 3


def test_a_new_signal_never_doubles_a_position_that_is_still_open_from_an_earlier_date(book, monkeypatch):
    put(book, "AAA", entered=et(*MON, 14))
    monkeypatch.setattr(dts, "_entry_allowed", lambda *a, **k: True)               # the gate would allow it
    monkeypatch.setattr(dts, "_can_open", lambda n=None: True)
    sig = {"symbol": "AAA", "signal": "BUY", "score": 72.0, "entry": 100.2, "now": 100.2, "sl": 99.0, "tp": 102.0,
           "tradable": True, "data": {"ok": True, "price_age_s": 5}}
    monkeypatch.setattr(dts, "_et_now", lambda: et(*TUE, 11))
    monkeypatch.setattr(dts, "_board", lambda spec: {"buyers": [sig]})
    monkeypatch.setattr(dts.intraday_safety if hasattr(dts, "intraday_safety") else __import__("intraday_safety"),
                        "tradable", lambda r: True)
    dts.tick(force=True)
    with book.Session() as s:
        assert s.query(DemoTrade).filter_by(symbol="AAA").count() == 1
    put(book, "ZZZ", entered=et(*MON, 14))                                         # but a different symbol may open
    sig2 = dict(sig, symbol="YYY")
    monkeypatch.setattr(dts, "_board", lambda spec: {"buyers": [sig2]})
    dts.tick(force=True)
    with book.Session() as s:
        assert s.query(DemoTrade).filter_by(symbol="YYY").count() == 1


def test_missing_price_data_changes_nothing_and_never_invents_a_fill(book, monkeypatch):
    tid = put(book, last=None)
    run(book, monkeypatch, et(*TUE, 11))                                           # no board row, no quote at all
    r = row(book, tid)
    assert r.status == "OPEN" and r.exit_price is None and r.last is None          # not even a refresh from the entry price
    run(book, monkeypatch, et(*TUE, 16, 30))                                       # the close is due and STILL no price:
    r = row(book, tid)
    assert r.status == "OPEN" and r.exit_price is None                             # deferred, never closed at an invented price
    run(book, monkeypatch, et(*TUE, 16, 45), price=100.8, score=69)                # a reliable price arrives: it closes at it
    r = row(book, tid)
    assert (r.status, r.exit_price) == ("TIMED_EXIT", 100.8)


def test_a_close_with_no_reliable_price_for_days_ends_as_no_price_without_pnl(book, monkeypatch):
    tid = put(book, entered=et(*MON, 10), last=100.2)
    run(book, monkeypatch, et(2026, 10, 23, 16, 30))                               # the 4th trading day after the Monday close
    assert row(book, tid).status == "OPEN"
    run(book, monkeypatch, et(*NEXT_MON, 16, 30))                                  # the 5th: given up
    r = row(book, tid)
    assert r.status == "NO_PRICE" and r.exit_price is None and r.profit is None
    with book.Session() as s_:
        t = s_.get(DemoTrade, tid)
        assert t.profit is None and t.profit_pct is None and t.exit_time is not None


def test_a_stale_board_price_or_a_wait_row_is_not_an_executable_price(book, monkeypatch):
    tid = put(book)
    stale = {"symbol": "AAA", "signal": "NEUTRAL", "score": 68, "now": 98.0, "data": {"ok": True, "price_age_s": 900}}
    wait = {"symbol": "AAA", "signal": "WAIT", "score": None, "score_raw": 68, "now": 98.0, "wait": True,
            "data": {"ok": False, "price_age_s": 5}}
    for bad in (stale, wait):
        run(book, monkeypatch, et(*TUE, 11), extra=[bad])
        r = row(book, tid)
        assert r.status == "OPEN" and r.exit_price is None                         # a breached stop at a stale price: no fill
    run(book, monkeypatch, et(*TUE, 11, 2), price=98.0, score=68)                  # the same price, fresh: now it executes
    assert row(book, tid).status == "STOP"


def test_a_live_quote_is_an_executable_price_when_no_board_has_one(book, monkeypatch):
    tid = put(book)
    monkeypatch.setattr(dts, "_quote_price", lambda sym: 98.0)
    run(book, monkeypatch, et(*TUE, 11))
    assert row(book, tid).status == "STOP"


def test_a_database_failure_during_the_tick_loses_nothing_and_the_next_tick_closes_once(book, monkeypatch):
    tid = put(book)
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
    run(book, monkeypatch, et(*TUE, 11), price=98.0, score=68)                     # the commit fails: nothing persisted
    assert row(book, tid).status == "OPEN"
    run(book, monkeypatch, et(*TUE, 11, 1), price=98.0, score=68)                  # recovered: closed exactly once
    r = row(book, tid)
    assert (r.status, r.exit_price, r.profit) == ("STOP", 99.0, -100.0)


def test_a_competing_close_is_never_overwritten_or_duplicated(book, monkeypatch):
    tid = put(book)
    real_reason = dts._exit_reason

    def competing(t, last, score, after_close):
        with book.engine.begin() as c:                                             # another process closes it first
            c.execute(text("UPDATE demo_trades SET status='TARGET', exit_price=102.0, last_price=102.0, "
                           "profit=200.0, profit_pct=2.0 WHERE id = :i AND status = 'OPEN'"), {"i": tid})
        return real_reason(t, last, score, after_close)
    monkeypatch.setattr(dts, "_exit_reason", competing)
    run(book, monkeypatch, et(*TUE, 11), price=98.0, score=68)                     # this tick wants a STOP at 99
    r = row(book, tid)
    assert (r.status, r.exit_price, r.profit) == ("TARGET", 102.0, 200.0)          # the first close stands


def test_a_closed_trades_pnl_is_never_refreshed_by_a_stale_snapshot(book, monkeypatch):
    tid = put(book)
    with book.engine.begin() as c:
        c.execute(text("UPDATE demo_trades SET status='STOP', exit_price=99.0, last_price=99.0, profit=-100.0 "
                       "WHERE id = :i"), {"i": tid})
    run(book, monkeypatch, et(*TUE, 11), price=101.0, score=68)
    r = row(book, tid)
    assert (r.status, r.exit_price, r.last, r.profit) == ("STOP", 99.0, 99.0, -100.0)


def test_weekends_and_holidays_keep_the_position_open_and_unharmed(book, monkeypatch):
    tid = put(book, entered=et(*FRI, 10))
    run(book, monkeypatch, et(*SAT, 16, 30), price=100.3, score=69)                # Saturday after "the close"
    assert row(book, tid).status == "OPEN"
    run(book, monkeypatch, et(2026, 10, 25, 17, 0), price=100.3, score=69)         # Sunday evening
    assert row(book, tid).status == "OPEN"
    thanks = put(book, "HHH", entered=et(2026, 11, 25, 10))
    run(book, monkeypatch, et(2026, 11, 26, 16, 30), price=100.3, score=69, symbol="HHH")   # Thanksgiving
    assert row(book, thanks).status == "OPEN"
    run(book, monkeypatch, et(2026, 11, 27, 16, 1), price=100.9, score=69, symbol="HHH")    # the next trading close
    assert row(book, thanks).status == "TIMED_EXIT"


# ----------------------------------------------------- entry lock, reporting, rollback ---
def test_the_entry_lock_blocks_new_entries_but_never_monitoring_or_exits(book, monkeypatch):
    entry_lock.install(book.engine)
    entry_lock.set_locked(True, "release", book.engine)
    tid = put_unlocked(book)
    assert entry_lock.is_locked(book.engine, fresh=True) is True                  # (read once: an in-memory SQLite shares ONE connection)
    monkeypatch.setattr(dts, "_entry_allowed", lambda *a, **k: False)             # what the real gate answers while locked
    monkeypatch.setattr(dts, "_can_open", lambda n=None: True)
    sig = {"symbol": "NEW", "signal": "BUY", "score": 72.0, "entry": 50.0, "now": 50.0, "tradable": True,
           "data": {"ok": True, "price_age_s": 5}}
    monkeypatch.setattr(dts, "_et_now", lambda: et(*TUE, 11))
    monkeypatch.setattr(dts, "_board", lambda spec: {"buyers": [sig], "neutral": [
        {"symbol": "AAA", "signal": "NEUTRAL", "score": 68, "now": 98.0, "data": {"ok": True, "price_age_s": 5}}]})
    import intraday_safety
    monkeypatch.setattr(intraday_safety, "tradable", lambda r: True)
    dts.tick(force=True)
    r = row(book, tid)
    assert (r.status, r.exit_price) == ("STOP", 99.0)                              # the exit went through the triggers
    with book.Session() as s:
        assert s.query(DemoTrade).filter_by(symbol="NEW").count() == 0             # and the entry did not happen
    with book.engine.begin() as c:                                                 # the trigger itself, for any build
        with pytest.raises(Exception):
            c.execute(text("INSERT INTO demo_trades (strategy, symbol, side, shares, session_date, entry_price, status) "
                           "VALUES ('grok','X','LONG',100,'2026-10-20',1.0,'OPEN')"))


def put_unlocked(book, **kw):
    """A position that exists from before the lock (inserted with the lock briefly off)."""
    entry_lock.set_locked(False, "seed", book.engine)
    tid = put(book, **kw)
    entry_lock.set_locked(True, "release", book.engine)
    return tid


def test_a_rollback_is_refused_while_a_carried_position_is_open_and_allowed_when_flat(book, monkeypatch):
    entry_lock.install(book.engine)
    entry_lock.set_locked(False, "live", book.engine)
    tid = put(book, entered=et(*MON, 14))                                          # opened on an EARLIER date
    assert entry_lock.open_positions(book.engine)["demo_trades"] == 1              # counted whatever its date
    refused = rt.rollback_prepare(engine=book.engine)
    assert refused["ok"] is False and refused["open_positions"]["demo_trades"] == 1
    monkeypatch.setattr(dts, "_et_now", lambda: et(*TUE, 11))
    entry_lock.set_locked(True, "release", book.engine)
    monkeypatch.setattr(dts, "_board", lambda spec: {"neutral": [
        {"symbol": "AAA", "signal": "NEUTRAL", "score": 68, "now": 98.0, "data": {"ok": True, "price_age_s": 5}}]})
    dts.tick(force=True)                                                           # monitoring continues while locked
    assert row(book, tid).status == "STOP"
    assert rt.rollback_prepare(engine=book.engine)["ok"] is True


def test_the_blotter_lists_carried_positions_without_touching_todays_totals(book, monkeypatch):
    put(book, "AAA", entered=et(*MON, 14))                                         # carried
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
    past = dts.blotter(date="2026-10-19")                                          # a past date's blotter is its own day
    assert [r["symbol"] for r in past["rows"]] == ["AAA"] and past["carried_open"] == []


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


# ============================================================ overnight gaps (the fill accounting) ===
def touch(book, tid, when):
    """Make the position's LAST OBSERVATION happen at ``when`` (ET)."""
    with book.engine.begin() as c:
        c.execute(text("UPDATE demo_trades SET updated_at = :t WHERE id = :i"),
                  {"t": when.astimezone(timezone.utc).replace(tzinfo=None), "i": tid})


@pytest.mark.parametrize("side, stop, target, open_px, status, filled, profit", [
    ("LONG", 99.0, 102.0, 94.0, "STOP", 94.0, -600.0),      # gaps DOWN through a long stop: filled at the open, not at 99
    ("LONG", 99.0, 102.0, 105.0, "TARGET", 105.0, 500.0),   # gaps UP through the target: the better open price
    ("SHORT", 101.0, 98.0, 106.0, "STOP", 106.0, -600.0),   # the mirror image: gaps UP through a short stop
    ("SHORT", 101.0, 98.0, 95.0, "TARGET", 95.0, 500.0),
])
def test_an_overnight_gap_through_a_level_is_booked_at_the_first_executable_price(
        book, monkeypatch, side, stop, target, open_px, status, filled, profit):
    tid = put(book, side=side, stop=stop, target=target, entered=et(*MON, 14))
    touch(book, tid, et(*MON, 15, 55))
    run(book, monkeypatch, et(*TUE, 9, 35), price=open_px, score=68)
    r = row(book, tid)
    assert (r.status, r.exit_price, r.profit) == (status, filled, profit)


def test_inside_a_continuous_session_a_level_is_still_booked_at_the_level(book, monkeypatch):
    tid = put(book, stop=99.0)
    touch(book, tid, et(*TUE, 10, 0))
    run(book, monkeypatch, et(*TUE, 10, 1), price=98.4, score=68)
    r = row(book, tid)
    assert (r.status, r.exit_price, r.profit) == ("STOP", 99.0, -100.0)


def test_downtime_inside_one_session_is_not_a_gap(book, monkeypatch):
    tid = put(book, stop=99.0)
    touch(book, tid, et(*TUE, 10, 0))
    run(book, monkeypatch, et(*TUE, 13, 0), price=94.0, score=68)
    assert row(book, tid).exit_price == 99.0


@pytest.mark.parametrize("last_seen, now, gap", [
    (et(*MON, 15, 55), et(*TUE, 9, 35), True),
    (et(*FRI, 15, 55), et(*NEXT_MON, 9, 35), True),
    (et(2026, 11, 25, 15, 55), et(2026, 11, 27, 9, 35), True),
    (et(*MON, 10, 0), et(*MON, 13, 0), False),
    (et(*MON, 19, 0), et(*MON, 19, 30), False),
    (et(*MON, 19, 30), et(*TUE, 4, 5), True),
    (et(*MON, 16, 5), et(*MON, 17, 0), False),
])
def test_a_gap_is_a_closed_period_between_two_observations(last_seen, now, gap):
    import paper_fills as pf
    assert pf.observation_gap(last_seen, now) is gap


def test_a_breach_found_without_an_executable_price_waits_and_is_then_booked_at_the_gap_price(book, monkeypatch):
    tid = put(book, stop=99.0)
    touch(book, tid, et(*MON, 15, 55))
    stale = {"symbol": "AAA", "signal": "NEUTRAL", "score": 68, "now": 94.0, "data": {"ok": True, "price_age_s": 4000}}
    run(book, monkeypatch, et(*TUE, 9, 31), extra=[stale])
    assert row(book, tid).status == "OPEN"
    touch(book, tid, et(*MON, 15, 55))                       # a deferred tick never refreshes the last observation
    run(book, monkeypatch, et(*TUE, 9, 40), price=93.0, score=68)
    r = row(book, tid)
    assert (r.status, r.exit_price, r.profit) == ("STOP", 93.0, -700.0)


def test_the_old_level_convention_is_still_available_for_comparison(book, monkeypatch):
    import tab_settings
    monkeypatch.setitem(tab_settings.PAPER_EXECUTION, "fill_model", "level")
    tid = put(book, stop=99.0)
    touch(book, tid, et(*MON, 15, 55))
    run(book, monkeypatch, et(*TUE, 9, 35), price=94.0, score=68)
    assert row(book, tid).exit_price == 99.0


def test_score_exits_are_booked_at_the_observed_price_not_a_level(book, monkeypatch):
    sc = put(book, "AAA", score=70.0, stop=90.0, target=110.0)
    touch(book, sc, et(*MON, 15, 55))
    run(book, monkeypatch, et(*TUE, 9, 35), price=96.0, score=50.0)
    r = row(book, sc)
    assert r.status == "SCORE_EXIT" and r.exit_price == 96.0


def test_the_gap_effect_and_realized_pnl_are_reported_without_distorting_the_entry_date_view(book, monkeypatch):
    tid = put(book, stop=99.0, entered=et(*MON, 14))
    touch(book, tid, et(*MON, 15, 55))
    put(book, "TDY", entered=et(*TUE, 9, 40), last=100.0)
    run(book, monkeypatch, et(*TUE, 9, 45), price=94.0, score=68, extra=[
        {"symbol": "TDY", "signal": "NEUTRAL", "score": 68, "now": 101.0, "data": {"ok": True, "price_age_s": 5}}])
    monkeypatch.setattr(dts, "tick", lambda force=False: None)
    tue = dts.blotter(date="2026-10-20")
    assert [r["symbol"] for r in tue["rows"]] == ["TDY"]
    assert tue["realized_on_date"] == -600.0 and tue["closed_on_date"] == 1
    mon = dts.blotter(date="2026-10-19")
    assert [r["symbol"] for r in mon["rows"]] == ["AAA"] and mon["total_profit"] == -600.0
    r = mon["rows"][0]
    assert (r["exit_basis"], r["gap_effect"], r["exit_date"]) == ("GAP", -500.0, "2026-10-20")
    assert mon["gap_effect_total"] == -500.0 and tue["gap_effect_total"] == 0.0


def test_a_no_price_close_is_in_no_total_win_rate_or_realized_pnl(book, monkeypatch):
    tid = put(book, entered=et(*MON, 10), last=100.2)
    run(book, monkeypatch, et(*NEXT_MON, 16, 30))
    assert row(book, tid).status == "NO_PRICE"
    monkeypatch.setattr(dts, "tick", lambda force=False: None)
    out = dts.blotter(date="2026-10-19")
    assert out["total_profit"] == 0 and out["wins"] == 0
    assert out["rows"][0]["profit"] is None and out["rows"][0]["exit_basis"] is None
    assert dts.blotter(date="2026-10-26")["realized_on_date"] == 0.0


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


@pytest.mark.parametrize("entered, now, expect", [
    (et(2026, 11, 27, 10), et(2026, 11, 27, 12, 59), False),
    (et(2026, 11, 27, 10), et(2026, 11, 27, 13, 0), True),
    (et(2026, 11, 27, 14), et(2026, 11, 27, 16, 0), False),
    (et(2026, 11, 27, 14), et(2026, 11, 30, 16, 0), True),
    (et(2026, 12, 24, 10), et(2026, 12, 24, 13, 0), True),
    (et(*TUE, 10), et(*TUE, 13, 0), False),
])
def test_the_timed_exit_follows_the_sessions_real_close(entered, now, expect):
    assert dts._exit_timed(trade(entered), now) is expect


def test_a_carried_position_exits_at_the_early_close_not_at_four(book, monkeypatch):
    tid = put(book, entered=et(2026, 11, 25, 10))
    run(book, monkeypatch, et(2026, 11, 27, 12, 30), price=100.5, score=69)
    assert row(book, tid).status == "OPEN"
    run(book, monkeypatch, et(2026, 11, 27, 13, 1), price=100.6, score=69)
    r = row(book, tid)
    assert (r.status, r.exit_price) == ("TIMED_EXIT", 100.6)


def test_an_unexpected_closure_the_calendar_does_not_know_never_invents_a_fill(book, monkeypatch):
    tid = put(book, stop=99.0, entered=et(*MON, 14))
    touch(book, tid, et(*MON, 15, 55))
    stale = {"symbol": "AAA", "signal": "NEUTRAL", "score": 68, "now": 94.0, "data": {"ok": True, "price_age_s": 61000}}
    for hh, mm in ((9, 35), (12, 0), (16, 5)):
        run(book, monkeypatch, et(*TUE, hh, mm), extra=[stale])
        assert row(book, tid).status == "OPEN"


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


# ================================================================================ instruments ===
def test_only_stocks_and_etfs_are_modelled():
    got = [mc.instrument_kind(x) for x in ("AAPL", "SPY", "QQQ", "BRK.B", "ES", "/ES", "ES=F", "SPX", "^VIX", "VIX", "")]
    assert got == ["equity", "equity", "equity", "equity", "equity", "future", "future", "index", "index", "index", "unknown"]


