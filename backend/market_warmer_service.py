"""
Keeps the TWS connection and the quote cache warm across the trading day.

The problem this solves
-----------------------
At 9:30 the app showed Friday's close. TWS had not gone away -- it was
servicing every subscription at once and its own connection handshake was
timing out:

    positions request timed out
    account updates for U7445328 request timed out
    2157: Sec-def data farm connection is broken: secdefeu

The live-price call gave up after 25 seconds and the screen fell back to the
provider snapshot, which is dated to the last completed session. So the app
was at its least accurate at the exact moment it mattered most.

Connecting during the crush is the thing to avoid. A connection established
beforehand stays established through it, because the expensive part is the
handshake rather than the steady state.

What it does
------------
Holds the connection open from before the pre-market bell and refreshes
quotes for the symbols on screen on a cadence that follows the session. Warm
cache entries also mean the first visitor of the morning does not pay for the
cold fetch.

Deliberately not aggressive: it refreshes a small symbol set, backs off hard
when TWS is unreachable, and never retries in a tight loop. A warmer that
hammers a struggling TWS makes the outage worse.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, time as dtime, timedelta
from typing import Optional

import live_market_service as market
from live_market_service import EASTERN

# Connect this long before the pre-market bell, so the handshake completes
# while TWS is quiet rather than during the rush.
PRE_MARKET_LEAD_MINUTES = 20

# Refresh cadence per session. Regular hours is tightest; outside trading
# hours the loop only keeps the connection alive.
REFRESH_OPEN = 20.0
REFRESH_EXTENDED = 45.0
REFRESH_CLOSED = 600.0

# How long to wait after TWS refuses a connection. Long on purpose: retrying
# every few seconds against a TWS that is already struggling is how a
# transient problem becomes a sustained one.
BACKOFF_AFTER_FAILURE = 120.0

# Ceiling for one warm cycle, so a stalled TWS cannot wedge the loop.
WARM_CYCLE_BUDGET = 90.0

# Symbols kept warm. Small by design -- every one costs a market-data line,
# and IBKR caps how many can be open at once.
MAX_WARM_SYMBOLS = 12

_state: dict = {
    "running": False,
    "last_warm": None,
    "last_error": None,
    "warmed": 0,
    "connection_ok": None,
    "cycles": 0,
}


def status() -> dict:
    """What the warmer has been doing, for the settings screen."""
    return {**_state, "symbols": MAX_WARM_SYMBOLS,
            "lead_minutes": PRE_MARKET_LEAD_MINUTES}


def _symbols() -> list[str]:
    """Watchlist first, topped up from the default basket."""
    try:
        import workspace_service as workspace
        from api_routes import DEFAULT_STRIP

        saved = [r["symbol"] for r in
                 workspace.list_watchlist(with_quotes=False)["rows"]]
        merged = saved + [s for s in DEFAULT_STRIP if s not in saved]
        return merged[:MAX_WARM_SYMBOLS]
    except Exception:  # noqa: BLE001
        return []


def _ensure_connection() -> bool:
    """
    Make sure TWS is connected, without hammering it.

    ``force_retry`` clears the circuit breaker so a scheduled warm-up is not
    blocked by an earlier failure; everything else is left to the client.
    """
    from ibkr_client import ibkr

    try:
        ibkr.force_retry()
        probe = ibkr.probe()
        ok = bool(probe.get("connected"))
        _state["connection_ok"] = ok
        _state["last_error"] = None if ok else probe.get("last_error")
        return ok
    except Exception as exc:  # noqa: BLE001
        _state["connection_ok"] = False
        _state["last_error"] = str(exc)[:200]
        return False


def warm_now(symbols: Optional[list[str]] = None) -> dict:
    """
    Refresh quotes for the warm set, populating the cache.

    Each quote is fetched through the normal path, so whatever the app would
    serve is exactly what gets warmed -- including the live TWS price that
    the providers cannot supply.
    """
    targets = symbols or _symbols()
    if not targets:
        return {"status": "NO_SYMBOLS", "warmed": 0}

    if not _ensure_connection():
        return {"status": "IBKR_UNAVAILABLE", "warmed": 0,
                "detail": _state.get("last_error")}

    # Warmed concurrently. Serially, twelve symbols against a slow TWS is
    # five minutes -- longer than the interval between cycles, so the warmer
    # would never finish one before the next was due.
    from concurrent.futures import ThreadPoolExecutor, as_completed

    warmed, failed = 0, []
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="warm") as pool:
        jobs = {pool.submit(_warm_one, s): s for s in targets}
        for job in as_completed(jobs, timeout=WARM_CYCLE_BUDGET):
            symbol = jobs[job]
            try:
                if job.result():
                    warmed += 1
                else:
                    failed.append(symbol)
            except Exception:  # noqa: BLE001
                failed.append(symbol)

    _state["last_warm"] = datetime.now(EASTERN).isoformat()
    _state["warmed"] = warmed
    return {"status": "OK", "warmed": warmed, "failed": failed,
            "symbols": len(targets)}


def _warm_one(symbol: str) -> bool:
    """
    Fetch one symbol through the normal path, with the background budget.

    The long budget belongs here rather than in the request path: this runs
    with nobody waiting, and its whole purpose is to absorb the slow fetch so
    a visitor never has to.
    """
    try:
        quote = market.get_quote(
            symbol, live_timeout=market.LIVE_PRICE_BUDGET_BACKGROUND)
        return bool(quote and quote.get("price") is not None)
    except Exception:  # noqa: BLE001
        return False


def _seconds_until(target: dtime) -> float:
    """Seconds from now until the next occurrence of an Eastern wall time."""
    now = datetime.now(EASTERN)
    when = now.replace(hour=target.hour, minute=target.minute,
                       second=0, microsecond=0)
    if when <= now:
        when += timedelta(days=1)
    return (when - now).total_seconds()


def _next_delay() -> float:
    """
    How long to sleep before the next cycle.

    Follows the session rather than running at one fixed rate: refreshing
    every twenty seconds overnight would be pointless, and every ten minutes
    during the open would be useless.
    """
    session = market.market_clock().get("session")
    if session == "OPEN":
        return REFRESH_OPEN
    if session in market.EXTENDED_SESSIONS:
        return REFRESH_EXTENDED

    # Closed: sleep until shortly before the pre-market bell, unless the
    # routine keep-alive comes round sooner.
    return min(REFRESH_CLOSED, max(30.0, _seconds_until_lead()))


def _seconds_until_lead() -> float:
    """
    Seconds until the warm-up window opens, a little before the pre-market
    bell. Computed on datetimes rather than by juggling hours and minutes,
    which is how a lead that crosses midnight goes wrong unnoticed.
    """
    now = datetime.now(EASTERN)
    bell = now.replace(hour=market.PRE_OPEN.hour,
                       minute=market.PRE_OPEN.minute,
                       second=0, microsecond=0)
    lead = bell - timedelta(minutes=PRE_MARKET_LEAD_MINUTES)
    if lead <= now:
        lead += timedelta(days=1)
    return (lead - now).total_seconds()


def _loop() -> None:
    while True:
        try:
            _state["cycles"] += 1
            session = market.market_clock().get("session")

            if session == "OPEN" or session in market.EXTENDED_SESSIONS:
                result = warm_now()
                if result.get("status") == "IBKR_UNAVAILABLE":
                    # Back off rather than retry immediately: a struggling TWS
                    # is made worse by a warmer that keeps knocking.
                    time.sleep(BACKOFF_AFTER_FAILURE)
                    continue
            else:
                # Outside trading hours there is nothing to refresh, but the
                # connection is kept alive so the pre-market warm-up does not
                # have to establish one from cold.
                _ensure_connection()

            time.sleep(_next_delay())
        except Exception as exc:  # noqa: BLE001
            _state["last_error"] = str(exc)[:200]
            time.sleep(BACKOFF_AFTER_FAILURE)


def start() -> None:
    """Start the warmer once. Safe to call repeatedly."""
    if _state["running"]:
        return
    _state["running"] = True
    threading.Thread(target=_loop, daemon=True, name="market-warmer").start()
