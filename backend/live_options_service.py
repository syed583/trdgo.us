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

import os
import math
from datetime import datetime, timedelta, timezone
import freshness as _freshness
from typing import Any, Optional


import black_scholes as bs
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
# The feed answers in about three seconds but does not cover every symbol --
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
    # Both spellings occur: "20260914" and "2026-09-14".
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

    The feed publishes "2026-09-16" where the compact "20260916" is wanted.
    Once it became the primary chain source its dashed dates reached the
    contract builder unchanged, and every qualification failed with error
    10372 -- a whole panel of option quotes lost to four hyphens.
    """
    return str(value or "").replace("-", "")


def _fmt_expiry(expiry: str) -> str:
    parsed = _parse_expiry(expiry)
    return parsed.strftime("%m/%d/%y") if parsed else expiry


# ---------------------------------------------------------------------------
# chain loading
# ---------------------------------------------------------------------------


def _stamp_chain(payload: dict) -> dict:
    """Mark a chain with its real freshness -- live, delayed, or last close."""
    try:
        from live_market_service import market_clock
        session = (market_clock() or {}).get("session")
        payload["freshness"] = _freshness.for_chain(
            payload.get("source") or "", payload.get("as_of"), session)
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

    # One source, and not merely as a stand-in: their chain arrives in one
    # request with published greeks, IV and the day's side split, and answers
    # in about a second. Quoting the same chain through TWS took ~183s and
    # still came back with zero rows, which is what left the Earnings page
    # stuck on "Loading live data" -- and the IV it produced was solved here
    # from bid/ask rather than published by anyone.
    import unusualwhales_service as od

    if od.configured():
        chain = od.load_chain(symbol, expiry)
        if chain and chain.get("rows"):
            _stamp_chain(chain)
            cache.put(key, chain)
            return chain

    offline = _offline_chain(
        symbol,
        "PROVIDER_NOT_CONFIGURED" if not od.configured() else "NO_CHAIN",
        "UNUSUAL_WHALES_API_KEY is not set." if not od.configured()
        else f"No option chain returned for {symbol}.")
    _stamp_chain(offline)
    cache.put(key, offline)
    return offline


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
        "source": "UNUSUAL_WHALES",
        "error": error,
    }


# ---------------------------------------------------------------------------
# historical implied volatility -> IV rank & percentile
# ---------------------------------------------------------------------------


def _provider_iv_history(symbol: str) -> Optional[dict]:
    """
    IV rank and percentile, solved by the provider against its own history.

    Strictly better than what could be computed here: this app never had an
    implied-volatility series of its own, and the broker feed that used to
    sit behind this served no OPTION_IMPLIED_VOLATILITY bars on most symbols,
    so IV rank rendered blank. Their ranks arrive as a 0-1 fraction and are
    expressed in the 0-100 this app talks in.

    There is no series to chart -- these are the solved statistics rather
    than the underlying history -- so ``series`` stays empty.
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

    # The feed is the only source that publishes rank and percentile. The
    # TWS path that stood behind this asked for a year of
    # OPTION_IMPLIED_VOLATILITY bars and computed the rank here -- a
    # different measurement under the same name, which is worth not having
    # two of.
    provider = _provider_iv_history(symbol)
    if provider:
        cache.put(key, provider)
        return provider

    return {
        "symbol": symbol,
        "status": "DATA_UNAVAILABLE",
        "source": "UNUSUAL_WHALES",
        "error": f"No implied-volatility history for {symbol}.",
    }


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

    # This used to pull a year of daily bars from IBKR on every cold
    # overview, which cost 15 seconds for what is ultimately a handful of
    # closes. The chart already loads the same history and caches it, so by
    # the time this runs the bars are usually free.
    from live_market_service import _fallback_bars

    bars, source = _fallback_bars(symbol, "1 Y")
    if bars:
        closes = [float(b["close"]) for b in bars if num(b.get("close"))]
        result = summarise(closes, source or "PROVIDER")
        cache.put(key, result)
        return result

    return {
        "symbol": symbol,
        "status": "DATA_UNAVAILABLE",
        "source": "UNUSUAL_WHALES",
        "error": f"No daily history for {symbol}.",
    }


# ---------------------------------------------------------------------------
# the flow tape
# ---------------------------------------------------------------------------


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
    """
    The print tape for one symbol.

    This used to build the tape here: pull historical TRADES ticks per
    contract, pull BID_ASK ticks alongside them, and infer from where each
    print landed in the spread whether it was a buy or a sell. That inference
    was the weakest link on the page -- a reconstruction, and one that cost
    up to five minutes of tick requests per symbol.

    The feed publishes the side itself, counted rather than inferred, so the
    work is no longer ours to do. The signature is kept because the scorer
    and the analytics both call it with a chain they have already loaded;
    ``chain`` and ``limit`` are now accepted and unused, since the provider
    returns the whole tape in one request and ranks it itself.
    """
    symbol = symbol.upper()
    import uw_flow_service as uwflow

    return uwflow.get_flow(symbol)
