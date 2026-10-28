"""Legacy mode (and shadow / audit) must not depend on the V2 evidence columns existing in demo_trades: a production table in the
shape of release 277f30f has to keep working with this code, untouched by any migration."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, text, update
from sqlalchemy.orm import sessionmaker

import models_demo_trades as m

V2_COLUMNS = ("entry_session", "signal_event_id", "snapshot_id", "entry_quote", "risk_checks", "validation", "exit_price_source",
              "fill_assumption")
RELEASE_DDL = """CREATE TABLE demo_trades (id INTEGER PRIMARY KEY, strategy VARCHAR(24) NOT NULL, symbol VARCHAR(16) NOT NULL,
    side VARCHAR(5) NOT NULL, shares INTEGER NOT NULL, session_date DATE NOT NULL, entry_time DATETIME NOT NULL, entry_price FLOAT NOT NULL,
    stop FLOAT, target FLOAT, score_at_entry FLOAT, last_price FLOAT, profit FLOAT, profit_pct FLOAT, status VARCHAR(16) NOT NULL,
    exit_time DATETIME, exit_price FLOAT, updated_at DATETIME, CONSTRAINT uq_demo_strategy_symbol_day UNIQUE (strategy, symbol, session_date))"""


@pytest.fixture
def release_db():
    e = create_engine("sqlite://")
    with e.begin() as c:
        c.execute(text(RELEASE_DDL))
    stmts = []
    event.listen(e, "before_cursor_execute", lambda conn, cur, st, p, ctx, many: stmts.append(st))
    return e, sessionmaker(bind=e), stmts


def test_orm_reads_inserts_and_updates_work_on_a_release_shaped_table(release_db):
    e, S, stmts = release_db
    s = S()
    s.add(m.DemoTrade(strategy="grok", symbol="AAPL", side="LONG", shares=100, session_date=date(2026, 10, 27), entry_price=150.0,
                      stop=149.0, target=152.0, score_at_entry=57.0, status="OPEN"))
    s.commit()
    t = s.query(m.DemoTrade).filter_by(status="OPEN").one()
    assert (t.strategy, t.symbol, t.entry_price) == ("grok", "AAPL", 150.0)
    s.execute(update(m.DemoTrade).where(m.DemoTrade.id == t.id, m.DemoTrade.status == "OPEN").values(status="STOP", exit_price=149.0, profit=-100.0))
    s.commit()
    s.query(m.DemoTrade.strategy, m.DemoTrade.symbol).filter_by(session_date=date(2026, 10, 27)).all()
    s.query(m.DemoTrade).filter(m.DemoTrade.status != "OPEN", m.DemoTrade.profit.isnot(None)).all()
    s.close()
    touched = [st for st in stmts if any(c in st for c in V2_COLUMNS)]
    assert not touched, touched[:2]                                                  # not one statement mentions a V2 column


def test_the_v2_columns_still_exist_on_the_model_for_v2_and_the_shadow_book():
    assert set(V2_COLUMNS) <= set(m.DemoTrade.__table__.c.keys()) and set(V2_COLUMNS) <= set(m.ShadowTrade.__table__.c.keys())
    assert set(V2_COLUMNS) <= set(m.DemoTradeV2.__mapper__.attrs.keys())                # V2 maps them
    assert not (set(V2_COLUMNS) & set(m.DemoTrade.__mapper__.attrs.keys()))              # Legacy does not


def test_the_legacy_tracker_runs_on_a_release_shaped_table(monkeypatch):
    """The real dts.tick(), a legacy-mode book whose demo_trades has none of the V2 columns."""
    import demo_trade_service as dts
    import intraday_safety
    import tab_settings as cfg
    from wf_harness import TUE, et, quote, row, rising_bars, falling_bars  # noqa: F401
    e = create_engine("sqlite://")
    with e.begin() as c:
        c.execute(text(RELEASE_DDL))
    S = sessionmaker(bind=e)
    import database
    now = {"t": et(*TUE, 11, 0)}
    monkeypatch.setattr(database, "SessionLocal", S)
    monkeypatch.setitem(cfg.INTRADAY_WORKFLOW, "mode", "legacy")
    monkeypatch.setitem(cfg.INTRADAY_VERIFICATION, "mode", "off")
    monkeypatch.setattr(dts, "ensure_schema", lambda: None)
    monkeypatch.setattr(dts, "MODELS", {"chatgpt": ("chatgpt", "chatgpt")})
    monkeypatch.setattr(dts, "_et_now", lambda: now["t"])
    monkeypatch.setattr(dts, "_quote_price", lambda sym: 50.0)
    r = row("chatgpt", "AMD", now["t"], signal="BUY", session="REGULAR", price=50.0, score=56.0)
    monkeypatch.setattr(dts, "_board", lambda spec: {"buyers": [r], "sellers": [], "neutral": [], "wait": []})
    monkeypatch.setattr(intraday_safety, "tradable", lambda row_: True)
    monkeypatch.setattr(dts, "_entry_allowed", lambda *a: True)
    dts.tick(force=True)
    with e.connect() as c:
        got = c.execute(text("SELECT strategy, symbol, side, entry_price, status FROM demo_trades")).fetchall()
    assert [tuple(x) for x in got] == [("chatgpt", "AMD", "LONG", 50.0, "OPEN")]


def test_audit_mode_creates_only_its_own_table_and_never_touches_demo_trades():
    import intraday_workflow as wf
    e = create_engine("sqlite://")
    with e.begin() as c:
        c.execute(text(RELEASE_DDL))
        c.execute(text("INSERT INTO demo_trades (strategy, symbol, side, shares, session_date, entry_time, entry_price, status) "
                       "VALUES ('grok','AAPL','LONG',100,'2026-10-27','2026-10-27 14:00:00',150.0,'OPEN')"))
    from sqlalchemy import inspect
    before = {i["name"] for i in inspect(e).get_indexes("demo_trades")}, [c["name"] for c in inspect(e).get_columns("demo_trades")]
    done = wf.ensure_tables(e, ["intraday_signal_audit"])
    assert done == {"tables": ["intraday_signal_audit"], "skipped": []}
    assert wf.ensure_tables(e, ["intraday_signal_audit"])["tables"] == []                      # idempotent
    after = {i["name"] for i in inspect(e).get_indexes("demo_trades")}, [c["name"] for c in inspect(e).get_columns("demo_trades")]
    assert before == after
    assert set(inspect(e).get_table_names()) == {"demo_trades", "intraday_signal_audit"}
