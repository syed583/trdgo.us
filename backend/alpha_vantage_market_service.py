"""
Alpha Vantage market data: quotes and daily bars.

IBKR is the primary market-data source and stays that way. This module is the
fallback used when TWS is not running, so that technicals, the market
environment score and the quote strip keep working on a real feed instead of
going dark.

What this is NOT: Alpha Vantage serves **end-of-day** bars and a delayed
GLOBAL_QUOTE. It is not a substitute for the IBKR tick stream. Every payload
produced here is tagged ``source="ALPHA_VANTAGE"`` and ``delayed=True`` so the
UI can say which feed a number came from, and so a delayed print is never
presented as a live one.

There is no bid/ask here — Alpha Vantage does not publish one on this
endpoint. Those fields stay ``None`` rather than being filled with the last
trade, which would fabricate a spread.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

import provider_config as cfg
from live_market_service import cache, market_clock, num

# EOD data changes once a day; the quote is delayed regardless. Long TTLs keep
# us well inside the plan's request budget.
QUOTE_TTL = 300.0
BARS_TTL = 3600.0

SOURCE = "ALPHA_VANTAGE"

# Alpha Vantage paces at roughly 1 request/second and answers a burst with a
# rate-limit note rather than an error code.
BURST_PAUSE = 1.5

# Whether this plan includes outputsize=full on TIME_SERIES_DAILY. Probed once
# and latched on the first refusal — see get_daily_bars.
_FULL_ENTITLED = True

_call_lock = threading.Lock()
_last_call_at = 0.0

# Status of the most recent provider answer (cfg.OK, RATE_LIMITED, ...).
# Recorded so callers can explain why a fallback did not cover without
# spending another request to find out.
last_status: Optional[str] = None


def configured() -> bool:
    return cfg.ALPHA_VANTAGE.configured


def _throttle() -> None:
    """
    Hold requests to one per BURST_PAUSE seconds.

    Alpha Vantage answers a burst with a 200 and a rate-limit note, which is
    indistinguishable from "no data" at the call site. Two symbols fetched
    back to back (SPY then QQQ for the market environment score) is enough to
    trip it, so the pacing lives here rather than at each caller.
    """
    global _last_call_at

    with _call_lock:
        wait = BURST_PAUSE - (time.monotonic() - _last_call_at)
        if wait > 0:
            time.sleep(wait)
        _last_call_at = time.monotonic()


def _call(params: dict) -> dict:
    """
    GET a JSON document, translating Alpha Vantage's 200-with-a-note answers
    into the same ProviderError vocabulary the rest of the app speaks.
    """
    # A spent daily quota will not refill within the request; skipping saves a
    # full round trip per attempt.
    if cfg.in_cooldown(SOURCE):
        raise cfg.ProviderError(
            cfg.RATE_LIMITED, "Alpha Vantage quota exhausted; retrying later.")

    _throttle()
    payload = cfg.fetch_json(cfg.ALPHA_VANTAGE.base_url, {
        **params,
        "apikey": cfg.ALPHA_VANTAGE.api_key,
    })

    global last_status

    if not isinstance(payload, dict):
        last_status = cfg.DATA_UNAVAILABLE
        raise cfg.ProviderError(cfg.DATA_UNAVAILABLE, "Unexpected payload.")

    # Rate limits, bad keys and unentitled endpoints all arrive as HTTP 200
    # with a prose note. The key is echoed back inside it, hence redact().
    note = payload.get("Note") or payload.get("Information")
    if note:
        text = cfg.redact(note).lower()
        if "rate limit" in text or "call frequency" in text or "requests per" in text:
            last_status = cfg.RATE_LIMITED
            cfg.start_cooldown(SOURCE)
            raise cfg.ProviderError(cfg.RATE_LIMITED, cfg.redact(note))
        last_status = cfg.ENTITLEMENT_REQUIRED
        raise cfg.ProviderError(cfg.ENTITLEMENT_REQUIRED, cfg.redact(note))

    if payload.get("Error Message"):
        last_status = cfg.DATA_UNAVAILABLE
        raise cfg.ProviderError(
            cfg.DATA_UNAVAILABLE, cfg.redact(payload["Error Message"]))

    last_status = cfg.OK
    return payload


# ---------------------------------------------------------------------------
# daily bars
# ---------------------------------------------------------------------------

def _duration_to_rows(duration: str) -> int:
    """
    Translate an IBKR duration string ("1 Y", "6 M", "200 D") into a number of
    trading rows to keep. The 200-day EMA needs 200 closes, so anything
    shorter than that is still fetched at full size and trimmed here.
    """
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
    Daily OHLCV in the same shape ``market_data_service.get_historical_bars``
    returns: oldest first, ``date`` as an ISO string.

    Returns [] on any provider failure — callers already treat an empty list
    as "no market data" rather than as zero.
    """
    symbol = symbol.upper()
    key = f"av:bars:{symbol}"
    rows = cache.get(key, BARS_TTL)

    if rows is None:
        # "full" carries enough closes for the 200-day EMA, but it is a
        # premium parameter on TIME_SERIES_DAILY. On a plan without it we fall
        # back to "compact" (100 rows) — enough for EMA20/50, RSI and ATR, so
        # trend still resolves; ema_200 simply stays None, which
        # calculate_technicals already handles.
        global _FULL_ENTITLED

        sizes = ("full", "compact") if _FULL_ENTITLED else ("compact",)
        payload = None
        for size in sizes:
            try:
                payload = _call({
                    "function": "TIME_SERIES_DAILY",
                    "symbol": symbol,
                    "outputsize": size,
                })
                break
            except cfg.ProviderError as exc:
                if size == "full" and exc.status == cfg.ENTITLEMENT_REQUIRED:
                    # Latch it: the plan will not grow "full" mid-session, and
                    # retrying it every call burns half the request budget.
                    _FULL_ENTITLED = False
                    continue
                return []

        if payload is None:
            return []

        series = payload.get("Time Series (Daily)")
        if not isinstance(series, dict) or not series:
            return []

        rows = []
        for day in sorted(series):
            bar = series[day]
            try:
                rows.append({
                    "date": day,
                    "open": float(bar["1. open"]),
                    "high": float(bar["2. high"]),
                    "low": float(bar["3. low"]),
                    "close": float(bar["4. close"]),
                    "volume": float(bar.get("5. volume") or 0.0),
                })
            except (KeyError, TypeError, ValueError):
                # One malformed day must not discard the whole series.
                continue

        if not rows:
            return []
        cache.put(key, rows)

    wanted = _duration_to_rows(duration)
    return rows[-wanted:] if wanted and len(rows) > wanted else rows


# ---------------------------------------------------------------------------
# quote
# ---------------------------------------------------------------------------

def get_quote(symbol: str) -> Optional[dict]:
    """
    A delayed quote in the shape ``live_market_service.get_quote`` returns.

    Returns None when Alpha Vantage cannot answer, so the caller can fall back
    to its own offline payload rather than showing a half-filled card.
    """
    symbol = symbol.upper()
    key = f"av:quote:{symbol}"
    cached = cache.get(key, QUOTE_TTL)
    if cached:
        return cached

    try:
        payload = _call({"function": "GLOBAL_QUOTE", "symbol": symbol})
    except cfg.ProviderError:
        return None

    quote = payload.get("Global Quote") or {}
    price = num(quote.get("05. price"))
    if price is None:
        return None

    prev_close = num(quote.get("08. previous close"))
    change = None
    change_pct = None
    if prev_close:
        change = round(price - prev_close, 2)
        change_pct = round((price - prev_close) / prev_close * 100, 2)

    result = {
        "symbol": symbol,
        "name": symbol,
        "exchange": None,
        "con_id": None,
        "tags": [],
        "price": round(price, 2),
        "change": change,
        "change_percent": change_pct,
        "previous_close": round(prev_close, 2) if prev_close else None,
        # Alpha Vantage publishes no bid/ask on this endpoint. Leaving these
        # None is deliberate: filling them from the last trade would invent a
        # spread that nobody quoted.
        "bid": None,
        "ask": None,
        "open": num(quote.get("02. open")),
        "high": num(quote.get("03. high")),
        "low": num(quote.get("04. low")),
        "close": price,
        "volume": num(quote.get("06. volume")),
        "as_of": quote.get("07. latest trading day"),
        "market": market_clock(),
        "status": "OK",
        "source": SOURCE,
        "delayed": True,
    }
    cache.put(key, result)
    return result
