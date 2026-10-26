"""Who is asking, and in what order they are served: Unusual Whales request priorities.

Priority classes (lower number = served first):

    1  LIVE       the live intraday BUY/SELL engine
    2  ESSENTIAL  options flow / market data feeds and the six AI boards
    3  DASHBOARD  everything else a page asks for (the default)
    4  WARMER     background / historical cache warming

Every request carries a (priority, module) label taken from the CALLER's context:

    with uw_priority.use(uw_priority.LIVE, "intraday_engine"): ...
    @uw_priority.tagged(uw_priority.ESSENTIAL, "ai_boards")     # a whole function
    uw_priority.set_thread_default(4, "warmer")                 # a background thread

The label travels into worker threads (ThreadPoolExecutor.submit is patched once to copy the
caller's context), so a screen that fans out keeps its priority.

PriorityGate replaces a plain semaphore. It is BOUNDED (queue limits), TIMED (every class has a
maximum wait, after which the caller gets a clean "busy" answer instead of hanging) and FAIR
(a waiting request ages upward, so nothing essential starves), and each class has a ceiling on
how many requests it may hold in flight so the low classes can never fill the wire.
"""
from __future__ import annotations

import contextvars
import functools
import threading
import time
from contextlib import contextmanager
from typing import Callable, Optional

LIVE, ESSENTIAL, DASHBOARD, WARMER = 1, 2, 3, 4
NAMES = {LIVE: "live-intraday", ESSENTIAL: "essential", DASHBOARD: "dashboard", WARMER: "warmer"}

_ctx: contextvars.ContextVar = contextvars.ContextVar("uw_request_ctx", default=None)


class Tracker:
    """Counts what happened to the requests of one logical job (used by the warmer so it can
    discard a result that was built while some of its requests were refused)."""

    def __init__(self) -> None:
        self.denied = 0
        self.busy = 0
        self.errors = 0


def is_explicit() -> bool:
    """Was this call chain labelled by its caller (vs the anonymous default)?"""
    return _ctx.get() is not None


def current() -> tuple[int, str, Optional[Tracker]]:
    c = _ctx.get()
    return c if c else (DASHBOARD, "dashboard", None)


@contextmanager
def use(priority: int, module: str, tracker: Optional[Tracker] = None):
    token = _ctx.set((int(priority), str(module), tracker))
    try:
        yield tracker
    finally:
        _ctx.reset(token)


def set_thread_default(priority: int, module: str) -> None:
    """Label the CURRENT thread for its whole life (background loops)."""
    _ctx.set((int(priority), str(module), None))


def tagged(priority: int, module: str) -> Callable:
    """Decorator: run the function under a priority label unless a stricter (lower-number) one
    is already active for this call chain."""
    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*a, **k):
            cur = _ctx.get()
            if cur and cur[0] < priority:
                return fn(*a, **k)
            with use(priority, module, cur[2] if cur else None):
                return fn(*a, **k)
        return wrapper
    return deco


# --- carry the label into worker threads ----------------------------------------------
_patched = False


def patch_executors() -> None:
    """ThreadPoolExecutor.submit runs the callable inside a copy of the caller's context."""
    global _patched
    if _patched:
        return
    import concurrent.futures as cf
    orig = cf.ThreadPoolExecutor.submit

    def submit(self, fn, /, *args, **kwargs):
        ctx = contextvars.copy_context()
        return orig(self, ctx.run, fn, *args, **kwargs)

    cf.ThreadPoolExecutor.submit = submit  # type: ignore[assignment]
    _patched = True


patch_executors()


# --- the gate ---------------------------------------------------------------------------
class Busy(Exception):
    """The gate refused: queue full or the class's maximum wait elapsed."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


DEFAULTS = {
    "slots": 8,
    # most requests of each class that may be on the wire at once. P3+P4 together can never fill
    # the gate: at least two slots are always left for P1/P2.
    "class_cap": {LIVE: 8, ESSENTIAL: 7, DASHBOARD: 4, WARMER: 2},
    "queue_limit": {LIVE: 400, ESSENTIAL: 400, DASHBOARD: 200, WARMER: 40},
    "max_wait_s": {LIVE: 60.0, ESSENTIAL: 45.0, DASHBOARD: 30.0, WARMER: 10.0},
    # P3+P4 together may hold at most this many slots (None: all but two, never fewer than one), so the
    # live engine and essential data always have room on the wire.
    "low_cap": None,
    "brake_hold_s": 60.0,                              # the adaptive brake lasts this long per step
    "aging_s": 10.0,                                   # a waiter moves up one level per 10 s ...
    "floor": {LIVE: 1, ESSENTIAL: 1, DASHBOARD: 2, WARMER: 3},   # ... but never past its floor
}


class PriorityGate:
    def __init__(self, cfg: Optional[dict] = None, clock: Callable[[], float] = time.monotonic) -> None:
        c = dict(DEFAULTS)
        c.update(cfg or {})
        self.cfg = c
        self._clock = clock
        self._cond = threading.Condition()
        self._waiting: list[list] = []          # [prio, seq, enqueued_at]
        self._seq = 0
        self.inflight = 0
        self.by_class = {p: 0 for p in NAMES}
        self.stats = {p: {"granted": 0, "busy": 0, "wait_total_s": 0.0, "max_wait_s": 0.0} for p in NAMES}
        self._brake = 0                          # slots taken away after the provider said "too many at once"
        self._brake_until = 0.0
        self.peak_inflight = 0

    # -- the adaptive brake: another consumer of the token (another machine) is using some of the slots --
    def brake(self, steps: int = 1) -> int:
        """The provider answered `concurrent_limit_exceeded`: take ``steps`` slots away for a while
        (never below one). Each ``brake_hold_s`` without another refusal gives one back."""
        with self._cond:
            self._brake = min(max(0, self.cfg["slots"] - 1), self._brake + max(1, steps))
            self._brake_until = self._clock() + self.cfg["brake_hold_s"]
            return self._brake

    def effective_slots(self, now: Optional[float] = None) -> int:
        now = self._clock() if now is None else now
        if self._brake and now >= self._brake_until:
            self._brake -= 1
            self._brake_until = now + self.cfg["brake_hold_s"]
        return max(1, self.cfg["slots"] - self._brake)

    def _low_cap(self) -> int:
        c = self.cfg["low_cap"]
        return int(c) if c is not None else max(1, self.cfg["slots"] - 2)

    # -- internals (call with the lock held) --
    def _eff(self, w: list, now: float) -> int:
        waited = max(0.0, now - w[2])
        return max(self.cfg["floor"][w[0]], w[0] - int(waited / self.cfg["aging_s"]))

    def _runnable(self, w: list, now: float) -> bool:
        if self.inflight >= self.effective_slots(now) or self.by_class[w[0]] >= self.cfg["class_cap"][w[0]]:
            return False
        if w[0] in (DASHBOARD, WARMER) and self.by_class[DASHBOARD] + self.by_class[WARMER] >= self._low_cap():
            return False
        mine = (self._eff(w, now), w[1])
        for o in self._waiting:
            if o is w or self.by_class[o[0]] >= self.cfg["class_cap"][o[0]]:
                continue                        # a waiter blocked by its own class cap does not block others
            if (self._eff(o, now), o[1]) < mine:
                return False
        return True

    def queue_depth(self) -> dict:
        with self._cond:
            d = {p: 0 for p in NAMES}
            for w in self._waiting:
                d[w[0]] += 1
            return d

    def acquire(self, prio: int) -> None:
        prio = prio if prio in NAMES else DASHBOARD
        with self._cond:
            if sum(1 for w in self._waiting if w[0] == prio) >= self.cfg["queue_limit"][prio]:
                self.stats[prio]["busy"] += 1
                raise Busy(f"{NAMES[prio]} queue is full")
            self._seq += 1
            t0 = self._clock()
            w = [prio, self._seq, t0]
            self._waiting.append(w)
            deadline = t0 + self.cfg["max_wait_s"][prio]
            try:
                while True:
                    now = self._clock()
                    if self._runnable(w, now):
                        self.inflight += 1
                        self.peak_inflight = max(self.peak_inflight, self.inflight)
                        self.by_class[prio] += 1
                        st = self.stats[prio]
                        st["granted"] += 1
                        st["wait_total_s"] += now - t0
                        st["max_wait_s"] = max(st["max_wait_s"], now - t0)
                        return
                    if now >= deadline:
                        self.stats[prio]["busy"] += 1
                        raise Busy(f"waited {self.cfg['max_wait_s'][prio]:.0f}s for a free slot "
                                   f"({NAMES[prio]})")
                    self._cond.wait(min(0.25, max(0.01, deadline - now)))
            finally:
                self._waiting.remove(w)
                self._cond.notify_all()

    def release(self, prio: int) -> None:
        prio = prio if prio in NAMES else DASHBOARD
        with self._cond:
            self.inflight = max(0, self.inflight - 1)
            self.by_class[prio] = max(0, self.by_class[prio] - 1)
            self._cond.notify_all()

    @contextmanager
    def slot(self, prio: int):
        self.acquire(prio)
        try:
            yield
        finally:
            self.release(prio)
