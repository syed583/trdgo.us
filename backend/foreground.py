"""
Who is waiting, right now.

The app has one expensive shared resource -- a single TWS connection, plus a
handful of rate-limited HTTP providers -- and two kinds of caller competing
for it: a page someone is staring at, and background work nobody is waiting
for. Left alone they compete on equal terms, so a rolling market scan makes
every screen feel slow while it runs.

This is the referee. Request handlers mark themselves in flight; background
workers call ``yield_to_foreground()`` between units of work and stand aside
while a person is waiting. Nothing is cancelled and nothing is queued -- the
background job simply does not start its next item until the screen is served.

Deliberately not a priority queue or a semaphore over the providers: those
change how every call is made. This changes only when background work takes
its turn, which is the actual problem.
"""

from __future__ import annotations

import threading
import time

# Paths that are background work themselves, or are too cheap to be worth
# pausing a scan for. A poll of the board is the page asking what the scan has
# found; treating it as foreground would stop the scan it is asking about.
IGNORE_PREFIXES = (
    "/api/ai-trade/board",
    "/api/health",
    "/api/status",
)

# After the last foreground request finishes, keep standing aside this long. A
# page load is a burst of requests with gaps between them, and resuming inside
# a gap puts the scan back in the way of the next one.
GRACE = 1.5

# Never stand aside for longer than this in one wait. A request that hangs --
# a provider that never answers -- must not stop background work for ever.
MAX_YIELD = 20.0

_lock = threading.Lock()
_inflight = 0
_last_finished = 0.0


class _Request:
    """Marks one foreground request as in flight for as long as it runs."""

    def __enter__(self) -> "_Request":
        global _inflight
        with _lock:
            _inflight += 1
        return self

    def __exit__(self, *exc) -> None:
        global _inflight, _last_finished
        with _lock:
            _inflight = max(0, _inflight - 1)
            _last_finished = time.monotonic()


def request() -> _Request:
    return _Request()


def busy() -> bool:
    """True while a person is waiting for something."""
    with _lock:
        if _inflight > 0:
            return True
        return (time.monotonic() - _last_finished) < GRACE


def inflight() -> int:
    with _lock:
        return _inflight


def yield_to_foreground(max_wait: float = MAX_YIELD) -> float:
    """
    Block while a foreground request is in flight. Returns seconds waited.

    Called between units of background work, never inside one: a half-finished
    scan holding a provider connection open helps nobody.
    """
    if not busy():
        return 0.0
    started = time.monotonic()
    while busy():
        if time.monotonic() - started >= max_wait:
            break
        time.sleep(0.1)
    return round(time.monotonic() - started, 2)
