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
    return value


def _refresh(key: str) -> None:
    fn, _ = _fns[key]
    if singleflight.running(f"swr:{key}"):
        return
    threading.Thread(target=singleflight.call,
                     args=(f"swr:{key}", lambda: _run(key, fn), FIRST_WAIT),
                     daemon=True, name=f"swr-{key}").start()


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
        if now - at > fresh_for:
            _refresh(key)
        return value

    value, done = singleflight.call(f"swr:{key}", lambda: _run(key, fn), FIRST_WAIT)
    if done and value is not None:
        return value
    return {"status": "LOADING", "detail": "Still building; try again shortly."}


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
               and now - _values.get(k, (0, None))[0] > fresh]
    for key in due:
        fn, _ = _fns[key]
        singleflight.call(f"swr:{key}", lambda k=key, f=fn: _run(k, f), FIRST_WAIT)


def start_warmer(every: float = 30.0) -> None:
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
