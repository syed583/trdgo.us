"""Shadow mode: Legacy stays the only workflow that opens paper trades; V2 judges the same boards into its own tables."""
import json
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine, inspect, text

import demo_trade_service as dts
import intraday_execution as ex
import intraday_safety
import intraday_shadow as sh
import intraday_workflow as wf
import market_calendar as mc
import models_demo_trades as m
import tab_settings as cfg
from wf_harness import TUE, bar, book, et, falling_bars, quote, rising_bars, row  # noqa: F401


@pytest.fixture
def shadow(book, monkeypatch):
    monkeypatch.setitem(cfg.INTRADAY_WORKFLOW, "mode", "shadow")
    return book


def regular_board(book, sym, now, signal="BUY", price=100.0, score=56.0, **kw):
    book.quotes[sym] = quote(price)
    book.bars5[sym] = rising_bars(now, 24, warmup=True) if signal == "BUY" else falling_bars(now, 24, warmup=True)
    book.r15[sym] = {"status": "OK", "bias": 1 if signal == "BUY" else -1, "last_bar_end_epoch": now.timestamp() - 60, "detail": "15m"}
    book.set_board("chatgpt", row("chatgpt", sym, now, signal=signal, session="REGULAR", price=price, score=score, **kw))


def premarket_board(book, sym, now, price=100.0, score=56.0):
    book.quotes[sym] = quote(price)
    book.set_board("chatgpt", row("chatgpt", sym, now, signal="BUY", price=price, score=score))


# ---------------------------------------------------------------------------------------------------- the invariants --
def test_the_default_mode_is_legacy_and_shadow_is_not_the_active_workflow(shadow):
    assert cfg.INTRADAY_WORKFLOW["mode"] == "shadow" and not wf.active() and wf.shadow()    # (the fixture switched it for this test only)


def test_premarket_signal_is_logged_by_both_engines_but_only_v2_shadow_trades_it(shadow):
    for k in range(3):
        n = et(*TUE, 7, 15) + timedelta(minutes=3 * k)
        premarket_board(shadow, "NVDA", n)
        shadow.full_tick(n)
    assert shadow.trades() == []                                                                 # Legacy: no pre-market entry (release gate)
    st = shadow.shadow_trades()
    assert len(st) == 1 and st[0].entry_session == "PREMARKET" and st[0].status == "OPEN"
    log = shadow.shadow_log()
    legacy = [x for x in log if x.engine == "LEGACY"]
    v2 = [x for x in log if x.engine == "V2"]
    assert legacy and legacy[-1].decision == "REJECTED" and legacy[-1].code.startswith("LEGACY:") and legacy[-1].reasons != "[]"
    assert [x.decision for x in v2] == ["REJECTED", "WOULD_OPEN"] and v2[-1].trade_id == st[0].id
    assert all(x.session_type == "PREMARKET" for x in log)


def test_v2_in_shadow_never_writes_to_the_live_paper_book(shadow):
    before = shadow.trades()
    for k in range(4):
        n = et(*TUE, 7, 15) + timedelta(minutes=3 * k)
        premarket_board(shadow, "NVDA", n)
        shadow.full_tick(n)
    assert shadow.trades() == before == []
    assert shadow.shadow_trades()
    assert shadow.exceptions() == []


def test_legacy_decisions_are_identical_with_and_without_shadow_mode(book, monkeypatch):
    """Legacy trades in shadow mode == Legacy trades in legacy mode, to the cent, on the same tape."""
    outcomes = {}
    for mode in ("legacy", "shadow"):
        monkeypatch.setitem(cfg.INTRADAY_WORKFLOW, "mode", mode)
        with book.Session() as s:                                                                   # same tape, empty books
            for model in (m.DemoTrade, m.ShadowTrade, m.IntradayShadowLog, m.IntradaySignalEvent, m.IntradaySnapshot):
                s.query(model).delete()
            s.commit()
        book._stamped = 0
        sh._last.clear()
        for hh in [(9, 41), (9, 44), (10, 5), (10, 8), (15, 59), (16, 1)]:
            n = et(*TUE, *hh)
            regular_board(book, "AMD", n, price=50.0)
            regular_board(book, "AAPL", n, signal="SELL", price=120.0, score=44.0)
            book.full_tick(n)
        outcomes[mode] = [(t.strategy, t.symbol, t.side, t.entry_price, t.status, t.exit_price, t.profit, t.stop, t.target) for t in book.trades()]
    assert outcomes["legacy"] == outcomes["shadow"] and outcomes["legacy"]
    assert book.shadow_trades() and book.shadow_log()                                                # V2 did observe in the second pass


# ------------------------------------------------------------------------------ carry, close and reconciliation -------
def test_shadow_premarket_position_carries_into_regular_hours_and_closes_at_the_bell(shadow):
    for k in range(2):
        n = et(*TUE, 7, 15) + timedelta(minutes=3 * k)
        premarket_board(shadow, "NVDA", n)
        shadow.full_tick(n)
    t0 = shadow.shadow_trades()[0]
    for hh in [(9, 29, 30), (9, 31), (12, 0), (15, 59, 30)]:
        n = et(*TUE, *hh)
        premarket_board(shadow, "NVDA", n)
        shadow.full_tick(n)
        assert [x.status for x in shadow.shadow_trades()] == ["OPEN"] and shadow.shadow_trades()[0].id == t0.id
    n = et(*TUE, 16, 0, 10)
    premarket_board(shadow, "NVDA", n, price=100.3)
    shadow.full_tick(n)
    t = shadow.shadow_trades()[0]
    assert t.status == "MARKET_CLOSE" and t.exit_price == 100.3
    assert shadow.trades() == []


def test_shadow_close_without_a_price_goes_to_reconciliation_with_a_shadow_exception(shadow):
    for k in range(2):
        n = et(*TUE, 7, 15) + timedelta(minutes=3 * k)
        premarket_board(shadow, "NVDA", n)
        shadow.full_tick(n)
    shadow.quotes.clear()
    shadow.boards.clear()
    shadow.full_tick(et(*TUE, 16, 7))
    t = shadow.shadow_trades()[0]
    assert t.status == "PENDING_RECON" and t.exit_price is None
    assert [e.kind for e in shadow.exceptions()] == ["SHADOW_CLOSE_PRICE_MISSING"]


def test_tracker_keeps_ticking_for_an_open_shadow_book_after_hours(shadow):
    for k in range(2):
        n = et(*TUE, 7, 15) + timedelta(minutes=3 * k)
        premarket_board(shadow, "NVDA", n)
        shadow.full_tick(n)
    assert dts.has_open_trades()                                                                  # only the shadow book has anything open


# ----------------------------------------------------------------------------------------- the opening rule ----------
def test_opening_rule_is_off_by_default():
    assert cfg.INTRADAY_EXEC["opening_rule"]["enabled"] is False
    assert ex.opening_state(et(*TUE, 9, 35), ex.REGULAR)["state"] == "OFF"


@pytest.fixture
def rule_on(monkeypatch):
    monkeypatch.setitem(cfg.INTRADAY_EXEC, "opening_rule", {"enabled": True, "no_entry_start": "09:30", "no_entry_end": "10:00",
                                                           "require_both_after_end": True})


def test_opening_rule_states(rule_on):
    assert ex.opening_state(et(*TUE, 9, 30), ex.REGULAR)["state"] == "NO_ENTRY"
    assert ex.opening_state(et(*TUE, 9, 59, 59), ex.REGULAR)["state"] == "NO_ENTRY"
    assert ex.opening_state(et(*TUE, 10, 0), ex.REGULAR)["state"] == "BOTH_REQUIRED"
    assert ex.opening_state(et(*TUE, 9, 35), ex.PREMARKET)["state"] == "OFF"                     # the rule never touches the pre-market


def test_no_new_entry_inside_the_window_but_open_positions_are_still_managed(book, rule_on):
    # a pre-market position, stopped out inside the window
    for k in range(2):
        n = et(*TUE, 7, 15) + timedelta(minutes=3 * k)
        premarket_board(book, "NVDA", n)
        book.tick(n)
    assert book.trades()[0].status == "OPEN"
    for k in range(3):                                                                            # a perfectly valid new regular signal at 09:41
        n = et(*TUE, 9, 41) + timedelta(minutes=3 * k)
        regular_board(book, "AMD", n, price=50.0)
        book.set_board("chatgpt", row("chatgpt", "NVDA", n, signal="NEUTRAL", price=98.9, score=60.0))   # NVDA breaks its stop
        book.tick(n)
    assert [t.symbol for t in book.trades()] == ["NVDA"] and book.trades()[0].status == "STOP"       # position managed
    ev = book.events(symbol="AMD")[0]
    assert ev.validation_status == ex.WAIT_OPENING and "09:30 and 10:00" in json.loads(ev.reject_reasons)[-1]["reasons"][0]


@pytest.mark.parametrize("case,expected", [("ok", 1), ("warmup", 0), ("missing", 0)])
def test_from_ten_both_timeframes_are_required_and_a_missing_15m_refuses(book, rule_on, case, expected):
    for k in range(2):
        n = et(*TUE, 10, 5) + timedelta(minutes=3 * k)
        regular_board(book, "AMD", n, price=50.0)
        book.r15["AMD"] = {"ok": book.r15["AMD"], "warmup": {"status": "WARMUP", "detail": "w"}, "missing": None}[case]
        book.tick(n)
    assert len(book.trades()) == expected


def test_without_the_rule_a_15m_warmup_is_still_tolerated_after_ten(book):
    for k in range(2):
        n = et(*TUE, 10, 5) + timedelta(minutes=3 * k)
        regular_board(book, "AMD", n, price=50.0)
        book.r15["AMD"] = {"status": "WARMUP", "detail": "w"}
        book.tick(n)
    assert len(book.trades()) == 1                                                                    # the tested default is unchanged


# ----------------------------------------------------------------------------------------------- decision logging ----
def test_every_logged_decision_carries_session_and_data_timestamps(shadow):
    for k in range(3):
        n = et(*TUE, 7, 15) + timedelta(minutes=3 * k)
        premarket_board(shadow, "NVDA", n)
        shadow.full_tick(n)
    v2 = [x for x in shadow.shadow_log() if x.engine == "V2"]
    d = json.loads(v2[-1].data_ts)
    assert d["decided_at"] and d["sources"] and d["board_built_at"] and v2[-1].session_type == "PREMARKET" and v2[-1].direction == "BUY"
    assert v2[-1].window_state == "OFF"
    lg = [x for x in shadow.shadow_log() if x.engine == "LEGACY"][-1]
    assert json.loads(lg.data_ts)["decided_at"] and lg.score is not None


def test_a_decision_is_logged_only_when_it_changes(shadow):
    for k in range(6):
        n = et(*TUE, 7, 15) + timedelta(minutes=3 * k)
        premarket_board(shadow, "NVDA", n)
        shadow.full_tick(n)
    assert len([x for x in shadow.shadow_log() if x.engine == "LEGACY"]) == 1
    assert len([x for x in shadow.shadow_log() if x.engine == "V2"]) == 2                        # rejected, then would-open (a TRADED event is not re-evaluated)


def test_report_summarises_both_engines_and_their_overlap(shadow):
    for hh in [(9, 41), (9, 44), (10, 5), (10, 8)]:
        n = et(*TUE, *hh)
        regular_board(shadow, "AMD", n, price=50.0)
        regular_board(shadow, "AAPL", n, signal="SELL", price=120.0, score=44.0)
        shadow.full_tick(n)
    shadow.full_tick(et(*TUE, 16, 1))
    rep = sh.report(shadow.Session, date(*TUE))
    assert rep["legacy_book"]["trades"] >= 1 and rep["v2_shadow_book"]["trades"] >= 1
    assert set(rep["overlap"]) == {"both", "legacy_only", "v2_only"}
    assert any(k.startswith("LEGACY/") for k in rep["per_strategy"]) and any(k.startswith("V2/") for k in rep["per_strategy"])


# ------------------------------------------------------------------------------------------ the gate is untouched ----
def test_the_legacy_gate_decides_the_same_in_shadow_mode(monkeypatch):
    base = dict(symbol="AMD", signal="BUY", score=60.0, entry=50.0, sl=49.0, tp=52.0, params=[], low_confidence=False)
    monkeypatch.setattr(intraday_safety, "evaluate", lambda row, **k: {"ok": False, "reasons": ["stale"], "missing": []})
    out = {}
    for mode in ("legacy", "shadow"):
        monkeypatch.setitem(cfg.INTRADAY_WORKFLOW, "mode", mode)
        out[mode] = intraday_safety.gate(dict(base), phase="rth", signals_age_s=10, price_age=5, meta={"meta": {"a": {"age_s": 1}}})
    drop = {"signal_meta", "gate_ts", "raw_levels"}
    for o in out.values():
        o["health"] = {k: v for k, v in o["health"].items() if not k.startswith("signal_generated")}
    a = {k: v for k, v in out["legacy"].items() if k not in drop}
    b = {k: v for k, v in out["shadow"].items() if k not in drop}
    assert a == b and out["shadow"]["signal"] == intraday_safety.WAIT and out["shadow"]["raw_levels"]["entry"] == 50.0
    assert "raw_levels" not in out["legacy"]


# ------------------------------------------------------------------------------------------------- migration ---------
def _release_table(engine):
    with engine.begin() as c:
        c.execute(text("""CREATE TABLE demo_trades (id INTEGER PRIMARY KEY, strategy VARCHAR(24) NOT NULL, symbol VARCHAR(16) NOT NULL,
            side VARCHAR(5) NOT NULL, shares INTEGER NOT NULL, session_date DATE NOT NULL, entry_time DATETIME, entry_price FLOAT NOT NULL,
            stop FLOAT, target FLOAT, score_at_entry FLOAT, last_price FLOAT, profit FLOAT, profit_pct FLOAT, status VARCHAR(16) NOT NULL,
            exit_time DATETIME, exit_price FLOAT, updated_at DATETIME, CONSTRAINT uq_demo_strategy_symbol_day UNIQUE (strategy, symbol, session_date))"""))
        c.execute(text("INSERT INTO demo_trades (strategy, symbol, side, shares, session_date, entry_price, status) "
                       "VALUES ('grok','AAPL','LONG',100,'2026-10-26',150.0,'OPEN'), ('meta','AAPL','LONG',100,'2026-10-26',150.0,'OPEN')"))


def test_additive_migration_changes_no_constraint_or_index_and_keeps_history():
    e = create_engine("sqlite://")
    _release_table(e)
    before = {i["name"] for i in inspect(e).get_indexes("demo_trades")}
    plan = wf.migration_plan(e)
    assert set(plan["columns_to_add"]) == set(wf._NEW_COLUMNS) and "intraday_shadow_log" in plan["tables_to_create"] and plan["blockers"] == []
    assert "entry_session" not in {c["name"] for c in inspect(e).get_columns("demo_trades")}          # the plan itself changed nothing
    done = wf.migrate_additive(e)
    assert set(done["columns"]) == set(wf._NEW_COLUMNS) and set(done["tables"]) >= {"intraday_shadow_trades", "intraday_shadow_log"}
    assert {i["name"] for i in inspect(e).get_indexes("demo_trades")} == before                       # no index created or dropped
    assert wf.migrate_additive(e)["columns"] == [] and wf.migrate_additive(e)["tables"] == []
    with e.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM demo_trades")).scalar() == 2
    assert wf.migration_plan(e)["columns_to_add"] == [] and wf.migration_plan(e)["tables_to_create"] == []


def test_activation_indexes_are_table_named_idempotent_and_blocked_by_open_duplicates_without_forcing():
    e = create_engine("sqlite://")
    _release_table(e)
    done = wf.migrate(e)
    assert set(done["indexes"]) == {"uq_demo_trades_strategy_symbol_day_session", "uq_demo_trades_one_open"} and done["skipped"] == []
    assert wf.migrate(e)["skipped"] == []
    e2 = create_engine("sqlite://")
    with e2.begin() as c:                                                                              # a database that already has OPEN duplicates
        c.execute(text("""CREATE TABLE demo_trades (id INTEGER PRIMARY KEY, strategy VARCHAR(24), symbol VARCHAR(16), side VARCHAR(5), shares INTEGER,
            session_date DATE, entry_time DATETIME, entry_price FLOAT, stop FLOAT, target FLOAT, score_at_entry FLOAT, last_price FLOAT, profit FLOAT,
            profit_pct FLOAT, status VARCHAR(16), exit_time DATETIME, exit_price FLOAT, updated_at DATETIME)"""))
        c.execute(text("INSERT INTO demo_trades (strategy, symbol, side, shares, session_date, entry_price, status) VALUES "
                       "('grok','AAPL','LONG',100,'2026-10-26',1.0,'OPEN'), ('grok','AAPL','LONG',100,'2026-10-27',1.0,'OPEN')"))
    assert wf.migration_plan(e2)["blockers"]
    d2 = wf.migrate(e2)
    assert any("uq_demo_trades_one_open" in x for x in d2["skipped"]) and "uq_demo_trades_one_open" not in d2["indexes"]
    with e2.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM demo_trades")).scalar() == 2                        # nothing deleted to make it fit


def test_every_index_name_contains_its_table_name_so_the_development_twin_rewrite_cannot_collide():
    import database
    for t in (m.DemoTrade, m.IntradaySnapshot, m.IntradaySignalEvent, m.IntradayDataException, m.ShadowTrade, m.IntradayShadowLog):
        for i in t.__table__.indexes:
            if i.name in ("ix_demo_session", "ix_demo_strategy_session"):
                continue                                                                              # pre-existing production names, unchanged
            assert t.__tablename__ in i.name, (t.__tablename__, i.name)
    for name in ("intraday_snapshots", "intraday_signal_events", "intraday_data_exceptions", "intraday_shadow_trades", "intraday_shadow_log"):
        assert name in database.ISOLATED_TABLES


def test_a_shadow_tick_outside_the_entry_hours_still_commits_legacy_exits(shadow, monkeypatch):
    """Regression (found by the replay): the shadow log call must work when Legacy's entry window is closed (21:00 ET)."""
    n0 = et(*TUE, 15, 0)
    shadow.set_board("chatgpt", row("chatgpt", "AMD", n0, signal="BUY", session="REGULAR", price=50.0, score=56.0))
    with shadow.Session() as s:
        s.add(m.DemoTrade(strategy="chatgpt", symbol="AMD", side="LONG", shares=100, session_date=date(*TUE), entry_price=50.0, stop=49.0,
                          target=52.0, status="OPEN", entry_time=n0.astimezone(__import__("datetime").timezone.utc)))
        s.commit()
    n = et(*TUE, 21, 0)
    shadow.set_board("chatgpt", row("chatgpt", "AMD", n, signal="NEUTRAL", session="REGULAR", price=48.0, score=60.0))
    shadow.quotes["AMD"] = quote(48.0)
    shadow.full_tick(n)
    assert shadow.trades()[0].status == "STOP"                                                       # Legacy's exit was committed


def test_shadow_decision_logging_cannot_roll_back_a_legacy_trade(shadow, monkeypatch):
    """Regression (found by the replay): reading the real gate for a refusal reason uses the shared cache, which can touch the
    database. It must happen after Legacy's transaction is committed and closed, in its own session."""
    import database
    import intraday_15m

    def touchy(sym, sig, phase=None, **k):
        s2 = database.SessionLocal()
        s2.execute(text("SELECT 1"))
        s2.rollback()
        s2.close()
        return {"allowed": False, "reason": "no 15-minute candles fetched yet", "status": "MISSING", "confirm15": None}
    monkeypatch.setattr(intraday_15m, "entry_check", touchy)
    for k in range(2):
        n = et(*TUE, 11, 0) + timedelta(minutes=3 * k)
        regular_board(shadow, "AMD", n, price=50.0)                                  # Legacy opens this one (15m agrees)
        shadow.set_board("chatgpt", row("chatgpt", "NVDA", n, signal="BUY", session="REGULAR", price=100.0, score=56.0))   # no 15m: refused
        shadow.r15.pop("NVDA", None)
        shadow.full_tick(n)
    assert [t.symbol for t in shadow.trades()] == ["AMD"]
    assert any("no 15-minute candles" in (x.code or "") for x in shadow.shadow_log() if x.engine == "LEGACY")
