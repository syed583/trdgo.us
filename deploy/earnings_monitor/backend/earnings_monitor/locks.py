"""Cross-process scan lock: at most one scan job runs at a time, across threads and processes (several workers, a restart overlapping the old process). An OS file lock is released by the kernel
if the holder dies, so a crash can never leave the monitor wedged."""
from __future__ import annotations

import os
import sys
from pathlib import Path


class ScanLock:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = None

    def acquire(self) -> bool:
        """Non-blocking. False when another scan holds the lock."""
        fh = open(self.path, "a+b")
        try:
            if sys.platform == "win32":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        try:
            fh.seek(0)
            fh.truncate()
            fh.write(("pid=%d\n" % os.getpid()).encode())
            fh.flush()
        except OSError:
            pass
        return True

    def release(self):
        fh, self._fh = self._fh, None
        if fh is None:
            return
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
        fh.close()

    def __enter__(self):
        if not self.acquire():
            raise BlockingIOError("another scan holds the lock")
        return self

    def __exit__(self, *a):
        self.release()
