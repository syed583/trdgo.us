"""
Live market data sourced from IBKR.

Everything here is real: quotes, OHLCV history, EMAs, index levels and the
US market session clock. Nothing is synthesised. When a value cannot be
obtained it comes back as ``None`` with a status field explaining why.
"""

from __future__ import annotations

import math
import os
import threading
import time
from datetime import date, datetime, time as dtime, timedelta
from collections import OrderedDict
from typing import Any, Optional

import price_levels as _levels
from zoneinfo import ZoneInfo


from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout



EASTERN = ZoneInfo("America/New_York")

# Parallel fan-out for fallback quotes. Each provider keeps its own pacing, so
# this bounds concurrency rather than overriding any rate limit.
BATCH_QUOTE_WORKERS = 8

# Wall-clock ceiling for one fallback batch. Sized so that an IBKR batch that
# spends its own budget and then falls back still answers inside the proxy's
# 120s limit.
FALLBACK_BATCH_BUDGET = 25.0

RTH_OPEN = dtime(9, 30)
RTH_CLOSE = dtime(16, 0)
PRE_OPEN = dtime(4, 0)
AFTER_CLOSE = dtime(20, 0)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def num(value: Any) -> Optional[float]:
    """IBKR uses nan and -1 as 'no value'. Normalise both to None."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    if f == -1.0:
        return None
    return f


def _first_num(*values: Any) -> Optional[float]:
    for v in values:
        n = num(v)
        if n is not None:
            return n
    return None


def ema(values: list[float], period: int) -> list[Optional[float]]:
    """Standard EMA seeded with the SMA of the first ``period`` values."""
    out: list[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


# ---------------------------------------------------------------------------
# TTL cache
# ---------------------------------------------------------------------------


class _TTLCache:
    # A hard ceiling on entries. Cache keys are built from request input
    # (symbols, ranges), so without a cap a caller asking for endless distinct
    # symbols would grow this without bound. At the ceiling the oldest-written
    # entries are dropped -- an LRU-by-insertion, which is enough because
    # every entry expires by TTL anyway; this only bounds a flood.
    _MAX_ENTRIES = 5000

    def __init__(self) -> None:
        self._data: "OrderedDict[str, tuple[float, Any]]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str, ttl: float) -> Any:
        with self._lock:
            hit = self._data.get(key)
        if not hit:
            return None
        stamped, value = hit
        if (time.time() - stamped) > ttl:
            return None
        return value

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = (time.time(), value)
            self._data.move_to_end(key)
            while len(self._data) > self._MAX_ENTRIES:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def purge(self, token: str, keep: tuple[str, ...] = ()) -> int:
        """
        Drop every entry belonging to one symbol. Returns how many went.

        Used when a reader explicitly asks for a fresh analysis: the point of
        pressing Analyse is to re-read the market, and serving that request
        from a cache filled ten minutes ago answers a different question.
        Matched on the symbol surrounded by a delimiter so purging "C" cannot
        take "CRM" and "MSFT" with it.

        ``keep`` names cache families to leave alone -- the ones holding data
        that cannot have moved since the last read, where re-fetching costs
        time and changes nothing.
        """
        needle = token.strip().upper()
        if not needle:
            return 0
        with self._lock:
            doomed = []
            for key in self._data:
                parts = {
                    part.upper()
                    for chunk in key.split(":")
                    for part in chunk.split("?")
                }
                if needle not in parts:
                    continue
                if any(k.upper() in parts for k in keep):
                    continue
                doomed.append(key)
            for key in doomed:
                del self._data[key]
        return len(doomed)


cache = _TTLCache()


# ---------------------------------------------------------------------------
# market session clock
# ---------------------------------------------------------------------------


def market_clock() -> dict:
    now = datetime.now(EASTERN)
    t = now.time()
    weekday = now.weekday() < 5

    if not weekday:
        session = "CLOSED"
    elif RTH_OPEN <= t < RTH_CLOSE:
        session = "OPEN"
    elif PRE_OPEN <= t < RTH_OPEN:
        session = "PRE_MARKET"
    elif RTH_CLOSE <= t < AFTER_CLOSE:
        session = "AFTER_HOURS"
    else:
        session = "CLOSED"

    return {
        "session": session,
        "is_open": session == "OPEN",
        "label": {
            "OPEN": "Open",
            "PRE_MARKET": "Pre-Market",
            "AFTER_HOURS": "After Hours",
            "CLOSED": "Closed",
        }[session],
        "time_et": now.strftime("%I:%M %p ET").lstrip("0"),
        "date_et": now.strftime("%b %d, %Y"),
        "iso": now.isoformat(),
    }


# Sessions where the last regular-hours close is no longer the current price.
EXTENDED_SESSIONS = {"PRE_MARKET", "AFTER_HOURS"}

# Sessions where a live print exists at all. Outside these the last close is
# genuinely the current price and there is nothing to overlay.
TRADING_SESSIONS = EXTENDED_SESSIONS | {"OPEN"}

def session_ttl(open_secs: float, closed_secs: float) -> float:
    """
    Cache window that follows the trading session.

    Nothing on a quote, chart or index can change while the market is shut,
    so refetching on the open-market cadence outside RTH just burns TWS
    round trips and makes every page slow.

    Pre- and post-market are the exception: prices move in those sessions, and
    treating them as "closed" pinned the extended quote for five minutes at a
    time -- stale enough that it was barely worth showing.
    """
    session = market_clock().get("session")
    if session == "OPEN":
        return open_secs
    if session in EXTENDED_SESSIONS:
        # Thinner than regular hours, but still a live tape.
        return max(open_secs, min(closed_secs, 20.0))
    return closed_secs


def _session_dates() -> dict:
    """The Today / Tomorrow / This Week ... ranges used by the date strip."""
    today = datetime.now(EASTERN).date()
    monday = today - timedelta(days=today.weekday())
    friday = monday + timedelta(days=4)
    next_monday = monday + timedelta(days=7)
    next_friday = next_monday + timedelta(days=4)

    def f(d: date) -> str:
        return f"{d.strftime('%b')} {d.day}"

    month_start = today.replace(day=1)
    month_end = (month_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)

    windows = {
        "today": ("TODAY", "Today", f(today), today, today),
        "tomorrow": ("TOMORROW", "Tomorrow", f(today + timedelta(days=1)),
                     today + timedelta(days=1), today + timedelta(days=1)),
        "this_week": ("THIS_WEEK", "This Week", f"{f(monday)} - {f(friday)}",
                      monday, monday + timedelta(days=6)),
        "next_week": ("NEXT_WEEK", "Next Week",
                      f"{f(next_monday)} - {f(next_friday)}",
                      next_monday, next_monday + timedelta(days=6)),
        "this_month": ("THIS_MONTH", "This Month", today.strftime("%B %Y"),
                       month_start, month_end),
    }

    # How many companies actually report in each window. The strip used to
    # show only a date range, which told the operator nothing about whether
    # anything was happening in it.
    #
    # Six weeks past month end, not the year this used to ask for. The old
    # window was free against a local calendar cache; the feed that replaced
    # it is read a day at a time, so a year was forty round trips -- 48
    # seconds, spent on every cold load of the Market Overview, to fill four
    # counts and a "next earnings" date. Forty weekday requests was also the
    # feed's own ceiling, so the year was never really covered.
    counts = _earnings_counts(min(w[3] for w in windows.values()),
                              month_end + timedelta(days=42))

    out = {}
    for slot, (key, label, value, start, end) in windows.items():
        rows = [r for r in counts if start <= r[0] <= end]
        out[slot] = {
            "key": key,
            "label": label,
            "value": value,
            "earnings_count": len(rows),
            # A couple of names make the card scannable without opening it.
            "earnings_symbols": [r[1] for r in rows[:4]],
            "range_start": start.isoformat(),
            "range_end": end.isoformat(),
        }

    # Every window can legitimately be empty -- Dow-30 names report in
    # January/April/July/October, so a September strip of zeros is correct but
    # useless. Point at the next real date instead of just saying "none".
    ahead = [r for r in counts if r[0] >= today]
    if ahead:
        first_date = ahead[0][0]
        same_day = [r[1] for r in ahead if r[0] == first_date]
        out["next_up"] = {
            "key": "NEXT_UP",
            "label": "Next Earnings",
            "value": f(first_date),
            "earnings_count": len(same_day),
            "earnings_symbols": same_day[:4],
            "days_away": (first_date - today).days,
            "range_start": first_date.isoformat(),
            "range_end": first_date.isoformat(),
        }
    return out


def _earnings_counts(start: date, end: date) -> list[tuple]:
    """[(date, symbol)] from the earnings feed, or [] if it is unavailable."""
    # Cached across callers. Who reports next week does not change between
    # two page loads, and this is the single most expensive thing behind the
    # date strip -- one request per weekday in the window.
    key = f"earnings_counts:{start}:{end}"
    cached = cache.get(key, session_ttl(1800.0, 21600.0))
    if cached is not None:
        return [(date.fromisoformat(d), sym) for d, sym in cached]

    try:
        import uw_earnings_feed as feed

        rows = feed.get_upcoming(start, end).get("rows") or []
    except Exception:  # noqa: BLE001 - the strip must render without a provider
        return []

    out = []
    for row in rows:
        raw = row.get("date")
        if not raw:
            continue
        try:
            out.append((date.fromisoformat(str(raw)[:10]),
                        str(row.get("symbol") or "").upper()))
        except ValueError:
            continue
    out.sort()
    cache.put(key, [(d.isoformat(), sym) for d, sym in out])
    return out


# ---------------------------------------------------------------------------
# quote
# ---------------------------------------------------------------------------



# Why the most recent live-price attempt failed, per symbol. Surfaced on the
# quote so a stale price can be explained rather than silently accepted.
_LIVE_FAILURES: dict[str, str] = {}


def _note_live_failure(symbol: str, reason: str) -> None:
    _LIVE_FAILURES[symbol.upper()] = reason


def live_price_status(symbol: str) -> Optional[str]:
    """The last reason a live price could not be had for this symbol."""
    return _LIVE_FAILURES.get(symbol.upper())


# A user waiting on a page gets a short budget; the background warmer gets a
# long one. The same call serves both, because the difference is only how
# long it is worth blocking for -- nobody should wait twenty-five seconds for
# a quote, but a warmer with nothing else to do certainly can.
LIVE_PRICE_BUDGET_REQUEST = 4.0
LIVE_PRICE_BUDGET_BACKGROUND = 25.0


def extended_hours_quote(symbol: str,
                         reference_close: Optional[float] = None,
                         timeout: float = LIVE_PRICE_BUDGET_REQUEST) -> Optional[dict]:
    """
    The pre- or post-market price.

    This was TWS's job, and TWS was the only source here that quoted the
    extended sessions live. What replaces it is the feed's own last print,
    which is a weaker claim and is treated as one: it is only used during
    regular hours, because outside them the last trade *is* the closing
    print, and presenting that as a pre-market quote would invent a session
    that has not traded yet.

    So between four and nine-thirty, and again after the close, this now
    returns None where it used to return a real extended print. The caller
    keeps the last completed session's close and the screen labels it as
    such, which is the honest reading -- not a gap, but not a live quote
    either.

    ``reference_close`` is supplied by the caller: the feed's snapshot
    already carries the last completed session's close, which is exactly the
    right reference to measure a move against.
    """
    symbol = symbol.upper()
    clock = market_clock()
    if clock.get("session") not in TRADING_SESSIONS:
        return None

    key = f"ext:{symbol}:{reference_close}"
    cached = cache.get(key, 20.0)
    if cached is not None:
        return cached or None

    result = None
    if reference_close:
        result = _provider_live_price(symbol, reference_close, clock)
    if not result:
        _note_live_failure(symbol, "No live print outside the regular session")

    cache.put(key, result or {})
    return result


def _provider_live_price(symbol: str, reference_close: float,
                         clock: dict) -> Optional[dict]:
    """
    The feed's live last price.

    Regular hours only. The endpoint reports the last trade, which outside
    RTH is the closing print -- passing it off as a pre-market quote would
    invent a session that has not traded.
    """
    if clock.get("session") != "OPEN":
        return None

    try:
        import unusualwhales_service as uw

        price = (uw.get_quote(symbol) or {}).get("price")
    except Exception:  # noqa: BLE001
        return None

    if not price or abs(price - reference_close) < 1e-9:
        return None

    change = price - reference_close
    return {
        "price": round(price, 2),
        "previous_close": round(reference_close, 2),
        "change": round(change, 2),
        "change_percent": round(change / reference_close * 100, 2),
        # No book on this endpoint; only the last trade.
        "bid": None,
        "ask": None,
        "session": clock.get("session"),
        "session_label": clock.get("label"),
        "as_of": None,
        "source": "UNUSUAL_WHALES",
    }


def get_quote(symbol: str, ttl: Optional[float] = None,
              live_timeout: float = LIVE_PRICE_BUDGET_REQUEST) -> dict:
    symbol = symbol.upper()
    key = f"quote:{symbol}"
    cached = cache.get(key, ttl if ttl is not None else session_ttl(5.0, 300.0))
    if cached:
        return cached

    # The provider first when preferred: it is real time and
    # answers in ~0.3s. The TWS path qualifies the contract, pulls five days of
    # history and waits 4s for the market-data line to settle -- about 15s per
    # symbol, which is what made opening a new ticker feel stuck.
    if _feed_quotes_first():
        import freshness
        import unusualwhales_service as od

        fast = od.get_quote(symbol)
        if fast:
            # The provider snapshot is dated to the last completed session, so
            # outside regular hours its "price" is the previous close. The
            # extended print is attached alongside rather than replacing it --
            # both are true, and the screen needs to distinguish them.
            # The provider snapshot is dated to the last completed session --
            # its "price" is that session's close whatever the clock says. TWS
            # has the live print, so it supplies the price and the provider
            # supplies everything around it.
            session = (market_clock() or {}).get("session")

            if session == "OPEN":
                # During regular hours the live print *is* the price, measured
                # against the prior close. Leaving the stale figure in place
                # would show yesterday's number all day.
                live = extended_hours_quote(
                    symbol, num(fast.get("close")) or num(fast.get("previous_close")),
                    timeout=live_timeout)
                if live:
                    fast = {
                        **fast,
                        "price": live["price"],
                        "change": live["change"],
                        "change_percent": live["change_percent"],
                        "bid": live.get("bid") or fast.get("bid"),
                        "ask": live.get("ask") or fast.get("ask"),
                        "as_of": live.get("as_of") or fast.get("as_of"),
                        "price_source": live.get("source", "UNUSUAL_WHALES"),
                    }
                else:
                    # The figure below is the last completed session's close,
                    # not a live price, and the screen should be able to say so.
                    fast = {
                        **fast,
                        "price_source": "PROVIDER_SNAPSHOT",
                        "price_stale_reason": live_price_status(symbol),
                    }
            elif session in EXTENDED_SESSIONS:
                # Outside regular hours the close stays the headline and the
                # extended print sits beside it; they mean different things.
                extended = extended_hours_quote(
                    symbol, num(fast.get("close")) or num(fast.get("price")),
                    timeout=live_timeout)
                if extended:
                    fast = {**fast, "extended": extended}

            fast["freshness"] = freshness.for_quote(
                fast.get("price_source") or fast.get("source") or "",
                (market_clock() or {}).get("session") or "")
            cache.put(key, fast)
            return fast

    # The feed is the only source now. The block that stood here opened a
    # market-data line, waited four seconds for it to settle and pulled five
    # days of history to find the prior close -- about fifteen seconds a
    # symbol, which is what made opening a new ticker feel stuck.
    fallback = _fallback_quote(symbol)
    if fallback:
        cache.put(key, fallback)
        return fallback
    return _offline_quote(symbol, "The feed did not return a quote.")


def _offline_quote(symbol: str, error: str) -> dict:
    return {
        "symbol": symbol,
        "name": symbol,
        "tags": [],
        "price": None,
        "change": None,
        "change_percent": None,
        "previous_close": None,
        "bid": None,
        "ask": None,
        "open": None,
        "high": None,
        "low": None,
        "close": None,
        "volume": None,
        "market": market_clock(),
        "status": "DATA_UNAVAILABLE",
        "source": "UNUSUAL_WHALES",
        "error": error,
    }


# ---------------------------------------------------------------------------
# chart / history
# ---------------------------------------------------------------------------

# range -> (duration requested from IB, bar size, how many bars to keep)
RANGE_SPECS: dict[str, tuple[str, str, Optional[int]]] = {
    "1D": ("2 D", "5 mins", 78),
    # Five sessions of 15-minute bars including extended hours (04:00-20:00,
    # 64 bars a day). This kept 130, which is two sessions -- so the 5D chart
    # showed two days, and anything measuring "the same time on earlier days"
    # had one day to compare with, or none.
    "5D": ("10 D", "15 mins", 320),
    "1M": ("3 M", "1 day", 22),
    "3M": ("9 M", "1 day", 64),
    "6M": ("1 Y", "1 day", 126),
    "YTD": ("2 Y", "1 day", None),
    "1Y": ("2 Y", "1 day", 252),
    "5Y": ("6 Y", "1 week", 261),
    "ALL": ("20 Y", "1 month", None),
}


def _bar_providers():
    """
    Fallback bar sources, best first.

    One feed. Twelve Data and Alpha Vantage sat behind this on free tiers of
    800 and 25 requests a day and were removed: a fallback that only fires
    when the paid feed is down would be carrying a price bar onto a screen
    whose tape, chain and quotes had already gone with it.
    """
    import unusualwhales_service as uw

    return [uw] if uw.configured() else []


def _live_today_bar(symbol: str) -> Optional[dict]:
    """
    Today's session as a bar, from the live quote.

    The provider publishes open/high/low/last/volume for the current session,
    so the newest candle can be live even though the history behind it is
    end-of-day. Without this, technicals computed at 2pm would still be
    reading yesterday's close as the latest price.
    """
    import unusualwhales_service as od

    if not od.configured():
        return None
    quote = od.get_quote(symbol)
    if not quote or quote.get("price") is None:
        return None

    stamp = datetime.now(EASTERN).date().isoformat()
    close = quote["price"]
    open_ = quote.get("open")
    high = quote.get("high")
    low = quote.get("low")
    if open_ is None or high is None or low is None:
        return None
    return {
        "date": stamp,
        "open": float(open_),
        # The last trade can print outside the session range the provider
        # reported a moment earlier; keep the candle self-consistent.
        "high": float(max(high, close)),
        "low": float(min(low, close)),
        "close": float(close),
        "volume": float(quote.get("volume") or 0.0),
    }


def _fallback_bars(symbol: str, duration: str) -> tuple[list[dict], Optional[str]]:
    """
    (bars, source) from the first provider that answers, else ([], None).

    The history is end-of-day, but the final candle is replaced with the live
    session when a real-time quote is available -- so trend, RSI and the EMAs
    reflect today rather than lagging a day behind.
    """
    for provider in _bar_providers():
        bars = provider.get_daily_bars(symbol, duration)
        if not bars:
            continue

        today = _live_today_bar(symbol)
        if today:
            if bars[-1]["date"] == today["date"]:
                bars = bars[:-1] + [today]
            elif today["date"] > bars[-1]["date"]:
                bars = bars + [today]
        return bars, provider.SOURCE

    # Last resort: the daily chart the app already serves. Twelve Data's free
    # tier answers 429 once the day's requests are gone and Alpha Vantage's is
    # twenty-five a day, so on a busy afternoon both went quiet -- and with
    # them went EMA, RSI and the whole price-action parameter, which read as
    # "not enough history" on a stock with a year of it sitting in cache.
    #
    # The chart is the same daily history, from IBKR or whichever provider
    # answered last, and it keeps its own cache -- so this costs nothing and
    # is often fresher than the fallbacks above.
    try:
        chart = get_chart(symbol, "1Y") or {}
        rows = chart.get("bars") or []
        bars = [{
            "date": row.get("t") or row.get("date"),
            "open": row.get("open"), "high": row.get("high"),
            "low": row.get("low"), "close": row.get("close"),
            "volume": row.get("volume") or 0,
        } for row in rows
            if row.get("close") is not None and (row.get("t") or row.get("date"))]
        if len(bars) >= 20:
            return bars, chart.get("source") or "CHART"
    except Exception:  # noqa: BLE001 - a missing chart is not an error here
        pass
    return [], None


def _fallback_batch(symbols: list[str]) -> dict:
    """
    Per-symbol rows from the quote chain, shaped like get_batch's output.

    Bars come with the row. They used to be left out to save a request each
    -- the reasoning being that the strip needs prices far more often than
    sparklines -- but with TWS off this is the only path the Market Overview
    ever takes, and a card with no bars has no moving averages either: all
    fifteen of them read "Insufficient data" for their trend while showing a
    perfectly good price. One cached daily-bar call per symbol, fetched in
    the same parallel pass as the quote, is worth that.
    """
    def one(symbol: str) -> tuple[str, dict]:
        quote = _fallback_quote(symbol)
        if not quote:
            return symbol, {
                "symbol": symbol, "price": None, "change": None,
                "change_percent": None, "bars": [],
                "status": "DATA_UNAVAILABLE",
            }
        bars, _source = _fallback_bars(symbol, "1 Y")
        return symbol, {
            "symbol": symbol,
            "price": quote.get("price"),
            "change": quote.get("change"),
            "change_percent": quote.get("change_percent"),
            "bars": bars or [],
            "status": "OK",
            "source": quote.get("source"),
            "delayed": quote.get("delayed"),
        }

    # Fetched in parallel: the dashboard asks for the strip, eleven sector
    # ETFs and the calendar's symbols in one render, and one-at-a-time that
    # was ~44s of pure round-trip latency.
    #
    # Collected in completion order under a deadline rather than with
    # ``pool.map``, which waits for the slowest symbol. Twenty-four symbols
    # across three waves of a slow provider took 83s that way -- long enough
    # on its own to blow the proxy's 120s ceiling after an IBKR timeout had
    # already spent its budget. A symbol that misses the deadline is reported
    # as pending, not as having no price.
    out: dict[str, dict] = {}
    if symbols:
        deadline = time.time() + FALLBACK_BATCH_BUDGET
        pool = ThreadPoolExecutor(
            max_workers=min(BATCH_QUOTE_WORKERS, len(symbols)),
            thread_name_prefix="quote-batch")
        futures = {pool.submit(one, s): s for s in symbols}
        try:
            for future in as_completed(
                    futures, timeout=max(deadline - time.time(), 0.1)):
                try:
                    symbol, row = future.result()
                except Exception:  # noqa: BLE001 - one symbol is not fatal
                    continue
                out[symbol] = row
        except FuturesTimeout:
            pass
        finally:
            # Not a ``with`` block: the context manager joins every worker on
            # exit, so the threads still waiting on a slow provider would run
            # the deadline out anyway and the budget above would do nothing.
            # Abandon them instead and answer with what arrived.
            pool.shutdown(wait=False, cancel_futures=True)
        for symbol in symbols:
            out.setdefault(symbol, {
                "symbol": symbol, "price": None, "change": None,
                "change_percent": None, "bars": [],
                "status": "SLOW_PROVIDER",
            })
    return out if any(r.get("price") is not None for r in out.values()) else {}


def _feed_quotes_first() -> bool:
    """
    Whether to take the provider's quote path.

    Always, now. This gate existed when TWS was the primary quote source and
    the provider was a fallback behind it; with TWS removed the provider is
    the only source, so the provider path -- which handles the session clock
    and the freshness badge -- must always run. Left as a function (rather
    than deleted) so the one caller reads the same, and set
    QUOTES_PREFER_PROVIDER=0 only to force the bare fallback path for a test.
    """
    return os.getenv("QUOTES_PREFER_PROVIDER", "1").strip() not in ("0", "false", "no")


def _quote_providers():
    """
    Fallback quote sources, best first.

    One feed serves both the live quote and the bar history, so this list
    and _bar_providers now name the same module. De-duplicated rather than
    left to repeat: a symbol the feed does not carry -- SPX, NDX, VIX on the
    index strip -- was looked up, missed, and looked up again.
    """
    import unusualwhales_service as uw

    seen, out = set(), []
    for provider in ([uw] if uw.configured() else []) + _bar_providers():
        if id(provider) not in seen:
            seen.add(id(provider))
            out.append(provider)
    return out


# A provider quote is worth keeping for a few seconds.
#
# This had no cache at all, so every caller went back to the network for a
# figure that had just been fetched -- and the market overview asks for
# seventy of them on a sixty-second cycle. Short enough during the session
# that nobody is reading a stale price; long enough that one page load does
# not re-ask for what another just answered.
FALLBACK_QUOTE_TTL_OPEN = 20.0
FALLBACK_QUOTE_TTL_CLOSED = 900.0


def _fallback_quote(symbol: str) -> Optional[dict]:
    """The first provider quote that comes back, or None."""
    key = f"fbq:{symbol.upper()}"
    ttl = session_ttl(FALLBACK_QUOTE_TTL_OPEN, FALLBACK_QUOTE_TTL_CLOSED)
    cached = cache.get(key, ttl)
    if cached:
        return cached

    for provider in _quote_providers():
        quote = provider.get_quote(symbol)
        if quote:
            cache.put(key, quote)
            return quote
    return None


def _bars_fallback_note(intraday: bool) -> str:
    """Why the Alpha Vantage bar fallback could not cover for IBKR."""
    import provider_config as cfg

    providers = _bar_providers()
    if not providers:
        return ("No bar source configured: set UNUSUAL_WHALES_API_KEY "
                "in backend/.env to chart without TWS.")
    if intraday:
        return ("Fallback providers serve daily bars only, so intraday "
                "ranges need TWS.")

    # Read each provider's latched status: probing with live calls would spend
    # the very requests being reported as exhausted.
    notes = []
    for provider in providers:
        name = provider.SOURCE.replace("_", " ").title()
        status = provider.last_status
        if status == cfg.RATE_LIMITED:
            notes.append(f"{name} is rate-limited (quota resets).")
        elif status == cfg.ENTITLEMENT_REQUIRED:
            notes.append(f"{name} rejected the configured key.")
        elif status is None:
            notes.append(f"{name} has not been reached yet.")
        else:
            notes.append(f"{name} returned no bars for this symbol.")
    return "Fallback: " + " ".join(notes)


# Which Unusual Whales candle their sizes correspond to. Theirs are named
# for the interval, TWS's for the phrase.
_UW_SIZES = {"5 mins": "5m", "15 mins": "15m", "1 min": "1m", "1 hour": "1h"}


def _uw_intraday_chart(symbol: str, range_key: str, bar_size: str,
                       keep: Optional[int]) -> Optional[dict]:
    """
    An intraday chart from Unusual Whales when TWS will not serve one.

    This is the gap that had no fallback at all. TWS intermittently refuses
    one request and serves the identical one a second later, and while it
    refused, everything measured from intraday bars -- VWAP, the opening
    range, relative volume, the whole session half of the Today outlook --
    reported "no data" with no second source to ask.
    """
    import unusualwhales_service as uw

    size = _UW_SIZES.get(bar_size)
    if not size or not uw.configured():
        return None

    days = 2 if range_key == "1D" else 10
    raw = uw.get_intraday_bars(symbol, size, days)
    if not raw:
        return None

    closes = [b["close"] for b in raw]
    e20, e50, e200 = ema(closes, 20), ema(closes, 50), ema(closes, 200)

    rows = []
    for i, b in enumerate(raw):
        try:
            stamp = datetime.fromisoformat(
                str(b["t"]).replace("Z", "+00:00")).astimezone(EASTERN)
        except (TypeError, ValueError):
            continue
        rows.append({
            "t": stamp.isoformat(),
            "label": stamp.strftime("%H:%M"),
            "open": round(b["open"], 2), "high": round(b["high"], 2),
            "low": round(b["low"], 2), "close": round(b["close"], 2),
            "volume": b["volume"] or 0.0,
            "ema20": round(e20[i], 2) if e20[i] is not None else None,
            "ema50": round(e50[i], 2) if e50[i] is not None else None,
            "ema200": round(e200[i], 2) if e200[i] is not None else None,
        })
    if keep:
        rows = rows[-keep:]
    if not rows:
        return None

    first, last = rows[0], rows[-1]
    change = change_pct = None
    if first["close"]:
        change = round(last["close"] - first["close"], 2)
        change_pct = round(change / first["close"] * 100, 2)

    return _levels.attach({
        "symbol": symbol, "range": range_key, "bar_size": bar_size,
        "bars": rows,
        "ohlc": {"open": first["open"],
                 "high": max(r["high"] for r in rows),
                 "low": min(r["low"] for r in rows),
                 "close": last["close"]},
        "ema": {"ema20": last["ema20"], "ema50": last["ema50"],
                "ema200": last["ema200"]},
        "change": change, "change_percent": change_pct,
        "volume": sum(r["volume"] for r in rows),
        "status": "OK", "source": uw.SOURCE, "delayed": False,
    })


def _av_chart(symbol: str, range_key: str, bar_size: str,
              keep: Optional[int], intraday: bool) -> Optional[dict]:
    """
    Rebuild the get_chart payload from the feed's daily bars when TWS is
    down. Returns None if it cannot answer, so the caller can fall back to
    its DATA_UNAVAILABLE payload.

    Daily bars only: an intraday range gets daily candles instead, which is
    why the result carries delayed=True rather than pretending the requested
    granularity was met.
    """
    if intraday:
        return _uw_intraday_chart(symbol, range_key, bar_size, keep)

    raw, source = _fallback_bars(symbol, "2 Y")
    if not raw:
        return None

    closes = [b["close"] for b in raw]
    e20, e50, e200 = ema(closes, 20), ema(closes, 50), ema(closes, 200)

    rows = []
    for i, b in enumerate(raw):
        try:
            stamp = datetime.fromisoformat(b["date"])
        except (TypeError, ValueError):
            continue
        rows.append({
            "t": stamp.isoformat(),
            "label": stamp.strftime("%b %d"),
            "open": round(b["open"], 2),
            "high": round(b["high"], 2),
            "low": round(b["low"], 2),
            "close": round(b["close"], 2),
            "volume": b["volume"] or 0.0,
            "ema20": round(e20[i], 2) if e20[i] is not None else None,
            "ema50": round(e50[i], 2) if e50[i] is not None else None,
            "ema200": round(e200[i], 2) if e200[i] is not None else None,
        })

    if not rows:
        return None

    if range_key == "YTD":
        year = datetime.now(EASTERN).year
        rows = [r for r in rows if r["t"][:4] == str(year)]
    elif keep:
        rows = rows[-keep:]

    if not rows:
        return None

    first, last = rows[0], rows[-1]
    change = change_pct = None
    if first["close"]:
        change = round(last["close"] - first["close"], 2)
        change_pct = round(change / first["close"] * 100, 2)

    return _levels.attach({
        "symbol": symbol,
        "range": range_key,
        "bar_size": "1 day",
        "bars": rows,
        "ohlc": {
            "open": last["open"],
            "high": max(r["high"] for r in rows),
            "low": min(r["low"] for r in rows),
            "close": last["close"],
        },
        "ema": {
            "ema20": last["ema20"],
            "ema50": last["ema50"],
            "ema200": last["ema200"],
        },
        "change": change,
        "change_percent": change_pct,
        "volume": sum(r["volume"] for r in rows),
        "status": "OK",
        "source": source,
        "delayed": True,
    })


def get_chart(symbol: str, range_key: str = "6M", ttl: Optional[float] = None) -> dict:
    symbol = symbol.upper()
    range_key = (range_key or "6M").upper()
    if range_key not in RANGE_SPECS:
        range_key = "6M"

    key = f"chart:{symbol}:{range_key}"
    cached = cache.get(key, ttl if ttl is not None else session_ttl(30.0, 900.0))
    if cached:
        return cached

    duration, bar_size, keep = RANGE_SPECS[range_key]
    intraday = "min" in bar_size or "hour" in bar_size

    # One source. _av_chart handles the daily ranges and re-applies the live
    # session candle on top; intraday goes to the feed's own intraday bars.
    # TWS used to serve both and took ~23s to return the same end-of-day
    # candles the feed returns in about one.
    result = _av_chart(symbol, range_key, bar_size, keep, intraday)
    if result:
        cache.put(key, result)
        return result

    return {
        "symbol": symbol,
        "range": range_key,
        "bars": [],
        "status": "DATA_UNAVAILABLE",
        "source": "UNUSUAL_WHALES",
        "error": _bars_fallback_note(intraday),
    }


# ---------------------------------------------------------------------------
# indices
# ---------------------------------------------------------------------------

# NDX and INDU need index subscriptions this account does not carry, so the
# liquid ETF tracker is used instead and the payload says so explicitly.
# Indices this account returned no bars for. Asked once per server run.
_NO_INDEX_DATA: set[str] = set()

INDEX_SPECS = [
    {"label": "S&P 500", "index": ("SPX", "CBOE"), "proxy": "SPY"},
    {"label": "NASDAQ", "index": ("NDX", "NASDAQ"), "proxy": "QQQ"},
    {"label": "DOW", "index": ("INDU", "CME"), "proxy": "DIA"},
    {"label": "VIX", "index": ("VIX", "CBOE"), "proxy": None},
]

# What to ask the provider for when TWS is not connected, best first. The
# index itself where the provider carries it, and an ETF only where it does
# not -- the Dow has no index quote here, so DIA stands in and the row says so
# rather than presenting an ETF price as the index level.
INDEX_FALLBACK = {
    "S&P 500": ["SPX", "SPY"],
    "NASDAQ": ["NDX", "QQQ"],
    "DOW": ["DIA"],
    "VIX": ["VIX"],
}


def _provider_indices() -> Optional[dict]:
    """
    The index strip.

    Fetched in parallel: four sequential calls is four round trips for a
    strip that sits at the top of every page.
    """
    providers = _quote_providers()
    if not providers:
        return None

    def one(spec: dict) -> dict:
        row = {
            "label": spec["label"],
            "value": None, "change": None, "change_percent": None,
            "spark": [], "instrument": None, "is_proxy": False,
            "status": "NO_DATA", "source": None,
        }
        for candidate in INDEX_FALLBACK.get(spec["label"], []):
            for provider in providers:
                try:
                    quote = provider.get_quote(candidate)
                except Exception:  # noqa: BLE001 - try the next source
                    continue
                if not quote or quote.get("price") is None:
                    continue
                index_symbol = (spec.get("index") or (None,))[0]
                row.update({
                    "value": quote.get("price"),
                    "change": quote.get("change"),
                    "change_percent": quote.get("change_percent"),
                    "instrument": candidate,
                    # An ETF standing in for an index is a different number,
                    # not a worse version of the same one. Flagged so the UI
                    # never presents DIA's price as the Dow.
                    "is_proxy": candidate != index_symbol,
                    "status": "OK",
                    "source": quote.get("source"),
                    "delayed": quote.get("delayed"),
                })
                return row
        return row

    with ThreadPoolExecutor(max_workers=len(INDEX_SPECS),
                            thread_name_prefix="indices") as pool:
        rows = list(pool.map(one, INDEX_SPECS))

    if not any(r["status"] == "OK" for r in rows):
        return None

    priced = sum(1 for r in rows if r["status"] == "OK")
    return {
        "indices": rows,
        "market": market_clock(),
        "dates": _session_dates(),
        "status": "OK" if priced == len(rows) else "PARTIAL_DATA",
        "source": "FALLBACK",
        "detail": (f"{priced} of {len(rows)} index levels. The feed carries "
                   "no quote for SPX, NDX or VIX, so where a level is "
                   "missing the row says so rather than showing its ETF."),
    }


def get_indices(ttl: Optional[float] = None) -> dict:
    cached = cache.get("indices", ttl if ttl is not None else session_ttl(20.0, 600.0))
    if cached:
        return cached

    result = _provider_indices()
    if result:
        cache.put("indices", result)
        return result

    # The strip is on every page, and four blank cells read as a broken app.
    # Said as unavailable rather than left empty.
    return {
        "indices": [
            {
                "label": spec["label"],
                "value": None,
                "change": None,
                "change_percent": None,
                "spark": [],
                "instrument": None,
                "is_proxy": False,
                "status": "DATA_UNAVAILABLE",
            }
            for spec in INDEX_SPECS
        ],
        "market": market_clock(),
        "dates": _session_dates(),
        "status": "DATA_UNAVAILABLE",
        "source": "UNUSUAL_WHALES",
        "error": "The feed returned no index levels.",
    }


# ---------------------------------------------------------------------------
# batched watchlist fetch
# ---------------------------------------------------------------------------


# Every caller of get_batch is on a request path behind a proxy that cuts the
# connection at 120s. The old 180s ceiling meant a cold TWS could only ever
# produce a gateway timeout: the request was killed before the call it was
# waiting on was allowed to give up. A batch that overruns falls back to the
# HTTP provider, which answers in seconds.
def get_batch(symbols: list[str], ttl: Optional[float] = None,
              budget: float = 30.0) -> dict:
    """
    Quotes plus one year of daily bars for many symbols in a single pass.

    The TWS version of this opened one market-data line per symbol, let them
    settle together and gathered the history calls concurrently -- one round
    trip for ten symbols instead of ten. ``_fallback_batch`` does the same
    shape of thing against the feed: every symbol fetched in parallel under a
    deadline, with a symbol that misses it reported as pending rather than as
    having no price.

    ``budget`` is accepted and unused. It bounded the TWS call; the deadline
    that matters now lives in ``_fallback_batch``.
    """
    symbols = [s.upper() for s in symbols]
    key = "batch:" + ",".join(symbols)
    cached = cache.get(key, ttl if ttl is not None else session_ttl(60.0, 900.0))
    if cached:
        return cached

    rows = _fallback_batch(symbols)
    if rows:
        result = {"symbols": rows, "status": "OK", "source": "UNUSUAL_WHALES"}
        cache.put(key, result)
        return result

    return {
        "symbols": {
            s: {"symbol": s, "price": None, "change": None,
                "change_percent": None, "bars": [],
                "status": "DATA_UNAVAILABLE"}
            for s in symbols
        },
        "status": "DATA_UNAVAILABLE",
        "source": "UNUSUAL_WHALES",
        "error": "The feed returned no rows.",
    }
