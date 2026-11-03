"""Shared concurrency slots for the Unusual Whales token.

The provider's one real limit is THREE simultaneous REST requests per token, counted across every process and machine that uses it (support, 2026-10-23 and again on 2026-11-03; WebSocket messages are not counted).
The client already caps threads inside one process (`unusualwhales_service._inflight`). This module extends the same cap to:

  1. every process on ONE machine: three slot files held with OS file locks (released by the kernel if the holder dies, so a crash can never leak a slot);
  2. every machine: an optional lease broker (`uw_lease_broker`) that hands out at most three short leases with a time-to-live.

Both layers are OFF unless switched on by environment (UW_MACHINE_SLOTS=1, UW_BROKER_URL), so deploying this file changes nothing by itself. Acquisition order is always: in-process semaphore, then machine slot, then broker
lease, and release is the reverse, so two waiters can never deadlock each other."""
from __future__ import annotations

import contextlib
import os
import random
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

PROVIDER_CONCURRENCY = 3


class SlotTimeout(Exception):
    """No slot became free inside the allowed wait."""


def _lock(fh) -> bool:
    try:
        if sys.platform == "win32":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fh) -> None:
    try:
        if sys.platform == "win32":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


class MachineSlots:
    """N slot files in one directory. `hold()` waits (with jitter) for any free slot and holds it for the duration of the block."""

    def __init__(self, directory=None, n: int = PROVIDER_CONCURRENCY):
        self.dir = Path(directory or os.environ.get("UW_SLOT_DIR") or Path(tempfile.gettempdir()) / "trdgo_uw_slots")
        self.n = max(1, int(n))
        self.dir.mkdir(parents=True, exist_ok=True)

    def _try_any(self):
        order = list(range(self.n))
        random.shuffle(order)                                      # no slot is favoured
        for i in order:
            fh = open(self.dir / ("slot_%d.lock" % i), "a+b")
            if _lock(fh):
                try:
                    fh.seek(0)
                    fh.truncate()
                    fh.write(("pid=%d\n" % os.getpid()).encode())
                    fh.flush()
                except OSError:
                    pass
                return i, fh
            fh.close()
        return None

    @contextlib.contextmanager
    def hold(self, timeout: float = 30.0):
        deadline = time.monotonic() + timeout
        got = self._try_any()
        while got is None:
            if time.monotonic() >= deadline:
                raise SlotTimeout("no Unusual Whales slot free on this machine within %.0f s" % timeout)
            time.sleep(random.uniform(0.005, 0.025))               # jittered poll
            got = self._try_any()
        i, fh = got
        try:
            yield i
        finally:
            _unlock(fh)
            fh.close()


_machine: Optional[MachineSlots] = None
_broker = None


def machine_enabled() -> bool:
    return str(os.environ.get("UW_MACHINE_SLOTS", "")).strip().lower() in ("1", "true", "yes")


def machine() -> Optional[MachineSlots]:
    global _machine
    if not machine_enabled():
        return None
    if _machine is None:
        _machine = MachineSlots()
    return _machine


def broker():
    """The remote lease client, when UW_BROKER_URL is set (and a secret is configured)."""
    global _broker
    url = os.environ.get("UW_BROKER_URL", "").strip()
    if not url:
        return None
    if _broker is None or _broker.url != url.rstrip("/"):
        from uw_lease_broker import LeaseClient
        _broker = LeaseClient(url, os.environ.get("UW_BROKER_SECRET", ""))
    return _broker


@contextlib.contextmanager
def global_slot(timeout: float = 30.0):
    """Everything above the in-process semaphore: a machine slot and, if configured, a broker lease. A no-op when neither is configured."""
    with contextlib.ExitStack() as stack:
        m = machine()
        if m is not None:
            stack.enter_context(m.hold(timeout))
        b = broker()
        if b is not None:
            stack.enter_context(b.hold(timeout))
        yield


def status() -> dict:
    return {"machine_slots": machine_enabled(), "slot_dir": str(machine().dir) if machine() else None, "broker": bool(os.environ.get("UW_BROKER_URL", "").strip()), "provider_concurrency": PROVIDER_CONCURRENCY}
