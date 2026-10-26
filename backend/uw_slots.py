"""Machine-wide cap on simultaneous Unusual Whales requests.

The provider allows 3 simultaneous in-flight REST requests PER TOKEN (more -> 429 concurrent_limit_exceeded).
The in-process priority gate already bounds one process; this module bounds EVERY process on the machine
that shares the token (the service, the CLI, scripts, a dev server) with OS file locks:

    N lock files, one per slot. A request holds one lock for as long as it is on the wire.
    The OS releases a lock the moment its holder exits or crashes, so a slot can never leak.

Honest limits of this design -- the part that cannot be enforced from one machine:
  * Another MACHINE using the same token is invisible here. The only shared store would be the production
    database, which a development machine must never touch, so cross-machine protection is: a smaller
    default on non-production machines (1 slot), plus the gate's adaptive brake that lowers its own slots
    whenever the provider answers `concurrent_limit_exceeded` (see uw_priority.PriorityGate.brake).
  * If the lock directory is not writable the cap degrades to the in-process gate (and says so once).
"""
from __future__ import annotations

import logging
import os
import random
import sys
import tempfile
import threading
import time
from typing import Optional

_LOG = logging.getLogger("uw_slots")

if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass


class Handle:
    __slots__ = ("fd", "slot")

    def __init__(self, fd: Optional[int], slot: int) -> None:
        self.fd = fd
        self.slot = slot


class MachineSlots:
    """``n`` slots shared by every process that uses the same directory."""

    def __init__(self, n: int, directory: Optional[str] = None) -> None:
        self.n = max(1, int(n))
        self.dir = directory or os.environ.get("UW_SLOT_DIR") or os.path.join(tempfile.gettempdir(), "trdgo-uw-slots")
        self._ok = True
        self._warned = False
        self._lock = threading.Lock()
        self.stats = {"acquired": 0, "waited_s": 0.0, "timeouts": 0, "failopen": 0}
        try:
            os.makedirs(self.dir, exist_ok=True)
        except OSError as exc:
            self._fail(exc)

    def _fail(self, exc: Exception) -> None:
        self._ok = False
        if not self._warned:
            self._warned = True
            _LOG.warning("uw_slots: lock directory %s unusable (%s); only the in-process cap applies", self.dir, exc)

    def acquire(self, timeout: float) -> Optional[Handle]:
        """A slot, or None when none came free within ``timeout`` seconds. Fails open (a no-op handle) if the
        lock files cannot be used, so a broken disk never takes the app off the air."""
        if not self._ok:
            self.stats["failopen"] += 1
            return Handle(None, -1)
        t0 = time.monotonic()
        deadline = t0 + max(0.0, timeout)
        while True:
            for slot in random.sample(range(self.n), self.n):          # spread over the files
                try:
                    fd = os.open(os.path.join(self.dir, f"slot{slot}.lock"), os.O_RDWR | os.O_CREAT, 0o666)
                except OSError as exc:
                    self._fail(exc)
                    self.stats["failopen"] += 1
                    return Handle(None, -1)
                if _try_lock(fd):
                    with self._lock:
                        self.stats["acquired"] += 1
                        self.stats["waited_s"] += time.monotonic() - t0
                    return Handle(fd, slot)
                os.close(fd)
            if time.monotonic() >= deadline:
                with self._lock:
                    self.stats["timeouts"] += 1
                return None
            time.sleep(random.uniform(0.004, 0.02))

    def release(self, handle: Optional[Handle]) -> None:
        if handle is None or handle.fd is None:
            return
        _unlock(handle.fd)
        try:
            os.close(handle.fd)
        except OSError:
            pass
        handle.fd = None
