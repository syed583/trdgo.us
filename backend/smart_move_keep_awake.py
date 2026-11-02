"""Per-process "do not idle-sleep while I run" request for long Smart Move collections (Windows SetThreadExecutionState). It changes NO system or power setting: Windows drops the request
when the process ends or when `release()` runs. It cannot stop a closed lid, a manual Sleep, or a forced shutdown (Windows says so in its documentation); for unattended collection use an always-on host
(see docs/smart_move/ALWAYS_ON_SCHEDULING.md). Non-Windows: a no-op that reports itself as inactive."""
from __future__ import annotations

import sys

ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001


class KeepAwake:
    def __init__(self, api=None, platform: str = sys.platform):
        self.active = False
        self._api = api
        if self._api is None and platform.startswith("win"):
            import ctypes
            self._api = ctypes.windll.kernel32.SetThreadExecutionState
        self.supported = self._api is not None

    def request(self) -> bool:
        if not self.supported:
            return False
        self.active = bool(self._api(ES_CONTINUOUS | ES_SYSTEM_REQUIRED))      # non-zero return = the request was accepted
        return self.active

    def release(self):
        if self.supported and self.active:
            self._api(ES_CONTINUOUS)                                            # clears our request, nothing else
        self.active = False

    def __enter__(self):
        self.request()
        return self

    def __exit__(self, *exc):
        self.release()
        return False
