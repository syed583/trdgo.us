"""One rate limit for the whole broker connection, shared by the operator's book and every user's.

The broker counts orders per second across the ACCOUNT, not per program. On 2026-11-16 four agents fired the
same symbol within the same second and the broker answered:

    {"error":"Rate limit exceeded. Max 3 orders per second."}

C.2 had a limiter, but only over user orders, and it never saw the operator's. Two limiters that cannot see
each other do not add up to a limit - so this is one gate that both sides pass through.

It is deliberately tiny: a lock, the time of the last order, and a sleep. No queue, no thread, no ordering
guarantees beyond "orders leave at most MAX_PER_SECOND apart". A caller that is made to wait is simply slower,
never refused: dropping a trade to keep a rate limit would be worse than placing it a fraction late.
"""
from __future__ import annotations

import os
import threading
import time

# The broker's documented ceiling is 3/s. We aim just under it, because the limit is enforced on their clock,
# not ours, and a burst that arrives on the boundary is rejected outright with NO_RETRY.
DEFAULT_MAX_PER_SECOND = 2.5
MAX_PER_SECOND = float(os.getenv("MT5_MAX_ORDERS_PER_SECOND") or DEFAULT_MAX_PER_SECOND)

_lock = threading.Lock()
_last = 0.0
_stats = {"orders": 0, "waited_s": 0.0, "max_wait_s": 0.0}


def gap_seconds() -> float:
    try:
        n = float(MAX_PER_SECOND)
    except (TypeError, ValueError):
        n = 2.5
    return 1.0 / n if n > 0 else 0.0


def acquire(who: str = "") -> float:
    """Hold the caller until the next order may go out. Returns how long it waited, in seconds."""
    global _last
    gap = gap_seconds()
    with _lock:
        now = time.time()
        wait = (_last + gap) - now
        if wait > 0:
            time.sleep(wait)
        else:
            wait = 0.0
        _last = time.time()
        _stats["orders"] += 1
        _stats["waited_s"] = round(_stats["waited_s"] + wait, 3)
        _stats["max_wait_s"] = round(max(_stats["max_wait_s"], wait), 3)
    return wait


def stats() -> dict:
    d = dict(_stats)
    d["max_per_second"] = MAX_PER_SECOND
    return d


def reset() -> None:
    """Tests only."""
    global _last
    with _lock:
        _last = 0.0
        _stats.update(orders=0, waited_s=0.0, max_wait_s=0.0)
