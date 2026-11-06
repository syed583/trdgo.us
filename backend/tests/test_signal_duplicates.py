"""Several open live-signal rows for one symbol (the production state of 2026-10-26: AMD, AMZN, NVDA and QCOM each
had two open rows from different sessions, 16 of 17 open rows were 4-10 days old).

Rules under test:
  * EVERY open row inside the carry window is managed, each by its own side / levels / entry -- not only the newest.
  * A row older than the carry window is STALE: never managed, never closed automatically, never blocks a new signal
    for its symbol, reported as unresolved, and expired only by an operator action (kept, no P&L, history intact).
  * Never two open rows from this build: a second open attempt (restart, race) writes nothing.
  * BUY and SELL are symmetric; everything is idempotent.
"""
import types
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import entry_lock
import intraday_signal_v2 as eng

ET = ZoneInfo("America/New_York")


def et(y, m, d, hh=10, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


TUE, WED = (2026, 10, 20), (2026, 10, 21)


@pytest.fixture
def db(monkeypatch):
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


def add(db, symbol="AAA", session="2026-10-20", side="BUY", entry=100.0, sl=99.0, tp=102.0, is_open=1):
    if side == "SELL":
        sl, tp = (sl if sl > entry else entry + 1.0), (tp if tp < entry else entry - 2.0)
    with db.engine.begin() as c:
        c.execute(text("INSERT INTO intraday_sig_v2 (symbol, session_date, state, side, is_open, entry, now_price, tp, sl, "
                       "best_price, atr, score_in, score_now, signal_ts, status_ts) VALUES (:s,:d,:st,:sd,:o,:e,:e,:tp,:sl,"
                       ":e,2.0,72,72,:ts,:ts)"),
                  {"s": symbol, "d": session, "st": side, "sd": side, "o": is_open, "e": entry, "tp": tp, "sl": sl,
                   "ts": session + "T14:00:00-04:00"})


def rows(db, **where):
    with db.engine.connect() as c:
        q = "SELECT * FROM intraday_sig_v2" + (" WHERE " + " AND ".join(f"{k}=:{k}" for k in where) if where else "") + " ORDER BY id"
        return [dict(r) for r in c.execute(text(q), where).mappings().all()]


def go(symbol, price, now, score=72.0):
    return eng.step(symbol, {"score": score, "spot": price, "call": "NO EDGE", "side": None, "status": "OK",
                             "spy_above_vwap": None}, now)


# ------------------------------------------------------------------------------ every open row is managed ---
def test_every_open_row_of_a_symbol_is_managed_not_only_the_newest(db):
    add(db, "AAA", session="2026-10-20", side="BUY", entry=100.0, sl=99.0, tp=102.0)       # id 1: the older one
    add(db, "AAA", session="2026-10-21", side="SELL", entry=100.0, sl=101.0, tp=98.0)      # id 2: the newest, opposite side
    go("AAA", 101.5, et(*WED, 11))                  # SELL's stop (>= 101) is hit; the BUY is in profit, not at a level
    r1, r2 = rows(db)
    assert (r2["is_open"], r2["exit_reason"]) == (0, "SL")
    assert r1["is_open"] == 1 and r1["now_price"] == 101.5                                  # the older row was refreshed too
    go("AAA", 102.5, et(*WED, 11, 5))               # now the BUY's target
    r1, r2 = rows(db)
    assert (r1["is_open"], r1["exit_reason"], r1["exit_price"]) == (0, "TP", 102.5)
    assert (r2["is_open"], r2["exit_reason"]) == (0, "SL")                                   # untouched: closed once


@pytest.mark.parametrize("side, entry, sl, tp, spot, reason", [
    ("BUY", 100.0, 99.0, 102.0, 98.5, "SL"), ("SELL", 100.0, 101.0, 98.0, 101.5, "SL"),
    ("BUY", 100.0, 99.0, 102.0, 102.5, "TP"), ("SELL", 100.0, 101.0, 98.0, 97.5, "TP"),
])
def test_both_rows_of_a_symbol_exit_symmetrically(db, side, entry, sl, tp, spot, reason):
    add(db, "AAA", session="2026-10-20", side=side, entry=entry, sl=sl, tp=tp)
    add(db, "AAA", session="2026-10-21", side=side, entry=entry, sl=sl, tp=tp)
    go("AAA", spot, et(*WED, 11))
    assert [(r["is_open"], r["exit_reason"]) for r in rows(db)] == [(0, reason), (0, reason)]


def test_a_restart_changes_nothing_and_every_close_is_logged_once(db):
    add(db, "AAA", session="2026-10-20")
    add(db, "AAA", session="2026-10-21")
    go("AAA", 98.0, et(*WED, 11))
    snap = rows(db)
    go("AAA", 97.0, et(*WED, 11, 1))                # the "restart": the next scan finds nothing open and changes nothing
    assert rows(db) == snap
    with db.engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM intraday_sig_v2_changes WHERE to_state='CLOSED'")).scalar() == 2


def test_two_processes_closing_the_same_rows_write_one_close_each(db):
    add(db, "AAA", session="2026-10-20")
    add(db, "AAA", session="2026-10-21")
    with db.Session() as a, db.Session() as b:
        ra, rb = [dict(r) for r in eng._open_rows(a, "AAA", et(*WED, 11))], [dict(r) for r in eng._open_rows(b, "AAA", et(*WED, 11))]
        sc = {"score": 72.0, "spot": 98.0, "spy_above_vwap": None}
        for r in ra:
            eng._manage_open(a, r, sc, et(*WED, 11))
        a.commit()
        for r in rb:                                 # the slower process still holds both rows as open
            eng._manage_open(b, r, dict(sc, spot=95.0), et(*WED, 11, 1))
        b.commit()
    assert [r["exit_price"] for r in rows(db)] == [98.0, 98.0]                               # the first close stands
    with db.engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM intraday_sig_v2_changes WHERE to_state='CLOSED'")).scalar() == 2


# --------------------------------------------------------------------------------------- never a second open row ---
def test_an_open_row_inside_the_window_blocks_a_new_signal_even_if_a_racing_scan_missed_it(db):
    add(db, "AAA", session="2026-10-21")                      # opened by another scan / process a moment ago
    now = et(*WED, 11)
    with db.Session() as s:
        s.execute(text("INSERT INTO intraday_sig_v2_pending (symbol, session_date, side, count, first_ts, last_ts) "
                       "VALUES ('AAA','2026-10-21','BUY',1,'x',:t)"), {"t": "2026-10-21T10:00:00-04:00"})
        s.commit()
        sc = {"call": "BUY", "side": "BUY", "score": 72.0, "spot": 100.0, "atr": 2.0, "status": "OK"}
        assert eng._maybe_open(s, "AAA", "2026-10-21", sc, now) is None      # confirmed, but the guard sees the open row
        s.commit()
    assert len(rows(db)) == 1


# ------------------------------------------------------------------------------------------ stale rows ---
def test_a_stale_row_is_never_managed_or_closed_and_never_blocks_a_new_signal(db):
    add(db, "AMD", session="2026-10-16", side="BUY", entry=550.0, sl=540.0, tp=560.0)       # 3 trading days old on Wed 21
    before = rows(db)
    out = go("AMD", 400.0, et(*WED, 11))            # far beyond its stop: a week-old row must not be booked at this price
    assert rows(db) == before and out is None
    assert eng._open_rows(db.Session(), "AMD", et(*WED, 11)) == []
    assert eng._active_symbols("2026-10-21") == set()                                          # it does not force a rescore
    # and a fresh row for the same symbol can still be opened (the stale row does not block the symbol)
    add(db, "AMD", session="2026-10-21", side="SELL", entry=400.0)
    assert len(eng._open_rows(db.Session(), "AMD", et(*WED, 11))) == 1


def test_stale_rows_are_reported_not_counted_as_carried_or_protected_positions(db, monkeypatch):
    monkeypatch.setattr(eng, "_session", lambda now=None: "2026-10-21")
    add(db, "AMD", session="2026-10-16")
    add(db, "AMZN", session="2026-10-20")                                                   # carried, inside the window
    add(db, "NFLX", session="2026-10-21")                                                   # today
    s = eng.summary("2026-10-21")
    assert (s["open"], s["carried_open"], s["stale_open"]) == (1, 1, 1)
    log = {r["symbol"]: r for r in eng.log_rows("2026-10-21")}
    assert log["AMD"]["stale"] is True and log["AMZN"]["stale"] is False and log["AMZN"]["carried"] is True
    assert [r["symbol"] for r in eng.stale_open_rows(et(*WED, 11))] == ["AMD"]


def test_the_entry_lock_does_not_count_stale_rows_as_positions_to_protect(db):
    import market_calendar
    from datetime import date
    today = datetime.now(market_calendar.ET).date().isoformat()
    old = market_calendar.trading_days_back(date.fromisoformat(today), 5).isoformat()
    add(db, "AMD", session=old)
    add(db, "NFLX", session=today)
    assert entry_lock.open_positions(db.engine)["intraday_sig_v2"] == 1
    assert entry_lock.stale_unresolved(db.engine) == 1
    assert entry_lock.status(db.engine)["stale_unresolved"] == 1


# ---------------------------------------------------------------------------------------- operator expiry ---
def test_expiring_the_stale_rows_is_a_dry_run_by_default_and_keeps_every_row(db):
    add(db, "AMD", session="2026-10-16", side="BUY", entry=550.0)
    add(db, "QCOM", session="2026-10-19", side="SELL", entry=176.0)
    add(db, "NFLX", session="2026-10-21")                                                   # fresh: never touched
    before = rows(db)
    dry = eng.expire_stale(apply=False, now=et(*WED, 11))
    assert dry["applied"] is False and dry["would_expire"] == 2 and rows(db) == before
    done = eng.expire_stale(apply=True, now=et(*WED, 11))
    assert done["applied"] is True and done["expired"] == 2
    after = rows(db)
    assert len(after) == 3                                                                  # nothing deleted
    for old, new in zip(before, after):
        for k in ("symbol", "session_date", "side", "entry", "tp", "sl", "signal_ts", "score_in", "atr"):
            assert old[k] == new[k]                                                         # history and original timestamps kept
    assert [(r["symbol"], r["is_open"], r["exit_reason"], r["exit_price"]) for r in after] == \
        [("AMD", 0, "EXPIRED", None), ("QCOM", 0, "EXPIRED", None), ("NFLX", 1, None, None)]
    again = eng.expire_stale(apply=True, now=et(*WED, 11))                                  # idempotent
    assert again["applied"] is False and rows(db) == after


def test_expired_rows_carry_no_pnl_and_enter_no_win_rate(db):
    add(db, "AMD", session="2026-10-21", side="BUY", entry=100.0)
    with db.engine.begin() as c:
        c.execute(text("UPDATE intraday_sig_v2 SET is_open=0, state='CLOSED', exit_reason='EXPIRED'"))
    s = eng.summary("2026-10-21")
    assert (s["issued"], s["closed"], s["open"], s["expired"], s["wins"], s["losses"], s["win_rate"]) == (1, 0, 0, 1, 0, 0, None)
