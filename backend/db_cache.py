"""
A shared, persistent tier under the in-memory SWR cache.

The SWR cache in ``swr.py`` is fast but per-process: a restart wipes it, and the
local box and the VPS keep their own copies, so both re-fetch the same data. This
module keeps the last good answer for each cache key in the shared Supabase
database, so:

* after a restart the first request for a key is served from the DB instead of
  paying the provider again, and
* whichever server fetched a key first fills the cache the other one reads.

It stores JSON payloads keyed by the same string ``swr.serve`` already uses.
Writes are best-effort and happen off the request thread; a database that is
briefly unreachable degrades to the in-memory behaviour, never an error.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Optional

from sqlalchemy import text

from database import SessionLocal


def _disabled() -> bool:
    # Never touch the shared DB from the test suite: a test building a cached
    # value would otherwise write fixture data into the live cache.
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))

# Statuses that mean "no real answer right now" -- never persist these, or a
# transient rate-limit page would be handed to the other server as if it were
# data and served back for the key's whole freshness window.
_SKIP_STATUS = {
    "LOADING", "RATE_LIMITED", "BUDGET_EXHAUSTED", "PROVIDER_NOT_CONFIGURED",
    "ERROR", "BAD_REQUEST",
}

# Payloads larger than this are not worth a round trip to store; the in-memory
# tier still holds them for this process.
_MAX_BYTES = 1_500_000

_ready = False
_ready_lock = threading.Lock()


def _ensure_table() -> bool:
    """Create the cache table once. Returns False if the DB is unreachable."""
    global _ready
    if _ready:
        return True
    with _ready_lock:
        if _ready:
            return True
        db = SessionLocal()
        try:
            db.execute(text(
                "CREATE TABLE IF NOT EXISTS swr_cache ("
                "  cache_key   TEXT PRIMARY KEY,"
                "  value       TEXT NOT NULL,"
                "  updated_at  DOUBLE PRECISION NOT NULL"
                ")"))
            db.commit()
            _ready = True
            return True
        except Exception:  # noqa: BLE001 - no cache table means no DB tier
            db.rollback()
            return False
        finally:
            db.close()


def get(key: str) -> Optional[tuple[float, Any]]:
    """The stored (updated_epoch, value) for ``key``, or None."""
    if _disabled() or not _ensure_table():
        return None
    db = SessionLocal()
    try:
        row = db.execute(
            text("SELECT value, updated_at FROM swr_cache WHERE cache_key = :k"),
            {"k": key}).first()
        if not row:
            return None
        return float(row[1]), json.loads(row[0])
    except Exception:  # noqa: BLE001 - a read miss is just a miss
        return None
    finally:
        db.close()


def get_many(keys: list[str]) -> dict[str, tuple[float, Any]]:
    """
    The stored (updated_epoch, value) for each key present, in ONE query.

    Reading N keys with ``get`` is N round trips to a remote DB -- colouring a
    ~200-stock list that way took over a minute. This fetches them all at once.
    """
    out: dict[str, tuple[float, Any]] = {}
    ks = [k for k in (keys or []) if k]
    if not ks or _disabled() or not _ensure_table():
        return out
    db = SessionLocal()
    try:
        rows = db.execute(
            text("SELECT cache_key, value, updated_at FROM swr_cache "
                 "WHERE cache_key = ANY(:ks)"),
            {"ks": ks}).all()
        for k, v, t in rows:
            try:
                out[k] = (float(t), json.loads(v))
            except Exception:  # noqa: BLE001 - skip a single corrupt row
                pass
    except Exception:  # noqa: BLE001 - a read miss is just a miss
        return out
    finally:
        db.close()
    return out


def _write(key: str, value: Any, when: float) -> None:
    if not _ensure_table():
        return
    try:
        blob = json.dumps(value, default=str)
    except (TypeError, ValueError):
        return
    if len(blob) > _MAX_BYTES:
        return
    db = SessionLocal()
    try:
        db.execute(text(
            "INSERT INTO swr_cache (cache_key, value, updated_at) "
            "VALUES (:k, :v, :t) "
            "ON CONFLICT (cache_key) DO UPDATE SET value = :v, updated_at = :t"),
            {"k": key, "v": blob, "t": when})
        db.commit()
    except Exception:  # noqa: BLE001 - a failed write just leaves the old row
        db.rollback()
    finally:
        db.close()


def put(key: str, value: Any) -> None:
    """Persist a good answer for ``key``, off the caller's thread."""
    if _disabled():
        return
    if isinstance(value, dict) and value.get("status") in _SKIP_STATUS:
        return
    when = time.time()
    threading.Thread(target=_write, args=(key, value, when),
                     daemon=True, name="dbcache-put").start()
