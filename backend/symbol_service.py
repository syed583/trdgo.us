"""
Symbol lookup and validation.

Used by the search box: typing gives suggestions, and committing a symbol
validates it so the UI can say SYMBOL NOT FOUND with certainty rather than
after a failed page load.

Two sources, and they answer different questions. SEC's registry says the
ticker belongs to a real US issuer; the market feed says whether it actually
carries data for it. A symbol can pass the first and fail the second -- a
delisted shell, a ticker that has not traded in years -- and the page would
then load with every panel empty, so validation asks both.
"""

from __future__ import annotations

import difflib
from typing import Optional

from live_market_service import cache

SEARCH_TTL = 900.0
VALIDATE_TTL = 3600.0

# SEC's company_tickers.json is the universe: every SEC-registered US
# issuer, no API key and no request quota. It carries ticker, company name and
# CIK -- but no exchange, so that comes back None rather than guessed.
SEC_SOURCE = "SEC"
FEED_SOURCE = "UNUSUAL_WHALES"


def _sec_universe() -> list[dict]:
    """
    [{symbol, name, cik}], cached by sec_service's own HTTP cache.

    Returns [] if SEC cannot be reached, so callers report that rather than
    an empty-but-successful search.
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
    """
    Tickers matching what has been typed so far.

    This used to ask TWS (``reqMatchingSymbols``) and fall back to the SEC
    registry. The registry now leads outright: it was already doing the work
    -- TWS returned an empty description on most rows, so every result
    rendered as "MSFT - MSFT" until the company name was filled in from here
    anyway.
    """
    query = (query or "").strip()
    if len(query) < 1:
        return {"query": query, "matches": [], "status": "OK",
                "source": SEC_SOURCE}

    key = f"symsearch:{query.upper()}:{limit}"
    cached = cache.get(key, SEARCH_TTL)
    if cached:
        return cached

    matches = _sec_search(query, limit)
    if matches:
        result = {"query": query, "matches": matches, "count": len(matches),
                  "status": "OK", "source": SEC_SOURCE}
        cache.put(key, result)
        return result

    if not _sec_universe():
        return {"query": query, "matches": [], "status": "PROVIDER_OFFLINE",
                "detail": "The SEC ticker registry could not be read.",
                "source": SEC_SOURCE}

    # Nothing matched exactly; offer the nearest tickers instead of an empty
    # box, which gives the operator no idea whether they mistyped or the
    # symbol does not exist.
    suggestions = _fuzzy_search(query, limit)
    result = {
        "query": query,
        "matches": suggestions,
        "count": len(suggestions),
        "approximate": bool(suggestions),
        "status": "OK",
        "detail": (f"No exact match for {query.upper()}; showing closest "
                   f"tickers." if suggestions else None),
        "source": SEC_SOURCE,
    }
    cache.put(key, result)
    return result


def validate(symbol: str) -> dict:
    """
    Is this a real US ticker, and does the feed carry it?

    Both are asked, because they are different claims. The registry proves
    the issuer exists; it says nothing about whether any screen in this app
    will have data to show. A ticker the feed does not know is reported as
    found-but-uncovered rather than as valid, so the page can say which of
    the two went wrong instead of loading empty.
    """
    symbol = (symbol or "").strip().upper()
    if not symbol:
        return {"symbol": symbol, "valid": False, "status": "INVALID",
                "detail": "Empty symbol"}

    key = f"symvalid:{symbol}"
    cached = cache.get(key, VALIDATE_TTL)
    if cached:
        return cached

    registered = next((row for row in _sec_universe()
                       if row["symbol"] == symbol), None)

    info = None
    try:
        import unusualwhales_service as uw

        if uw.configured():
            out = uw.get(f"/api/stock/{symbol}/info")
            info = out.get("data") if out.get("status") == "OK" else None
    except Exception:  # noqa: BLE001 - the registry answer still stands
        info = None

    if not registered and not info:
        result = {"symbol": symbol, "valid": False,
                  "status": "SYMBOL_NOT_FOUND",
                  "detail": f"No US issuer or market data found for {symbol}.",
                  "source": SEC_SOURCE}
        cache.put(key, result)
        return result

    result = {
        "symbol": symbol,
        "name": ((info or {}).get("full_name")
                 or (registered or {}).get("name") or symbol),
        "cik": (registered or {}).get("cik"),
        "con_id": None,
        "exchange": None,
        "industry": (info or {}).get("sector"),
        "category": (info or {}).get("issue_type"),
        "valid": True,
        "status": "OK",
        # Said plainly: registered but uncovered means the pages will load
        # with nothing on them, and that is worth knowing before they do.
        "covered": bool(info),
        "detail": (None if info else
                   f"{symbol} is a registered issuer, but the market feed "
                   f"carries no data for it."),
        "source": FEED_SOURCE if info else SEC_SOURCE,
    }
    cache.put(key, result)
    return result
