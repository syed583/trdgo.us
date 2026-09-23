"""
Live options intelligence sourced from IBKR.

Everything on the Options Flow screen is derived from real TWS data:

* chain quotes  -> reqMktData with generic ticks 100 (volume) and 101 (open interest)
* implied vol   -> solved locally from real bid/ask (see black_scholes)
* IV rank/pct   -> reqHistoricalData whatToShow=OPTION_IMPLIED_VOLATILITY
* the flow tape -> reqHistoricalTicks whatToShow=TRADES, classified against
                   real BID_ASK ticks

Sweep / block classification follows the standard market definition:
a cluster of prints on the same contract inside a short window that touches
several exchanges is a sweep; a single oversized print is a block.
"""

from __future__ import annotations

import asyncio
import os
import math
from datetime import datetime, timedelta, timezone
import freshness as _freshness
from typing import Any, Optional

import ib_bootstrap  # noqa: F401  (must precede ib_insync)
from ib_insync import IB, Option, Stock

import black_scholes as bs
from ibkr_client import IBKR_MARKET_DATA_TYPE, IBKRUnavailable, ibkr
from live_market_service import EASTERN, cache, market_clock, num


# How far either side of spot to pull strikes, and the hard cap on strike count.
STRIKE_BAND = 0.20
MAX_STRIKES = 24

# TWS allows ~100 concurrent market data lines by default.
MD_BATCH = 44

# Settle windows for the two market-data passes (see _snapshot).
OI_SETTLE = 10.0   # live pass: open interest arrives slowly
PX_SETTLE = 7.0    # frozen pass: bid/ask lands quickly

# Flow tape tuning.
FLOW_CONTRACTS = 10          # contracts to pull a trade tape for
TICKS_PER_CONTRACT = 220

# Each classified trade costs one anchored BID_ASK request, so this is capped
# to keep the page inside IBKR's historical pacing budget.
SIDE_RESOLVE_LIMIT = 16
SWEEP_WINDOW_MS = 900        # prints inside this window may form one sweep
SWEEP_MIN_EXCHANGES = 2
BLOCK_MIN_SIZE = 50          # single print size that qualifies as a block
MIN_FLOW_NOTIONAL = 10_000   # ignore retail-sized prints

# During RTH the chain genuinely moves, so the window is short enough that a
# page polling every ten seconds sees new quotes each time. It cannot go lower
# than a build takes, and does not need to: concurrent requests share one
# build (see load_chain), so a short window costs one snapshot per interval,
# never one per viewer.
CHAIN_TTL_OPEN = 10.0
CHAIN_TTL_CLOSED = 900.0  # outside it, every value is frozen - don't refetch
IV_HISTORY_TTL = 3600.0


# A failed chain load is held briefly. Building an overview calls load_chain
# more than once (the score does its own pass), so without this a single TWS
# outage is paid for at the full 180s timeout on every one of those calls.
CHAIN_FAIL_TTL = 90.0

# Ceiling for building a chain from TWS.
#
# OptionData answers in about three seconds but does not cover every symbol --
# PEP and WMT come back empty while AAPL is fine -- and those fall through to
# TWS, which quotes each contract individually and took a hundred and
# twenty-four seconds. The proxy in front of this app gives up at a hundred
# and twenty, so the page showed "Backend unreachable" rather than a slow
# panel, and the whole earnings screen failed with it.
#
# Better to return a labelled empty chain quickly: every other panel on that
# page still renders, and the options panels say why they are blank.
# Below this, an expiry has too little time value left for a solved implied
# volatility to mean anything.
MIN_FRONT_DTE = 0.5

CHAIN_IBKR_BUDGET = 40.0


def chain_ttl() -> float:
    """
    How long a chain snapshot stays usable.

    Assembling a chain plus its tape costs about two minutes of TWS round
    trips. Re-paying that every 45 seconds while the market is shut - when
    not one number can have changed - is pure waste, so the cache window
    follows the session.
    """
    return CHAIN_TTL_OPEN if market_clock()["is_open"] else CHAIN_TTL_CLOSED


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _mid(bid: Optional[float], ask: Optional[float], last: Optional[float],
         close: Optional[float]) -> Optional[float]:
    if bid is not None and ask is not None and ask >= bid > 0:
        return round((bid + ask) / 2.0, 4)
    for v in (last, close):
        if v is not None and v > 0:
            return round(v, 4)
    return None


def _parse_expiry(value: str) -> Optional[datetime]:
    # Both spellings occur: IBKR returns "20260914", OptionData "2026-09-14".
    # Parsing only the first meant every date-aware check silently no-opped on
    # a provider-sourced chain.
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value), fmt).replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
    return None


def _dte(expiry: str) -> Optional[float]:
    parsed = _parse_expiry(expiry)
    if not parsed:
        return None
    delta = parsed - datetime.now(timezone.utc)
    return max(delta.total_seconds() / 86400.0, 0.0)


def _ib_expiry(value: str) -> str:
    """
    An expiry in the only format TWS accepts.

    OptionData publishes "2026-09-16" and IBKR requires "20260916". Once
    OptionData became the primary chain source its dashed dates reached the
    contract builder unchanged, and every qualification failed with error
    10372 -- a whole panel of option quotes lost to four hyphens.
    """
    return str(value or "").replace("-", "")


def _fmt_expiry(expiry: str) -> str:
    parsed = _parse_expiry(expiry)
    return parsed.strftime("%m/%d/%y") if parsed else expiry


# reqMarketDataType is connection-wide, so two overlapping requests would
# fight over it (a quote refresh flipping the chain harvest to frozen
# mid-pass, or vice versa). Every switch is serialised behind this lock.
# It is only ever taken from the IBKR worker loop.
_MD_TYPE_LOCK = asyncio.Lock()


async def _harvest(
    ib: IB, contracts: list, market_data_type: int, ticks: str, settle: float
) -> dict[int, dict]:
    """
    reqMktData over a contract list in batches and copy the fields out.

    The ticker objects are reused by ib_insync after cancelMktData, so the
    values are snapshotted into plain dicts before releasing the line.
    """
    ib.reqMarketDataType(market_data_type)
    await asyncio.sleep(0.3)

    out: dict[int, dict] = {}
    for i in range(0, len(contracts), MD_BATCH):
        batch = contracts[i : i + MD_BATCH]
        tickers = [ib.reqMktData(c, ticks, False, False) for c in batch]
        await asyncio.sleep(settle)
        for c, t in zip(batch, tickers):
            right = "C" if c.right.upper().startswith("C") else "P"
            out[c.conId] = {
                "bid": num(t.bid),
                "ask": num(t.ask),
                "last": num(t.last),
                "close": num(t.close),
                "volume": num(t.volume),
                "open_interest": num(
                    t.callOpenInterest if right == "C" else t.putOpenInterest
                ),
            }
            ib.cancelMktData(c)
        await asyncio.sleep(0.2)
    return out


async def _snapshot(
    ib: IB, contracts: list, with_prices: bool = True
) -> dict[int, dict]:
    """
    Quote a set of option contracts.

    TWS will not serve both halves of what we need on one market data type:
    live (1) carries open interest and session volume but stops quoting
    bid/ask outside RTH, while frozen (2) keeps the last bid/ask but drops
    the open-interest ticks. So each contract is requested twice and the two
    payloads are merged.

    ``with_prices=False`` skips the frozen pass. Back expiries only feed the
    by-expiration volume panel, and halving their round trips keeps the whole
    overview inside a usable response time.
    """
    async with _MD_TYPE_LOCK:
        try:
            live = await _harvest(ib, contracts, 1, "100,101", OI_SETTLE)
            frozen = (
                await _harvest(ib, contracts, 2, "", PX_SETTLE) if with_prices else {}
            )
        finally:
            # Leave the connection on the default type for everyone else.
            ib.reqMarketDataType(IBKR_MARKET_DATA_TYPE)

    merged: dict[int, dict] = {}
    for c in contracts:
        a = live.get(c.conId, {})
        b = frozen.get(c.conId, {})
        merged[c.conId] = {
            "bid": b.get("bid") if b.get("bid") is not None else a.get("bid"),
            "ask": b.get("ask") if b.get("ask") is not None else a.get("ask"),
            "last": a.get("last") if a.get("last") is not None else b.get("last"),
            "close": b.get("close") if b.get("close") is not None else a.get("close"),
            "volume": a.get("volume") or 0.0,
            "open_interest": a.get("open_interest"),
        }
    return merged


# ---------------------------------------------------------------------------
# chain loading
# ---------------------------------------------------------------------------


async def _chain_meta(ib: IB, symbol: str) -> dict:
    stock = Stock(symbol, "SMART", "USD")
    await ib.qualifyContractsAsync(stock)
    if not stock.conId:
        raise ValueError(f"Unknown symbol: {symbol}")

    params = await ib.reqSecDefOptParamsAsync(symbol, "", "STK", stock.conId)
    pool = [p for p in params if p.exchange == "SMART"] or list(params)
    if not pool:
        raise ValueError(f"No option chain for {symbol}")

    # reqSecDefOptParams returns one row per (exchange, trading class), and the
    # non-standard classes left behind by past corporate actions come back
    # alongside the regular one. Taking the first SMART row picked '2SPY' for
    # SPY - an adjusted class listing a single strike - instead of the standard
    # 'SPY' chain. Prefer the class named after the symbol, then whichever
    # lists the most contracts.
    chosen = next((p for p in pool if p.tradingClass == symbol), None)
    if chosen is None:
        chosen = max(
            pool,
            key=lambda p: len(p.expirations or ()) * len(p.strikes or ()),
        )

    ticker = ib.reqMktData(stock, "", False, False)
    await asyncio.sleep(2.5)
    spot = None
    for v in (ticker.last, ticker.close, ticker.markPrice):
        n = num(v)
        if n:
            spot = n
            break
    ib.cancelMktData(stock)

    if spot is None:
        bars = await ib.reqHistoricalDataAsync(
            stock, "", "2 D", "1 day", "TRADES", True, 1
        )
        if bars:
            spot = float(bars[-1].close)
    if spot is None:
        raise ValueError(f"No underlying price for {symbol}")

    # TWS lists expirations that have already passed. Taking the first one
    # blindly meant the front month could be yesterday: every strike on it then
    # failed to qualify one at a time ("Unknown contract"), which cost around
    # forty seconds and produced a chain headed by an expired date.
    today = datetime.now(EASTERN).strftime("%Y%m%d")
    live_expirations = [e for e in sorted(chosen.expirations) if e >= today]

    return {
        "stock": stock,
        "spot": spot,
        "expirations": live_expirations or sorted(chosen.expirations),
        "strikes": sorted(chosen.strikes),
        "trading_class": chosen.tradingClass,
        "multiplier": int(chosen.multiplier or 100),
    }


async def _expiry_contracts(
    ib: IB, symbol: str, meta: dict, expiry: str, max_strikes: int
) -> list:
    """
    Resolve the real contracts listed on one expiry.

    reqSecDefOptParams returns the union of strikes across every expiry, so
    building Option() objects from it produces many strikes that do not exist
    on the chosen date. One reqContractDetails call returns exactly what is
    listed, already qualified with conIds.
    """
    template = Option(
        symbol, _ib_expiry(expiry), 0, "", "SMART",
        tradingClass=meta["trading_class"]
    )
    try:
        details = await ib.reqContractDetailsAsync(template)
    except Exception:  # noqa: BLE001
        return []

    spot = meta["spot"]
    contracts = [
        cd.contract
        for cd in details
        if cd.contract.strike and abs(cd.contract.strike - spot) / spot <= STRIKE_BAND
    ]
    if not contracts:
        contracts = sorted(
            (cd.contract for cd in details if cd.contract.strike),
            key=lambda c: abs(c.strike - spot),
        )[: max_strikes * 2]

    strikes = sorted({c.strike for c in contracts})
    if len(strikes) > max_strikes:
        keep = set(sorted(strikes, key=lambda s: abs(s - spot))[:max_strikes])
        contracts = [c for c in contracts if c.strike in keep]

    return sorted(contracts, key=lambda c: (c.strike, c.right))


async def _load_expiry_rows(
    ib: IB,
    symbol: str,
    meta: dict,
    expiry: str,
    max_strikes: int = MAX_STRIKES,
    with_prices: bool = True,
) -> list[dict]:
    """Quote every call and put on one expiry and enrich with IV + greeks."""
    qualified = await _expiry_contracts(ib, symbol, meta, expiry, max_strikes)
    if not qualified:
        return []

    tickers = await _snapshot(ib, qualified, with_prices=with_prices)

    spot = meta["spot"]
    mult = meta["multiplier"]
    dte = _dte(expiry) or 0.0
    t_years = bs.years_to_expiry(dte)

    rows: list[dict] = []
    for c in qualified:
        t = tickers.get(c.conId)
        if t is None:
            continue

        bid, ask = t.get("bid"), t.get("ask")
        last, close = t.get("last"), t.get("close")
        mid = _mid(bid, ask, last, close)

        right = "C" if c.right.upper().startswith("C") else "P"
        oi = t.get("open_interest")
        volume = t.get("volume") or 0.0

        iv = bs.implied_vol(mid, spot, c.strike, t_years, right) if mid else None
        g = bs.greeks(spot, c.strike, t_years, iv, right) if iv else {
            "delta": None, "gamma": None, "vega": None, "theta": None, "rho": None
        }

        rows.append(
            {
                "con_id": c.conId,
                "symbol": symbol,
                "expiry": expiry,
                "expiry_label": _fmt_expiry(expiry),
                "dte": round(dte, 2),
                "strike": float(c.strike),
                "right": right,
                "bid": bid,
                "ask": ask,
                "last": last,
                "close": close,
                "mid": mid,
                "volume": volume,
                "open_interest": oi,
                "iv": round(iv, 4) if iv else None,
                "notional": round(volume * (mid or 0.0) * mult, 2),
                "oi_notional": round((oi or 0.0) * (mid or 0.0) * mult, 2),
                **g,
            }
        )

    return rows


def _stamp_chain(payload: dict) -> dict:
    """Mark a chain with the source that actually served it."""
    try:
        payload["freshness"] = _freshness.for_chain(payload.get("source") or "")
    except Exception:  # noqa: BLE001 - a badge must never break a chain
        pass
    return payload


def load_chain(
    symbol: str,
    expiry: Optional[str] = None,
    extra_expiries: int = 0,
) -> dict:
    """
    Load the option chain for ``symbol``, one build per chain at a time.

    With a ten-second cache window and a page that polls, two viewers -- or
    one viewer and the analysis scan -- asking in the same second would each
    start a full round of TWS snapshots for the same ladder. They share one
    instead. Without this the short window would multiply load rather than
    freshness.
    """
    import singleflight

    key = f"chain-build:{(symbol or '').upper()}:{expiry or 'front'}:{extra_expiries}"
    value, done = singleflight.call(
        key, lambda: _load_chain(symbol, expiry, extra_expiries), timeout=180.0)
    if done and value is not None:
        return value
    return _load_chain(symbol, expiry, extra_expiries)


def _load_chain(
    symbol: str,
    expiry: Optional[str] = None,
    extra_expiries: int = 0,
) -> dict:
    """
    Load the option chain for ``symbol``.

    ``extra_expiries`` additionally quotes a narrower band on the following
    expiries, which is what the 'Flow by Expiration' panel needs.
    """
    symbol = symbol.upper()
    key = f"chain:{symbol}:{expiry or 'front'}:{extra_expiries}"
    cached = cache.get(key, chain_ttl())
    # An empty chain is not a usable answer even when it is flagged OK. Serving
    # it from cache short-circuited the provider fallback below, so a single
    # empty TWS response kept the Options panel blank for the whole TTL.
    if cached and cached.get("status") == "OK" and cached.get("rows"):
        # A chain cached before its front expiry passed is worse than no chain:
        # every metric derived from it -- implied volatility, the expected move,
        # the score -- is solved against a contract that no longer exists. AAPL
        # was serving a 0-DTE chain from the previous session, reporting 646%
        # IV and a 0.16% expected move. Refetch instead.
        if not _expired_front(cached):
            return cached
    recent_failure = cache.get(key, CHAIN_FAIL_TTL)
    if recent_failure and recent_failure.get("status") != "OK":
        return recent_failure

    # The provider leads, and not merely as a stand-in: their chain arrives
    # in one request with published greeks, IV and the day's side split, and
    # answers in about a second. Quoting the same chain through TWS took
    # ~183s and still came back with zero rows, which is what left the
    # Earnings page stuck on "Loading live data".
    if _optiondata_first():
        import unusualwhales_service as od

        fast = od.load_chain(symbol, expiry)
        if fast and fast.get("rows"):
            fast["freshness"] = _freshness.for_chain(fast.get("source") or "")
            cache.put(key, fast)
            return fast

    async def job(ib: IB) -> dict:
        meta = await _chain_meta(ib, symbol)
        spot = meta["spot"]

        expirations = meta["expirations"]
        if not expirations:
            raise ValueError(f"No expirations for {symbol}")

        front = expiry if expiry in expirations else _front_expiry(expirations)
        rows = await _load_expiry_rows(ib, symbol, meta, front, MAX_STRIKES)

        others: list[dict] = []
        if extra_expiries:
            # Later expiries only feed the by-expiration panel, so a narrow
            # band around the money is enough and keeps the request count down.
            for nxt in expirations[1 : 1 + extra_expiries]:
                others.extend(
                    await _load_expiry_rows(
                        ib, symbol, meta, nxt, 8, with_prices=False
                    )
                )

        return {
            "symbol": symbol,
            "spot": round(spot, 2),
            "expiry": front,
            "expiry_label": _fmt_expiry(front),
            "dte": round(_dte(front) or 0.0, 2),
            "expirations": expirations[:24],
            "expiration_labels": [_fmt_expiry(e) for e in expirations[:24]],
            "rows": rows,
            "other_expiry_rows": others,
            "multiplier": meta["multiplier"],
            "market": market_clock(),
            "status": "OK" if rows else "NO_DATA",
            "source": "IBKR",
        }

    try:
        result = ibkr.run(job, timeout=CHAIN_IBKR_BUDGET)
    except IBKRUnavailable as exc:
        # Their chain publishes greeks and IV directly, so it is a better
        # source than a locally solved one -- not just a stand-in. Only fall
        # back to the offline payload if it cannot answer.
        import unusualwhales_service as od

        if od.configured():
            fallback = od.load_chain(symbol, expiry)
            if fallback:
                _stamp_chain(fallback)
                cache.put(key, fallback)
                return fallback

        offline = _offline_chain(symbol, "IBKR_UNAVAILABLE", str(exc))
        _stamp_chain(offline)
        cache.put(key, offline)
        return offline
    except ValueError as exc:
        offline = _offline_chain(symbol, "NO_CHAIN", str(exc))
        _stamp_chain(offline)
        cache.put(key, offline)
        return offline

    if not result.get("rows"):
        # A successful-but-empty chain is not a usable answer; try the other
        # provider before caching an empty panel.
        import unusualwhales_service as od

        if od.configured():
            fallback = od.load_chain(symbol, expiry)
            if fallback and fallback.get("rows"):
                _stamp_chain(fallback)
                cache.put(key, fallback)
                return fallback

    _stamp_chain(result)
    cache.put(key, result)
    return result


def _optiondata_first() -> bool:
    """
    Whether to ask the provider before TWS for option chains.

    Defaults on when Unusual Whales is configured: their chain arrives in one
    request with volume, open interest and the greeks already on it, where
    TWS streams it contract by contract and takes the best part of a minute
    on a wide name. Set OPTIONS_PREFER_IBKR=1 to put TWS back in front.
    """
    import unusualwhales_service as uw

    if os.getenv("OPTIONS_PREFER_IBKR", "").strip() in ("1", "true", "yes"):
        return False
    return uw.configured()


def _front_expiry(expirations: list[str]) -> str:
    """
    The expiry the panels should default to.

    Not simply the first one. On an expiration day the front contract has
    hours of life left, and solving implied volatility against it returns
    nonsense -- AAPL reported 646% IV and a 0.16% expected move from its
    0-DTE chain. A same-day expiry is skipped when a later one exists; it is
    still reachable by asking for it explicitly, which is what the flow panels
    do when 0-DTE activity is the point.
    """
    for candidate in expirations:
        dte = _dte(candidate)
        if dte is not None and dte >= MIN_FRONT_DTE:
            return candidate
    return expirations[0]


def _expired_front(chain: dict) -> bool:
    """True when the chain's front expiry is no longer tradeable."""
    front = str(chain.get("expiry") or "")
    parsed = _parse_expiry(front)
    if not parsed:
        return False
    # Compared on the trading date rather than the instant: a chain cached
    # during the session its contracts expire in is still the right chain for
    # that session.
    return parsed.date() < datetime.now(EASTERN).date()


def _offline_chain(symbol: str, status: str, error: str) -> dict:
    return {
        "symbol": symbol,
        "spot": None,
        "expiry": None,
        "expiry_label": None,
        "dte": None,
        "expirations": [],
        "expiration_labels": [],
        "rows": [],
        "other_expiry_rows": [],
        "multiplier": 100,
        "market": market_clock(),
        "status": status,
        "source": "IBKR",
        "error": error,
    }


# ---------------------------------------------------------------------------
# historical implied volatility -> IV rank & percentile
# ---------------------------------------------------------------------------


def _optiondata_iv_history(symbol: str) -> Optional[dict]:
    """
    IV rank and percentile, solved by the provider against its own history.

    Strictly better than what can be computed here: TWS serves no
    OPTION_IMPLIED_VOLATILITY bars on most symbols, so the IBKR path returns
    NO_DATA and IV rank renders blank. Their ranks arrive as a 0-1 fraction
    and are expressed in the 0-100 this app talks in.

    There is no series to chart -- these are the solved statistics rather
    than the underlying history -- so ``series`` stays empty and the caller
    falls back to IBKR when it actually wants to draw a curve.
    """
    try:
        import unusualwhales_service as uw
    except ImportError:
        return None
    if not uw.configured():
        return None

    # Oldest first: the current reading is the last row, not the first.
    rows = uw._rows(uw.iv_rank(symbol))
    latest = rows[-1] if rows else {}
    if not latest:
        return None

    iv30 = num(latest.get("volatility") or latest.get("implied_volatility"))
    rank = num(latest.get("iv_rank_1y"))
    pct = num(latest.get("iv_percentile_1y") or latest.get("iv_percentile"))
    if iv30 is None and rank is None:
        return None

    # Realised volatility is a separate reading of theirs; asked for only
    # when it is wanted rather than carried on every IV row.
    realised = uw._rows(uw.get(f"/api/stock/{symbol}/volatility/realized",
                               {"timeframe": "1m"}))
    hv = num((realised[-1] if realised else {}).get("realized_volatility"))

    # These arrive as fractions (0.46), not percentages.
    def as_pct(value):
        if value is None:
            return None
        return round(value * 100, 2) if abs(value) <= 5 else round(value, 2)

    # A whole year of IV, for the chart the caller may want to draw.
    series = [{"date": r.get("date"), "iv": as_pct(num(r.get("volatility")))}
              for r in rows if r.get("date")]

    return {
        "symbol": symbol,
        "iv": as_pct(iv30),
        "iv_low": min((p["iv"] for p in series if p["iv"] is not None),
                      default=None),
        "iv_high": max((p["iv"] for p in series if p["iv"] is not None),
                       default=None),
        "iv_rank": as_pct(rank),
        "iv_percentile": as_pct(pct),
        "hv": as_pct(hv),
        "rv20": None,
        "rv30": None,
        "skew_25d_30d": None,
        "iv_term_slope_30_90": None,
        "as_of": latest.get("date"),
        "samples": len(series),
        "series": series,
        "status": "OK",
        "source": "Unusual Whales",
    }


def get_iv_history(symbol: str) -> dict:
    symbol = symbol.upper()
    key = f"ivhist:{symbol}"
    cached = cache.get(key, IV_HISTORY_TTL)
    if cached:
        return cached

    # OptionData answers without TWS and is the only source that reliably
    # returns rank and percentile, so it leads rather than backstops.
    provider = _optiondata_iv_history(symbol)
    if provider and provider.get("iv_rank") is not None:
        cache.put(key, provider)
        return provider

    async def job(ib: IB) -> dict:
        stock = Stock(symbol, "SMART", "USD")
        await ib.qualifyContractsAsync(stock)

        iv_bars = await ib.reqHistoricalDataAsync(
            stock, "", "1 Y", "1 day", "OPTION_IMPLIED_VOLATILITY", True, 1
        )
        hv_bars = await ib.reqHistoricalDataAsync(
            stock, "", "1 Y", "1 day", "HISTORICAL_VOLATILITY", True, 1
        )

        series = [float(b.close) for b in iv_bars if num(b.close)]
        if not series:
            return provider or {
                "symbol": symbol, "status": "NO_DATA", "source": "IBKR"}

        current = series[-1]
        lo, hi = min(series), max(series)
        rank = ((current - lo) / (hi - lo) * 100.0) if hi > lo else None
        below = sum(1 for v in series if v < current)
        percentile = below / len(series) * 100.0

        hv = [float(b.close) for b in hv_bars if num(b.close)]

        return {
            "symbol": symbol,
            "iv": round(current * 100, 2),
            "iv_low": round(lo * 100, 2),
            "iv_high": round(hi * 100, 2),
            "iv_rank": round(rank, 1) if rank is not None else None,
            "iv_percentile": round(percentile, 1),
            "hv": round(hv[-1] * 100, 2) if hv else None,
            "samples": len(series),
            "series": [round(v * 100, 2) for v in series[-120:]],
            "status": "OK",
            "source": "IBKR",
        }

    try:
        result = ibkr.run(job, timeout=90)
    except IBKRUnavailable as exc:
        if provider:
            cache.put(key, provider)
            return provider
        return {
            "symbol": symbol,
            "status": "IBKR_UNAVAILABLE",
            "source": "IBKR",
            "error": str(exc),
        }

    cache.put(key, result)
    return result


# ---------------------------------------------------------------------------
# realized move over the last N comparable windows
# ---------------------------------------------------------------------------


def get_realized_move(symbol: str, horizon_days: int, windows: int = 4) -> dict:
    """
    Average absolute return over the last ``windows`` non-overlapping windows
    of ``horizon_days`` trading days. This is the realised counterpart to the
    option-implied expected move.
    """
    symbol = symbol.upper()
    horizon = max(int(horizon_days), 1)
    key = f"realized:{symbol}:{horizon}:{windows}"
    cached = cache.get(key, 600.0)
    if cached:
        return cached

    def summarise(closes: list[float], source: str) -> dict:
        needed = horizon * windows + 1
        if len(closes) < needed:
            return {"symbol": symbol, "status": "INSUFFICIENT_DATA",
                    "source": source}

        moves = []
        for w in range(windows):
            end = len(closes) - 1 - w * horizon
            start = end - horizon
            if start < 0:
                break
            moves.append(abs(closes[end] - closes[start]) / closes[start] * 100.0)

        if not moves:
            return {"symbol": symbol, "status": "INSUFFICIENT_DATA",
                    "source": source}

        return {
            "symbol": symbol,
            "horizon_days": horizon,
            "windows": len(moves),
            "moves": [round(m, 2) for m in moves],
            "average": round(sum(moves) / len(moves), 2),
            "status": "OK",
            "source": source,
        }

    # This used to pull a year of daily bars from IBKR on every cold overview,
    # which cost 15 seconds for what is ultimately a handful of closes. The
    # chart already loads the same history from a fast provider and caches it,
    # so by the time this runs the bars are usually free.
    from live_market_service import _fallback_bars

    bars, source = _fallback_bars(symbol, "1 Y")
    if bars:
        closes = [float(b["close"]) for b in bars if num(b.get("close"))]
        result = summarise(closes, source or "PROVIDER")
        cache.put(key, result)
        return result

    # No provider answered; fall back to TWS rather than showing nothing.
    async def job(ib: IB) -> dict:
        stock = Stock(symbol, "SMART", "USD")
        await ib.qualifyContractsAsync(stock)
        ibkr_bars = await ib.reqHistoricalDataAsync(
            stock, "", "1 Y", "1 day", "TRADES", True, 1
        )
        return summarise(
            [float(b.close) for b in ibkr_bars if num(b.close)], "IBKR")

    try:
        result = ibkr.run(job, timeout=90)
    except IBKRUnavailable as exc:
        return {
            "symbol": symbol,
            "status": "IBKR_UNAVAILABLE",
            "source": "IBKR",
            "error": str(exc),
        }

    cache.put(key, result)
    return result


# ---------------------------------------------------------------------------
# the flow tape
# ---------------------------------------------------------------------------


def _classify_side(price: float, bid: Optional[float], ask: Optional[float]) -> str:
    """Where a print landed relative to the quote at that moment."""
    if bid is None or ask is None or ask <= bid:
        return "MID"
    span = ask - bid
    if price >= ask - span * 0.15:
        return "BUY"
    if price <= bid + span * 0.15:
        return "SELL"
    return "MID"


def _quote_at(quotes: list[tuple[datetime, float, float]], when: datetime):
    """Last bid/ask observed at or before ``when`` (quotes are time-ordered)."""
    lo, hi = 0, len(quotes) - 1
    found = None
    while lo <= hi:
        mid = (lo + hi) // 2
        if quotes[mid][0] <= when:
            found = quotes[mid]
            lo = mid + 1
        else:
            hi = mid - 1
    if not found:
        return None, None
    return found[1], found[2]


def _cluster_prints(prints: list[dict]) -> list[dict]:
    """
    Collapse prints on one contract into trades.

    Consecutive prints inside SWEEP_WINDOW_MS that hit two or more exchanges
    become a single SWEEP. A lone oversized print becomes a BLOCK. Everything
    else stays an ordinary print.
    """
    if not prints:
        return []

    prints.sort(key=lambda p: p["time"])
    out: list[dict] = []
    group: list[dict] = [prints[0]]

    def flush(g: list[dict]) -> None:
        if not g:
            return
        exchanges = {p["exchange"] for p in g if p["exchange"]}
        size = sum(p["size"] for p in g)
        notional = sum(p["notional"] for p in g)
        vwap = (notional / (size * 100.0)) if size else g[0]["price"]

        if len(g) > 1 and len(exchanges) >= SWEEP_MIN_EXCHANGES:
            kind = "SWEEP"
        elif size >= BLOCK_MIN_SIZE and len(exchanges) <= 1:
            kind = "BLOCK"
        else:
            kind = "SPLIT" if len(g) > 1 else "SINGLE"

        buys = sum(p["size"] for p in g if p["side"] == "BUY")
        sells = sum(p["size"] for p in g if p["side"] == "SELL")
        side = "BUY" if buys > sells else "SELL" if sells > buys else "MID"

        out.append(
            {
                "time": g[0]["time"],
                "price": round(vwap, 4),
                "size": int(size),
                "notional": round(notional, 2),
                "kind": kind,
                "side": side,
                "exchanges": sorted(exchanges),
                "prints": len(g),
            }
        )

    for p in prints[1:]:
        gap = (p["time"] - group[-1]["time"]).total_seconds() * 1000.0
        if gap <= SWEEP_WINDOW_MS:
            group.append(p)
        else:
            flush(group)
            group = [p]
    flush(group)

    return out


async def _contract_tape(ib: IB, row: dict, contract: Option) -> list[dict]:
    """Real prints for one contract, clustered into sweeps / blocks / singles."""
    end = datetime.now(timezone.utc)
    try:
        trades = await ib.reqHistoricalTicksAsync(
            contract, "", end, TICKS_PER_CONTRACT, "TRADES", False, False
        )
    except Exception:  # noqa: BLE001 - one bad contract must not kill the tape
        return []

    prints: list[dict] = []
    for t in trades:
        px, size = num(t.price), num(t.size)
        if not px or not size or t.time is None:
            continue
        prints.append(
            {
                "time": t.time,
                "price": px,
                "size": size,
                "exchange": getattr(t, "exchange", "") or "",
                "side": "MID",
                "notional": px * size * 100.0,
            }
        )

    clustered = _cluster_prints(prints)

    out = []
    for c in clustered:
        if c["notional"] < MIN_FLOW_NOTIONAL:
            continue
        local = c["time"].astimezone(EASTERN)
        out.append(
            {
                "time": local.strftime("%H:%M:%S"),
                "timestamp": local.isoformat(),
                "epoch": c["time"].timestamp(),
                "utc": c["time"],
                "symbol": row["symbol"],
                "con_id": row["con_id"],
                "expiry": row["expiry"],
                "expiry_label": row["expiry_label"],
                "strike": row["strike"],
                "right": row["right"],
                "type": "Call" if row["right"] == "C" else "Put",
                "contracts": c["size"],
                "price": c["price"],
                "notional": c["notional"],
                "kind": c["kind"],
                "side": "Mid",
                "sentiment": None,
                "exchanges": c["exchanges"],
                "prints": c["prints"],
            }
        )
    return out


async def _resolve_side(ib: IB, contract: Option, trade: dict) -> None:
    """
    Classify one trade against the quote that was standing when it printed.

    A bulk BID_ASK pull is no use here: option quotes update thousands of
    times a second, so the last N ticks before 'now' all land inside the final
    moment of the session and cover none of the earlier prints. Anchoring the
    request to the trade's own timestamp returns the quote that actually
    applied to it.
    """
    try:
        quotes = await ib.reqHistoricalTicksAsync(
            contract, "", trade["utc"], 1, "BID_ASK", False, False
        )
    except Exception:  # noqa: BLE001
        return
    if not quotes:
        return

    q = quotes[-1]
    bid, ask = num(q.priceBid), num(q.priceAsk)
    side = _classify_side(trade["price"], bid, ask)
    trade["side"] = side.title()
    trade["quote_bid"] = bid
    trade["quote_ask"] = ask


def _apply_sentiment(trade: dict) -> None:
    is_call = trade["right"] == "C"
    side = trade["side"].upper()
    if side == "MID":
        # Direction of an unclassified print is genuinely unknown.
        trade["sentiment"] = "Bullish" if is_call else "Bearish"
        trade["sentiment_confidence"] = "LOW"
    else:
        bullish = (is_call and side == "BUY") or (not is_call and side == "SELL")
        trade["sentiment"] = "Bullish" if bullish else "Bearish"
        trade["sentiment_confidence"] = "HIGH"


def get_flow(symbol: str, chain: Optional[dict] = None, limit: int = 40) -> dict:
    symbol = symbol.upper()
    chain = chain or load_chain(symbol, extra_expiries=3)

    if chain.get("status") != "OK":
        return {
            "symbol": symbol,
            "trades": [],
            "status": chain.get("status", "NO_DATA"),
            "source": "IBKR",
            "error": chain.get("error"),
        }

    key = f"flow:{symbol}:{chain.get('expiry')}:{limit}"
    cached = cache.get(key, chain_ttl())
    if cached:
        return cached

    all_rows = chain["rows"] + chain["other_expiry_rows"]

    # During RTH the busiest contracts are the ones with volume. Pre-market and
    # after the close nothing has traded yet today, so fall back to ranking by
    # open interest - the tape request still returns the previous session.
    traded = [r for r in all_rows if r.get("volume")]
    if traded:
        traded.sort(key=lambda r: r["notional"], reverse=True)
        targets = traded[:FLOW_CONTRACTS]
        basis = "SESSION_VOLUME"
    else:
        resting = [r for r in all_rows if r.get("open_interest")]
        resting.sort(key=lambda r: r["oi_notional"], reverse=True)
        targets = resting[:FLOW_CONTRACTS]
        basis = "OPEN_INTEREST"

    if not targets:
        result = {
            "symbol": symbol,
            "trades": [],
            "status": "NO_VOLUME",
            "note": "No option volume or open interest reported.",
            "source": "IBKR",
        }
        cache.put(key, result)
        return result

    async def job(ib: IB) -> list[dict]:
        contracts = [
            Option(symbol, _ib_expiry(r["expiry"]), r["strike"], r["right"],
                   "SMART", tradingClass=symbol)
            for r in targets
        ]
        qualified = await ib.qualifyContractsAsync(*contracts)
        pairs = [(row, c) for row, c in zip(targets, qualified) if c and c.conId]

        tapes = await asyncio.gather(
            *[_contract_tape(ib, row, c) for row, c in pairs],
            return_exceptions=True,
        )

        merged: list[dict] = []
        for tape in tapes:
            if isinstance(tape, list):
                merged.extend(tape)

        # Side classification costs one anchored request per trade, so only the
        # prints that actually reach the UI are resolved.
        by_con = {c.conId: c for _, c in pairs}
        merged.sort(key=lambda t: t["notional"], reverse=True)
        head = merged[:SIDE_RESOLVE_LIMIT]
        await asyncio.gather(
            *[
                _resolve_side(ib, by_con[t["con_id"]], t)
                for t in head
                if t["con_id"] in by_con
            ],
            return_exceptions=True,
        )
        return merged

    try:
        trades = ibkr.run(job, timeout=300)
    except IBKRUnavailable as exc:
        return {
            "symbol": symbol,
            "trades": [],
            "status": "IBKR_UNAVAILABLE",
            "source": "IBKR",
            "error": str(exc),
        }

    for t in trades:
        _apply_sentiment(t)
        t.pop("utc", None)

    trades.sort(key=lambda t: t["epoch"], reverse=True)

    # The tape is the only real record of traded size when TWS reports no
    # session volume (pre-market / after the close), so expose it separately.
    tape_volume: dict[tuple, float] = {}
    for t in trades:
        tape_volume[(t["expiry"], t["strike"], t["right"])] = (
            tape_volume.get((t["expiry"], t["strike"], t["right"]), 0.0)
            + t["contracts"]
        )

    classified = [t for t in trades if t["side"].upper() != "MID"]

    result = {
        "symbol": symbol,
        "trades": trades[:limit],
        "all_count": len(trades),
        "sweeps": sum(1 for t in trades if t["kind"] == "SWEEP"),
        "blocks": sum(1 for t in trades if t["kind"] == "BLOCK"),
        "classified": len(classified),
        "ranking_basis": basis,
        "contracts_sampled": len(targets),
        "tape_volume": [
            {"expiry": k[0], "strike": k[1], "right": k[2], "contracts": v}
            for k, v in tape_volume.items()
        ],
        "session_date": trades[0]["timestamp"][:10] if trades else None,
        "status": "OK" if trades else "NO_TRADES",
        "source": "IBKR",
    }
    cache.put(key, result)
    return result


# ---------------------------------------------------------------------------
# live quotes over the displayed ladder
# ---------------------------------------------------------------------------

# How long a live quote pass stands. The chain page asks every few seconds
# while TWS is serving, and viewers asking in the same instant share a pass.
LIVE_QUOTE_TTL = 3.0

# Long enough for TWS to fill bid, ask and last on a subscribed line. Open
# interest is not requested -- it is a once-a-day figure, and waiting for it
# is what makes a full TWS chain build take ten seconds per batch.
LIVE_QUOTE_SETTLE = 2.0

# The listed contracts on an expiry do not change during the day, so they are
# qualified once and reused. Re-asking TWS for contract details on every
# refresh would cost more than the quotes themselves.
LIVE_CONTRACT_TTL = 1800.0


def live_quotes(symbol: str, expiry: str, strikes: list[float],
                budget: float = 8.0) -> dict:
    """
    Live bid, ask and last from TWS for the strikes on screen.

    The chain's ladder -- which strikes, which expiry, the provider's IV and
    greeks -- comes from the fast provider chain, and stays there: building a
    whole chain through TWS once took three minutes and everything else in
    the app is built on that chain. What the provider cannot give is a price
    that moves, so this overlays one: a short snapshot of just the contracts
    being looked at. About forty lines, a two-second settle, no open interest.

    Returns ``{"quotes": {"C:215.0": {...}}, "status": ...}``. Never raises:
    a page that cannot get live quotes shows the delayed ones, labelled.
    """
    import singleflight

    symbol = (symbol or "").upper()
    if not symbol or not expiry or not strikes:
        return {"status": "NO_DATA", "quotes": {}}
    if ibkr.is_offline() or ibkr.serving_error():
        return {"status": "PROVIDER_OFFLINE", "quotes": {},
                "detail": ibkr.serving_error() or "TWS is not serving data."}

    wanted = sorted({round(float(s), 4) for s in strikes})
    key = f"livequotes:{symbol}:{expiry}:{wanted[0]}:{wanted[-1]}:{len(wanted)}"
    cached = cache.get(key, LIVE_QUOTE_TTL)
    if cached:
        return cached

    def build() -> dict:
        async def job(ib: IB) -> dict:
            ckey = f"livecontracts:{symbol}:{_ib_expiry(expiry)}"
            contracts = cache.get(ckey, LIVE_CONTRACT_TTL)
            if not contracts:
                template = Option(symbol, _ib_expiry(expiry), 0, "", "SMART")
                details = await ib.reqContractDetailsAsync(template)
                listed = [cd.contract for cd in details if cd.contract.strike]
                # One symbol can list several trading classes on the same date;
                # the one named after the symbol is the standard contract.
                standard = [c for c in listed if c.tradingClass == symbol]
                contracts = standard or listed
                cache.put(ckey, contracts)

            want = set(wanted)
            chosen = [c for c in contracts
                      if round(float(c.strike), 4) in want]
            if not chosen:
                return {"status": "NO_DATA", "quotes": {}}

            out: dict[str, dict] = {}
            async with _MD_TYPE_LOCK:
                ib.reqMarketDataType(IBKR_MARKET_DATA_TYPE)
                await asyncio.sleep(0.2)
                for i in range(0, len(chosen), MD_BATCH):
                    batch = chosen[i:i + MD_BATCH]
                    tickers = [ib.reqMktData(c, "", False, False) for c in batch]
                    await asyncio.sleep(LIVE_QUOTE_SETTLE)
                    for c, t in zip(batch, tickers):
                        right = "C" if c.right.upper().startswith("C") else "P"
                        bid, ask, last = num(t.bid), num(t.ask), num(t.last)
                        out[f"{right}:{round(float(c.strike), 4)}"] = {
                            "bid": bid if bid and bid > 0 else None,
                            "ask": ask if ask and ask > 0 else None,
                            "last": last if last and last > 0 else None,
                        }
                        ib.cancelMktData(c)
            return {"status": "OK", "quotes": out,
                    "as_of": datetime.now(timezone.utc).isoformat()}

        try:
            result = ibkr.run(job, timeout=budget)
        except Exception as exc:  # noqa: BLE001 - delayed quotes still render
            return {"status": "PROVIDER_OFFLINE", "quotes": {},
                    "detail": f"Live quotes unavailable: {exc}"[:200]}
        if result.get("status") == "OK" and result.get("quotes"):
            cache.put(key, result)
        return result

    value, done = singleflight.call(key, build, timeout=budget + 2.0)
    if done and value is not None:
        return value
    return {"status": "PENDING", "quotes": {},
            "detail": "Live quotes are still arriving."}
