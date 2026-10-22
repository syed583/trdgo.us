"""Don't repeat background work just because the server restarted.

Every background loop used to run its first pass the moment the process booted, whatever
had happened before the restart. Each restart therefore re-fetched hundreds to thousands
of Unusual Whales calls' worth of data that was already cached and fresh.

A loop now records when it last finished (in the environment-namespaced DB cache, so it
survives restarts) and, on boot, sleeps only until its NEXT pass is due:

    boot_guard.wait_until_due("snapshots", 6 * 3600)   # once, before the loop
    ...
    boot_guard.mark("snapshots")                      # after each completed pass

No record (first ever boot, or a long outage) means "due now", so nothing is skipped
that actually needs running. Unavailable DB or a test run means "due now" as well.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

_KEY = "bootrun:{}"


def last_run(name: str) -> Optional[float]:
    try:
        import db_cache
        held = db_cache.get(_KEY.format(name))
    except Exception:  # noqa: BLE001
        return None
    if held and isinstance(held[1], dict):
        try:
            return float(held[1].get("at"))
        except (TypeError, ValueError):
            return None
    return None


def mark(name: str) -> None:
    """Record that `name` just finished a pass."""
    try:
        import db_cache
        db_cache.put(_KEY.format(name), {"at": time.time()})
    except Exception:  # noqa: BLE001
        pass


def seconds_until_due(name: str, interval: float) -> float:
    lr = last_run(name)
    if lr is None:
        return 0.0
    return max(0.0, lr + interval - time.time())


def recent(name: str, within: float) -> bool:
    """True when `name` finished a pass less than `within` seconds ago."""
    return seconds_until_due(name, within) > 0


def wait_until_due(name: str, interval: float, sleep: Callable[[float], None] = time.sleep) -> float:
    """Sleep until `name`'s next pass is due; returns how long it waited."""
    wait = seconds_until_due(name, interval)
    if wait > 0:
        sleep(wait)
    return wait
