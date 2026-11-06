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

import base64
import gzip
import json
import logging
import os
import threading
import time
from typing import Any, Optional

from sqlalchemy import text

from database import SessionLocal


def namespace() -> str:
    """Key prefix that keeps environments' cache rows apart in the shared DB.

    Production (APP_ENV=production, set in the VPS .env) keeps the bare keys it has
    always used. Anything else -- a developer machine, CI, an unset variable -- is
    "local:" so it can neither read nor overwrite what production cached. Fail-closed:
    forgetting to set the variable isolates, it never shares."""
    env = (os.environ.get("APP_ENV") or "").strip().lower()
    return "" if env == "production" else "local:"


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
# tier still holds them for this process. The limit applies to what is actually
# STORED, i.e. after compression below.
_MAX_BYTES = 1_500_000

# Large JSON payloads (a 537-name Claude Pro scan is ~1.7 MB) compress ~8x, so values above this size
# are stored gzip+base64 behind a prefix instead of raising the limit. Smaller values stay plain JSON,
# and rows written before this change (plain JSON) remain readable.
_COMPRESS_OVER = 200_000
_GZ_PREFIX = "gz1:"

log = logging.getLogger(__name__)
_dropped: dict[str, dict] = {}
_dropped_lock = threading.Lock()


def _encode(blob: str) -> str:
    """JSON text -> stored text (compressed when large)."""
    if len(blob) <= _COMPRESS_OVER:
        return blob
    return _GZ_PREFIX + base64.b64encode(gzip.compress(blob.encode("utf-8"), 6)).decode("ascii")


def _decode(stored: str) -> Any:
    """Stored text -> value (plain JSON or gz1: compressed)."""
    if stored.startswith(_GZ_PREFIX):
        return json.loads(gzip.decompress(base64.b64decode(stored[len(_GZ_PREFIX):])).decode("utf-8"))
    return json.loads(stored)


def dropped() -> dict:
    """Keys whose value was too large to persist even after compression (key -> sizes, when)."""
    with _dropped_lock:
        return {k: dict(v) for k, v in _dropped.items()}

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
            {"k": namespace() + key}).first()
        if not row:
            return None
        return float(row[1]), _decode(row[0])
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
    ns = namespace()
    db = SessionLocal()
    try:
        rows = db.execute(
            text("SELECT cache_key, value, updated_at FROM swr_cache "
                 "WHERE cache_key = ANY(:ks)"),
            {"ks": [ns + k for k in ks]}).all()
        for k, v, t in rows:
            try:
                out[k[len(ns):]] = (float(t), _decode(v))
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
        raw = json.dumps(value, default=str)
    except (TypeError, ValueError):
        return
    blob = _encode(raw)
    if len(blob) > _MAX_BYTES:
        # Never drop silently: record and log it so an oversized screen is visible, not mysteriously cold.
        with _dropped_lock:
            _dropped[key] = {"raw_bytes": len(raw), "stored_bytes": len(blob), "limit": _MAX_BYTES, "at": when}
        log.warning("db_cache: %s not persisted (%d bytes raw, %d stored, limit %d)", key, len(raw), len(blob), _MAX_BYTES)
        return
    with _dropped_lock:
        _dropped.pop(key, None)
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
    threading.Thread(target=_write, args=(namespace() + key, value, when),
                     daemon=True, name="dbcache-put").start()
