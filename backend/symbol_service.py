"""
Symbol lookup and validation, backed by IBKR's own contract database.

Used by the search box: typing gives suggestions from reqMatchingSymbols, and
committing a symbol validates it against a real qualified contract so the UI
can say SYMBOL NOT FOUND with certainty rather than after a failed page load.
"""

from __future__ import annotations

import difflib
from typing import Optional

import ib_bootstrap  # noqa: F401  (must precede ib_insync)
from ib_insync import IB, Stock

from ibkr_client import IBKRUnavailable, ibkr
from live_market_service import cache

SEARCH_TTL = 900.0
VALIDATE_TTL = 3600.0

# SEC's company_tickers.json is the fallback universe when TWS is down: every
# SEC-registered US issuer, no API key and no request quota. It carries ticker,
# company name and CIK -- but no exchange and no IBKR conId, so those come back
# None rather than guessed.
SEC_SOURCE = "SEC"


def _sec_universe() -> list[dict]:
    """
    [{symbol, name, cik}], cached by sec_service's own HTTP cache.

    Returns [] if SEC cannot be reached, so callers fall back to reporting the
    original IBKR failure rather than an empty-but-successful search.
    """
    import sec_service

    key = "sec:universe"
    cached = cache.get(key, 86400.0)
    if cached is not None:
        return cached

    try:
        data = sec_service._get_json(sec_service.SEC_TICKER_URL,
                                     use_sec_host=False)
    except Exception:  # noqa: BLE001 - any transport failure means no fallback
        return []

    rows = []
    for item in (data or {}).values():
        ticker = str(item.get("ticker") or "").upper()
        if ticker:
            rows.append({
                "symbol": ticker,
                "name": item.get("title") or ticker,
                "cik": str(item.get("cik_str") or "").zfill(10),
            })

    if rows:
        cache.put(key, rows)
    return rows


def _sec_search(query: str, limit: int) -> list[dict]:
    """Exact ticker first, then prefix, then name substring."""
    universe = _sec_universe()
    if not universe:
        return []

    upper = query.upper()
    lower = query.lower()

    exact, prefix, by_name = [], [], []
    for row in universe:
        if row["symbol"] == upper:
            exact.append(row)
        elif row["symbol"].startswith(upper):
            prefix.append(row)
        elif lower in row["name"].lower():
            by_name.append(row)

    prefix.sort(key=lambda r: r["symbol"])
    by_name.sort(key=lambda r: r["name"])

    out = []
    for row in exact + prefix + by_name:
        out.append({
            "symbol": row["symbol"],
            "name": row["name"],
            # SEC publishes neither of these; inventing them would put a
            # wrong exchange on a card.
            "exchange": None,
            "currency": "USD",
            "con_id": None,
            "cik": row["cik"],
        })
        if len(out) >= limit:
            break
    return out


def _fuzzy_search(query: str, limit: int) -> list[dict]:
    """
    Nearest tickers by edit distance, for a mistyped symbol.

    "MSIFT" is one transposition from MSFT but matches nothing by prefix or
    substring, so the box simply went blank. Only consulted when the exact
    search finds nothing, and results are marked `approximate` so the UI can
    present them as suggestions rather than as matches.
    """
    universe = _sec_universe()
    if not universe or len(query) < 3:
        return []

    upper = query.upper()
    by_symbol = {row["symbol"]: row for row in universe}
    close = difflib.get_close_matches(upper, by_symbol.keys(), n=limit, cutoff=0.7)

    out = []
    for symbol in close:
        row = by_symbol[symbol]
        out.append({
            "symbol": row["symbol"],
            "name": row["name"],
            "exchange": None,
            "currency": "USD",
            "con_id": None,
            "cik": row["cik"],
            "approximate": True,
        })
    return out


def search(query: str, limit: int = 12) -> dict:
    query = (query or "").strip()
    if len(query) < 1:
        return {"query": query, "matches": [], "status": "OK", "source": "IBKR"}

    key = f"symsearch:{query.upper()}:{limit}"
    cached = cache.get(key, SEARCH_TTL)
    if cached:
        return cached

    async def job(ib: IB):
        return await ib.reqMatchingSymbolsAsync(query)

    try:
        found = ibkr.run(job, timeout=30)
    except IBKRUnavailable as exc:
        matches = _sec_search(query, limit)
        if matches:
            result = {"query": query, "matches": matches,
                      "count": len(matches), "status": "OK",
                      "source": SEC_SOURCE}
            cache.put(key, result)
            return result
        return {"query": query, "matches": [], "status": "PROVIDER_OFFLINE",
                "detail": str(exc), "source": "IBKR"}

    # IBKR's reqMatchingSymbols often returns an empty description, which made
    # every row render as "MSFT - MSFT". SEC's registry has the real names and
    # is already cached, so use it to fill the gaps.
    sec_names = {row["symbol"]: row["name"] for row in _sec_universe()}

    matches = []
    for item in found or []:
        contract = item.contract
        # US stocks only: this application scores equities, not FX or futures.
        if contract.secType != "STK" or contract.currency != "USD":
            continue
        matches.append({
            "symbol": contract.symbol,
            "name": (getattr(item, "description", None)
                     or sec_names.get(contract.symbol.upper())
                     or contract.symbol),
            "exchange": contract.primaryExchange or contract.exchange,
            "currency": contract.currency,
            "con_id": contract.conId,
        })
        if len(matches) >= limit:
            break

    if not matches:
        # Nothing matched exactly; offer the nearest tickers instead of an
        # empty box, which gives the operator no idea whether they mistyped or
        # the symbol does not exist.
        suggestions = _fuzzy_search(query, limit)
        result = {
            "query": query,
            "matches": suggestions,
            "count": len(suggestions),
            "approximate": bool(suggestions),
            "status": "OK",
            "detail": (f"No exact match for {query.upper()}; showing closest "
                       f"tickers." if suggestions else None),
            "source": SEC_SOURCE if suggestions else "IBKR",
        }
        cache.put(key, result)
        return result

    result = {"query": query, "matches": matches, "count": len(matches),
              "status": "OK", "source": "IBKR"}
    cache.put(key, result)
    return result


def validate(symbol: str) -> dict:
    symbol = (symbol or "").strip().upper()
    if not symbol:
        return {"symbol": symbol, "valid": False, "status": "INVALID",
                "detail": "Empty symbol"}

    key = f"symvalid:{symbol}"
    cached = cache.get(key, VALIDATE_TTL)
    if cached:
        return cached

    async def job(ib: IB):
        contract = Stock(symbol, "SMART", "USD")
        try:
            await ib.qualifyContractsAsync(contract)
        except Exception:  # noqa: BLE001 - unknown symbols raise here
            return None
        if not contract.conId:
            return None
        details = await ib.reqContractDetailsAsync(contract)
        detail = details[0] if details else None
        return {
            "symbol": contract.symbol,
            "con_id": contract.conId,
            "exchange": contract.primaryExchange or contract.exchange,
            "name": (detail.longName if detail else None) or contract.symbol,
            "industry": getattr(detail, "industry", None) if detail else None,
            "category": getattr(detail, "category", None) if detail else None,
        }

    try:
        info = ibkr.run(job, timeout=40)
    except IBKRUnavailable as exc:
        # A ticker present in SEC's registry is a real US issuer, which is
        # enough to let the page load. It is not proof of an IBKR tradable
        # contract, hence source=SEC on the payload.
        for row in _sec_universe():
            if row["symbol"] == symbol:
                result = {
                    "symbol": symbol, "name": row["name"], "cik": row["cik"],
                    "con_id": None, "exchange": None,
                    "industry": None, "category": None,
                    "valid": True, "status": "OK", "source": SEC_SOURCE,
                }
                cache.put(key, result)
                return result
        return {"symbol": symbol, "valid": False, "status": "PROVIDER_OFFLINE",
                "detail": str(exc), "source": "IBKR"}

    if not info:
        result = {"symbol": symbol, "valid": False, "status": "SYMBOL_NOT_FOUND",
                  "detail": f"IBKR has no US stock contract for {symbol}",
                  "source": "IBKR"}
    else:
        result = {**info, "valid": True, "status": "OK", "source": "IBKR"}

    cache.put(key, result)
    return result
