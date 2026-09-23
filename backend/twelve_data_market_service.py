"""
Twelve Data market data: daily bars and quotes.

This is the first fallback when TWS is not running. It sits ahead of
``alpha_vantage_market_service`` because the free tiers are not comparable:
Twelve Data allows 800 requests/day and 8/minute, Alpha Vantage 25/day. A
25-request budget cannot serve a dashboard that charts ten symbols.

Scope, stated plainly: these are **end-of-day** bars and a delayed quote, not
the IBKR tick stream. Every payload is tagged ``source="TWELVE_DATA"`` and
``delayed=True`` so a stale print is never shown as live, and the quote carries
no bid/ask because this endpoint does not publish one -- filling those from the
last trade would invent a spread nobody quoted.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Any, Optional

import provider_config as cfg
from live_market_service import cache, market_clock, num

QUOTE_TTL = 300.0
BARS_TTL = 3600.0

SOURCE = "TWELVE_DATA"

# The free plan allows 8 requests per MINUTE -- not one every 8 seconds. A flat
# sleep made the first load of any symbol pay ~8s per call even when no other
# request had been made for an hour, which is what made a cold page take 90s+.
# A token bucket honours the same ceiling while letting an idle app burst.
RATE_LIMIT = int(os.getenv("TWELVE_DATA_PER_MINUTE", "8"))
RATE_WINDOW = 60.0

_call_lock = threading.Lock()
_recent_calls: deque = deque()

# Endpoints this plan does not include. A 403 is a fact about the subscription,
# not a transient failure, so retrying it per symbol only burns the per-minute
# budget that the endpoints we CAN use need. Latched for the process.
_unentitled: set[str] = set()

# Status of the most recent provider answer, so callers can explain a failure
# without spending another request to discover it.
last_status: Optional[str] = None


def configured() -> bool:
    return cfg.TWELVE_DATA.configured


def _throttle() -> None:
    """
    Hold to RATE_LIMIT calls per rolling minute, bursting when idle.

    Waits only when the window is genuinely full, and only for as long as the
    oldest call needs to age out.
    """
    while True:
        with _call_lock:
            now = time.monotonic()
            while _recent_calls and now - _recent_calls[0] >= RATE_WINDOW:
                _recent_calls.popleft()
            if len(_recent_calls) < RATE_LIMIT:
                _recent_calls.append(now)
                return
            wait = RATE_WINDOW - (now - _recent_calls[0]) + 0.05
        # Sleep outside the lock so other threads can still drain the window.
        time.sleep(max(wait, 0.05))


def _call(path: str, params: dict) -> dict:
    """
    GET a JSON document, translating Twelve Data's error envelope into the
    status vocabulary the rest of the app speaks.

    Errors arrive as HTTP 200 with ``{"status": "error", "code": 429, ...}``,
    so the body has to be inspected rather than the status line.
    """
    global last_status

    if path in _unentitled:
        raise cfg.ProviderError(
            cfg.ENTITLEMENT_REQUIRED,
            f"/{path} is not included in this Twelve Data plan.")

    if cfg.in_cooldown(SOURCE):
        raise cfg.ProviderError(
            cfg.RATE_LIMITED, "Twelve Data quota exhausted; retrying later.")

    _throttle()
    try:
        payload = cfg.fetch_json(f"{cfg.TWELVE_DATA.base_url}/{path}", {
            **params,
            "apikey": cfg.TWELVE_DATA.api_key,
        })
    except cfg.ProviderError as exc:
        # An HTTP 401/403 is raised by fetch_json before the body is parsed, so
        # the latch has to happen here too -- otherwise every symbol re-pays a
        # request to discover the same plan limit.
        last_status = exc.status
        if exc.status == cfg.ENTITLEMENT_REQUIRED:
            _unentitled.add(path)
        elif exc.status == cfg.RATE_LIMITED:
            cfg.start_cooldown(SOURCE)
        raise

    if not isinstance(payload, dict):
        last_status = cfg.DATA_UNAVAILABLE
        raise cfg.ProviderError(cfg.DATA_UNAVAILABLE, "Unexpected payload.")

    if payload.get("status") == "error":
        message = cfg.redact(payload.get("message") or "Provider error")
        code = payload.get("code")
        if code == 429:
            status = cfg.RATE_LIMITED
            cfg.start_cooldown(SOURCE)
        elif code in (401, 403):
            status = cfg.ENTITLEMENT_REQUIRED
            # Remember, so the next symbol does not spend a token finding out.
            _unentitled.add(path)
        else:
            status = cfg.DATA_UNAVAILABLE
        last_status = status
        raise cfg.ProviderError(status, str(message)[:400])

    last_status = cfg.OK
    return payload


# ---------------------------------------------------------------------------
# daily bars
# ---------------------------------------------------------------------------

def _duration_to_rows(duration: str) -> int:
    """Translate an IBKR duration string ("1 Y", "6 M") into trading rows."""
    try:
        amount_text, unit = duration.strip().split()
        amount = int(amount_text)
    except (ValueError, AttributeError):
        return 252

    unit = unit.upper()
    if unit.startswith("Y"):
        return amount * 252
    if unit.startswith("M"):
        return amount * 21
    if unit.startswith("W"):
        return amount * 5
    return amount


def get_daily_bars(symbol: str, duration: str = "1 Y") -> list[dict]:
    """
    Daily OHLCV, oldest first, in the shape the technical services expect.

    Returns [] on any provider failure; callers already treat an empty list as
    "no market data" rather than as zero.
    """
    symbol = symbol.upper()
    key = f"td:bars:{symbol}"
    rows = cache.get(key, BARS_TTL)

    if rows is None:
        try:
            # 5000 is the free plan's per-request ceiling and covers the 200-day
            # EMA comfortably, unlike Alpha Vantage's 100-row compact cap.
            payload = _call("time_series", {
                "symbol": symbol,
                "interval": "1day",
                "outputsize": 5000,
                "order": "ASC",
            })
        except cfg.ProviderError:
            return []

        values = payload.get("values")
        if not isinstance(values, list) or not values:
            return []

        rows = []
        for bar in values:
            try:
                rows.append({
                    "date": str(bar["datetime"])[:10],
                    "open": float(bar["open"]),
                    "high": float(bar["high"]),
                    "low": float(bar["low"]),
                    "close": float(bar["close"]),
                    "volume": float(bar.get("volume") or 0.0),
                })
            except (KeyError, TypeError, ValueError):
                # One malformed day must not discard the whole series.
                continue

        if not rows:
            return []
        # "order=ASC" is requested, but sort anyway rather than trust it: the
        # technical services assume oldest-first and would silently invert
        # every trend if the provider ever changed its default.
        rows.sort(key=lambda r: r["date"])
        cache.put(key, rows)

    wanted = _duration_to_rows(duration)
    return rows[-wanted:] if wanted and len(rows) > wanted else rows


# ---------------------------------------------------------------------------
# quote
# ---------------------------------------------------------------------------

def get_quote(symbol: str) -> Optional[dict]:
    """
    A delayed quote shaped like ``live_market_service.get_quote``.

    Returns None when Twelve Data cannot answer, so the caller can fall through
    to the next provider instead of rendering a half-filled card.
    """
    symbol = symbol.upper()
    key = f"td:quote:{symbol}"
    cached = cache.get(key, QUOTE_TTL)
    if cached:
        return cached

    try:
        quote = _call("quote", {"symbol": symbol})
    except cfg.ProviderError:
        return None

    price = num(quote.get("close"))
    if price is None:
        return None

    prev_close = num(quote.get("previous_close"))
    change = change_pct = None
    if prev_close:
        change = round(price - prev_close, 2)
        change_pct = round((price - prev_close) / prev_close * 100, 2)

    result = {
        "symbol": symbol,
        "name": quote.get("name") or symbol,
        "exchange": quote.get("exchange"),
        "con_id": None,
        "tags": [],
        "price": round(price, 2),
        "change": change,
        "change_percent": change_pct,
        "previous_close": round(prev_close, 2) if prev_close else None,
        # Not published on this endpoint; left None rather than invented.
        "bid": None,
        "ask": None,
        "open": num(quote.get("open")),
        "high": num(quote.get("high")),
        "low": num(quote.get("low")),
        "close": price,
        "volume": num(quote.get("volume")),
        "as_of": quote.get("datetime"),
        "market": market_clock(),
        "status": "OK",
        "source": SOURCE,
        "delayed": True,
    }
    cache.put(key, result)
    return result


# ---------------------------------------------------------------------------
# analyst estimates and revision windows
# ---------------------------------------------------------------------------

ESTIMATE_TTL = 6 * 3600.0
PROFILE_TTL = 7 * 24 * 3600.0

HORIZONS = (7, 30, 60, 90)


def _pct(old: Optional[float], new: Optional[float]) -> Optional[float]:
    if old is None or new is None or not old:
        return None
    return round((new - old) / abs(old) * 100, 4)


def revision_windows(symbol: str) -> dict:
    """
    Estimate revisions in the shape ``score_estimates`` consumes.

    ``/eps_trend`` returns the consensus as it stood 7/30/60/90 days ago in one
    response, so all four windows exist from the first call -- no waiting for
    locally accumulated snapshots, and unlike Alpha Vantage the 90-day window
    is included, which is what lets the component reach full confidence.
    """
    symbol = symbol.upper()
    key = f"td:revisions:{symbol}"
    cached = cache.get(key, ESTIMATE_TTL)
    if cached:
        return cached

    empty = {
        "symbol": symbol, "status": "NO_DATA", "snapshot_count": 0,
        "revisions": {}, "available_windows": 0,
    }

    try:
        trend = _call("eps_trend", {"symbol": symbol})
    except cfg.ProviderError:
        return empty

    rows = [r for r in (trend.get("eps_trend") or [])
            if r.get("period") == "current_quarter"] or (trend.get("eps_trend") or [])
    if not rows:
        return empty
    row = rows[0]

    current = num(row.get("current_estimate"))
    if current is None:
        return empty

    # Analyst coverage lives on a sibling endpoint; it is worth one more
    # request because coverage is 4 of the component's 25 points.
    analysts = revenue = None
    try:
        est = _call("earnings_estimate", {"symbol": symbol})
        matching = [r for r in (est.get("earnings_estimate") or [])
                    if r.get("period") == "current_quarter"]
        if matching:
            analysts = num(matching[0].get("number_of_analysts"))
            analysts = int(analysts) if analysts else None
    except cfg.ProviderError:
        pass

    revisions = {}
    available = 0
    for horizon in HORIZONS:
        old = num(row.get(f"{horizon}_days_ago"))
        if old is None:
            revisions[f"{horizon}d"] = {
                "days": horizon, "available": False,
                "reason": f"Provider supplied no {horizon}-day consensus",
            }
            continue
        revisions[f"{horizon}d"] = {
            "days": horizon,
            "available": True,
            "eps_old": old,
            "eps_current": current,
            "eps_revision_percent": _pct(old, current),
            # Twelve Data publishes no revenue trend, so this stays absent
            # rather than being inferred from the EPS move.
            "revenue_old": None,
            "revenue_current": None,
            "revenue_revision_percent": None,
        }
        available += 1

    result = {
        "symbol": symbol,
        "status": "GOOD" if available == len(HORIZONS)
                  else "PARTIAL" if available else "INSUFFICIENT_HISTORY",
        "snapshot_count": available + 1,
        "current_snapshot": {
            "snapshot_time": row.get("date"),
            "eps_estimate": current,
            "revenue_estimate": revenue,
            "analyst_count": analysts,
            "source": SOURCE,
        },
        "available_windows": available,
        "required_windows": len(HORIZONS),
        "revisions": revisions,
    }
    cache.put(key, result)
    return result


def get_profile(symbol: str) -> Optional[dict]:
    """Sector and industry for a symbol, or None if unavailable."""
    symbol = symbol.upper()
    key = f"td:profile:{symbol}"
    cached = cache.get(key, PROFILE_TTL)
    if cached:
        return cached

    try:
        payload = _call("profile", {"symbol": symbol})
    except cfg.ProviderError:
        return None

    if not payload.get("sector"):
        return None

    result = {
        "symbol": symbol,
        "name": payload.get("name"),
        "sector": payload.get("sector"),
        "industry": payload.get("industry"),
        "exchange": payload.get("exchange"),
        "source": SOURCE,
    }
    cache.put(key, result)
    return result


# ---------------------------------------------------------------------------
# live last price
# ---------------------------------------------------------------------------

# Short: this is the live tape, and a minute-old "live" price is just a
# slower stale one.
LIVE_PRICE_TTL = 15.0


def live_price(symbol: str) -> Optional[float]:
    """
    The current traded price, from the dedicated real-time endpoint.

    Distinct from ``get_quote``, which returns the daily OHLC snapshot and is
    dated to the last *completed* session -- so during Monday's session it
    reports Friday's close. That difference is invisible unless you compare
    the two, and it is why the app showed stale prices all day without any
    provider reporting an error.

    Only the price is available here; the change has to be measured against a
    close the caller already holds.
    """
    symbol = (symbol or "").upper().strip()
    if not symbol or not cfg.TWELVE_DATA.configured:
        return None

    key = f"td:live:{symbol}"
    cached = cache.get(key, LIVE_PRICE_TTL)
    if cached is not None:
        return cached or None

    try:
        payload = _call("price", {"symbol": symbol})
    except cfg.ProviderError:
        cache.put(key, 0.0)
        return None

    price = num((payload or {}).get("price"))
    cache.put(key, price or 0.0)
    return price


def live_prices(symbols: list[str]) -> dict[str, float]:
    """
    Live prices for several symbols in one request.

    The endpoint accepts a comma-separated list and answers with a map, which
    matters on a plan capped at eight calls a minute: warming a dozen symbols
    one at a time would exhaust the budget before it finished.
    """
    wanted = [s.upper().strip() for s in symbols if s and s.strip()]
    if not wanted or not cfg.TWELVE_DATA.configured:
        return {}

    try:
        payload = _call("price", {"symbol": ",".join(wanted)})
    except cfg.ProviderError:
        return {}

    if not isinstance(payload, dict):
        return {}

    # A single symbol comes back as a bare {"price": ...} rather than a map.
    if "price" in payload and len(wanted) == 1:
        price = num(payload.get("price"))
        return {wanted[0]: price} if price else {}

    out: dict[str, float] = {}
    for symbol, entry in payload.items():
        price = num((entry or {}).get("price")) if isinstance(entry, dict) else None
        if price:
            out[symbol.upper()] = price
            cache.put(f"td:live:{symbol.upper()}", price)
    return out
