"""
Shared IBKR connection manager.

The rest of the codebase historically opened a brand new IB() connection on
every request with a hardcoded clientId. That collides under concurrency and
costs ~1s of handshake per call. This module owns exactly one IB connection,
living on a dedicated background thread with its own asyncio event loop, and
lets synchronous FastAPI endpoints submit coroutines onto it.

Usage:

    from ibkr_client import ibkr

    async def job(ib):
        c = Stock("NVDA", "SMART", "USD")
        await ib.qualifyContractsAsync(c)
        return c.conId

    con_id = ibkr.run(job)
"""

import asyncio
import os
import threading
import time
from typing import Any, Awaitable, Callable, Optional

import ib_bootstrap  # noqa: F401  (must precede ib_insync)
from ib_insync import IB


IBKR_HOST = os.getenv("IBKR_HOST", "127.0.0.1")
IBKR_PORT = int(os.getenv("IBKR_PORT", "7496"))
IBKR_CLIENT_ID = int(os.getenv("IBKR_CLIENT_ID", "17"))

# 1=live, 2=frozen, 3=delayed, 4=delayed-frozen.
# Frozen returns live prices during RTH and the last known snapshot outside of
# it, which is what every panel in the UI wants.
IBKR_MARKET_DATA_TYPE = int(os.getenv("IBKR_MARKET_DATA_TYPE", "2"))

DEFAULT_TIMEOUT = float(os.getenv("IBKR_TIMEOUT", "45"))

# How long to stop attempting after a failed connect.
#
# A refused connect is not free: on Windows it costs ~2s per attempt. With the
# old 5s window, a closed TWS meant every page load paid that 2s again, and a
# ten-symbol strip paid it repeatedly. Retrying a shut TWS once a minute
# instead loses nothing -- probe() and the reconnect button still force an
# immediate attempt.
IBKR_RETRY_SECONDS = float(os.getenv("IBKR_RETRY_SECONDS", "60"))

# A connected socket that answers nothing is the worst case for the app: the
# breaker below only ever opened on a failed *connect*, so when TWS was up but
# its data farm was broken, every request paid the full timeout before falling
# back to an HTTP provider that would have answered in a second. Three
# timeouts in a row is TWS telling us it cannot serve; believe it, and stop
# paying twenty seconds a call to be told again.
IBKR_TIMEOUT_STRIKES = int(os.getenv("IBKR_TIMEOUT_STRIKES", "3"))


# 1100 = TWS lost its uplink to IBKR entirely. Nothing will be served until it
# comes back, so this is the only notice that blocks dispatch.
BLOCKING_DOWN_CODES = {1100}

# 1101/1102 = that uplink was restored.
BLOCKING_UP_CODES = {1101, 1102}

# 2103/2105/2157 name a single data farm (often a regional one such as
# "secdefeu"). US requests frequently keep working through these, so they are
# recorded and surfaced but must not veto a request on their own - letting the
# call fail on its own merits is more accurate than a blanket refusal.
DEGRADED_DOWN_CODES = {2103, 2105, 2157}
DEGRADED_UP_CODES = {2104, 2106, 2158}

# Kept for callers that want the whole set.
FARM_DOWN_CODES = BLOCKING_DOWN_CODES | DEGRADED_DOWN_CODES
FARM_UP_CODES = BLOCKING_UP_CODES | DEGRADED_UP_CODES


class OrderPlacementBlocked(RuntimeError):
    """Raised if anything ever tries to place, modify or cancel an order."""


# Every ib_insync entry point that can create or alter an order. The connection
# is already opened readonly=True and TWS enforces its own Read-Only API
# setting, but both of those are configuration: a wrong .env, a future edit, or
# a TWS setting someone unticks would silently re-enable trading. These are
# neutered on the live IB object so an order cannot leave this process even if
# the other two layers are misconfigured.
_ORDER_METHODS = (
    "placeOrder", "placeOrderAsync",
    "cancelOrder", "cancelOrderAsync",
    "reqGlobalCancel", "reqGlobalCancelAsync",
    "exerciseOptions", "exerciseOptionsAsync",
    "oneCancelsAll", "bracketOrder",
    "whatIfOrder", "whatIfOrderAsync",
)


def _install_order_block(ib: IB) -> None:
    """Replace every order-placing method with one that refuses."""
    for name in _ORDER_METHODS:
        if not hasattr(ib, name):
            continue

        def refuse(*args, _name=name, **kwargs):
            raise OrderPlacementBlocked(
                f"IB.{_name} is blocked: US-Stock Reader is a read-only "
                f"research tool and never places, modifies or cancels orders."
            )

        setattr(ib, name, refuse)


class IBKRUnavailable(RuntimeError):
    """Raised when TWS/IB Gateway cannot be reached or is not serving data."""


class _IBKRWorker:
    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ib: Optional[IB] = None
        self._ready = threading.Event()
        self._connect_lock: Optional[asyncio.Lock] = None
        self._thread = threading.Thread(
            target=self._run_forever,
            name="ibkr-worker",
            daemon=True,
        )
        self._started = False
        self._start_lock = threading.Lock()
        self._last_failure: Optional[str] = None
        self._last_failure_at: float = 0.0
        self._timeout_strikes = 0
        self._farm_ok = True
        self._farm_note: Optional[str] = None
        self._degraded_note: Optional[str] = None

    # -- lifecycle ---------------------------------------------------------

    def _run_forever(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ib = IB()
        self._ib.errorEvent += self._on_error
        self._connect_lock = asyncio.Lock()
        self._ready.set()
        loop.run_forever()

    def _on_error(self, reqId, errorCode, errorString, contract) -> None:
        """
        Track TWS connectivity notices.

        The socket to TWS can stay open while TWS itself loses its link to
        IBKR. isConnected() keeps returning True and every request then hangs
        until it times out, so these notices are the only way to tell the
        difference between "working" and "silently broken".
        """
        if errorCode in BLOCKING_DOWN_CODES:
            self._farm_ok = False
            self._farm_note = f"{errorCode}: {errorString}"
        elif errorCode in BLOCKING_UP_CODES:
            self._farm_ok = True
            self._farm_note = None
        elif errorCode in DEGRADED_DOWN_CODES:
            self._degraded_note = f"{errorCode}: {errorString}"
        elif errorCode in DEGRADED_UP_CODES:
            self._degraded_note = None

    def _ensure_thread(self) -> None:
        if self._started:
            return
        with self._start_lock:
            if self._started:
                return
            self._thread.start()
            self._ready.wait(timeout=10)
            self._started = True

    async def _ensure_connected(self) -> IB:
        assert self._ib is not None and self._connect_lock is not None

        if self._ib.isConnected():
            return self._ib

        async with self._connect_lock:
            if self._ib.isConnected():
                return self._ib

            # Back off after a failure so a burst of requests doesn't hammer
            # a TWS that isn't running. force_retry() clears this.
            if self._offline_for() is not None:
                raise IBKRUnavailable(self._last_failure)

            try:
                await self._ib.connectAsync(
                    IBKR_HOST,
                    IBKR_PORT,
                    clientId=IBKR_CLIENT_ID,
                    readonly=True,
                    timeout=15,
                )
                # Before anything else can use the connection.
                _install_order_block(self._ib)
                self._ib.reqMarketDataType(IBKR_MARKET_DATA_TYPE)
                self._last_failure = None
                self._farm_ok = True
                self._farm_note = None
                self._degraded_note = None
            except Exception as exc:  # noqa: BLE001 - surfaced to the caller
                self._last_failure = (
                    f"Cannot reach IBKR at {IBKR_HOST}:{IBKR_PORT} ({exc.__class__.__name__}: {exc})"
                )
                self._last_failure_at = time.time()
                raise IBKRUnavailable(self._last_failure) from exc

        return self._ib

    # -- public API --------------------------------------------------------

    def _offline_for(self) -> Optional[float]:
        """
        Seconds since the last failed connect while still inside the retry
        window, or None if a connection attempt is worth making.
        """
        if not self._last_failure:
            return None
        age = time.time() - self._last_failure_at
        return age if age < IBKR_RETRY_SECONDS else None

    def is_offline(self) -> bool:
        """True when a connect attempt would be skipped as pointless."""
        return self._offline_for() is not None

    def force_retry(self) -> None:
        """Clear the backoff so the next call attempts a real connection."""
        self._last_failure = None
        self._timeout_strikes = 0

    def run(
        self,
        job: Callable[[IB], Awaitable[Any]],
        timeout: float = DEFAULT_TIMEOUT,
    ) -> Any:
        """Execute ``job(ib)`` on the IBKR event loop and return its result."""
        # Refuse before touching the worker thread: inside the retry window we
        # already know the answer, so there is no reason to pay the dispatch.
        if self.is_offline():
            raise IBKRUnavailable(self._last_failure)

        self._ensure_thread()

        if self._loop is None:
            raise IBKRUnavailable("IBKR worker thread failed to start")

        async def wrapper() -> Any:
            ib = await self._ensure_connected()
            blocked = self.serving_error()
            if blocked:
                raise IBKRUnavailable(blocked)
            return await job(ib)

        future = asyncio.run_coroutine_threadsafe(wrapper(), self._loop)
        try:
            result = future.result(timeout=timeout)
            self._timeout_strikes = 0
            return result
        except (TimeoutError, asyncio.TimeoutError) as exc:
            future.cancel()
            self._note_timeout()
            raise IBKRUnavailable(
                f"IBKR request exceeded {timeout:.0f}s"
            ) from exc
        except ConnectionRefusedError as exc:
            raise IBKRUnavailable(
                f"IBKR refused the connection on {IBKR_HOST}:{IBKR_PORT}"
            ) from exc

    def _note_timeout(self) -> None:
        """
        Count a request that never came back, and open the breaker on a run.

        One timeout is a slow request. Several in a row is a connection that
        is up but not serving, and every further call is twenty seconds spent
        to learn nothing -- which is twenty seconds the fallback provider
        could have spent answering.
        """
        self._timeout_strikes += 1
        if self._timeout_strikes < IBKR_TIMEOUT_STRIKES:
            return
        self._last_failure = (
            f"TWS accepted the connection but {self._timeout_strikes} requests "
            f"in a row timed out; using fallback providers."
        )
        self._last_failure_at = time.time()

    def serving_error(self) -> Optional[str]:
        """
        Why a request would be pointless right now, or None if it is fine.

        Kept separate from status() so the dispatch guard and the health badge
        cannot drift apart, and so it can be exercised without a socket.
        """
        if not self._farm_ok:
            return (
                f"TWS is connected but not serving data ({self._farm_note}). "
                "Check the connection indicator in TWS."
            )
        return None

    def status(self) -> dict:
        socket_up = bool(self._ib is not None and self._ib.isConnected())
        connected = socket_up and self._farm_ok
        return {
            "connected": connected,
            "socket_connected": socket_up,
            "data_farm_ok": self._farm_ok,
            "host": IBKR_HOST,
            "port": IBKR_PORT,
            "client_id": IBKR_CLIENT_ID,
            "market_data_type": IBKR_MARKET_DATA_TYPE,
            "degraded_note": self._degraded_note,
            "last_error": (
                self._farm_note if socket_up and not self._farm_ok
                else None if connected else self._last_failure
            ),
        }

    def probe(self) -> dict:
        """Force a connection attempt and report the outcome."""
        self.force_retry()
        try:

            async def job(ib: IB) -> bool:
                return ib.isConnected()

            self.run(job, timeout=20)
        except IBKRUnavailable as exc:
            return {**self.status(), "connected": False, "last_error": str(exc)}
        return self.status()


ibkr = _IBKRWorker()
