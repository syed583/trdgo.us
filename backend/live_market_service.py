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
from typing import Any, Iterable, Optional

import price_levels as _levels
from zoneinfo import ZoneInfo

import ib_bootstrap  # noqa: F401  (must precede ib_insync)
from ib_insync import IB, Index, Stock

from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout

from ibkr_client import IBKRUnavailable, ibkr


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
    def __init__(self) -> None:
        self._data: dict[str, tuple[float, Any]] = {}
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

    # How many companies actually report in each window, from the cached
    # provider calendar. The strip used to show only a date range, which told
    # the operator nothing about whether anything was happening in it.
    # Look a year ahead: the fixed windows only need this month, but next_up
    # has to find the next report even when the whole month is empty.
    counts = _earnings_counts(min(w[3] for w in windows.values()),
                              today + timedelta(days=400))

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
    """[(date, symbol)] from the cached calendar, or [] if it is unavailable."""
    try:
        import benzinga_earnings_service as benzinga

        rows = benzinga.get_upcoming(start, end).get("rows") or []
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
    return sorted(out)


# ---------------------------------------------------------------------------
# contract helpers
# ---------------------------------------------------------------------------


async def _stock(ib: IB, symbol: str) -> Stock:
    c = Stock(symbol.upper(), "SMART", "USD")
    await ib.qualifyContractsAsync(c)
    if not c.conId:
        raise ValueError(f"Unknown symbol: {symbol.upper()}")
    return c


async def _settle(ib: IB, tickers: Iterable, seconds: float = 3.0) -> None:
    """Give TWS time to populate the ticker fields."""
    import asyncio

    deadline = time.time() + seconds
    while time.time() < deadline:
        await asyncio.sleep(0.25)
        if all(num(t.last) is not None or num(t.close) is not None for t in tickers):
            # One more beat so bid/ask/volume land too.
            await asyncio.sleep(0.4)
            return


# ---------------------------------------------------------------------------
# quote
# ---------------------------------------------------------------------------



# Why the most recent live-price attempt failed, per symbol. Surfaced on the
# quote so a stale price can be explained rather than silently accepted.
_LIVE_FAILURES: dict[str, str] = {}


def _note_live_failure(symbol: str, reason: str) -> None:
    _LIVE_FAILURES[symbol.upper()] = reason


def live_price_status(symbol: str) -> Optional[str]:
    """The last reason TWS could not supply a live price for this symbol."""
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
    The pre- or post-market price, from TWS.

    The data providers do not have this. Their snapshots carry an effective
    date of the last completed session, so at nine in the morning they return
    Friday's close -- which the screen would otherwise present as the current
    price. TWS quotes the extended session live, and it is the only source
    here that does.

    ``reference_close`` is supplied by the caller because TWS does not
    reliably populate it on this line: outside regular hours ``ticker.close``
    comes back as NaN, and deriving the change from it silently produced
    nothing at all. The provider snapshot already carries the last completed
    session's close, which is exactly the right reference.

    Returns None when TWS is unavailable or has no extended print, so the
    caller keeps the provider figure rather than showing a gap.
    """
    symbol = symbol.upper()
    clock = market_clock()
    if clock.get("session") not in TRADING_SESSIONS:
        return None

    key = f"ext:{symbol}:{reference_close}"
    cached = cache.get(key, 20.0)
    if cached is not None:
        return cached or None

    async def job(ib: IB) -> Optional[dict]:
        contract = await _stock(ib, symbol)
        ticker = ib.reqMktData(contract, "", False, False)
        # Leave room inside the caller's budget for the contract lookup and
        # the round trip, or the settle alone eats the whole allowance.
        await _settle(ib, [ticker], max(1.5, timeout - 1.5))

        last = _first_num(ticker.last, ticker.markPrice)
        bid, ask = num(ticker.bid), num(ticker.ask)
        stamp = getattr(ticker, "time", None)
        # TWS populates close inconsistently outside regular hours, so it is
        # only a fallback behind the value the caller passed in.
        fallback_close = num(ticker.close)
        ib.cancelMktData(contract)

        prev_close = reference_close if reference_close else fallback_close
        if last is None or not prev_close:
            return None
        # An extended print identical to the close is the close being echoed
        # back rather than a trade; reporting it as a move of exactly zero
        # would imply the session has traded when it has not.
        if abs(last - prev_close) < 1e-9:
            return None

        change = last - prev_close
        return {
            "price": round(last, 2),
            "previous_close": round(prev_close, 2),
            "change": round(change, 2),
            "change_percent": round(change / prev_close * 100, 2),
            "bid": bid,
            "ask": ask,
            "session": clock.get("session"),
            "session_label": clock.get("label"),
            "as_of": stamp.isoformat() if stamp else None,
            "source": "IBKR",
        }

    # A failure here is silent by design -- the provider figure still renders
    # -- so the reason is recorded rather than swallowed.
    # Failures fall through to the provider fallback rather than returning
    # here. Returning early was why the fallback never ran when TWS was
    # missing entirely -- the one case it exists for.
    result = None
    try:
        result = ibkr.run(job, timeout=timeout)
    except IBKRUnavailable as exc:
        _note_live_failure(symbol, str(exc)[:160])
    except Exception as exc:  # noqa: BLE001
        _note_live_failure(symbol, f"{type(exc).__name__}: {str(exc)[:120]}")

    if not result:
        _note_live_failure(symbol, "TWS returned no last price")

    # TWS is preferred because it is the only source that quotes the extended
    # sessions, but it is not the only live price available. Twelve Data has a
    # dedicated real-time endpoint -- distinct from its daily snapshot, which
    # is dated to the last completed session -- so the app is not dark when
    # TWS is down or was never running.
    if not result and reference_close:
        result = _provider_live_price(symbol, reference_close, clock)

    cache.put(key, result or {})
    return result


def _provider_live_price(symbol: str, reference_close: float,
                         clock: dict) -> Optional[dict]:
    """
    Live last price from Twelve Data, when TWS cannot supply one.

    Regular hours only. That endpoint reports the last trade, which outside
    RTH is the closing print -- passing it off as a pre-market quote would
    invent a session that has not traded.
    """
    if clock.get("session") != "OPEN":
        return None

    try:
        import twelve_data_market_service as td

        price = td.live_price(symbol)
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
        "source": "TWELVE_DATA",
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
    if _optiondata_quotes_first():
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
                        "price_source": live.get("source", "IBKR"),
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

    async def job(ib: IB) -> dict:
        contract = await _stock(ib, symbol)

        details = await ib.reqContractDetailsAsync(contract)
        detail = details[0] if details else None

        bars = await ib.reqHistoricalDataAsync(
            contract,
            endDateTime="",
            durationStr="5 D",
            barSizeSetting="1 day",
            whatToShow="TRADES",
            useRTH=True,
            formatDate=1,
        )

        ticker = ib.reqMktData(contract, "", False, False)
        await _settle(ib, [ticker], 4.0)

        last = _first_num(ticker.last, ticker.close, ticker.markPrice)
        prev_close = None

        if bars:
            today = datetime.now(EASTERN).date()
            last_bar = bars[-1]
            bar_date = last_bar.date if isinstance(last_bar.date, date) else None
            if bar_date == today and len(bars) >= 2:
                prev_close = num(bars[-2].close)
            else:
                prev_close = num(last_bar.close)
            if last is None:
                last = num(last_bar.close)

        # ticker.close is the prior session close during RTH.
        if prev_close is None:
            prev_close = num(ticker.close)

        ib.cancelMktData(contract)

        change = None
        change_pct = None
        if last is not None and prev_close:
            change = round(last - prev_close, 2)
            change_pct = round((last - prev_close) / prev_close * 100, 2)

        last_bar = bars[-1] if bars else None
        tags: list[str] = []
        if detail:
            for value in (detail.industry, detail.category, detail.subcategory):
                if value and value not in tags:
                    tags.append(value)

        return {
            "symbol": symbol,
            "name": (detail.longName if detail else None) or symbol,
            "exchange": contract.primaryExchange or contract.exchange,
            "con_id": contract.conId,
            "tags": tags[:3],
            "price": round(last, 2) if last is not None else None,
            "change": change,
            "change_percent": change_pct,
            "previous_close": round(prev_close, 2) if prev_close else None,
            "bid": num(ticker.bid),
            "ask": num(ticker.ask),
            "open": num(last_bar.open) if last_bar else None,
            "high": num(last_bar.high) if last_bar else None,
            "low": num(last_bar.low) if last_bar else None,
            "close": num(last_bar.close) if last_bar else None,
            "volume": num(last_bar.volume) if last_bar else None,
            "market": market_clock(),
            "status": "OK" if last is not None else "NO_PRICE",
            "source": "IBKR",
        }

    try:
        result = ibkr.run(job)
    except IBKRUnavailable as exc:
        # Fall back to a delayed Alpha Vantage quote rather than a blank card.
        # It is tagged source=ALPHA_VANTAGE and delayed=True so the UI never
        # presents an end-of-day print as a live one.
        fallback = _fallback_quote(symbol)
        if fallback:
            cache.put(key, fallback)
            return fallback
        return _offline_quote(symbol, str(exc))
    except ValueError as exc:
        return {
            "symbol": symbol,
            "status": "UNKNOWN_SYMBOL",
            "source": "IBKR",
            "error": str(exc),
            "market": market_clock(),
        }

    cache.put(key, result)
    return result


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
        "status": "IBKR_UNAVAILABLE",
        "source": "IBKR",
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

    Unusual Whales leads when it is configured: it is a paid subscription
    with a daily allowance in the tens of thousands, against Twelve Data's
    800 free requests a day and Alpha Vantage's 25. Both stay behind it, so
    a lapsed key drops the app back to where it was rather than blanking it.
    """
    import alpha_vantage_market_service as av
    import twelve_data_market_service as td
    import unusualwhales_service as uw

    return [p for p in (uw, td, av) if p.configured()]


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

    Bars are attached only when a bar provider already has them cached -- the
    strip needs prices far more often than it needs a sparkline, and fetching a
    year of history per symbol here would cost one request each.
    """
    def one(symbol: str) -> tuple[str, dict]:
        quote = _fallback_quote(symbol)
        if not quote:
            return symbol, {
                "symbol": symbol, "price": None, "change": None,
                "change_percent": None, "bars": [],
                "status": "IBKR_UNAVAILABLE",
            }
        return symbol, {
            "symbol": symbol,
            "price": quote.get("price"),
            "change": quote.get("change"),
            "change_percent": quote.get("change_percent"),
            "bars": [],
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


def _optiondata_quotes_first() -> bool:
    """
    Whether to ask the provider before TWS for quotes.

    TWS leads: it is the live subscription the user already pays for and has
    no per-request budget, and Unusual Whales now carries bid and ask too, so
    there is nothing to gain by spending a request first. Set
    QUOTES_PREFER_PROVIDER=1 to put the provider in front -- worth doing when
    TWS is not running, because the fallback then answers immediately rather
    than after a connection attempt.
    """
    return os.getenv("QUOTES_PREFER_PROVIDER", "").strip() in ("1", "true", "yes")


def _quote_providers():
    """
    Fallback quote sources, best first.

    Unusual Whales serves both the live quote and the bar history, so it
    appears once at the head of this list and again through _bar_providers;
    the duplicate is harmless because both answers come out of the same
    short-lived cache entry.
    """
    import unusualwhales_service as uw

    return ([uw] if uw.configured() else []) + _bar_providers()


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
        return ("No fallback bar source configured: set TWELVE_DATA_API_KEY "
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
    Rebuild the get_chart payload from Alpha Vantage daily bars when TWS is
    down. Returns None if Alpha Vantage cannot answer, so the caller can fall
    back to its IBKR_UNAVAILABLE payload.

    Daily bars only: an intraday range gets daily candles instead, which is
    why the result carries source=ALPHA_VANTAGE and delayed=True rather than
    pretending the requested granularity was met.
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

    # Daily ranges come from the bar providers first: TWS took ~23s to return
    # the same end-of-day candles that Twelve Data serves in about one, and
    # _av_chart re-applies the live session candle on top, so nothing is lost
    # by not asking TWS. Intraday still needs TWS - no fallback sells it.
    if not intraday and _bar_providers():
        fast = _av_chart(symbol, range_key, bar_size, keep, intraday)
        if fast:
            cache.put(key, fast)
            return fast

    async def job(ib: IB) -> dict:
        contract = await _stock(ib, symbol)
        bars = await ib.reqHistoricalDataAsync(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow="TRADES",
            useRTH=not intraday,
            formatDate=1,
        )
        if not bars:
            return {
                "symbol": symbol,
                "range": range_key,
                "bars": [],
                "status": "NO_DATA",
                "source": "IBKR",
            }

        closes = [float(b.close) for b in bars]
        # EMAs are computed over the full fetched series so the trimmed window
        # still shows a correct EMA200 instead of a truncated one.
        e20 = ema(closes, 20)
        e50 = ema(closes, 50)
        e200 = ema(closes, 200)

        rows = []
        for i, b in enumerate(bars):
            ts = b.date
            if isinstance(ts, datetime):
                label = ts.strftime("%H:%M") if intraday else ts.strftime("%b %d")
                iso = ts.isoformat()
            else:
                label = ts.strftime("%b %d")
                iso = ts.isoformat()
            rows.append(
                {
                    "t": iso,
                    "label": label,
                    "open": round(float(b.open), 2),
                    "high": round(float(b.high), 2),
                    "low": round(float(b.low), 2),
                    "close": round(float(b.close), 2),
                    "volume": float(b.volume) if b.volume and b.volume > 0 else 0.0,
                    "ema20": round(e20[i], 2) if e20[i] is not None else None,
                    "ema50": round(e50[i], 2) if e50[i] is not None else None,
                    "ema200": round(e200[i], 2) if e200[i] is not None else None,
                }
            )

        if range_key == "YTD":
            year = datetime.now(EASTERN).year
            rows = [r for r in rows if r["t"][:4] == str(year)]
        elif keep:
            rows = rows[-keep:]

        first = rows[0] if rows else None
        last = rows[-1] if rows else None
        change = None
        change_pct = None
        if first and last and first["close"]:
            change = round(last["close"] - first["close"], 2)
            change_pct = round(change / first["close"] * 100, 2)

        total_volume = sum(r["volume"] for r in rows)

        return _levels.attach({
            "symbol": symbol,
            "range": range_key,
            "bar_size": bar_size,
            "bars": rows,
            "ohlc": {
                "open": last["open"] if last else None,
                "high": max((r["high"] for r in rows), default=None),
                "low": min((r["low"] for r in rows), default=None),
                "close": last["close"] if last else None,
            },
            "ema": {
                "ema20": last["ema20"] if last else None,
                "ema50": last["ema50"] if last else None,
                "ema200": last["ema200"] if last else None,
            },
            "change": change,
            "change_percent": change_pct,
            "volume": total_volume,
            "status": "OK",
            "source": "IBKR",
        })

    try:
        result = ibkr.run(job, timeout=60)
    except IBKRUnavailable as exc:
        fallback = _av_chart(symbol, range_key, bar_size, keep, intraday)
        if fallback:
            cache.put(key, fallback)
            return fallback
        return {
            "symbol": symbol,
            "range": range_key,
            "bars": [],
            "status": "IBKR_UNAVAILABLE",
            "source": "IBKR",
            # Name the fallback too. Reporting only the IBKR error made it look
            # like TWS was the single option, when the second source had been
            # tried and had its own reason for failing.
            "error": f"{exc} {_bars_fallback_note(intraday)}".strip(),
        }
    except ValueError as exc:
        return {
            "symbol": symbol,
            "range": range_key,
            "bars": [],
            "status": "UNKNOWN_SYMBOL",
            "source": "IBKR",
            "error": str(exc),
        }

    cache.put(key, result)
    return result


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
    The index strip from the quote providers, for when TWS is unavailable.

    Fetched in parallel: four sequential provider calls is four round trips for
    a strip that sits at the top of every page.
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
        "detail": (f"{priced} of {len(rows)} index levels from the quote "
                   "providers; TWS is not connected."),
    }


def get_indices(ttl: Optional[float] = None) -> dict:
    cached = cache.get("indices", ttl if ttl is not None else session_ttl(20.0, 600.0))
    if cached:
        return cached

    async def job(ib: IB) -> dict:
        out = []
        for spec in INDEX_SPECS:
            row = {
                "label": spec["label"],
                "value": None,
                "change": None,
                "change_percent": None,
                "spark": [],
                "instrument": None,
                "is_proxy": False,
                "status": "UNAVAILABLE",
            }

            candidates = []
            sym, exch = spec["index"]
            candidates.append((Index(sym, exch), sym, False))
            if spec["proxy"]:
                candidates.append(
                    (Stock(spec["proxy"], "SMART", "USD"), spec["proxy"], True)
                )

            for contract, name, is_proxy in candidates:
                if name in _NO_INDEX_DATA:
                    continue
                try:
                    await ib.qualifyContractsAsync(contract)
                    if not contract.conId:
                        continue
                    bars = await ib.reqHistoricalDataAsync(
                        contract,
                        endDateTime="",
                        durationStr="5 D",
                        barSizeSetting="1 hour",
                        whatToShow="TRADES",
                        useRTH=True,
                        formatDate=1,
                    )
                    if not bars:
                        # No permission for this index: stop asking every refresh.
                        if not is_proxy:
                            _NO_INDEX_DATA.add(name)
                        continue

                    closes = [float(b.close) for b in bars if num(b.close) is not None]
                    if len(closes) < 2:
                        continue

                    daily = await ib.reqHistoricalDataAsync(
                        contract,
                        endDateTime="",
                        durationStr="5 D",
                        barSizeSetting="1 day",
                        whatToShow="TRADES",
                        useRTH=True,
                        formatDate=1,
                    )
                    value = closes[-1]
                    prev = (
                        num(daily[-2].close)
                        if daily and len(daily) >= 2
                        else closes[0]
                    )

                    row.update(
                        {
                            "value": round(value, 2),
                            "change": round(value - prev, 2) if prev else None,
                            "change_percent": (
                                round((value - prev) / prev * 100, 2) if prev else None
                            ),
                            "spark": [round(c, 2) for c in closes[-40:]],
                            "instrument": name,
                            "is_proxy": is_proxy,
                            "status": "OK",
                        }
                    )
                    break
                except Exception:  # noqa: BLE001 - try the next candidate
                    continue

            out.append(row)

        return {
            "indices": out,
            "market": market_clock(),
            "dates": _session_dates(),
            "status": "OK",
            "source": "IBKR",
        }

    try:
        result = ibkr.run(job, timeout=60)
    except IBKRUnavailable as exc:
        # The strip is on every page, and four blank cells read as a broken
        # app rather than a disconnected broker. OptionData carries SPX, NDX
        # and VIX as real index quotes, so the row is the index itself rather
        # than an ETF wearing its name.
        fallback = _provider_indices()
        if fallback:
            fallback["error"] = str(exc)
            cache.put("indices", fallback)
            return fallback
        return {
            "indices": [
                {
                    "label": s["label"],
                    "value": None,
                    "change": None,
                    "change_percent": None,
                    "spark": [],
                    "instrument": None,
                    "is_proxy": False,
                    "status": "IBKR_UNAVAILABLE",
                }
                for s in INDEX_SPECS
            ],
            "market": market_clock(),
            "dates": _session_dates(),
            "status": "IBKR_UNAVAILABLE",
            "source": "IBKR",
            "error": str(exc),
        }

    cache.put("indices", result)
    return result


# ---------------------------------------------------------------------------
# batched watchlist fetch
# ---------------------------------------------------------------------------


# Every caller of get_batch is on a request path behind a proxy that cuts the
# connection at 120s. The old 180s ceiling meant a cold TWS could only ever
# produce a gateway timeout: the request was killed before the call it was
# waiting on was allowed to give up. A batch that overruns falls back to the
# HTTP provider, which answers in seconds.
BATCH_IBKR_BUDGET = 30.0


def get_batch(
    symbols: list[str], ttl: Optional[float] = None,
    budget: float = BATCH_IBKR_BUDGET,
) -> dict:
    """
    Quotes plus one year of daily bars for many symbols in a single pass.

    Fetching these one symbol at a time costs a market-data settle per symbol
    (~7s each). Requesting every ticker on one batch and gathering the
    historical calls concurrently turns ten symbols into roughly one round
    trip instead of ten.
    """
    import asyncio

    symbols = [s.upper() for s in symbols]
    key = "batch:" + ",".join(symbols)
    cached = cache.get(key, ttl if ttl is not None else session_ttl(60.0, 900.0))
    if cached:
        return cached

    async def job(ib: IB) -> dict:
        contracts: dict[str, Stock] = {}
        for symbol in symbols:
            c = Stock(symbol, "SMART", "USD")
            try:
                await ib.qualifyContractsAsync(c)
            except Exception:  # noqa: BLE001
                continue
            if c.conId:
                contracts[symbol] = c

        # One market-data line per symbol, all settling together.
        tickers = {s: ib.reqMktData(c, "", False, False) for s, c in contracts.items()}
        await asyncio.sleep(4.0)

        snapshots = {}
        for s, t in tickers.items():
            snapshots[s] = {
                "last": _first_num(t.last, t.close, t.markPrice),
                "close": num(t.close),
                "bid": num(t.bid),
                "ask": num(t.ask),
            }
            ib.cancelMktData(contracts[s])

        async def history(symbol: str, contract: Stock):
            try:
                return symbol, await ib.reqHistoricalDataAsync(
                    contract, "", "1 Y", "1 day", "TRADES", True, 1
                )
            except Exception:  # noqa: BLE001
                return symbol, []

        results = await asyncio.gather(
            *[history(s, c) for s, c in contracts.items()],
            return_exceptions=True,
        )

        out: dict[str, dict] = {}
        today = datetime.now(EASTERN).date()

        for item in results:
            if isinstance(item, Exception) or not isinstance(item, tuple):
                continue
            symbol, bars = item
            snap = snapshots.get(symbol, {})

            rows = [
                {
                    "date": str(b.date),
                    "open": float(b.open),
                    "high": float(b.high),
                    "low": float(b.low),
                    "close": float(b.close),
                    "volume": float(b.volume or 0.0),
                }
                for b in bars
            ]

            price = snap.get("last")
            prev = None
            if bars:
                last_bar = bars[-1]
                bar_date = last_bar.date if isinstance(last_bar.date, date) else None
                if bar_date == today and len(bars) >= 2:
                    prev = num(bars[-2].close)
                else:
                    prev = num(last_bar.close)
                if price is None:
                    price = num(last_bar.close)
            if prev is None:
                prev = snap.get("close")

            change = round(price - prev, 2) if price is not None and prev else None
            change_pct = (
                round((price - prev) / prev * 100, 2)
                if price is not None and prev else None
            )

            out[symbol] = {
                "symbol": symbol,
                "price": round(price, 2) if price is not None else None,
                "change": change,
                "change_percent": change_pct,
                "bars": rows,
                "status": "OK" if price is not None else "NO_PRICE",
            }

        for symbol in symbols:
            out.setdefault(
                symbol,
                {"symbol": symbol, "price": None, "change": None,
                 "change_percent": None, "bars": [], "status": "UNKNOWN_SYMBOL"},
            )

        return {"symbols": out, "status": "OK", "source": "IBKR"}

    try:
        result = ibkr.run(job, timeout=budget)
    except IBKRUnavailable as exc:
        # ``ibkr.run`` reports its own timeout as IBKRUnavailable, so an
        # overrun of ``budget`` arrives here too -- which is right: slow and
        # unavailable are the same thing once the caller's deadline has gone.
        # The strip and the calendar read this, so it needs the same fallback
        # chain as get_quote. Without it every card showed "--" even when a
        # provider had a perfectly good price.
        fallback = _fallback_batch(symbols)
        if fallback:
            result = {"symbols": fallback, "status": "OK",
                      "source": "FALLBACK", "error": str(exc)}
            cache.put(key, result)
            return result
        return {
            "symbols": {
                s: {"symbol": s, "price": None, "change": None,
                    "change_percent": None, "bars": [],
                    "status": "IBKR_UNAVAILABLE"}
                for s in symbols
            },
            "status": "IBKR_UNAVAILABLE",
            "source": "IBKR",
            "error": str(exc),
        }

    cache.put(key, result)
    return result
