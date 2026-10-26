"""The entry-lock triggers on a REAL PostgreSQL (the production engine family), in an isolated throw-away schema of
the DEVELOPMENT database (never production: database.py refuses that). Opt-in because it needs the network:

    set TRDGO_PG_TRIGGER_TEST=1   (PowerShell:  $env:TRDGO_PG_TRIGGER_TEST = "1")
    set TRDGO_PG_TEST_POOLER=1    also run through the TRANSACTION pooler (:6543), production's mode
    python -m pytest tests/test_entry_lock_postgres.py -q

Skipped otherwise. Creates schema entrylock_test_<random>, runs everything inside it, and drops it afterwards.
"""
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("TRDGO_PG_TRIGGER_TEST") != "1",
                                reason="opt-in: set TRDGO_PG_TRIGGER_TEST=1 (needs the development database)")


@pytest.fixture(scope="module")
def pg():
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.pool import NullPool
    import database
    url = database._development_url()
    if not url or database.PROD_REF in url:
        pytest.skip("no development database configured (or it points at production)")
    schema = "entrylock_test_" + uuid.uuid4().hex[:10]
    pooler = os.environ.get("TRDGO_PG_TEST_POOLER") == "1"
    if pooler:
        url = url.replace(":5432/", ":6543/")
        assert ":6543/" in url, "the development URL has no :5432 to switch to the transaction pooler"
    admin = create_engine(url, poolclass=NullPool, connect_args={"prepare_threshold": None} if pooler else {})
    with admin.begin() as c:
        c.execute(text(f"CREATE SCHEMA {schema}"))
    eng = create_engine(url, poolclass=NullPool, connect_args={"prepare_threshold": None} if pooler else {})

    if pooler:
        @event.listens_for(eng, "begin")
        def _search_path_local(conn):
            conn.exec_driver_sql(f"SET LOCAL search_path TO {schema}")
    else:
        @event.listens_for(eng, "connect")
        def _search_path(dbapi_conn, _rec):
            cur = dbapi_conn.cursor()
            cur.execute(f"SET search_path TO {schema}")
            cur.close()
            dbapi_conn.commit()

    try:
        yield eng, schema
    finally:
        with admin.begin() as c:
            c.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        admin.dispose()
        eng.dispose()


def test_the_triggers_on_postgres_block_entries_and_never_exits(pg, monkeypatch):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.orm import sessionmaker

    import entry_lock
    import intraday_signal_v2 as eng_mod
    from models_demo_trades import DemoTrade

    eng, schema = pg
    assert eng.dialect.name == "postgresql"
    # the real tables, created by the app's own code
    DemoTrade.__table__.create(bind=eng, checkfirst=True)
    monkeypatch.setattr("database.SessionLocal", sessionmaker(bind=eng))
    monkeypatch.setattr(eng_mod, "_ready", False)
    assert eng_mod._ensure() is True

    # ---- install: idempotent, starts LOCKED, one trigger per guarded table
    r = entry_lock.install(eng)
    assert sorted(r["installed_on"]) == ["demo_trades", "intraday_sig_v2"] and not r["skipped_missing_tables"]
    entry_lock.install(eng)                                            # again: no error, still two triggers
    s = entry_lock.status(eng)
    assert s["dialect"] == "postgresql" and s["installed"] and s["locked"] is True
    assert set(s["triggers"]) == {"demo_trades", "intraday_sig_v2"}
    assert entry_lock.is_locked(eng, fresh=True) is True

    def new_trade(session, symbol="AAA"):
        from datetime import date
        session.add(DemoTrade(strategy="grok", symbol=symbol, side="LONG", shares=100, session_date=date.today(),
                              entry_price=100.0, status="OPEN"))

    Session = sessionmaker(bind=eng)

    # ---- LOCKED: an INSERT is rejected by the database itself, for ANY build (the ORM path an old build uses)
    with pytest.raises(DBAPIError) as e:
        with Session() as ses:
            new_trade(ses)
            ses.commit()
    assert "locked" in str(e.value).lower()
    with pytest.raises(DBAPIError) as e2:                              # and the live engine's raw INSERT
        with eng.begin() as c:
            c.execute(text("INSERT INTO intraday_sig_v2 (symbol, session_date, state, side, is_open, entry) "
                           "VALUES ('AAA', CURRENT_DATE, 'BUY', 'BUY', TRUE, 100)"))
    assert "locked" in str(e2.value).lower()

    # ---- UNLOCKED: entries work again
    entry_lock.set_locked(False, "test", eng)
    with Session() as ses:
        new_trade(ses)
        ses.commit()
    with eng.begin() as c:
        c.execute(text("INSERT INTO intraday_sig_v2 (symbol, session_date, state, side, is_open, entry) "
                       "VALUES ('AAA', CURRENT_DATE, 'BUY', 'BUY', TRUE, 100)"))
    assert entry_lock.open_positions(eng) == {"demo_trades": 1, "intraday_sig_v2": 1}

    # ---- a rollback lock REFUSES while positions are open (and changes nothing)
    r = entry_lock.set_locked(True, "rollback", eng, for_rollback=True)
    assert r["ok"] is False and r["open_positions"] == {"demo_trades": 1, "intraday_sig_v2": 1}
    assert entry_lock.is_locked(eng, fresh=True) is False

    # ---- plain LOCK is allowed with positions open, and EXITS / UPDATES still work while locked
    entry_lock.set_locked(True, "lock for the swap", eng)
    assert entry_lock.is_locked(eng, fresh=True) is True
    with Session() as ses:                                             # the tracker's exit: update price, close the trade
        t = ses.query(DemoTrade).filter_by(symbol="AAA").one()
        t.last_price = 101.0
        t.status = "TARGET"
        t.exit_price = 101.0
        ses.commit()
    with eng.begin() as c:                                             # the engine's exit and trailing-stop updates
        c.execute(text("UPDATE intraday_sig_v2 SET now_price = 101, sl = 100.5, best_price = 101 WHERE symbol = 'AAA'"))
        c.execute(text("UPDATE intraday_sig_v2 SET is_open = FALSE, state = 'CLOSED', exit_reason = 'TP' "
                       "WHERE symbol = 'AAA'"))
        c.execute(text("INSERT INTO intraday_sig_v2_changes (symbol, session_date, ts, from_state, to_state, pl_pct) "
                       "VALUES ('AAA', CURRENT_DATE, now(), 'BUY', 'CLOSED', 1.0)"))      # unguarded tables stay writable
    assert entry_lock.open_positions(eng) == {"demo_trades": 0, "intraday_sig_v2": 0}

    # ---- the documented hazard: a transaction that carries BOTH an exit and a blocked INSERT fails as a whole
    with Session() as ses:                                             # open a position while unlocked first
        entry_lock.set_locked(False, "test", eng)
        new_trade(ses, "CCC")
        ses.commit()
    entry_lock.set_locked(True, "locked again", eng)
    with pytest.raises(DBAPIError):
        with Session() as ses:
            t = ses.query(DemoTrade).filter_by(status="OPEN").one()
            t.status = "STOP"
            ses.add(DemoTrade(strategy="meta", symbol="BBB", side="LONG", shares=100,
                              session_date=t.session_date, entry_price=1.0, status="OPEN"))
            ses.commit()
    with Session() as ses:
        assert ses.query(DemoTrade).filter_by(status="OPEN").count() == 1      # the exit was rolled back with it
    # ...which is exactly why this build checks the lock in code BEFORE it tries to insert (exits then commit alone),
    # and why a rollback lock requires zero open positions.

    # ---- now that nothing is open, the rollback lock is accepted
    with Session() as ses:
        t = ses.query(DemoTrade).filter_by(status="OPEN").one()
        t.status = "STOP"
        ses.commit()
    assert entry_lock.set_locked(True, "rollback", eng, for_rollback=True)["ok"] is True
    # a removed lock table means "not installed", an unreadable one means "locked"
    with eng.begin() as c:
        c.execute(text("SELECT 1"))


def test_carried_positions_on_postgres_close_once_and_are_never_blocked_by_the_lock(pg, monkeypatch):
    """The cross-midnight lifecycle on the real engine: a position opened on an earlier date is monitored and closed
    by its rules while entries are LOCKED; a competing close is never overwritten; one change-log row per close."""
    import types
    from datetime import date, datetime, timezone
    from zoneinfo import ZoneInfo

    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker

    import demo_trade_service as dts
    import entry_lock
    import intraday_signal_v2 as sig
    import release_tool as rt
    from models_demo_trades import DemoTrade

    eng, schema = pg
    ET = ZoneInfo("America/New_York")
    Session = sessionmaker(bind=eng)
    monkeypatch.setattr("database.SessionLocal", Session)
    monkeypatch.setattr(sig, "_ready", False)
    DemoTrade.__table__.create(bind=eng, checkfirst=True)
    assert sig._ensure() is True
    entry_lock.install(eng)
    with eng.begin() as c:                                              # a clean book for this test
        c.execute(text("DELETE FROM demo_trades"))
        c.execute(text("DELETE FROM intraday_sig_v2"))
        c.execute(text("DELETE FROM intraday_sig_v2_changes"))

    monday, tuesday = datetime(2026, 10, 19, 14, 0, tzinfo=ET), datetime(2026, 10, 20, 11, 0, tzinfo=ET)
    monkeypatch.setattr(entry_lock, "_cutoff", lambda: "2026-10-19")      # "today" is Tuesday 2026-10-20 in this test
    entry_lock.set_locked(False, "seed", eng)
    with Session() as s:
        for sym in ("AAA", "BBB"):
            s.add(DemoTrade(strategy="grok", symbol=sym, side="LONG", shares=100, session_date=monday.date(),
                            entry_time=monday.astimezone(timezone.utc), entry_price=100.0, stop=99.0, target=102.0,
                            score_at_entry=70.0, status="OPEN"))
        s.commit()
    with eng.begin() as c:
        for sym in ("AAA", "BBB"):
            c.execute(text("INSERT INTO intraday_sig_v2 (symbol, session_date, state, side, is_open, entry, now_price, tp, sl, "
                           "best_price, atr, score_in, score_now, signal_ts, status_ts) VALUES (:s, '2026-10-19', 'BUY', 'BUY', "
                           "TRUE, 100, 100, 102, 99, 100, 2, 72, 72, now(), now())"), {"s": sym})
    entry_lock.set_locked(True, "release", eng)                          # ENTRIES LOCKED from here on
    assert entry_lock.open_positions(eng) == {"demo_trades": 2, "intraday_sig_v2": 2}

    # ---- paper tracker: Tuesday, AAA through its stop; BBB untouched; a competing close for BBB is never overwritten
    monkeypatch.setattr(dts, "ensure_schema", lambda: None)
    monkeypatch.setattr(dts, "MODELS", {"grok": ("Grok", "x:y")})
    monkeypatch.setattr(dts, "_quote_price", lambda s: None)
    monkeypatch.setattr(dts, "_et_now", lambda: tuesday)
    monkeypatch.setattr(dts, "_entry_allowed", lambda *a, **k: False)
    monkeypatch.setattr(dts, "_board", lambda spec: {"neutral": [
        {"symbol": "AAA", "signal": "NEUTRAL", "score": 68, "now": 98.5, "data": {"ok": True, "price_age_s": 5}},
        {"symbol": "BBB", "signal": "NEUTRAL", "score": 68, "now": 98.5, "data": {"ok": True, "price_age_s": 5}}]})
    real_reason = dts._exit_reason

    def competing(t, last, score, after_close):
        if getattr(t, "side", None) and t.stop == 99.0:
            with eng.begin() as c:                                       # another process closes BBB first
                c.execute(text("UPDATE demo_trades SET status='TARGET', exit_price=102.0, last_price=102.0, profit=200.0 "
                               "WHERE symbol='BBB' AND status='OPEN'"))
        return real_reason(t, last, score, after_close)
    monkeypatch.setattr(dts, "_exit_reason", competing)
    dts._last_tick = 0.0
    dts.tick(force=True)
    with Session() as s:
        got = {t.symbol: (t.status, t.exit_price, t.profit) for t in s.query(DemoTrade).all()}
    assert got["AAA"][0] == "STOP" and got["AAA"][1] == 99.0 and got["AAA"][2] == -100.0
    assert got["BBB"] == ("TARGET", 102.0, 200.0)                         # the competing close stands, untouched
    assert entry_lock.open_positions(eng)["demo_trades"] == 0
    dts._last_tick = 0.0
    dts.tick(force=True)                                                  # a second tick changes nothing
    with Session() as s:
        assert {t.symbol: (t.status, t.exit_price, t.profit) for t in s.query(DemoTrade).all()} == got

    # ---- live engine: a signal from an EARLIER session is found, managed and closed with ONE change-log row, even
    #      when two scans race; EOD never fires merely because the date changed
    scan = {"score": 72.0, "spot": 100.5, "status": "OK", "spy_above_vwap": None}
    assert sig.step("AAA", scan, tuesday)["is_open"] is True               # next morning: still open and managed
    with Session() as a, Session() as b:
        row_a, row_b = dict(sig._open_row(a, "AAA", "2026-10-20")), dict(sig._open_row(b, "AAA", "2026-10-20"))
        sig._manage_open(a, row_a, dict(scan, spot=98.0), tuesday.replace(hour=11, minute=1))
        a.commit()
        out = sig._manage_open(b, row_b, dict(scan, spot=97.0), tuesday.replace(hour=11, minute=2))
        b.commit()
    assert out["is_open"] is False
    with eng.connect() as c:
        assert c.execute(text("SELECT count(*) FROM intraday_sig_v2_changes WHERE symbol='AAA' AND to_state='CLOSED'")).scalar() == 1
        assert float(c.execute(text("SELECT exit_price FROM intraday_sig_v2 WHERE symbol='AAA'")).scalar()) == 98.0
    assert sig.step("BBB", scan, tuesday.replace(hour=15, minute=54))["is_open"] is True
    assert sig.step("BBB", scan, tuesday.replace(hour=15, minute=55))["exit_reason"] == "EOD"
    assert entry_lock.open_positions(eng) == {"demo_trades": 0, "intraday_sig_v2": 0}

    # ---- a rollback while a carried position is open is refused, and allowed once flat
    entry_lock.set_locked(False, "seed", eng)
    with Session() as s:
        s.add(DemoTrade(strategy="grok", symbol="CCC", side="SHORT", shares=100, session_date=date(2026, 10, 19),
                        entry_time=monday.astimezone(timezone.utc), entry_price=100.0, stop=101.0, target=98.0,
                        status="OPEN"))
        s.commit()
    refused = rt.rollback_prepare(engine=eng)
    assert refused["ok"] is False and refused["open_positions"]["demo_trades"] == 1
    entry_lock.set_locked(True, "release", eng)
    monkeypatch.setattr(dts, "_exit_reason", real_reason)
    monkeypatch.setattr(dts, "_board", lambda spec: {"neutral": [
        {"symbol": "CCC", "signal": "NEUTRAL", "score": 40, "now": 97.0, "data": {"ok": True, "price_age_s": 5}}]})
    dts._last_tick = 0.0
    dts.tick(force=True)                                                  # the SHORT reaches its target while locked
    assert rt.rollback_prepare(engine=eng)["ok"] is True


def test_duplicate_and_stale_signal_rows_on_postgres(pg, monkeypatch):
    """Several open rows for one symbol, stale rows, the advisory lock and the operator expiry, on the real engine."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker

    import entry_lock
    import intraday_signal_v2 as sig

    eng, schema = pg
    Session = sessionmaker(bind=eng)
    monkeypatch.setattr("database.SessionLocal", Session)
    monkeypatch.setattr(sig, "_ready", False)
    assert sig._ensure() is True
    entry_lock.install(eng)
    entry_lock.set_locked(False, "test", eng)
    with eng.begin() as c:
        c.execute(text("DELETE FROM intraday_sig_v2"))
        for sym, d, side in (("DUP", "2026-10-20", "BUY"), ("DUP", "2026-10-21", "SELL"), ("OLD", "2026-10-16", "BUY")):
            c.execute(text("INSERT INTO intraday_sig_v2 (symbol, session_date, state, side, is_open, entry, now_price, tp, sl, "
                           "best_price, atr, score_in, score_now, signal_ts, status_ts) VALUES (:s, :d, :side, :side, TRUE, "
                           "100, 100, 98, 101.5, 100, 2, 70, 70, now(), now())"), {"s": sym, "d": d, "side": side})
    wed = datetime(2026, 10, 21, 11, 0, tzinfo=ZoneInfo("America/New_York"))
    with Session() as s:
        sig._lock_symbol(s, "DUP")                                          # a transaction-level advisory lock, any pooler
        got = sig._open_rows(s, "DUP", wed)
        assert [r["session_date"].isoformat() for r in got] == ["2026-10-20", "2026-10-21"]       # BOTH, oldest first
        assert sig._open_rows(s, "OLD", wed) == []                                                # stale: outside the window
        s.commit()
    assert [r["symbol"] for r in sig.stale_open_rows(wed)] == ["OLD"]
    assert sig.expire_stale(apply=False, now=wed)["would_expire"] == 1
    done = sig.expire_stale(apply=True, now=wed)
    assert done["applied"] is True and done["expired"] == 1
    with eng.connect() as c:
        r = c.execute(text("SELECT is_open, exit_reason, exit_price, entry, signal_ts IS NOT NULL FROM intraday_sig_v2 "
                           "WHERE symbol='OLD'")).one()
        assert (r[0], r[1], r[2], float(r[3]), r[4]) == (False, "EXPIRED", None, 100.0, True)      # kept, no P&L
        assert c.execute(text("SELECT count(*) FROM intraday_sig_v2 WHERE symbol='DUP' AND is_open")).scalar() == 2
    assert sig.expire_stale(apply=True, now=wed)["applied"] is False                                  # idempotent
