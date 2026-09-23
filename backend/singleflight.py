"""
One computation per key, however many callers ask for it.

A page that gives up on a slow provider and returns what it has is a good
page. A page that gives up and lets the *next* request start the same slow
work again is a pile-up: three refreshes of one symbol means three concurrent
scans of the same providers, each slower than the last because they are
competing with each other. That is not a theoretical failure -- it is exactly
what the earnings overview did, taking 16s, then 24s, then 36s for the same
symbol as the abandoned work accumulated behind it.

So: the first caller for a key does the work; everyone arriving while it runs
waits on that same result instead of starting their own. Nobody queues behind
anybody -- they share one answer.

Callers pass their own timeout. Timing out means "draw the page without this
part"; the work carries on and the next caller finds it finished, which is
the whole point of not restarting it.
"""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Any, Callable, Optional

# Shared pool. Bounded, because the point of this module is to stop unbounded
# duplicate work -- an unbounded pool would just move the pile-up here.
_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="flight")

_lock = threading.Lock()
_inflight: dict[str, Future] = {}


def call(key: str, fn: Callable[[], Any], timeout: float) -> tuple[Any, bool]:
    """
    Run ``fn`` under ``key``, or join the run already in progress.

    Returns ``(value, completed)``. ``completed`` is False when the work was
    still running at the timeout -- the value is then None and the caller
    should render without it.
    """
    with _lock:
        future = _inflight.get(key)
        if future is None or future.done():
            future = _POOL.submit(fn)
            _inflight[key] = future
            mine = True
        else:
            mine = False

    try:
        value = future.result(timeout=timeout)
        return value, True
    except FuturesTimeout:
        return None, False
    except Exception:  # noqa: BLE001 - the caller decides what a failure means
        return None, True
    finally:
        if mine or future.done():
            with _lock:
                # Only clear the entry if it is still the one just waited on:
                # a newer flight may have replaced it.
                if _inflight.get(key) is future and future.done():
                    _inflight.pop(key, None)


def running(key: str) -> bool:
    with _lock:
        future = _inflight.get(key)
        return future is not None and not future.done()


def active() -> int:
    with _lock:
        return sum(1 for f in _inflight.values() if not f.done())


def reset() -> None:
    """Drop finished entries. For tests."""
    with _lock:
        for key in [k for k, f in _inflight.items() if f.done()]:
            _inflight.pop(key, None)


def _pool_for_tests() -> Optional[ThreadPoolExecutor]:
    return _POOL
