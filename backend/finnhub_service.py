"""
Finnhub adapter -- the data UW does not carry, on the free plan:
analyst recommendation trends, company peers, insider sentiment/transactions,
company news, earnings & IPO calendars, and basic metrics.

Candles, news-sentiment and the economic calendar are premium-gated on this
plan, so we never call them (candles come from UW; news tone is computed here).
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from typing import Optional

BASE = "https://finnhub.io/api/v1"
SOURCE = "Finnhub"

_cache: dict[str, tuple[float, object]] = {}


def _key() -> Optional[str]:
    return (os.getenv("FINNHUB_API_KEY") or "").strip() or None


def configured() -> bool:
    return _key() is not None


# Account-wide request pacing. EVERY Finnhub call (analyst recommendation,
# peers, insider sentiment, the warmer, ...) goes through _get, so gating here
# -- not in any one caller -- means the whole app shares one budget and the
# free-tier per-minute limit is respected no matter which service calls. Spaced
# by _MIN_GAP seconds; FINNHUB_PER_MINUTE overrides (default 55/min, a margin
# under Finnhub's 60).
import threading as _threading

_rl_lock = _threading.Lock()
_next_slot = [0.0]


def _min_gap() -> float:
    try:
        per_min = int(os.getenv("FINNHUB_PER_MINUTE") or 55)
    except ValueError:
        per_min = 55
    return 60.0 / max(1, min(per_min, 60))


def _rate_gate() -> None:
    """Block just long enough that calls leave at most _per_minute_. Shared by
    every caller so other services' calls count against the same budget."""
    gap = _min_gap()
    with _rl_lock:
        now = time.time()
        slot = max(now, _next_slot[0])
        _next_slot[0] = slot + gap
    wait = slot - time.time()
    if wait > 0:
        time.sleep(wait)


def _get(path: str, ttl: float = 600.0) -> Optional[object]:
    """GET a Finnhub path with a short in-proc cache. Returns parsed JSON or None."""
    k = _key()
    if not k:
        return None
    now = time.time()
    hit = _cache.get(path)
    if hit and now - hit[0] < ttl:
        return hit[1]
    _rate_gate()   # pace every real request, across all callers
    try:
        req = urllib.request.Request(BASE + path, headers={"X-Finnhub-Token": k})
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        _cache[path] = (time.time(), data)
        return data
    except Exception:  # noqa: BLE001 - a missing provider just means no data
        return None


def recommendation(symbol: str) -> Optional[dict]:
    """Latest analyst recommendation trend: strongBuy/buy/hold/sell/strongSell."""
    rows = _get(f"/stock/recommendation?symbol={urllib.parse.quote(symbol)}", 3600)
    if isinstance(rows, list) and rows:
        rows.sort(key=lambda r: r.get("period", ""))
        return rows[-1]
    return None


def peers(symbol: str) -> list[str]:
    rows = _get(f"/stock/peers?symbol={urllib.parse.quote(symbol)}", 86400)
    out = [p.upper() for p in rows if isinstance(p, str)] if isinstance(rows, list) else []
    return [p for p in out if p != symbol.upper()][:8]


def insider_sentiment(symbol: str) -> Optional[dict]:
    """Most recent monthly insider sentiment (mspr = monthly share-purchase ratio)."""
    from datetime import date, timedelta
    frm = (date.today() - timedelta(days=180)).isoformat()
    to = date.today().isoformat()
    data = _get(f"/stock/insider-sentiment?symbol={urllib.parse.quote(symbol)}"
                f"&from={frm}&to={to}", 43200)
    rows = (data or {}).get("data") if isinstance(data, dict) else None
    if rows:
        rows.sort(key=lambda r: (r.get("year", 0), r.get("month", 0)))
        return rows[-1]
    return None


def metrics(symbol: str) -> dict:
    data = _get(f"/stock/metric?symbol={urllib.parse.quote(symbol)}&metric=all", 43200)
    return (data or {}).get("metric") or {} if isinstance(data, dict) else {}


def company_news(symbol: str, days: int = 7) -> list[dict]:
    from datetime import date, timedelta
    frm = (date.today() - timedelta(days=days)).isoformat()
    to = date.today().isoformat()
    rows = _get(f"/company-news?symbol={urllib.parse.quote(symbol)}&from={frm}&to={to}", 1800)
    return rows if isinstance(rows, list) else []


def company_earnings(symbol: str) -> Optional[dict]:
    """The symbol's most recent reported quarter: EPS actual vs estimate +
    surprise percent (Finnhub /stock/earnings, free plan). None if nothing."""
    rows = _get(f"/stock/earnings?symbol={urllib.parse.quote(symbol)}", 21600)
    if isinstance(rows, list) and rows:
        rows.sort(key=lambda r: r.get("period", ""))
        return rows[-1]
    return None


def earnings_history(symbol: str, n: int = 8) -> list[dict]:
    """Last `n` reported quarters (Finnhub /stock/earnings), oldest first, each with
    EPS actual / estimate. [] if nothing."""
    rows = _get(f"/stock/earnings?symbol={urllib.parse.quote(symbol)}", 21600)
    if not isinstance(rows, list):
        return []
    rows = sorted(rows, key=lambda r: r.get("period", ""))
    return rows[-n:]


def earnings_calendar(symbol: str) -> Optional[dict]:
    """The symbol's next scheduled earnings row, if any in the next ~40 days."""
    from datetime import date, timedelta
    frm = date.today().isoformat()
    to = (date.today() + timedelta(days=40)).isoformat()
    data = _get(f"/calendar/earnings?from={frm}&to={to}"
                f"&symbol={urllib.parse.quote(symbol)}", 10800)
    rows = (data or {}).get("earningsCalendar") if isinstance(data, dict) else None
    if rows:
        rows.sort(key=lambda r: r.get("date", ""))
        return rows[0]
    return None
