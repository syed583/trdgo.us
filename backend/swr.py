"""
Serve the last answer now; refresh it behind the page.

Some screens are built from work that takes tens of seconds -- the market-wide
options flow reads every print of the session across the watched tape. Their
own caches expire after a minute, which meant nearly every visit paid the full
cost again: 17s for the tape, 50s for unusual activity, on every open.

Here the page gets whatever answer is held, however old, immediately. If it is
older than the endpoint's freshness window a refresh starts in the background
(one per key, via singleflight), and the next poll sees the new one. Only the
very first request for a key ever waits, and the warmer below exists so that
is rarely a person.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable

import singleflight

try:
    import db_cache
except Exception:  # noqa: BLE001 - the DB tier is optional; SWR works without it
    db_cache = None  # type: ignore

# How long a first-ever request waits before giving up on the build. The work
# carries on, so the next request finds it finished.
FIRST_WAIT = 90.0

# A key nobody has asked for in this long stops being refreshed in the
# background: warming screens nobody has open spends provider budget for
# nothing.
IDLE_AFTER = 20 * 60.0

_lock = threading.Lock()
_values: dict[str, tuple[float, Any]] = {}
_fns: dict[str, tuple[Callable[[], Any], float]] = {}
_asked: dict[str, float] = {}


def _run(key: str, fn: Callable[[], Any]) -> Any:
    value = fn()
    with _lock:
        _values[key] = (time.time(), value)
    # Persist good answers to the shared DB tier so a restart -- or the other
    # server -- can serve this without paying the provider again.
    if db_cache is not None:
        try:
            db_cache.put(key, value)
        except Exception:  # noqa: BLE001 - persistence is best-effort
            pass
    return value


# Keys with a refresh in flight, so a burst of polls does not start a dozen
# refreshes of one screen.
_refreshing: set[str] = set()


def _refresh(key: str) -> None:
    """
    Rebuild ``key`` on a dedicated background thread.

    This used to hand the work to singleflight's shared request pool. That
    pool is bounded and also serves first-ever foreground builds, so under
    load -- many screens warming or a slow provider holding workers -- a
    background refresh could queue behind them and a stale screen would stay
    stale far longer than its window. Refreshes run on their own threads now,
    deduplicated per key, so a foreground pile-up can never starve them.
    """
    with _lock:
        if key in _refreshing:
            return
        entry = _fns.get(key)
        if entry is None:
            return
        _refreshing.add(key)
    fn, _ = entry

    def work() -> None:
        try:
            _run(key, fn)
        except Exception:  # noqa: BLE001 - a failed refresh keeps the last good value
            pass
        finally:
            with _lock:
                _refreshing.discard(key)

    threading.Thread(target=work, daemon=True, name=f"swr-{key}").start()


# Cache keys are built from request input, so the three dicts below must not
# be allowed to grow without bound. When _asked exceeds this ceiling the
# least-recently-asked keys are dropped from all three at once. IDLE_AFTER
# already stops refreshing them; this reclaims the memory a flood of distinct
# keys would otherwise pin forever.
_MAX_KEYS = 2000


def _evict_if_full(now: float) -> None:
    """Drop the least-recently-asked keys. Caller holds _lock."""
    if len(_asked) <= _MAX_KEYS:
        return
    ordered = sorted(_asked, key=_asked.get)
    for key in ordered[: len(_asked) - _MAX_KEYS]:
        _asked.pop(key, None)
        _fns.pop(key, None)
        _values.pop(key, None)


def serve(key: str, fn: Callable[[], Any], fresh_for: float) -> Any:
    """The held answer for ``key``, refreshing it in the background if old."""
    now = time.time()
    with _lock:
        _fns[key] = (fn, fresh_for)
        _asked[key] = now
        held = _values.get(key)
        _evict_if_full(now)

    if held is not None:
        at, value = held
        # >= not >: a value that has reached its freshness age is due for
        # refresh. With > and a coarse clock (Windows time.time() ticks every
        # ~16ms), two calls in one tick give now-at==0, and 0 > 0 is False --
        # so a 0-second window never refreshed at all.
        if now - at >= fresh_for:
            _refresh(key)
        return value

    # Cold in this process: before paying the provider, look in the shared DB
    # tier. After a restart (or on the second server) the last good answer is
    # usually there.
    #
    # But only serve it *instantly* when it is still within its freshness
    # window. A stale DB copy is NOT served as-is: a live buy/sell signal
    # checked while the market is open must not be answered from an old snapshot
    # (e.g. one left in the DB from before a restart). When the copy is stale we
    # build fresh -- hitting the provider -- and only fall back to the stale copy
    # if that build cannot finish in time, which still beats a blank screen.
    stored_stale: Any = None
    if db_cache is not None:
        try:
            stored = db_cache.get(key)
        except Exception:  # noqa: BLE001
            stored = None
        if stored is not None:
            at, value = stored
            if now - at < fresh_for:
                # Genuinely fresh: serve without paying the provider.
                with _lock:
                    _values[key] = (at, value)
                return value
            # Stale: keep it only as a last-resort fallback below.
            stored_stale = value

    # A cold build that raises must not become an HTTP 500 on the screen -- the
    # app speaks in status payloads, so a failed build returns one too (falling
    # back to a stale copy first if we have one).
    try:
        value, done = singleflight.call(f"swr:{key}", lambda: _run(key, fn), FIRST_WAIT)
    except Exception:  # noqa: BLE001
        if stored_stale is not None:
            return stored_stale
        return {"status": "DATA_UNAVAILABLE",
                "detail": "This data could not be built right now. Try again shortly."}
    if done and value is not None:
        return value
    if stored_stale is not None:
        return stored_stale
    return {"status": "LOADING", "detail": "Still building; try again shortly."}


def peek(key: str) -> Any:
    """The held value for ``key`` if present (in-process or DB), without building."""
    with _lock:
        held = _values.get(key)
    if held is not None:
        return held[1]
    if db_cache is not None:
        try:
            stored = db_cache.get(key)
        except Exception:  # noqa: BLE001
            stored = None
        if stored is not None:
            return stored[1]
    return None


def peek_many(keys: list[str]) -> dict[str, Any]:
    """
    Held values for many keys without building -- in-process first, then any
    still-missing keys in a SINGLE DB read (not one round trip per key).
    """
    out: dict[str, Any] = {}
    ks = [k for k in (keys or []) if k]
    with _lock:
        for k in ks:
            held = _values.get(k)
            if held is not None:
                out[k] = held[1]
    missing = [k for k in ks if k not in out]
    if missing and db_cache is not None:
        try:
            stored = db_cache.get_many(missing)
        except Exception:  # noqa: BLE001
            stored = {}
        # Pull each DB hit into the in-process cache too, so the next peek for it
        # is a dict lookup rather than another round trip. This matters most when
        # the DB is far away (a local backend against a remote Supabase): the
        # first list load pays the latency once, then polls are instant.
        with _lock:
            for k, (at, value) in stored.items():
                out[k] = value
                _values.setdefault(k, (at, value))
    return out


def register(key: str, fn: Callable[[], Any], fresh_for: float) -> None:
    """Make ``key`` known to the warmer before anyone has asked for it."""
    with _lock:
        _fns.setdefault(key, (fn, fresh_for))
        _asked.setdefault(key, time.time())


def warm_once() -> None:
    """Refresh every recently used key that has gone stale, one at a time."""
    now = time.time()
    with _lock:
        due = [k for k, (_, fresh) in _fns.items()
               if now - _asked.get(k, 0) < IDLE_AFTER
               and now - _values.get(k, (0, None))[0] >= fresh]
    for key in due:
        fn, _ = _fns[key]
        singleflight.call(f"swr:{key}", lambda k=key, f=fn: _run(k, f), FIRST_WAIT)


def start_warmer(every: float = 60.0) -> None:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return

    def loop() -> None:
        while True:
            try:
                warm_once()
            except Exception:  # noqa: BLE001 - the warmer must never die
                pass
            time.sleep(every)

    threading.Thread(target=loop, daemon=True, name="swr-warmer").start()


def clear_key(prefix: str) -> None:
    """Forget held answers under ``prefix`` -- their source just changed."""
    with _lock:
        for key in [k for k in _values if k.startswith(prefix)]:
            _values.pop(key, None)


def clear() -> None:
    """For tests."""
    with _lock:
        _values.clear()
        _fns.clear()
        _asked.clear()
        _refreshing.clear()
