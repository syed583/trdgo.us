"""
One place that decides what a valid request input looks like.

Every screen is keyed on a ticker symbol, and until this module existed a
symbol was whatever string arrived -- from a path, a query parameter, or a
stored watchlist row. That string was then interpolated straight into an
outbound URL (``/api/stock/{symbol}/quote``) and into cache keys. A symbol of
``../../api/darkpool/recent?limit=500`` therefore rewrote the request line
sent to the data provider, carrying this app's API token to an endpoint the
operator never asked for, and a symbol of ``JUNK<n>`` for a million values of
n filled an unbounded cache.

A ticker is a short run of letters, digits, dot and dash. Anything else is
not a symbol, and is refused here rather than sanitised into something
plausible -- guessing what the caller meant is how an injected string slips
through in a shape nobody tested.
"""

from __future__ import annotations

import re
from typing import Optional

from fastapi import HTTPException

# Real US tickers are 1-5 chars; class shares and some ETFs add a dot or dash
# suffix (BRK.B, RDS-A). Ten is comfortably above anything listed and well
# below anything that could carry a path or a query.
_SYMBOL = re.compile(r"^[A-Za-z][A-Za-z0-9.\-]{0,9}$")


def clean_symbol(value: str) -> str:
    """
    Return the upper-cased symbol, or raise 422.

    The single gate for a ticker reaching a provider URL or a cache key.
    """
    symbol = (value or "").strip().upper()
    if not _SYMBOL.match(symbol):
        raise HTTPException(
            status_code=422,
            detail="A symbol is 1-10 characters: letters, digits, '.' or '-'.")
    return symbol


def clean_symbols(value: Optional[str], *, limit: int = 50) -> list[str]:
    """
    Parse a comma-separated symbol list, dropping anything invalid.

    Used by the strip and watchlist endpoints, which take many symbols in one
    query parameter. Invalid entries are skipped rather than failing the whole
    request, and the count is capped so one request cannot fan out without
    bound.
    """
    out: list[str] = []
    for raw in (value or "").split(","):
        candidate = raw.strip().upper()
        if _SYMBOL.match(candidate) and candidate not in out:
            out.append(candidate)
        if len(out) >= limit:
            break
    return out


def is_symbol(value: str) -> bool:
    """True when ``value`` is a well-formed symbol. For non-HTTP callers."""
    return bool(_SYMBOL.match((value or "").strip().upper()))


def clamp_int(value: object, *, low: int, high: int, default: int) -> int:
    """
    Coerce a query integer into ``[low, high]``.

    Route integers (days, limits, quarters) reach provider calls and list
    slices. Left unbounded, ``?days=999999999`` walks a provider's whole
    history one archived filing at a time. Clamped, the worst case is bounded.
    """
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, n))
