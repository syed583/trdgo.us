"""
Historical daily bars for a symbol.

This used to open its own IB() connection (clientId 41) on every call, which
meant a second socket to TWS alongside the shared one and a fresh handshake
per request. It now delegates to the shared connection manager while keeping
the original signature and return shape, so every existing caller
(main.py, market_environment_service, the technical scorers) is unaffected.
"""

from __future__ import annotations

import ib_bootstrap  # noqa: F401  (must precede ib_insync)
from ib_insync import IB, Stock

from ibkr_client import IBKRUnavailable, ibkr
from live_market_service import cache


def get_historical_bars(symbol: str, duration: str = "1 Y") -> list[dict]:
    symbol = symbol.upper()
    key = f"histbars:{symbol}:{duration}"
    cached = cache.get(key, 120.0)
    if cached is not None:
        return cached

    async def job(ib: IB) -> list[dict]:
        contract = Stock(symbol, "SMART", "USD")
        await ib.qualifyContractsAsync(contract)
        if not contract.conId:
            return []

        bars = await ib.reqHistoricalDataAsync(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting="1 day",
            whatToShow="TRADES",
            useRTH=True,
            formatDate=1,
        )

        return [
            {
                "date": str(bar.date),
                "open": float(bar.open),
                "high": float(bar.high),
                "low": float(bar.low),
                "close": float(bar.close),
                "volume": float(bar.volume or 0.0),
            }
            for bar in bars
        ]

    try:
        result = ibkr.run(job, timeout=60)
    except IBKRUnavailable:
        result = []

    if not result:
        # TWS is down (or does not know the symbol). The fallback chain
        # (Twelve Data, then Alpha Vantage) serves end-of-day bars, which is
        # enough for the daily technicals and the market environment score.
        # Imported here rather than at module scope to avoid a cycle.
        from live_market_service import _fallback_bars

        fallback, _source = _fallback_bars(symbol, duration)
        if fallback:
            cache.put(key, fallback)
            return fallback

        # Callers already treat an empty list as "no market data".
        return []

    cache.put(key, result)
    return result
