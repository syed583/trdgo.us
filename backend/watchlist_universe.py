"""Which tickers the Watchlist search is allowed to offer: US common stock and ADRs.

WHY A SET AND NOT A SEARCH. The obvious implementation asks the provider's screener for whatever
has been typed so far, which is a paid request per keystroke on a box people type into. This builds
the allowed set ONCE A DAY, shared by every reader, and the search then filters the SEC ticker
registry against it - so typing costs nothing at all.

WHY NOT universe_service. That one is the top 1,000 by dollar volume, COMMON STOCK ONLY, and it is
what the earnings tabs and the cache warmer restrict themselves to. The owner asked for common
stock AND ADRs here, and a watchlist should not be capped at the thousand busiest names, so this
is its own set rather than a change to a list other features depend on.

COST, measured in requests: the screener is paged 500 rows at a time, up to 8 pages per issue type,
so at most 16 provider requests ONCE PER 24 HOURS, shared by everyone. Nothing is requested when a
reader opens the page or types in it.

FAILS CLOSED, deliberately. If the set cannot be built the search returns nothing and says why,
rather than offering every SEC registrant - which would include funds, trusts and foreign issuers
the owner explicitly ruled out. An empty box with a reason is better than a box full of things
that should not be in it.
"""
from __future__ import annotations

from typing import Optional

SOURCE = "US common stock and ADRs (Unusual Whales stock screener)"
ISSUE_TYPES = ("Common Stock", "ADR")
_PAGES = 8                      # 8 x 500 covers the screener's US equity rows per issue type
_KEY = "watchlist:universe"
_TTL = 24 * 3600.0


def build() -> dict:
    """The allowed tickers, with their issue type. RAISES on failure, never returns None.

    swr caches whatever a builder returns, and a cached None once poisoned a key for 24h - so a
    failure has to raise here or the Watchlist search would be dead for a day.
    """
    import time

    import unusualwhales_service as uw

    found: dict[str, str] = {}
    for issue in ISSUE_TYPES:
        for off in range(_PAGES):
            page: list[dict] = []
            status = "?"
            for attempt in range(4):            # a rate-limited page is retried, not read as the end
                out = uw.get("/api/screener/stocks",
                             {"limit": 500, "offset": off, "issue_types[]": issue})
                status = out.get("status")
                if status == "OK":
                    page = uw._rows(out)
                    break
                time.sleep(5 * (attempt + 1))
            else:
                if off == 0 and not found:
                    raise RuntimeError(f"screener page 0 failed for {issue} ({status})")
                break                            # later pages: keep what we have
            for row in page:
                ticker = (row.get("ticker") or "").upper().strip()
                if ticker:
                    found.setdefault(ticker, issue)
            if len(page) < 500:
                break

    if not found:
        raise RuntimeError("the screener returned no US common stock or ADR rows")
    return {"status": "OK", "source": SOURCE, "count": len(found), "tickers": found}


def get() -> Optional[dict]:
    """The held set, refreshed in the background when it ages out.

    swr.serve takes (key, fn, fresh_for) - checked, not assumed. A cold first call can return
    None while the build runs; the caller reports "unavailable" for that one request rather than
    blocking a page on up to 16 provider requests.
    """
    import swr
    try:
        value = swr.serve(_KEY, build, _TTL)
    except Exception:  # noqa: BLE001 - a provider outage must not 500 the search box
        return None
    return value if isinstance(value, dict) else None


def tickers() -> Optional[dict[str, str]]:
    """ticker -> issue type, or None when the set is not available."""
    data = get()
    if not data or data.get("status") != "OK":
        return None
    out = data.get("tickers")
    return out if isinstance(out, dict) and out else None
