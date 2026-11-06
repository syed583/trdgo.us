"""Operator lock on NEW trade entries: the switch that makes deploy and rollback safe.

It blocks only the creation of new paper trades (`demo_trades`) and new live-engine signals
(`intraday_sig_v2`). It never touches exits, stops, targets, trailing or any risk-reducing update.

Two layers, so it holds even for a build that knows nothing about it:

  1. the application (this build) checks `is_locked()` before it opens anything and simply refuses, so no
     error is raised and exits in the same transaction are unaffected;
  2. a database trigger on each guarded table REJECTS an INSERT while the lock is on. That is what stops an
     OLDER build (one without the 15-minute confirmation) if it is ever started against the same database.
     Such a build cannot be taught to read the lock, and its failed INSERT also fails the transaction that
     carries its exits -- so a rollback target may only be started with entries locked when there are NO open
     positions (`lock --for-rollback` refuses otherwise).

    python entry_lock.py status                  what is locked, what is installed, what is open
    python entry_lock.py install                 create the lock table and the triggers (idempotent; starts LOCKED)
    python entry_lock.py lock "reason"           block new entries (safe at any time with this build)
    python entry_lock.py lock --for-rollback "reason"   same, but only when no position is open
    python entry_lock.py unlock                  allow new entries again

Run it on the machine whose database you mean: a development machine uses the development database and can
never reach production (database.py refuses); on the server it uses the production one.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from typing import Optional

TABLE = "trade_entry_lock"
GUARDED = ("demo_trades", "intraday_sig_v2")
# A live signal still open from before the carry window is STALE: nothing manages or closes it (see
# intraday_signal_v2.expire_stale), so it is reported (stale_unresolved) but does not count as a position to protect.
_OPEN_SQL = {"demo_trades": "SELECT COUNT(*) FROM demo_trades WHERE status = 'OPEN'",
             "intraday_sig_v2": "SELECT COUNT(*) FROM intraday_sig_v2 WHERE is_open = TRUE AND session_date >= :cut"}
_STALE_SQL = "SELECT COUNT(*) FROM intraday_sig_v2 WHERE is_open = TRUE AND session_date < :cut"


def _cutoff(now=None) -> str:
    import market_calendar
    import tab_settings
    from datetime import datetime as _dt
    today = (now or _dt.now(market_calendar.ET)).astimezone(market_calendar.ET).date()
    return market_calendar.trading_days_back(today, int(tab_settings.INTRADAY_LIFECYCLE["max_carry_trading_days"])).isoformat()


def stale_unresolved(engine=None) -> int:
    """Live signal rows still open from before the carry window (never managed, never closed automatically)."""
    eng = _engine(engine)
    with eng.connect() as conn:
        if not _table_exists(conn, "intraday_sig_v2"):
            return 0
        return int(conn.execute(_text(_STALE_SQL), {"cut": _cutoff()}).scalar() or 0)
_CACHE_S = 5.0
_cache: dict = {"at": 0.0, "value": None}


def _engine(engine=None):
    if engine is not None:
        return engine
    from database import engine as default
    return default


def _text(sql: str):
    from sqlalchemy import text
    return text(sql)


def _dialect(engine) -> str:
    return engine.dialect.name


def _table_exists(conn, name: str) -> bool:
    from sqlalchemy import inspect
    return inspect(conn).has_table(name)


# ----------------------------------------------------------------- install ----
def install(engine=None, *, locked: bool = True) -> dict:
    """Create the lock table and one INSERT trigger per guarded table that exists. Idempotent. Starts LOCKED
    (the safe state) unless a row already exists."""
    eng = _engine(engine)
    done, skipped = [], []
    with eng.begin() as conn:
        conn.execute(_text(f"CREATE TABLE IF NOT EXISTS {TABLE} (id INTEGER PRIMARY KEY, locked BOOLEAN NOT NULL, "
                           f"reason TEXT, updated_at TEXT)"))
        have = conn.execute(_text(f"SELECT COUNT(*) FROM {TABLE} WHERE id = 1")).scalar()
        if not have:
            conn.execute(_text(f"INSERT INTO {TABLE} (id, locked, reason, updated_at) VALUES (1, :l, :r, :t)"),
                         {"l": bool(locked), "r": "installed (locked until explicitly unlocked)",
                          "t": datetime.now(timezone.utc).isoformat()})
        if _dialect(eng) == "postgresql":
            conn.execute(_text(
                "CREATE OR REPLACE FUNCTION trade_entry_lock_guard() RETURNS trigger LANGUAGE plpgsql AS $$ "
                f"BEGIN IF EXISTS (SELECT 1 FROM {TABLE} WHERE id = 1 AND locked) THEN "
                "RAISE EXCEPTION 'new trade entries are locked (trade_entry_lock)'; END IF; RETURN NEW; END $$"))
        for t in GUARDED:
            if not _table_exists(conn, t):
                skipped.append(t)
                continue
            if _dialect(eng) == "postgresql":
                conn.execute(_text(f"DROP TRIGGER IF EXISTS trade_entry_lock_guard ON {t}"))
                conn.execute(_text(f"CREATE TRIGGER trade_entry_lock_guard BEFORE INSERT ON {t} FOR EACH ROW "
                                   f"EXECUTE FUNCTION trade_entry_lock_guard()"))
            else:
                conn.execute(_text(f"DROP TRIGGER IF EXISTS trade_entry_lock_guard_{t}"))
                conn.execute(_text(
                    f"CREATE TRIGGER trade_entry_lock_guard_{t} BEFORE INSERT ON {t} "
                    f"WHEN EXISTS (SELECT 1 FROM {TABLE} WHERE id = 1 AND locked) "
                    f"BEGIN SELECT RAISE(ABORT, 'new trade entries are locked (trade_entry_lock)'); END"))
            done.append(t)
    _cache["at"] = 0.0
    return {"installed_on": done, "skipped_missing_tables": skipped}


# --------------------------------------------------------------- the switch ----
def open_positions(engine=None) -> dict:
    """{table: number of positions still open} for the guarded tables that exist."""
    eng = _engine(engine)
    out = {}
    with eng.connect() as conn:
        for t, sql in _OPEN_SQL.items():
            if _table_exists(conn, t):
                out[t] = int(conn.execute(_text(sql), {"cut": _cutoff()} if ":cut" in sql else {}).scalar() or 0)
    return out


def set_locked(locked: bool, reason: str = "", engine=None, *, for_rollback: bool = False) -> dict:
    eng = _engine(engine)
    if for_rollback and locked:
        still = {k: v for k, v in open_positions(eng).items() if v}
        if still:
            return {"ok": False, "error": "positions are still open", "open_positions": still}
    install(eng, locked=locked)                         # makes sure the table and triggers exist
    with eng.begin() as conn:
        conn.execute(_text(f"UPDATE {TABLE} SET locked = :l, reason = :r, updated_at = :t WHERE id = 1"),
                     {"l": bool(locked), "r": reason or ("locked by the operator" if locked else "unlocked"),
                      "t": datetime.now(timezone.utc).isoformat()})
    _cache["at"] = 0.0
    return {"ok": True, **status(eng)}


def status(engine=None) -> dict:
    eng = _engine(engine)
    import os
    out = {"table": TABLE, "guarded_tables": list(GUARDED), "installed": False, "locked": None, "reason": None,
           "updated_at": None, "triggers": {}, "open_positions": {},
           # which database this is, so a command can never be mistaken for another environment's
           "environment": (os.environ.get("APP_ENV") or "").strip().lower() or None, "dialect": _dialect(eng)}
    try:
        with eng.connect() as conn:
            if _table_exists(conn, TABLE):
                row = conn.execute(_text(f"SELECT locked, reason, updated_at FROM {TABLE} WHERE id = 1")).first()
                if row:
                    out.update(installed=True, locked=bool(row[0]), reason=row[1], updated_at=row[2])
            if _dialect(eng) == "postgresql":
                rows = conn.execute(_text("SELECT event_object_table FROM information_schema.triggers "
                                          "WHERE trigger_name = 'trade_entry_lock_guard'")).all()
                out["triggers"] = {r[0]: True for r in rows}
            else:
                rows = conn.execute(_text("SELECT name FROM sqlite_master WHERE type = 'trigger' "
                                          "AND name LIKE 'trade_entry_lock_guard_%'")).all()
                out["triggers"] = {r[0].replace("trade_entry_lock_guard_", ""): True for r in rows}
        out["open_positions"] = open_positions(eng)
        out["stale_unresolved"] = stale_unresolved(eng)
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"[:200]
    return out


def is_locked(engine=None, *, now: Optional[float] = None, fresh: bool = False) -> bool:
    """May this build NOT open a new trade? Cached for a few seconds.

    No lock table / no row = not locked (the feature is not installed). Any OTHER failure to read it counts
    as LOCKED: refusing a new entry is always safe, opening one on an unreadable lock is not."""
    t = time.time() if now is None else now
    if engine is None and not fresh and _cache["value"] is not None and t - _cache["at"] < _CACHE_S:
        return bool(_cache["value"])
    try:
        eng = _engine(engine)
        with eng.connect() as conn:
            if not _table_exists(conn, TABLE):
                value = False
            else:
                row = conn.execute(_text(f"SELECT locked FROM {TABLE} WHERE id = 1")).first()
                value = bool(row[0]) if row else False
    except Exception:  # noqa: BLE001
        value = True
    if engine is None:
        _cache["at"], _cache["value"] = t, value
    return value


def main(argv: list[str]) -> int:
    import json
    args = [a for a in argv if not a.startswith("--")]
    flags = {a for a in argv if a.startswith("--")}
    cmd = args[0] if args else "status"
    if cmd == "install":
        print(json.dumps(install(), indent=1))
    elif cmd == "lock":
        r = set_locked(True, " ".join(args[1:]), for_rollback="--for-rollback" in flags)
        print(json.dumps(r, indent=1, default=str))
        return 0 if r.get("ok") else 2
    elif cmd == "unlock":
        # Unlocking is only possible through the runtime check: the RUNNING service must report that it keeps the
        # mandatory 15-minute confirmation. There is no flag that skips it.
        import release_tool
        url = next((a.split("=", 1)[1] for a in argv if a.startswith("--url=")), "http://127.0.0.1:8000")
        r = release_tool.unlock(url, expect_environment="production" if (
            __import__("os").environ.get("APP_ENV") or "").strip().lower() == "production" else None,
            reason=" ".join(args[1:]) or "unlocked after runtime verification")
        print(json.dumps(r, indent=1, default=str))
        return 0 if r["ok"] else 2
    elif cmd == "status":
        print(json.dumps(status(), indent=1, default=str))
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
