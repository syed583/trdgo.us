"""G: each user's own map from our ticker to the name their broker uses.

Why a per-user map at all. Our operator's map is for ONE broker. A user may be at a different one, where the
same company is AAPL, AAPL.m, AAPL.US or something else again - and where some of our symbols simply do not
exist. C.2 guessed, by trying our name first and then a list of suffixes against the user's account. Guessing
is the wrong default when the result is a real order: a near-miss suffix can be a different instrument, which
is exactly how the operator's own book took wrong-symbol fills in A.18.

So from G a user's order goes ONLY to a symbol that user has mapped and we have verified against their
broker. Unmapped is not an error and not a hidden failure: the signal still shows on screen for everyone, and
the order is skipped with NOT_MAPPED so the reason is visible on their own page.

This also replaces the hard-coded exclusion list FOR USERS. That list (ADBE, AMD, AVGO, CAT, COP, COST, LLY,
MU, ORCL, QQQ, SPY, UNH - and a bogus "ORACLE" that matches nothing) was hand-written for the operator's
broker. A user whose broker offers AMD cleanly has no reason to be bound by it. The operator's own list is
untouched.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text

from database import SessionLocal

_log = logging.getLogger("user_mt5")

TABLE = "user_symbol_map"
MAX_PER_USER = 200          # our universe is 55; this is a guard against a runaway client, not a product rule

_schema_ready = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    db = SessionLocal()
    try:
        db.execute(text(
            "CREATE TABLE IF NOT EXISTS %s ("
            " username VARCHAR(80) NOT NULL,"
            " symbol VARCHAR(24) NOT NULL,"
            " broker_symbol VARCHAR(48) NOT NULL,"
            " contract_size DOUBLE PRECISION,"
            " volume_min DOUBLE PRECISION,"
            " volume_step DOUBLE PRECISION,"
            " volume_max DOUBLE PRECISION,"
            " verified_at TIMESTAMPTZ,"
            " created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
            " PRIMARY KEY (username, symbol))" % TABLE))
        db.commit()
    except Exception:  # noqa: BLE001 - a transient blip retries on the next call
        db.rollback()
        db.close()
        return
    db.close()
    _schema_ready = True


def all_for(username: str) -> list:
    """Every mapping this user has made, newest first."""
    _ensure_schema()
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT symbol, broker_symbol, contract_size, volume_min, volume_step, volume_max, verified_at,"
            " created_at FROM %s WHERE username = :u ORDER BY symbol" % TABLE), {"u": username}).mappings().all()
    finally:
        db.close()
    return [dict(r) for r in rows]


def get(username: str, symbol: str) -> Optional[dict]:
    """The user's mapping for one of our symbols, or None. This is what decides whether an order is sent."""
    _ensure_schema()
    db = SessionLocal()
    try:
        row = db.execute(text(
            "SELECT symbol, broker_symbol, contract_size, volume_min, volume_step, volume_max, verified_at"
            " FROM %s WHERE username = :u AND symbol = :s" % TABLE),
            {"u": username, "s": (symbol or "").upper()}).mappings().first()
    finally:
        db.close()
    return dict(row) if row else None


def save(username: str, symbol: str, broker_symbol: str, info: Optional[dict] = None) -> dict:
    """Record a mapping the user chose. `info` is what their broker said about the symbol when we checked it.

    A mapping is stored only with the broker's own volume rules attached: without them we cannot size an
    order, and a mapping we cannot size is a skip waiting to happen at the worst moment.
    """
    sym = (symbol or "").strip().upper()
    bsym = (broker_symbol or "").strip()
    if not sym or not bsym:
        return {"ok": False, "reason": "BAD_INPUT", "detail": "a symbol and a broker symbol are both needed"}
    info = info or {}
    if info.get("volume_min") is None or info.get("volume_step") is None:
        return {"ok": False, "reason": "NO_VOLUME_RULES",
                "detail": "your broker did not give volume rules for %s, so an order could not be sized" % bsym}

    _ensure_schema()
    if len(all_for(username)) >= MAX_PER_USER and not get(username, sym):
        return {"ok": False, "reason": "TOO_MANY", "detail": "at most %d mapped symbols" % MAX_PER_USER}

    db = SessionLocal()
    params = {"u": username, "s": sym, "b": bsym, "cs": info.get("contract_size"),
              "vmin": info.get("volume_min"), "vstep": info.get("volume_step"), "vmax": info.get("volume_max"),
              "t": _now()}
    try:
        res = db.execute(text(
            "UPDATE %s SET broker_symbol = :b, contract_size = :cs, volume_min = :vmin, volume_step = :vstep,"
            " volume_max = :vmax, verified_at = :t WHERE username = :u AND symbol = :s" % TABLE), params)
        if not res.rowcount:
            db.execute(text(
                "INSERT INTO %s (username, symbol, broker_symbol, contract_size, volume_min, volume_step,"
                " volume_max, verified_at, created_at) VALUES (:u, :s, :b, :cs, :vmin, :vstep, :vmax, :t, :t)"
                % TABLE), params)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        _log.exception("could not save the mapping %s -> %s for %s", sym, bsym, username)
        return {"ok": False, "reason": "SAVE_FAILED", "detail": "%s" % type(exc).__name__}
    finally:
        db.close()
    return {"ok": True, "symbol": sym, "broker_symbol": bsym}


def remove(username: str, symbol: str) -> dict:
    """Unmap one symbol. Nothing new is sent for it afterwards; positions already open still close."""
    _ensure_schema()
    db = SessionLocal()
    try:
        res = db.execute(text("DELETE FROM %s WHERE username = :u AND symbol = :s" % TABLE),
                         {"u": username, "s": (symbol or "").upper()})
        db.commit()
    finally:
        db.close()
    return {"ok": True, "removed": res.rowcount}


def clear(username: str) -> dict:
    _ensure_schema()
    db = SessionLocal()
    try:
        res = db.execute(text("DELETE FROM %s WHERE username = :u" % TABLE), {"u": username})
        db.commit()
    finally:
        db.close()
    return {"ok": True, "removed": res.rowcount}
