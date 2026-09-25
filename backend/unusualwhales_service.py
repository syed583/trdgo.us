"""
Unusual Whales: one paid feed in place of eight free ones.

What this is for
----------------
The app grew by adding whatever free provider could answer the next question:
Twelve Data and Alpha Vantage for bars when TWS was busy, Nasdaq for
dividends, Benzinga for earnings, Marketaux and Yahoo for headlines, Finviz
for fundamentals and screens, OptionData and OptionsBell for the options
tape. Each came with its own key, its own quota, its own outage, and its own
spelling of the same company -- and the model went dark parameter by
parameter whenever one of them ran out.

Unusual Whales answers nearly all of it from one subscription: the options
tape and its unusual filter, open-interest change, IV rank and the whole
volatility surface, OHLC candles, quotes, earnings history, dividends,
analyst actions, insider transactions, institutional ownership, headlines,
fundamentals and a screener. So this becomes the primary source for those,
and the free providers stay as fallbacks rather than as the floor.

What it does not replace
------------------------
* **Interactive Brokers** -- the live quote and the intraday bars the session
  parameters are measured from. That stays, by the user's decision.
* **SEC EDGAR** -- 8-K item codes are the company's own classification of
  what happened. Nothing else is the record of a filing.
* **Claude** -- words, never numbers.

House rules
-----------
One subscription, one person: the token is read from backend/.env, never
logged, and never returned by any status endpoint. Their limits are read
from the response headers and honoured -- a 429 is a signal to stop, not to
retry.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from dotenv import load_dotenv

from live_market_service import cache

load_dotenv()

SOURCE = "UNUSUAL_WHALES"
BASE = "https://api.unusualwhales.com"

# urllib follows 3xx by default and re-sends every header -- including the
# Authorization bearer -- to the redirect target, cross-origin included. A
# provider-side open redirect would then leak the API token. This opener
# refuses to follow: a redirect is surfaced as an HTTPError and handled like
# any other non-200 rather than chased.
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D401, ANN001
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)
ENV_KEY = "UNUSUAL_WHALES_API_KEY"
TIMEOUT = 30.0

# Their published self-serve tier is 500 requests a minute and 80,000 a day.
# The app's own ceiling sits well under it: nothing here should be able to
# spend a subscription on a loop nobody asked for.
APP_DAILY_BUDGET = 20000

# How long an answer stays good. Split by how fast the underlying thing
# actually moves rather than by how fast we could ask again.
TTL_TAPE = 60.0            # the options tape, while the market is open
TTL_QUOTE = 15.0
TTL_INTRADAY = 120.0
TTL_DAILY = 6 * 3600.0     # earnings, dividends, fundamentals, ownership
TTL_SETTLED = 30 * 24 * 3600.0   # a finished day cannot change

# The app fans out: a board scan touches thirty-eight symbols across several
# endpoints at once, and firing those together is what trips their burst
# limit -- after which every screen would be blocked for the length of the
# pause. Requests are spaced instead of refused.
#
# 0.06s = ~1000 requests/minute. The plan's own per-minute headroom is far
# above what any single screen needs (a cold dashboard is a few dozen calls),
# and this is the dispatch spacing every multi-call screen waits on, so it is
# kept as low as stays comfortably clear of the burst limit. It was 0.12s,
# which doubled the wait on every screen that fans out.
MIN_INTERVAL = 0.06

# The per-minute allowance is huge, but a 429 still fires on a *burst* -- too
# many requests in flight at once. Several screens fan out in parallel (the
# news desk alone opens a dozen), and if they all reach the API together they
# trip that burst limit and the whole app pauses. This semaphore caps how many
# requests are on the wire simultaneously, however many callers ask, so a
# parallel screen is smoothed into a steady trickle instead of a spike.
MAX_INFLIGHT = 3
_inflight = threading.BoundedSemaphore(MAX_INFLIGHT)

# Single-flight: when many users open the same symbol at once and the cache is
# cold, without this every one of them fires the same request -- a burst that
# trips the provider's 429 and burns budget N times for one answer. A per-key
# lock collapses those into one call the rest read from cache.
_flight_guard = threading.Lock()
_flights: dict[str, threading.Lock] = {}


def _flight_lock(key: str) -> threading.Lock:
    with _flight_guard:
        lock = _flights.get(key)
        if lock is None:
            lock = _flights[key] = threading.Lock()
        return lock

_gate = threading.Lock()
_last_call = 0.0

_limits: dict = {"remaining": None, "reset": None}
_spent: dict = {"day": None, "count": 0}
_blocked_until = 0.0
_last_status: Optional[str] = None


def api_key() -> str:
    return (os.environ.get(ENV_KEY) or "").strip()


def configured() -> bool:
    return bool(api_key())


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _seconds(value) -> Optional[int]:
    try:
        return max(0, int(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _note_limits(headers) -> None:
    """Record what their headers say is left, whatever they call it."""
    if not headers:
        return
    # Theirs are x-uw-req-per-minute-*; the generic spellings are kept so a
    # change of theirs to a standard header still registers.
    for name in ("x-uw-req-per-minute-remaining", "X-RateLimit-Remaining",
                 "Ratelimit-Remaining", "x-ratelimit-remaining"):
        value = _seconds(headers.get(name))
        if value is not None:
            _limits["remaining"] = value
            break
    for name in ("x-uw-req-per-minute-reset", "X-RateLimit-Reset",
                 "Ratelimit-Reset", "x-ratelimit-reset"):
        value = _seconds(headers.get(name))
        if value is not None:
            _limits["reset"] = value
            break


def _pause(seconds: float) -> None:
    global _blocked_until
    _blocked_until = max(_blocked_until, time.time() + max(1.0, seconds))


def _space_out() -> None:
    """Keep at least MIN_INTERVAL between calls, however many threads ask."""
    global _last_call

    with _gate:
        wait = MIN_INTERVAL - (time.time() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()


def _spend() -> bool:
    """Count one request against the app's own daily ceiling."""
    if _spent["day"] != _today():
        _spent["day"], _spent["count"] = _today(), 0
    if _spent["count"] >= APP_DAILY_BUDGET:
        return False
    _spent["count"] += 1
    return True


def get(path: str, params: Optional[dict] = None) -> dict:
    """
    One call to their API, with the limits they publish respected.

    Returns the decoded body under ``data`` with a status this app
    understands, never raises, and never puts the token anywhere but the
    Authorization header.
    """
    global _last_status

    if not configured():
        return {"status": "PROVIDER_NOT_CONFIGURED", "data": None,
                "detail": f"Add {ENV_KEY} to backend/.env to use Unusual Whales.",
                "source": SOURCE}

    if time.time() < _blocked_until:
        return {"status": "RATE_LIMITED", "data": None,
                "detail": "Waiting out Unusual Whales' rate limit.",
                "retry_in": round(_blocked_until - time.time()),
                "source": SOURCE}

    if not _spend():
        return {"status": "BUDGET_EXHAUSTED", "data": None,
                "detail": (f"This app's own daily ceiling of {APP_DAILY_BUDGET} "
                           "Unusual Whales requests is spent."),
                "source": SOURCE}

    if any(bad in path for bad in ("?", "#", "..", "//")) or not path.startswith("/"):
        # A well-formed path is built from validated segments. Anything with a
        # query, a fragment, a parent-directory hop or a doubled slash was not
        # built the way this function's callers build paths -- it is an
        # injected symbol, and it does not get sent with the token.
        _last_status = "BAD_REQUEST"
        return {"status": "BAD_REQUEST", "data": None,
                "detail": "Malformed request path.", "source": SOURCE}

    query = urllib.parse.urlencode(
        {k: v for k, v in (params or {}).items() if v not in (None, "")},
        doseq=True)
    request = urllib.request.Request(
        f"{BASE}{path}" + (f"?{query}" if query else ""),
        headers={"Authorization": f"Bearer {api_key()}",
                 "Accept": "application/json",
                 "User-Agent": "US-Stock-Reader/1.0"})

    # Only MAX_INFLIGHT requests hold the wire at once; the rest wait here.
    # Spacing is applied inside the gate so starts stay staggered too.
    with _inflight:
        _space_out()
        try:
            with _OPENER.open(request, timeout=TIMEOUT) as response:
                _note_limits(response.headers)
                body = json.loads(response.read().decode("utf-8", "ignore") or "{}")
        except urllib.error.HTTPError as exc:
            return _http_error(exc)
        except Exception as exc:  # noqa: BLE001
            _last_status = "PROVIDER_OFFLINE"
            return {"status": "PROVIDER_OFFLINE", "data": None,
                    "detail": type(exc).__name__, "source": SOURCE}

    _last_status = "OK"
    # Their payloads put the answer under "data"; a few return the object
    # itself. Both are handed back the same way so callers never branch.
    data = body.get("data") if isinstance(body, dict) and "data" in body else body
    return {"status": "OK", "data": data, "source": SOURCE}


def _http_error(exc) -> dict:
    global _last_status

    try:
        body = json.loads(exc.read().decode("utf-8", "ignore") or "{}")
    except Exception:  # noqa: BLE001
        body = {}
    detail = str(body.get("message") or body.get("error") or f"HTTP {exc.code}")

    if exc.code == 429:
        # Their Retry-After is authoritative. A refused request still counts
        # against the window, so retrying early spends the allowance on being
        # refused again.
        # Their per-minute allowance is in the millions, so a 429 here is a
        # short burst limit rather than a spent quota: waiting a full minute
        # would take the app off the air for far longer than the refusal
        # actually means. Their Retry-After wins when they send one.
        wait = _seconds(getattr(exc, "headers", {}).get("Retry-After")) or 5
        _pause(wait)
        _last_status = "RATE_LIMITED"
        return {"status": "RATE_LIMITED", "data": None, "detail": detail,
                "retry_in": wait, "source": SOURCE}
    if exc.code in (401, 403):
        _last_status = "ENTITLEMENT_REQUIRED"
        return {"status": "ENTITLEMENT_REQUIRED", "data": None,
                "detail": ("Unusual Whales rejected the token. Check "
                           f"{ENV_KEY} and that the subscription is active."),
                "source": SOURCE}
    if exc.code == 404:
        return {"status": "NO_DATA", "data": None, "detail": detail,
                "source": SOURCE}
    _last_status = "PROVIDER_OFFLINE"
    return {"status": "PROVIDER_OFFLINE", "data": None, "detail": detail,
            "source": SOURCE}


def _cached(key: str, path: str, params: Optional[dict] = None,
            ttl: float = TTL_DAILY) -> dict:
    hit = cache.get(key, ttl)
    if hit:
        return hit
    # Collapse a stampede: the first caller fetches, the rest wait here and then
    # read the cache it just filled -- one provider call for many users.
    lock = _flight_lock(key)
    with lock:
        hit = cache.get(key, ttl)
        if hit:
            return hit
        out = get(path, params)
        if out["status"] == "OK":
            cache.put(key, out)
        return out


def _rows(payload: dict) -> list:
    data = payload.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for value in data.values():
            if isinstance(value, list):
                return value
    return []


def _f(value) -> Optional[float]:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# price and session
# ---------------------------------------------------------------------------


def quote(symbol: str) -> dict:
    """The current bid, ask and last for one symbol."""
    symbol = (symbol or "").upper().strip()
    return _cached(f"uw:quote:{symbol}", f"/api/stock/{symbol}/quote",
                   ttl=TTL_QUOTE)


def _market_clock() -> dict:
    """The session clock every quote in this app carries."""
    try:
        import live_market_service as market

        return market.market_clock() or {}
    except Exception:  # noqa: BLE001
        return {}


def get_quote(symbol: str) -> Optional[dict]:
    """
    One symbol's quote, in the shape the fallback chain already reads.

    Same contract the OptionData client implemented, with one improvement:
    this carries bid and ask. The old provider published neither, so a quote
    served from it showed no spread at all and the app had to say so.
    """
    symbol = (symbol or "").upper().strip()
    out = quote(symbol)
    if out["status"] != "OK":
        return None

    data = out.get("data") or {}
    trade = data.get("last_trade") or {}
    book = data.get("quote") or {}
    price = _f(trade.get("price"))
    if price is None:
        return None

    bars = get_daily_bars(symbol, "5 D")
    day = bars[-1] if bars else {}
    # Previous close is the close of the last COMPLETED session. Whether that is
    # bars[-2] or bars[-1] depends on whether today's (still-forming) bar is
    # present: during and after the session it is, so the previous close is
    # bars[-2]; before it forms (pre-market) the latest bar is already the last
    # completed session, so it is bars[-1]. Always using bars[-2] made the
    # pre-market change compare against two sessions ago.
    prev_close = None
    if bars:
        try:
            from zoneinfo import ZoneInfo
            from datetime import datetime as _dt
            today = _dt.now(ZoneInfo("America/New_York")).date().isoformat()
        except Exception:  # noqa: BLE001 - tz db missing; degrade gracefully
            today = ""
        latest_is_today = str(day.get("date"))[:10] == today
        if latest_is_today:
            prev_close = _f(bars[-2].get("close")) if len(bars) > 1 else None
        else:
            prev_close = _f(day.get("close"))
    change = change_pct = None
    if prev_close:
        change = round(price - prev_close, 2)
        change_pct = round((price - prev_close) / prev_close * 100, 2)

    return {
        "symbol": symbol,
        "name": symbol,
        "exchange": None,
        "con_id": None,
        "tags": [],
        "price": round(price, 2),
        "change": change,
        "change_percent": change_pct,
        "previous_close": round(prev_close, 2) if prev_close else None,
        "bid": _f((book.get("bid") or {}).get("price")),
        "ask": _f((book.get("ask") or {}).get("price")),
        "open": _f(day.get("open")),
        "high": _f(day.get("high")),
        "low": _f(day.get("low")),
        "close": _f(day.get("close")),
        "volume": _f(trade.get("vol")) or _f(day.get("volume")),
        "average_volume": None,
        "market_cap": None,
        "session": data.get("market_time"),
        # Part of the quote contract, not decoration: screens render
        # "<session> - <time>" straight from it, and a quote without it took
        # the whole Earnings page down with "cannot read properties of
        # undefined" the moment TWS was not the one answering.
        "market": _market_clock(),
        "status": "OK",
        "source": SOURCE,
        "delayed": False,
    }


def candles(symbol: str, size: str = "1d", limit: int = 300,
            date: str = "") -> dict:
    """
    OHLC candles. ``size`` is their candle string: 1m, 5m, 15m, 1h, 1d.

    This is the piece that matters most for reliability: the app's intraday
    parameters went blank whenever TWS refused a request, because no other
    source served intraday bars. Now one does.
    """
    symbol = (symbol or "").upper().strip()
    ttl = TTL_SETTLED if date else (
        TTL_DAILY if size.endswith("d") else TTL_INTRADAY)
    return _cached(f"uw:ohlc:{symbol}:{size}:{limit}:{date}",
                   f"/api/stock/{symbol}/ohlc/{size}",
                   {"limit": limit, "date": date}, ttl=ttl)


# How long a duration string asks for, in trading days. Shared with the bar
# providers this stands in front of.
_DURATION_ROWS = {"1 D": 1, "2 D": 2, "5 D": 5, "10 D": 10, "1 M": 22,
                  "3 M": 64, "6 M": 126, "9 M": 190, "1 Y": 252, "2 Y": 504,
                  "5 Y": 1260, "6 Y": 1512, "20 Y": 5040}


def _duration_rows(duration: str) -> int:
    return _DURATION_ROWS.get((duration or "").strip().upper(), 252)


def get_daily_bars(symbol: str, duration: str = "1 Y") -> list[dict]:
    """
    Daily OHLCV, oldest first, in the shape the technical services expect.

    The same contract Twelve Data and Alpha Vantage implement, so this slots
    in ahead of them without any caller changing: [] means "no market data",
    never zero.
    """
    wanted = _duration_rows(duration)
    # Asked for generously: their limit counts *rows*, and one date can send
    # three (pre-market, regular, post) which collapse to one trading day
    # below. Asking for exactly as many rows as days wanted returned a third
    # of the history and quietly shortened every moving average.
    out = candles(symbol, "1d", limit=min(max(wanted * 3, 90), 5000))
    if out["status"] != "OK":
        return []

    rows = []
    for bar in _rows(out):
        day = str(bar.get("date") or bar.get("start_time") or "")[:10]
        close = _f(bar.get("close"))
        if not day or close is None:
            continue
        rows.append({
            "date": day,
            "open": _f(bar.get("open")) or close,
            "high": _f(bar.get("high")) or close,
            "low": _f(bar.get("low")) or close,
            "close": close,
            "volume": _f(bar.get("total_volume") or bar.get("volume")) or 0.0,
        })

    # One date can arrive twice: the regular session and the extended-hours
    # continuation are separate rows with the same date, and a duplicated day
    # would double-count in every moving average. The later row wins, since
    # it carries the fuller volume.
    merged: dict = {}
    for row in rows:
        merged[row["date"]] = row
    rows = [merged[d] for d in sorted(merged)]
    return rows[-wanted:] if len(rows) > wanted else rows


def get_intraday_bars(symbol: str, size: str = "5m",
                      days: int = 5) -> list[dict]:
    """
    Intraday bars, oldest first, stamped in UTC.

    This is the one the app had no substitute for. Every session parameter --
    VWAP, the opening range, relative volume -- is measured from intraday
    bars, and TWS was the only source of them: when it refused a request the
    parameters went blank and the outlook lost eight points of coverage with
    nothing to show for it.
    """
    per_day = {"1m": 960, "5m": 192, "15m": 64, "1h": 16}.get(size, 192)
    out = candles(symbol, size, limit=min(per_day * max(days, 1), 5000))
    if out["status"] != "OK":
        return []

    rows = []
    for bar in _rows(out):
        stamp = str(bar.get("start_time") or bar.get("date") or "")
        close = _f(bar.get("close"))
        if not stamp or close is None:
            continue
        rows.append({
            "t": stamp,
            "open": _f(bar.get("open")) or close,
            "high": _f(bar.get("high")) or close,
            "low": _f(bar.get("low")) or close,
            "close": close,
            "volume": _f(bar.get("volume")) or 0.0,
        })
    rows.sort(key=lambda r: r["t"])
    return rows


def stock_info(symbol: str) -> dict:
    """Sector, description, market cap and the rest of the company card."""
    symbol = (symbol or "").upper().strip()
    return _cached(f"uw:info:{symbol}", f"/api/stock/{symbol}/info")


# ---------------------------------------------------------------------------
# the options tape
# ---------------------------------------------------------------------------


def flow_alerts(symbol: str, limit: int = 50) -> dict:
    """Their own flow alerts for one ticker: the trades worth looking at."""
    symbol = (symbol or "").upper().strip()
    return _cached(f"uw:flowalerts:{symbol}:{limit}",
                   f"/api/stock/{symbol}/flow-alerts", {"limit": limit},
                   ttl=TTL_TAPE)


def unusual_activity(symbol: str = "", min_premium: int = 50000,
                     limit: int = 200, date: str = "") -> dict:
    """
    Contracts trading far above their own normal volume.

    Called without a symbol this is the market-wide scan, which is how the
    board should read it: one request covering every name rather than one
    per symbol.
    """
    symbol = (symbol or "").upper().strip()
    ttl = TTL_SETTLED if date else TTL_TAPE
    return _cached(f"uw:unusual:{symbol}:{min_premium}:{limit}:{date}",
                   "/api/option-activity/unusual",
                   {"ticker_symbol": symbol, "min_premium": min_premium,
                    "limit": limit, "date": date, "unusual": "true"}, ttl=ttl)


def oi_change(symbol: str, limit: int = 100, date: str = "") -> dict:
    """Overnight change in open interest: positions opened, not traded."""
    symbol = (symbol or "").upper().strip()
    return _cached(f"uw:oi:{symbol}:{limit}:{date}",
                   f"/api/stock/{symbol}/oi-change",
                   {"limit": limit, "date": date},
                   ttl=TTL_SETTLED if date else TTL_DAILY)


def iv_rank(symbol: str) -> dict:
    """Where implied volatility sits against its own year."""
    symbol = (symbol or "").upper().strip()
    return _cached(f"uw:ivrank:{symbol}", f"/api/stock/{symbol}/iv-rank",
                   ttl=TTL_INTRADAY)


def max_pain(symbol: str) -> dict:
    """The strike where the most open interest expires worthless."""
    symbol = (symbol or "").upper().strip()
    return _cached(f"uw:maxpain:{symbol}", f"/api/stock/{symbol}/max-pain")


def options_volume(symbol: str) -> dict:
    """Call and put volume, premium and open interest for the day."""
    symbol = (symbol or "").upper().strip()
    return _cached(f"uw:optvol:{symbol}", f"/api/stock/{symbol}/options-volume",
                   ttl=TTL_TAPE)


def net_premium_ticks(symbol: str) -> dict:
    """Net call and put premium through the session, minute by minute."""
    symbol = (symbol or "").upper().strip()
    return _cached(f"uw:ticks:{symbol}", f"/api/stock/{symbol}/net-prem-ticks",
                   ttl=TTL_TAPE)


def tape_rows(symbol: str = "", min_premium: int = 50000,
              limit: int = 200, date: str = "") -> dict:
    """
    The unusual tape in the shape this app has always read it.

    Same field names the OptionData and OptionsBell clients produced, so the
    Disparity readings and the flow parameters did not have to be rewritten
    around a new vocabulary. Two things here are better than what came
    before: the side split is theirs -- bid-side against ask-side volume,
    counted per contract -- rather than this app inferring it from a fill
    price, and the ratio is the day's volume against the open interest it
    traded against.
    """
    out = unusual_activity(symbol, min_premium, limit, date)
    if out["status"] != "OK":
        return {"symbol": symbol, "status": out["status"], "rows": [],
                "detail": out.get("detail"), "source": SOURCE}

    rows = []
    for r in _rows(out):
        volume = _f(r.get("volume")) or 0.0
        oi = _f(r.get("open_interest")) or 0.0
        ask, bid = _f(r.get("ask_side_volume")) or 0.0, _f(r.get("bid_side_volume")) or 0.0
        right = (str(r.get("option_type") or "")[:1]
                 or _right_from_symbol(str(r.get("option_symbol") or ""))).upper()
        rows.append({
            "symbol": (r.get("ticker_symbol") or symbol or "").upper(),
            "option_symbol": r.get("option_symbol"),
            "right": "P" if right == "P" else "C",
            "strike": _f(r.get("strike")),
            "expiry": r.get("expiry"),
            "volume": volume,
            "open_interest": oi,
            "premium": _f(r.get("premium")) or 0.0,
            "ratio": round(volume / oi, 2) if oi else None,
            # Their implied volatility is a fraction; every screen in this
            # app talks in percent, and 0.42 rendered as "0%".
            "iv": _pct(_f(r.get("prev_iv"))),
            "delta": _f(r.get("delta")),
            "gamma": _f(r.get("gamma")),
            # A sweep is one order filled across several exchanges at once:
            # urgency, in a way a single block is not.
            "sweep_like": bool(_f(r.get("sweep_volume"))),
            "ask_volume": ask,
            "bid_volume": bid,
            "side": ("ask" if ask > bid else "bid" if bid > ask else "mid"),
            "stock_price": _f(r.get("stock_price")),
            "last_fill": r.get("last_fill"),
        })

    rows.sort(key=lambda r: -(r["premium"] or 0))
    # Buying calls and selling puts is pressure one way; the reverse is the
    # other. Counted on premium, because size is what moves a market.
    bullish = sum(r["premium"] for r in rows
                  if (r["right"] == "C") == (r["side"] == "ask"))
    total = sum(r["premium"] for r in rows) or 0.0
    return {
        "symbol": symbol, "status": "OK", "rows": rows, "count": len(rows),
        "bullish_share": round(bullish / total * 100, 1) if total else None,
        "detail": ("Contracts trading far above their own normal volume, "
                   "with the side each one crossed on."),
        "source": SOURCE,
    }


def _pct(value: Optional[float]) -> Optional[float]:
    """A 0-1 fraction as 0-100, leaving an already-percentage figure alone."""
    if value is None:
        return None
    return round(value * 100, 2) if abs(value) <= 5 else round(value, 2)


def _right_from_symbol(option_symbol: str) -> str:
    """NVDA260923C00227500 -> C. Their OCC symbol carries it 9 from the end."""
    return option_symbol[-9:-8] if len(option_symbol) >= 9 else ""


# ---------------------------------------------------------------------------
# the three flows: the market, one stock, one chain
# ---------------------------------------------------------------------------


def market_tide(date: str = "") -> dict:
    """
    Where the whole market's option premium is going, through the session.

    Net call and put premium minute by minute. The Market Flow screen was
    assembling an approximation of this out of per-symbol calls; this is the
    measurement itself.
    """
    return _cached("uw:tide:" + date, "/api/market/market-tide",
                   {"date": date}, ttl=TTL_SETTLED if date else TTL_TAPE)


def sector_tide(sector: str, date: str = "") -> dict:
    """The same reading, for one sector."""
    return _cached("uw:sectortide:" + sector + ":" + date,
                   "/api/market/" + urllib.parse.quote(sector) + "/sector-tide",
                   {"date": date}, ttl=TTL_SETTLED if date else TTL_TAPE)


def total_options_volume(sessions: int = 1) -> dict:
    """
    Market-wide call and put volume and premium, newest session first.

    History comes from ``limit``, not from a date: their endpoint has no date
    parameter, and passing one is accepted and ignored -- which silently
    returned today's row for yesterday and made "today against the last
    session" compare today with itself.
    """
    return _cached("uw:totalvol:%d" % sessions,
                   "/api/market/total-options-volume",
                   {"limit": max(1, sessions)}, ttl=TTL_TAPE)


def options_pulse_top(limit: int = 50) -> dict:
    """The names the option market is actually busy in right now."""
    return _cached("uw:pulse:%d" % limit, "/api/options-pulse/top",
                   {"limit": limit}, ttl=TTL_TAPE)


def stock_flow(symbol: str, limit: int = 100) -> dict:
    """Recent option flow for one stock: its tape as it prints."""
    symbol = (symbol or "").upper().strip()
    return _cached("uw:flowrecent:%s:%d" % (symbol, limit),
                   "/api/stock/%s/flow-recent" % symbol, {"limit": limit},
                   ttl=TTL_TAPE)


def flow_per_expiry(symbol: str) -> dict:
    """One stock's flow split by expiry: near-dated, or further out."""
    symbol = (symbol or "").upper().strip()
    return _cached("uw:flowexp:" + symbol,
                   "/api/stock/%s/flow-per-expiry" % symbol, ttl=TTL_TAPE)


def flow_per_strike(symbol: str) -> dict:
    """The same by strike: where along the ladder the premium is going."""
    symbol = (symbol or "").upper().strip()
    return _cached("uw:flowstrike:" + symbol,
                   "/api/stock/%s/flow-per-strike" % symbol, ttl=TTL_TAPE)


def option_chain(symbol: str, expiry: str = "") -> dict:
    """
    The option chain: every contract, with volume, open interest and IV.

    Their chain carries the day's volume and the greeks per contract, which
    is what the key-level and open-interest readings are built from.
    """
    symbol = (symbol or "").upper().strip()
    return _cached("uw:chain:%s:%s" % (symbol, expiry),
                   "/api/stock/%s/option-chains" % symbol,
                   {"expiry": expiry} if expiry else None, ttl=TTL_INTRADAY)


def option_contracts(symbol: str, expiry: str = "", limit: int = 1500) -> dict:
    """Every contract with its quote, volume, open interest and greeks."""
    symbol = (symbol or "").upper().strip()
    params = {"limit": limit}
    if expiry:
        params["expiry"] = expiry
    return _cached("uw:contracts:%s:%s:%d" % (symbol, expiry, limit),
                   "/api/stock/%s/option-contracts" % symbol, params,
                   ttl=TTL_INTRADAY)


def load_chain(symbol: str, expiry: str = "") -> Optional[dict]:
    """
    One expiry's chain, in the shape the options screens already render.

    The same contract the OptionData client implemented, so the Options page,
    the key-level readings and the earnings screens did not change. Their
    greeks are published rather than solved locally, and each contract also
    carries the day's bid-side and ask-side volume, which is what the flow
    readings are built from.
    """
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return None

    dates = [str(r.get("expires") or "")[:10]
             for r in _rows(expirations(symbol)) if r.get("expires")]
    dates = sorted({d for d in dates if d})
    front = expiry or (dates[0] if dates else "")
    if not front:
        return None

    out = option_contracts(symbol, front)
    if out["status"] != "OK":
        return None

    spot = None
    quote_out = quote(symbol)
    if quote_out["status"] == "OK":
        spot = _f(((quote_out.get("data") or {}).get("last_trade") or {}).get("price"))

    try:
        from datetime import date as _date, datetime as _datetime
        dte = max((_datetime.strptime(front, "%Y-%m-%d").date()
                   - _date.today()).days, 0)
    except (TypeError, ValueError):
        dte = 0

    rows = []
    as_of = ""  # newest per-contract tape time = how fresh the chain really is
    for r in _rows(out):
        tape = str(r.get("last_tape_time") or "")
        if tape > as_of:
            as_of = tape
        option_symbol = str(r.get("option_symbol") or "")
        if not option_symbol.endswith(tuple("0123456789")):
            continue
        strike = _strike_from_symbol(option_symbol)
        if strike is None:
            continue
        bid, ask = _f(r.get("nbbo_bid")), _f(r.get("nbbo_ask"))
        last = _f(r.get("last_price"))
        mid = round((bid + ask) / 2, 4) if bid is not None and ask is not None else last
        volume = _f(r.get("volume")) or 0.0
        oi = _f(r.get("open_interest")) or 0.0
        iv = _f(r.get("implied_volatility"))
        rows.append({
            "con_id": option_symbol,
            "symbol": symbol,
            "expiry": front,
            "expiry_label": front,
            "dte": dte,
            "strike": strike,
            "right": _right_from_symbol(option_symbol) or "C",
            "bid": bid, "ask": ask, "last": last, "close": last, "mid": mid,
            "volume": volume,
            "open_interest": oi,
            "oi_change": (oi - (_f(r.get("prev_oi")) or 0.0)),
            "iv": round(iv, 4) if iv else None,
            "notional": round(volume * (mid or 0.0) * 100, 2),
            "oi_notional": round(oi * (mid or 0.0) * 100, 2),
            # Published, not solved here.
            "delta": _f(r.get("delta")),
            "gamma": _f(r.get("gamma")),
            "theta": _f(r.get("theta")),
            "vega": _f(r.get("vega")),
            "rho": _f(r.get("rho")),
            "bid_volume": _f(r.get("bid_volume")),
            "ask_volume": _f(r.get("ask_volume")),
            "premium": _f(r.get("total_premium")),
        })

    if not rows:
        return None
    rows.sort(key=lambda r: (r["strike"], r["right"]))

    return {
        "symbol": symbol,
        "spot": round(spot, 2) if spot else None,
        "expiry": front,
        "expiry_label": front,
        "dte": dte,
        "expirations": dates[:24],
        "expiration_labels": dates[:24],
        "rows": rows,
        # Only the front expiry is quoted here. The key must still exist:
        # callers concatenate it unconditionally.
        "other_expiry_rows": [],
        "multiplier": 100,
        # When the newest contract last printed. The freshness badge reads
        # this to say whether the chain is live or how far behind it is,
        # instead of assuming a fixed delay.
        "as_of": as_of or None,
        "status": "OK",
        "source": SOURCE,
    }


def _strike_from_symbol(option_symbol: str) -> Optional[float]:
    """NVDA260923C00227500 -> 227.5. The last eight digits are thousandths."""
    tail = option_symbol[-8:]
    if not tail.isdigit():
        return None
    return int(tail) / 1000.0


def expirations(symbol: str) -> dict:
    """Which expiries this stock has, with volume and open interest on each."""
    symbol = (symbol or "").upper().strip()
    return _cached("uw:expiries:" + symbol,
                   "/api/stock/%s/option/volume-oi-expiry" % symbol,
                   ttl=TTL_INTRADAY)


def darkpool(symbol: str, limit: int = 100) -> dict:
    """Off-exchange prints for one symbol."""
    symbol = (symbol or "").upper().strip()
    return _cached(f"uw:dark:{symbol}:{limit}", f"/api/darkpool/{symbol}",
                   {"limit": limit}, ttl=TTL_TAPE)


# ---------------------------------------------------------------------------
# the company
# ---------------------------------------------------------------------------


def earnings_history(symbol: str) -> dict:
    """Reported quarters: estimate, actual, and the market's answer."""
    symbol = (symbol or "").upper().strip()
    return _cached(f"uw:earnings:{symbol}", f"/api/stock/{symbol}/earnings")


def earnings_estimates(symbol: str) -> dict:
    """What analysts expect next quarter."""
    symbol = (symbol or "").upper().strip()
    return _cached(f"uw:estimates:{symbol}",
                   f"/api/companies/{symbol}/earnings-estimates")


def dividends(symbol: str) -> dict:
    """Declared dividends, newest first."""
    symbol = (symbol or "").upper().strip()
    return _cached(f"uw:dividends:{symbol}",
                   f"/api/companies/{symbol}/dividends")


def analyst_actions(symbol: str, limit: int = 50) -> dict:
    """Upgrades, downgrades and target changes."""
    symbol = (symbol or "").upper().strip()
    return _cached(f"uw:analysts:{symbol}:{limit}", "/api/screener/analysts",
                   {"ticker": symbol, "limit": limit})


def insider_trades(symbol: str) -> dict:
    """Officers' and directors' own trades, as filed."""
    symbol = (symbol or "").upper().strip()
    return _cached(f"uw:insider:{symbol}", f"/api/insider/{symbol}")


def institutional_ownership(symbol: str, limit: int = 100) -> dict:
    """Who holds it, and how that changed."""
    symbol = (symbol or "").upper().strip()
    return _cached(f"uw:owners:{symbol}:{limit}",
                   f"/api/institution/{symbol}/ownership", {"limit": limit})


def headlines(symbol: str = "", limit: int = 50) -> dict:
    """Market headlines, optionally for one ticker."""
    symbol = (symbol or "").upper().strip()
    return _cached(f"uw:news:{symbol}:{limit}", "/api/news/headlines",
                   {"ticker": symbol, "limit": limit}, ttl=300.0)


def fundamentals(symbol: str) -> dict:
    """Valuation, margins and growth for one company."""
    symbol = (symbol or "").upper().strip()
    return _cached(f"uw:fundamentals:{symbol}",
                   f"/api/stock/{symbol}/fundamental-breakdown")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def budget() -> dict:
    """What is left, for the settings screen. Never the token."""
    return {
        "day": _spent["day"],
        "used_by_this_app": _spent["count"],
        "app_budget": APP_DAILY_BUDGET,
        "app_left": max(0, APP_DAILY_BUDGET - _spent["count"]),
        "plan_minute_remaining": _limits["remaining"],
        "blocked": time.time() < _blocked_until,
        "blocked_for_seconds": max(0, round(_blocked_until - time.time())),
    }


def provider_status() -> dict:
    status = ("PROVIDER_NOT_CONFIGURED" if not configured()
              else _last_status or "UNKNOWN")
    return {"provider": "Unusual Whales", "configured": configured(),
            "status": status, "env_var": ENV_KEY,
            "signup": "https://unusualwhales.com/api", **budget()}
