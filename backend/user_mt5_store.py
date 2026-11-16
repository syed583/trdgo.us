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
DEFAULT_MAX_SHARES = 1          # C.3 will multiply this; starting at one share is the cautious default


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
                " updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())" % (TABLE, DEFAULT_MAX_SHARES)))
            db.commit()
        finally:
            db.close()
    except Exception:  # noqa: BLE001 - a transient DB blip retries on the next call
        return
    _schema_ready = True


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
