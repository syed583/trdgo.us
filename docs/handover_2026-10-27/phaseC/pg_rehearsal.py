"""Rehearse the audit-table migration (and the optional B / C migrations) and their rollbacks on a THROW-AWAY PostgreSQL.

Never run this against a real database: it refuses unless the target is 127.0.0.1:54329 (a local PGlite server started from the
scratch folder). It creates a release-shaped demo_trades with 172 OPEN paper positions, applies each migration file, checks that every
historical row is byte-identical afterwards (md5 over all rows), re-applies (idempotence), rolls back, and exercises the Legacy ORM
and the audit hook against the migrated schema -- including an audit write that fails.

    set DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:54329/postgres?sslmode=disable
    set APP_ENV=production  &  set DB_POOL_SIZE=1  &  set DB_MAX_OVERFLOW=0
    python docs/handover_2026-10-27/phaseC/pg_rehearsal.py
"""
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "tests"))

import database  # noqa: E402
from sqlalchemy import inspect, text  # noqa: E402

url = database.engine.url
if not (url.host == "127.0.0.1" and url.port in (54329, 54317)):
    sys.exit(f"REFUSING: target is {url.host}:{url.port}, not a throw-away 127.0.0.1:54329 / 54317")

MIG = ROOT / "backend" / "migrations"
results = []


def check(name, ok, detail=""):
    results.append({"check": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


def run_file(name):
    raw = database.engine.raw_connection()
    try:
        raw.driver_connection.autocommit = True
        raw.driver_connection.execute((MIG / name).read_text(encoding="utf-8"))
    except Exception:
        try:
            raw.driver_connection.execute("ROLLBACK")
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        raw.driver_connection.autocommit = False
        raw.close()


def scalar(sql, **p):
    with database.engine.connect() as c:
        return c.execute(text(sql), p).scalar()


def tables():
    return set(inspect(database.engine).get_table_names())


def columns(t):
    return [c["name"] for c in inspect(database.engine).get_columns(t)]


def checksum():
    return scalar("SELECT md5(string_agg(t::text, '|' ORDER BY id)) FROM (SELECT id, strategy, symbol, side, shares, session_date, entry_time, "
                  "entry_price, stop, target, score_at_entry, last_price, profit, profit_pct, status, exit_time, exit_price FROM demo_trades) t")


def try_file(name):
    try:
        run_file(name)
        return None
    except Exception as exc:  # noqa: BLE001
        return type(exc).__name__ + ": " + str(exc).splitlines()[0][:100]


# ----------------------------------------------------------------------------------------------------- the legacy table --
with database.engine.begin() as c:
    for t in ("intraday_signal_audit", "intraday_shadow_log", "intraday_shadow_trades", "intraday_data_exceptions", "intraday_signal_events",
              "intraday_snapshots", "demo_trades"):
        c.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
    c.execute(text("""CREATE TABLE demo_trades (id SERIAL PRIMARY KEY, strategy VARCHAR(24) NOT NULL, symbol VARCHAR(16) NOT NULL,
        side VARCHAR(5) NOT NULL, shares INTEGER NOT NULL, session_date DATE NOT NULL, entry_time TIMESTAMPTZ NOT NULL, entry_price FLOAT NOT NULL,
        stop FLOAT, target FLOAT, score_at_entry FLOAT, last_price FLOAT, profit FLOAT, profit_pct FLOAT, status VARCHAR(16) NOT NULL,
        exit_time TIMESTAMPTZ, exit_price FLOAT, updated_at TIMESTAMPTZ, CONSTRAINT uq_demo_strategy_symbol_day UNIQUE (strategy, symbol, session_date))"""))
    c.execute(text("CREATE INDEX ix_demo_session ON demo_trades (session_date)"))
    c.execute(text("CREATE INDEX ix_demo_strategy_session ON demo_trades (strategy, session_date)"))
    c.execute(text("""INSERT INTO demo_trades (strategy, symbol, side, shares, session_date, entry_time, entry_price, stop, target, score_at_entry, status)
        SELECT (ARRAY['chatgpt','grok','meta','kimi','gemini','claude_pro'])[1 + (g % 6)], 'SYM' || g, CASE WHEN g % 2 = 0 THEN 'LONG' ELSE 'SHORT' END,
               100, DATE '2026-10-26', TIMESTAMPTZ '2026-10-26 08:05:00+00', 100 + g, 99 + g, 102 + g, 56.0, 'OPEN' FROM generate_series(1, 172) g"""))
    c.execute(text("""INSERT INTO demo_trades (strategy, symbol, side, shares, session_date, entry_time, entry_price, stop, target, score_at_entry,
        profit, profit_pct, status, exit_time, exit_price)
        SELECT 'grok', 'OLD' || g, 'LONG', 100, DATE '2026-10-19' + (g % 5), TIMESTAMPTZ '2026-10-20 14:00:00+00', 50, 49, 52, 58.0, 12.5, 0.25,
               'TIMED_EXIT', TIMESTAMPTZ '2026-10-20 20:00:00+00', 50.125 FROM generate_series(1, 40) g"""))
before = checksum()
n_rows = scalar("SELECT COUNT(*) FROM demo_trades")
cols_before, idx_before = columns("demo_trades"), sorted(i["name"] for i in inspect(database.engine).get_indexes("demo_trades"))
print("server:", scalar("SELECT version()")[:60])
check("rehearsal book: 172 OPEN + 40 closed", n_rows == 212 and scalar("SELECT COUNT(*) FROM demo_trades WHERE status='OPEN'") == 172)

# -------------------------------------------------------------------------------------------- A: audit table only --------
err = try_file("2026_10_27_A_audit_only.sql")
check("A applies cleanly", err is None, err or "")
check("A creates the audit table and index only", tables() == {"demo_trades", "intraday_signal_audit"} and
      "ix_intraday_signal_audit_day" in {i["name"] for i in inspect(database.engine).get_indexes("intraday_signal_audit")})
check("A leaves demo_trades columns, indexes and every row byte-identical",
      columns("demo_trades") == cols_before and sorted(i["name"] for i in inspect(database.engine).get_indexes("demo_trades")) == idx_before
      and checksum() == before and scalar("SELECT COUNT(*) FROM demo_trades") == n_rows)
check("A is idempotent (second run)", try_file("2026_10_27_A_audit_only.sql") is None and checksum() == before)

# --- the ORM models against the migrated schema: Legacy writes work, the audit hook records, a failing audit changes nothing ---
import models_demo_trades as m  # noqa: E402
import signal_verification as sv  # noqa: E402
import demo_trade_service as dts  # noqa: E402
import tab_settings as cfg  # noqa: E402
from wf_harness import TUE, et, row  # noqa: E402

cfg.INTRADAY_VERIFICATION["mode"] = "audit"
S = database.SessionLocal
s = S()
s.add(m.DemoTrade(strategy="chatgpt", symbol="NEWT", side="LONG", shares=100, session_date=date(2026, 10, 27), entry_price=50.0, stop=49.0,
                  target=52.0, score_at_entry=56.0, status="OPEN"))
s.commit()
check("Legacy ORM insert works on the migrated PostgreSQL schema (no V2 column named)", scalar("SELECT COUNT(*) FROM demo_trades WHERE symbol='NEWT'") == 1)
s.query(m.DemoTrade).filter_by(symbol="NEWT").update({"status": "STOP"})
s.commit()
s.close()
check("Legacy ORM update works", scalar("SELECT status FROM demo_trades WHERE symbol='NEWT'") == "STOP")

now = et(*TUE, 11, 0)
good = row("chatgpt", "NEWT", now, signal="BUY", session="REGULAR", price=50.0, score=None)
boards = {"chatgpt": {"NEWT": dict(good, symbol="NEWT", raw_levels=None)}}
trades_before = checksum()
dts._audit_signals(now, boards, set(), {("chatgpt", "NEWT")})
check("the audit hook writes a row to PostgreSQL", scalar("SELECT COUNT(*) FROM intraday_signal_audit") == 1)
row_ = None
with database.engine.connect() as c:
    row_ = c.execute(text("SELECT verdict, data_status, legacy_opened, jsonb_array_length(breakdown::jsonb) FROM intraday_signal_audit")).one()
check("the stored audit row has the verdict, data status and a full parameter breakdown", row_[0] == "PASS" and row_[3] == len(sv.configured_weights("chatgpt")),
      str(tuple(row_)))
check("the audit write did not touch a single Legacy row", checksum() == trades_before)

if os.environ.get("REHEARSAL_SKIP_BULK"):          # PGlite 0.3.x (PostgreSQL 17.5) traps in WASM on one 240-row INSERT: an emulator limit, not PostgreSQL
    print("SKIPPED bulk audit timing (REHEARSAL_SKIP_BULK)")
else:
    import time  # noqa: E402
    big = {st: {f"T{i}": dict(row(st, f"T{i}", now, signal="BUY", session="REGULAR", price=50.0 + i, score=None), symbol=f"T{i}", raw_levels=None)
                for i in range(40)} for st in sv.configured_weights.__globals__["cfg"].INTRADAY_EXEC and ("chatgpt", "grok", "meta", "kimi", "gemini", "claude_pro")}
    t0 = time.monotonic()
    dts._audit_signals(now, big, set(), set())
    took = time.monotonic() - t0
    check("auditing 240 signals (6 strategies x 40 names) in one tick is fast", took < 5.0 and scalar("SELECT COUNT(*) FROM intraday_signal_audit") == 241,
          f"{took:.2f}s, rows={scalar('SELECT COUNT(*) FROM intraday_signal_audit')}")
    cfg.INTRADAY_VERIFICATION["max_seconds"] = 0.0           # the hard time budget: nothing audited, nothing raised, nothing else affected
    n0 = scalar("SELECT COUNT(*) FROM intraday_signal_audit")
    big2 = {st: {f"U{i}": dict(r_, symbol=f"U{i}") for i, r_ in enumerate(rows_.values())} for st, rows_ in big.items()}
    dts._audit_signals(now, big2, set(), set())
    check("an exhausted time budget audits nothing and raises nothing", scalar("SELECT COUNT(*) FROM intraday_signal_audit") == n0)
    cfg.INTRADAY_VERIFICATION["max_seconds"] = 5.0
run_file("2026_10_27_A_audit_only_ROLLBACK.sql")        # the audit table disappears: every audit write now FAILS
check("rollback A removes the audit table", "intraday_signal_audit" not in tables())
trades_before = checksum()
try:
    dts._audit_signals(now, boards, set(), {("chatgpt", "NEWT")})
    raised = None
except Exception as exc:  # noqa: BLE001
    raised = repr(exc)
check("a FAILING audit write (table missing) raises nothing", raised is None, raised or "")
check("...and leaves every Legacy row exactly as it was", checksum() == trades_before and scalar("SELECT COUNT(*) FROM demo_trades") == n_rows + 1)
check("...and does not leave a transaction open or locked", scalar("SELECT COUNT(*) FROM pg_locks WHERE locktype = 'relation' AND NOT granted") == 0)
s = S()
s.add(m.DemoTrade(strategy="grok", symbol="AFTER", side="SHORT", shares=100, session_date=date(2026, 10, 27), entry_price=60.0, stop=61.0,
                  target=58.0, score_at_entry=44.0, status="OPEN"))
s.commit()
s.close()
check("Legacy keeps trading right after the failed audit write", scalar("SELECT COUNT(*) FROM demo_trades WHERE symbol='AFTER'") == 1)
check("rollback A restored the original shape (columns and indexes)", columns("demo_trades") == cols_before and
      sorted(i["name"] for i in inspect(database.engine).get_indexes("demo_trades")) == idx_before)
check("re-applying A after a rollback works", try_file("2026_10_27_A_audit_only.sql") is None and "intraday_signal_audit" in tables())

# ------------------------------------------------------------------------------- B and C (not part of this activation) -----
base_sum, base_n = checksum(), scalar("SELECT COUNT(*) FROM demo_trades")
check("B applies, is idempotent and its rollback removes only its tables",
      try_file("2026_10_27_B_shadow_tables.sql") is None and try_file("2026_10_27_B_shadow_tables.sql") is None
      and {"intraday_shadow_trades", "intraday_shadow_log", "intraday_signal_events"} <= tables() and checksum() == base_sum)
run_file("2026_10_27_B_shadow_tables_ROLLBACK.sql")
check("rollback B", "intraday_shadow_trades" not in tables() and "intraday_signal_audit" in tables() and checksum() == base_sum)
err = try_file("2026_10_27_C_v2_activation.sql")
check("C applies (columns, constraint swap, unique indexes) on 173 OPEN-distinct rows", err is None, err or "")
check("C added the 8 nullable columns and left every historical value identical",
      {"entry_session", "fill_assumption"} <= set(columns("demo_trades")) and checksum() == base_sum)
check("C is idempotent", try_file("2026_10_27_C_v2_activation.sql") is None)
run_file("2026_10_27_C_v2_activation_ROLLBACK.sql")
check("rollback C removes the new indexes and changes no row", checksum() == base_sum and
      "uq_demo_trades_one_open" not in {i["name"] for i in inspect(database.engine).get_indexes("demo_trades")})
with database.engine.begin() as c:                      # a duplicate OPEN pair must make C fail ATOMICALLY (no half-applied state)
    c.execute(text("ALTER TABLE demo_trades ADD CONSTRAINT uq_demo_strategy_symbol_day UNIQUE (strategy, symbol, session_date)"))  # as after rollback C
    c.execute(text("INSERT INTO demo_trades (strategy, symbol, side, shares, session_date, entry_time, entry_price, status) "
                   "VALUES ('grok','SYM1','LONG',100,DATE '2026-10-27', now(), 1.0, 'OPEN')"))     # SYM1 is already OPEN for grok (another date)
err = try_file("2026_10_27_C_v2_activation.sql")
cons = scalar("SELECT COUNT(*) FROM pg_constraint WHERE conname='uq_demo_strategy_symbol_day'")
check("C refuses atomically when OPEN duplicates exist (old constraint still there, no new index)", err is not None and cons == 1 and
      "uq_demo_trades_one_open" not in {i["name"] for i in inspect(database.engine).get_indexes("demo_trades")}, err or "")

bad = [r for r in results if not r["ok"]]
print(f"\n{len(results) - len(bad)} passed, {len(bad)} failed")
(Path(__file__).with_name("pg_rehearsal_result.json")).write_text(json.dumps({"server": scalar("SELECT version()")[:60], "results": results}, indent=2))
sys.exit(1 if bad else 0)
