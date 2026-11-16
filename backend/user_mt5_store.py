"""C.1: one MT5 account per user, stored with the password encrypted.

What a row holds and why:
    username        who it belongs to; the only key, matching app_users
    login, server   the broker account and its server - needed to reconnect, not secret
    secret          the password, encrypted by user_secret_box. Never read by anything but the connect path
    consent_at      when the user ticked the risk disclaimer. No consent, no connection: C.3 will place real
                    orders in this account, so the record of agreement is part of the data, not the UI
    enabled         the user's own kill switch. C.2 and C.3 refuse to act when it is false
    max_shares      the user's size setting, carried now so C.3 has nowhere to invent one
    status, checked_at, last_error   what the last connection attempt did, for the user to see

The schema is created the way user_service does it: CREATE/ALTER ... IF NOT EXISTS, no migration tool.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text

from database import SessionLocal
import user_secret_box as box

_log = logging.getLogger("user_mt5")
_schema_ready = False

TABLE = "user_mt5_accounts"
DEFAULT_SHARES = 10             # C.2 (owner): one quantity for every stock
DEFAULT_MAX_POSITIONS = 20      # same ceiling the admin book uses
DEFAULT_DAILY_LOSS_PCT = 1.0    # percent of that user's own equity
ORDERS_TABLE = "user_mt5_orders"


def _ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    try:
        db = SessionLocal()
        try:
            db.execute(text(
                "CREATE TABLE IF NOT EXISTS %s ("
                " username VARCHAR(80) PRIMARY KEY,"
                " login VARCHAR(40) NOT NULL,"
                " server VARCHAR(120) NOT NULL,"
                " secret TEXT NOT NULL,"
                " consent_at TIMESTAMPTZ,"
                " enabled BOOLEAN NOT NULL DEFAULT FALSE,"
                " max_shares INTEGER NOT NULL DEFAULT %d,"
                " status VARCHAR(32),"
                " last_error TEXT,"
                " checked_at TIMESTAMPTZ,"
                " created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
                " updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())" % (TABLE, DEFAULT_SHARES)))
            db.commit()
        finally:
            db.close()
    except Exception:  # noqa: BLE001 - a transient DB blip retries on the next call
        return
    _add_c2_columns()
    _create_orders_table()
    _fix_share_default()                 # after both, because it reads auto_copy and the orders table
    _schema_ready = True


def _create_orders_table() -> None:
    """The per-user order log, in its OWN transaction.

    BIGSERIAL is Postgres-only, and while this ran inside the accounts transaction its failure on any other
    dialect rolled the accounts table back too - the table then did not exist at all. The key is now written
    for whichever dialect is connected, and a failure here costs only the log.
    """
    db = SessionLocal()
    try:
        pk = ("id INTEGER PRIMARY KEY AUTOINCREMENT" if db.bind.dialect.name == "sqlite"
              else "id BIGSERIAL PRIMARY KEY")
        db.execute(text(
            "CREATE TABLE IF NOT EXISTS %s ("
            " %s,"
            " username VARCHAR(80) NOT NULL,"
            " at TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
            " event VARCHAR(16) NOT NULL,"
            " symbol VARCHAR(24),"
            " broker_symbol VARCHAR(40),"
            " side VARCHAR(8),"
            " shares INTEGER,"
            " volume DOUBLE PRECISION,"
            " price DOUBLE PRECISION,"
            " admin_price DOUBLE PRECISION,"
            " slippage DOUBLE PRECISION,"
            " pnl DOUBLE PRECISION,"
            " ticket BIGINT,"
            " admin_trade_id VARCHAR(40),"
            " strategy VARCHAR(24),"
            " reason TEXT)" % (ORDERS_TABLE, pk)))
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_%s_user_at ON %s (username, at DESC)"
                        % (ORDERS_TABLE, ORDERS_TABLE)))
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        _log.exception("could not create the copy order log table")
    finally:
        db.close()


# C.2 columns, added to the table C.1 already created so an existing row keeps its account and its consent.
# Each one runs in its OWN transaction and swallows its own failure: a column that is already there, or a
# dialect without ADD COLUMN IF NOT EXISTS, must not roll back the table creation above - that is exactly how
# the first version of this broke, and the table then did not exist at all.
_C2_COLUMNS = (
    ("auto_copy", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("max_positions", "INTEGER NOT NULL DEFAULT %d" % DEFAULT_MAX_POSITIONS),
    ("daily_loss_pct", "DOUBLE PRECISION NOT NULL DEFAULT %s" % DEFAULT_DAILY_LOSS_PCT),
    ("admin_paused", "BOOLEAN NOT NULL DEFAULT FALSE"),
)


# C.1 created max_shares with a DEFAULT of 1, so every account connected before C.2 reads "1 share a trade"
# however the code's default changes - a column default does not apply to rows that already exist. The default
# is moved to 10 for new rows, and rows still sitting on the old default are lifted to 10 ONLY while they have
# never copied anything and have Auto-copy off. A deliberate choice of 1 by a user who is already copying is
# left exactly as it is.
def _fix_share_default() -> None:
    db = SessionLocal()
    try:
        try:
            db.execute(text("ALTER TABLE %s ALTER COLUMN max_shares SET DEFAULT %d" % (TABLE, DEFAULT_SHARES)))
            db.commit()
        except Exception:  # noqa: BLE001 - a dialect without ALTER COLUMN: new rows then use the code default
            db.rollback()
        try:
            res = db.execute(text(
                "UPDATE %s SET max_shares = :n, updated_at = NOW() WHERE max_shares = 1 AND auto_copy = FALSE"
                " AND username NOT IN (SELECT DISTINCT username FROM %s)" % (TABLE, ORDERS_TABLE)),
                {"n": DEFAULT_SHARES})
            db.commit()
            if res.rowcount:
                _log.info("lifted %d account(s) from the old 1-share default to %d", res.rowcount, DEFAULT_SHARES)
        except Exception:  # noqa: BLE001
            db.rollback()
    finally:
        db.close()


def _add_c2_columns() -> None:
    have = set()
    db = SessionLocal()
    try:
        try:
            have = {str(c) for c in db.execute(text("SELECT * FROM %s WHERE 1 = 0" % TABLE)).keys()}
        except Exception:  # noqa: BLE001
            have = set()
    finally:
        db.close()
    for name, spec in _C2_COLUMNS:
        if name in have:
            continue
        db = SessionLocal()
        try:
            db.execute(text("ALTER TABLE %s ADD COLUMN %s %s" % (TABLE, name, spec)))
            db.commit()
        except Exception:  # noqa: BLE001 - already there, or a dialect that refuses: the next call reads it
            db.rollback()
            _log.info("could not add the column %s (it may already exist)", name)
        finally:
            db.close()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def save(username: str, login: str, server: str, password: str, *, consent: bool) -> dict:
    """Store (or replace) a user's account. Refuses without consent, and refuses if it cannot encrypt."""
    if not consent:
        return {"ok": False, "reason": "CONSENT_REQUIRED",
                "detail": "the risk disclaimer must be accepted before an account is connected"}
    if not str(login).strip() or not str(server).strip() or not password:
        return {"ok": False, "reason": "INCOMPLETE", "detail": "login, server and password are all required"}
    try:
        sealed = box.seal(password)
    except Exception as exc:  # noqa: BLE001 - never store a password in the clear
        _log.error("refusing to store an MT5 password: %s", exc)
        return {"ok": False, "reason": "STORAGE_NOT_CONFIGURED", "detail": box.why_unavailable() or str(exc)}

    _ensure_schema()
    db = SessionLocal()
    try:
        db.execute(text(
            "INSERT INTO %s (username, login, server, secret, consent_at, enabled, updated_at)"
            " VALUES (:u, :l, :s, :k, :c, FALSE, :t)"
            " ON CONFLICT (username) DO UPDATE SET login = :l, server = :s, secret = :k,"
            " consent_at = :c, status = NULL, last_error = NULL, updated_at = :t" % TABLE),
            {"u": username, "l": str(login).strip(), "s": str(server).strip(), "k": sealed,
             "c": _now(), "t": _now()})
        db.commit()
    finally:
        db.close()
    # enabled stays FALSE: connecting an account and letting it trade are two separate decisions.
    return {"ok": True, "login_masked": box.mask(login), "server": str(server).strip(), "enabled": False}


def get(username: str) -> Optional[dict]:
    """The row WITHOUT the secret. Nothing outside the connect path ever needs the password."""
    _ensure_schema()
    db = SessionLocal()
    try:
        row = db.execute(text(
            "SELECT username, login, server, consent_at, enabled, max_shares, status, last_error, checked_at,"
            " created_at FROM %s WHERE username = :u" % TABLE), {"u": username}).mappings().first()
    finally:
        db.close()
    if not row:
        return None
    d = dict(row)
    d["login_masked"] = box.mask(d.pop("login", None))     # the full account number never leaves the server
    d["connected"] = bool(d.get("status") == "OK")
    return d


def secret_of(username: str) -> Optional[tuple]:
    """(login, server, password) for the connect path only, or None when it cannot be read."""
    _ensure_schema()
    db = SessionLocal()
    try:
        row = db.execute(text("SELECT login, server, secret FROM %s WHERE username = :u" % TABLE),
                         {"u": username}).mappings().first()
    finally:
        db.close()
    if not row:
        return None
    pw = box.open_(row["secret"])
    if pw is None:
        return None            # key rotated or row corrupt: the caller reports RECONNECT_NEEDED
    return (row["login"], row["server"], pw)


def record_check(username: str, status: str, error: Optional[str] = None) -> None:
    _ensure_schema()
    db = SessionLocal()
    try:
        db.execute(text("UPDATE %s SET status = :s, last_error = :e, checked_at = :t, updated_at = :t"
                        " WHERE username = :u" % TABLE),
                   {"u": username, "s": status[:32], "e": (error or "")[:500] or None, "t": _now()})
        db.commit()
    finally:
        db.close()


def set_enabled(username: str, enabled: bool) -> dict:
    """The user's own kill switch."""
    _ensure_schema()
    db = SessionLocal()
    try:
        res = db.execute(text("UPDATE %s SET enabled = :e, updated_at = :t WHERE username = :u" % TABLE),
                         {"u": username, "e": bool(enabled), "t": _now()})
        db.commit()
    finally:
        db.close()
    if not res.rowcount:
        return {"ok": False, "reason": "NO_ACCOUNT"}
    return {"ok": True, "enabled": bool(enabled)}


def set_max_shares(username: str, shares: int) -> dict:
    n = int(shares)
    if n < 1 or n > 1000:
        return {"ok": False, "reason": "OUT_OF_RANGE", "detail": "between 1 and 1000 shares"}
    _ensure_schema()
    db = SessionLocal()
    try:
        res = db.execute(text("UPDATE %s SET max_shares = :n, updated_at = :t WHERE username = :u" % TABLE),
                         {"u": username, "n": n, "t": _now()})
        db.commit()
    finally:
        db.close()
    if not res.rowcount:
        return {"ok": False, "reason": "NO_ACCOUNT"}
    return {"ok": True, "max_shares": n}


def disconnect(username: str) -> dict:
    """Delete the account and its secret outright. 'Disconnect' must mean the password is gone."""
    _ensure_schema()
    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM %s WHERE username = :u" % TABLE), {"u": username})
        db.commit()
    finally:
        db.close()
    return {"ok": True}


def enabled_accounts() -> list:
    """Every account that has opted in. C.2 and C.3 iterate this; C.1 does not use it."""
    _ensure_schema()
    db = SessionLocal()
    try:
        rows = db.execute(text("SELECT username, max_shares FROM %s WHERE enabled = TRUE" % TABLE)).mappings().all()
    finally:
        db.close()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ C.2 settings and log
SETTING_BOUNDS = {
    "shares": (1, 1000),
    "max_positions": (1, 50),
    "daily_loss_pct": (0.1, 20.0),
}


def settings_of(username: str) -> dict:
    """Every copy setting for one user, with the defaults when the row is missing."""
    _ensure_schema()
    db = SessionLocal()
    try:
        row = db.execute(text(
            "SELECT max_shares AS shares, max_positions, daily_loss_pct, auto_copy, enabled, admin_paused"
            " FROM %s WHERE username = :u" % TABLE), {"u": username}).mappings().first()
    finally:
        db.close()
    if not row:
        return {"shares": DEFAULT_SHARES, "max_positions": DEFAULT_MAX_POSITIONS,
                "daily_loss_pct": DEFAULT_DAILY_LOSS_PCT, "auto_copy": False, "enabled": False,
                "admin_paused": False, "has_account": False}
    d = dict(row)
    d["has_account"] = True
    return d


def update_settings(username: str, patch: dict) -> dict:
    """Set shares / max_positions / daily_loss_pct / auto_copy. Every number is bounded; nothing is free-form."""
    fields, params = [], {"u": username, "t": _now()}
    column = {"shares": "max_shares", "max_positions": "max_positions", "daily_loss_pct": "daily_loss_pct"}
    for key, col in column.items():
        if key not in patch:
            continue
        lo, hi = SETTING_BOUNDS[key]
        try:
            val = float(patch[key]) if key == "daily_loss_pct" else int(patch[key])
        except (TypeError, ValueError):
            return {"ok": False, "reason": "BAD_VALUE", "detail": "%s must be a number" % key}
        if not (lo <= val <= hi):
            return {"ok": False, "reason": "OUT_OF_RANGE", "detail": "%s must be between %s and %s" % (key, lo, hi)}
        fields.append("%s = :%s" % (col, key))
        params[key] = val
    if "auto_copy" in patch:
        fields.append("auto_copy = :auto_copy")
        params["auto_copy"] = bool(patch["auto_copy"])
    if not fields:
        return {"ok": False, "reason": "NOTHING_TO_DO"}
    _ensure_schema()
    db = SessionLocal()
    try:
        res = db.execute(text("UPDATE %s SET %s, updated_at = :t WHERE username = :u"
                              % (TABLE, ", ".join(fields))), params)
        db.commit()
    finally:
        db.close()
    if not res.rowcount:
        return {"ok": False, "reason": "NO_ACCOUNT", "detail": "connect an account first"}
    return dict(settings_of(username), ok=True)


def set_admin_paused(username: Optional[str], paused: bool) -> dict:
    """The operator's pause. username=None pauses EVERY user at once."""
    _ensure_schema()
    db = SessionLocal()
    try:
        if username:
            res = db.execute(text("UPDATE %s SET admin_paused = :p, updated_at = :t WHERE username = :u" % TABLE),
                             {"u": username, "p": bool(paused), "t": _now()})
        else:
            res = db.execute(text("UPDATE %s SET admin_paused = :p, updated_at = :t" % TABLE),
                             {"p": bool(paused), "t": _now()})
        db.commit()
    finally:
        db.close()
    return {"ok": True, "changed": res.rowcount, "admin_paused": bool(paused)}


def copying_users() -> list:
    """Users an order may be placed for: connected, consented, auto-copy on, not paused by anyone."""
    _ensure_schema()
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT username, max_shares AS shares, max_positions, daily_loss_pct FROM %s"
            " WHERE auto_copy = TRUE AND enabled = TRUE AND admin_paused = FALSE AND consent_at IS NOT NULL"
            % TABLE)).mappings().all()
    finally:
        db.close()
    return [dict(r) for r in rows]


def all_accounts() -> list:
    """Every connected account, for the operator's view. Masked account numbers; never a secret."""
    _ensure_schema()
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT username, login, server, consent_at, enabled, auto_copy, admin_paused, max_shares AS shares,"
            " max_positions, daily_loss_pct, status, last_error, checked_at FROM %s ORDER BY username" % TABLE
        )).mappings().all()
    finally:
        db.close()
    out = []
    for r in rows:
        d = dict(r)
        d["login_masked"] = box.mask(d.pop("login", None))
        out.append(d)
    return out


def log_order(username: str, event: str, **cols) -> None:
    """Record one order outcome. Never raises into the trading path: a log failure must not stop a close."""
    allowed = ("symbol", "broker_symbol", "side", "shares", "volume", "price", "admin_price", "slippage",
               "pnl", "ticket", "admin_trade_id", "strategy", "reason")
    data = {k: cols.get(k) for k in allowed}
    data.update(username=username, event=str(event)[:16])
    try:
        _ensure_schema()
        db = SessionLocal()
        try:
            db.execute(text(
                "INSERT INTO %s (username, event, symbol, broker_symbol, side, shares, volume, price,"
                " admin_price, slippage, pnl, ticket, admin_trade_id, strategy, reason)"
                " VALUES (:username, :event, :symbol, :broker_symbol, :side, :shares, :volume, :price,"
                " :admin_price, :slippage, :pnl, :ticket, :admin_trade_id, :strategy, :reason)" % ORDERS_TABLE),
                data)
            db.commit()
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        _log.exception("could not write the copy log for %s", username)


def orders_of(username: str, limit: int = 100) -> list:
    _ensure_schema()
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT at, event, symbol, broker_symbol, side, shares, volume, price, admin_price, slippage,"
            " pnl, ticket, strategy, reason FROM %s WHERE username = :u ORDER BY at DESC LIMIT :n"
            % ORDERS_TABLE), {"u": username, "n": max(1, min(int(limit), 500))}).mappings().all()
    finally:
        db.close()
    return [dict(r) for r in rows]


OPEN_COLS = ("admin_trade_id", "ticket", "symbol", "broker_symbol", "side", "shares", "volume", "price",
             "strategy")


def open_tickets(username: str) -> list:
    """Positions this system opened for a user and has not recorded closing - the position ceiling counts these."""
    _ensure_schema()
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT %s FROM %s WHERE username = :u AND event = 'FILLED' AND ticket IS NOT NULL"
            " AND admin_trade_id NOT IN (SELECT admin_trade_id FROM %s WHERE username = :u AND event = 'CLOSED'"
            " AND admin_trade_id IS NOT NULL)" % (", ".join(OPEN_COLS), ORDERS_TABLE, ORDERS_TABLE)),
            {"u": username}).mappings().all()
    finally:
        db.close()
    return [dict(r) for r in rows]


def open_row(username: str, admin_trade_id: str) -> Optional[dict]:
    """The position we opened for this user against one of our trades, or None when we never opened one."""
    for r in open_tickets(username):
        if str(r.get("admin_trade_id")) == str(admin_trade_id):
            return r
    return None


def realized_today(username: str) -> float:
    """What this user has already realized today, by New York date - the daily loss limit measures this."""
    _ensure_schema()
    db = SessionLocal()
    try:
        val = db.execute(text(
            "SELECT COALESCE(SUM(pnl), 0) FROM %s WHERE username = :u AND pnl IS NOT NULL"
            " AND (at AT TIME ZONE 'America/New_York')::date"
            " = (NOW() AT TIME ZONE 'America/New_York')::date" % ORDERS_TABLE), {"u": username}).scalar()
    finally:
        db.close()
    return float(val or 0.0)
