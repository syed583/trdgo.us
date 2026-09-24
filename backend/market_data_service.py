"""
Historical daily bars for a symbol.

This has been through two sources. It first opened its own IB() connection
(clientId 41) on every call -- a second socket to TWS alongside the shared
one, and a fresh handshake per request. It then delegated to the shared
connection manager, with the free providers behind it. Both are gone; the
signature and return shape have not changed through any of it, so every
existing caller (main.py, market_environment_service, the technical scorers)
is unaffected.

An empty list still means "no market data", never zero.
"""

from __future__ import annotations

from live_market_service import cache


def get_historical_bars(symbol: str, duration: str = "1 Y") -> list[dict]:
    symbol = symbol.upper()
    key = f"histbars:{symbol}:{duration}"
    cached = cache.get(key, 120.0)
    if cached is not None:
        return cached

    # Imported here rather than at module scope to avoid a cycle.
    from live_market_service import _fallback_bars

    bars, _source = _fallback_bars(symbol, duration)
    if not bars:
        return []

    cache.put(key, bars)
    return bars
