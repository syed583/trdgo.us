"""
The market at a glance: indices, sectors, breadth, movers and macro.

Built from quotes rather than a screener feed, because no configured provider
sells one. That shapes what can honestly be shown:

* **Breadth is measured over a named universe**, not "the S&P 500". Counting
  advancers across 500 names needs 500 quotes; this counts them across the
  basket below and says how many that was. A breadth figure whose denominator
  is hidden is worse than a smaller one that states it.
* **Some rows are ETF proxies.** There is no index quote here for the FTSE or
  for crude, so EWU and USO stand in -- and every proxy row is flagged, because
  an ETF is a different instrument with its own tracking error and hours, not a
  cheaper version of the index.

Nothing is invented. A symbol the provider cannot price is absent from the
totals rather than carried at zero.
"""

from __future__ import annotations

from typing import Any, Optional

import live_market_service as market

# Long enough to outlast a build. At sixty seconds the window expired while
# the next build was still running, so the page was permanently rebuilding
# and permanently slow. Index and sector prices on a summary screen do not
# need finer granularity than this.
PULSE_TTL_OPEN = 180.0
PULSE_TTL_CLOSED = 900.0

# Seventy-odd symbols, each a provider round trip. Serially that is minutes;
# even in parallel a slow provider can hold the page, so the fan-out is
# bounded and the page is drawn from whatever answered inside it.
WORKERS = 14
QUOTES_BUDGET = 20.0

# How long a page request will wait for a build before it is answered with
# whatever exists. Shorter than the build on purpose: the first visitor of the
# day should get a page that says it is filling in, not a thirty-second stare
# at a spinner. A warmer keeps this from being the common case.
PULSE_WAIT = 8.0

# Index cards. ``index`` is the real index symbol where a provider carries it;
# ``proxy`` is the ETF used when it does not.
INDEX_CARDS = [
    {"label": "S&P 500", "index": "SPX", "proxy": "SPY"},
    {"label": "Nasdaq 100", "index": "NDX", "proxy": "QQQ"},
    {"label": "Dow Jones", "index": None, "proxy": "DIA"},
    {"label": "Russell 2000", "index": None, "proxy": "IWM"},
    {"label": "VIX", "index": "VIX", "proxy": None},
]

SECTOR_ETFS = [
    {"symbol": "XLK", "label": "Technology"},
    {"symbol": "XLC", "label": "Communication Services"},
    {"symbol": "XLY", "label": "Consumer Cyclical"},
    {"symbol": "XLI", "label": "Industrials"},
    {"symbol": "XLF", "label": "Financials"},
    {"symbol": "XLV", "label": "Healthcare"},
    {"symbol": "XLU", "label": "Utilities"},
    {"symbol": "XLRE", "label": "Real Estate"},
    {"symbol": "XLP", "label": "Consumer Defensive"},
    {"symbol": "XLE", "label": "Energy"},
    {"symbol": "XLB", "label": "Materials"},
]

# The breadth and movers universe, grouped so the heatmap has its sectors.
# Large and liquid on purpose: these are the names whose moves a US desk reads
# as "the market", and every one of them is priced by the quote providers.
UNIVERSE = {
    "Technology": ["AAPL", "MSFT", "NVDA", "AVGO", "AMD", "MU", "ORCL", "CRM",
                   "INTC", "SMCI", "PLTR"],
    "Communication Services": ["GOOGL", "META", "NFLX", "DIS", "T"],
    "Consumer Cyclical": ["AMZN", "TSLA", "HD", "NKE", "MCD", "LOW", "F", "GM"],
    "Financials": ["JPM", "BAC", "WFC", "GS", "C", "AXP", "MS"],
    "Healthcare": ["LLY", "UNH", "JNJ", "PFE", "MRK", "ABBV", "AMGN"],
    "Industrials": ["CAT", "BA", "GE", "HON", "UPS", "MMM"],
    "Energy": ["XOM", "CVX", "COP", "OXY"],
    "Consumer Defensive": ["WMT", "COST", "KO", "PEP", "PG"],
}

# Macro rows. Every one is a proxy here: no configured provider quotes a yield,
# the dollar index or a crude contract directly, so the tracking instrument is
# named and flagged rather than dressed up as the thing it follows.
INDICATORS = [
    {"label": "10Y Treasury", "symbol": "IEF", "proxy_for": "7-10Y Treasury ETF"},
    {"label": "US Dollar", "symbol": "UUP", "proxy_for": "Dollar Index ETF"},
    {"label": "Gold", "symbol": "GLD", "proxy_for": "Gold ETF"},
    {"label": "Crude Oil", "symbol": "USO", "proxy_for": "Crude Oil ETF"},
    {"label": "Bitcoin", "symbol": "IBIT", "proxy_for": "Spot Bitcoin ETF"},
]

GLOBAL_MARKETS = [
    {"label": "FTSE 100", "symbol": "EWU", "proxy_for": "UK large cap ETF"},
    {"label": "DAX", "symbol": "EWG", "proxy_for": "Germany ETF"},
    {"label": "Nikkei 225", "symbol": "EWJ", "proxy_for": "Japan ETF"},
    {"label": "Hang Seng", "symbol": "EWH", "proxy_for": "Hong Kong ETF"},
    {"label": "China A50", "symbol": "FXI", "proxy_for": "China large cap ETF"},
]


def _num(value: Any) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _all_symbols() -> list[str]:
    names: list[str] = []
    for card in INDEX_CARDS:
        for candidate in (card["index"], card["proxy"]):
            if candidate and candidate not in names:
                names.append(candidate)
    for spec in SECTOR_ETFS + INDICATORS + GLOBAL_MARKETS:
        if spec["symbol"] not in names:
            names.append(spec["symbol"])
    for members in UNIVERSE.values():
        for symbol in members:
            if symbol not in names:
                names.append(symbol)
    return names


def _quotes(symbols: list[str]) -> dict[str, dict]:
    """
    One quote per symbol, in parallel, through the normal fallback chain.

    ``get_batch`` is not used: it asks TWS first and only falls back wholesale,
    and this page is mostly ETFs and index symbols that the HTTP providers
    answer faster than a broker round trip.
    """
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from concurrent.futures import TimeoutError as FuturesTimeout

    def one(symbol: str) -> tuple[str, Optional[dict]]:
        try:
            return symbol, market._fallback_quote(symbol)
        except Exception:  # noqa: BLE001 - one symbol must not stop the page
            return symbol, None

    out: dict[str, dict] = {}
    deadline = time.time() + QUOTES_BUDGET

    # Not a ``with`` block: it joins every worker on exit, so the deadline
    # would not actually end the wait. Whatever has not answered by then is
    # simply not measured -- and the page already reports how many names it
    # measured, so a short read is visible rather than silently assumed.
    pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="pulse")
    try:
        jobs = [pool.submit(one, symbol) for symbol in symbols]
        try:
            for job in as_completed(jobs, timeout=QUOTES_BUDGET):
                symbol, quote = job.result()
                if quote and quote.get("price") is not None:
                    out[symbol] = quote
                if time.time() >= deadline:
                    break
        except FuturesTimeout:
            pass
    finally:
        # The quotes that land late are cached by the fetch itself, so the
        # next build of this page finds them already there.
        pool.shutdown(wait=False, cancel_futures=True)
    return out


def _row(label: str, symbol: str, quotes: dict, *,
         is_proxy: bool = False, proxy_for: Optional[str] = None) -> dict:
    quote = quotes.get(symbol) or {}
    return {
        "label": label,
        "symbol": symbol,
        "price": _num(quote.get("price")),
        "change": _num(quote.get("change")),
        "change_percent": _num(quote.get("change_percent")),
        "is_proxy": is_proxy,
        "proxy_for": proxy_for,
        "source": quote.get("source"),
        "status": "OK" if quote.get("price") is not None else "NO_DATA",
    }


def get_pulse() -> dict:
    """Indices, sectors, breadth, movers, macro and global proxies."""
    cached = market.cache.get(
        "market_pulse", market.session_ttl(PULSE_TTL_OPEN, PULSE_TTL_CLOSED))
    if cached:
        return cached

    # Two people opening this page at once, or one refreshing it twice, must
    # not start two seventy-symbol scans that then compete with each other.
    import singleflight

    value, done = singleflight.call(
        "market_pulse", _build_pulse, timeout=PULSE_WAIT)
    if done and value is not None:
        return value
    stale = market.cache.get("market_pulse", PULSE_TTL_CLOSED * 4)
    if stale:
        return {**stale, "status": "STALE", "building": True}
    return {"status": "BUILDING", "building": True,
            "note": "Reading the market now; this page fills in shortly."}


def warm() -> None:
    """Build the page in the background so a visitor rarely waits for it."""
    import singleflight

    singleflight.call("market_pulse", _build_pulse, timeout=180.0)


def _build_pulse() -> dict:
    quotes = _quotes(_all_symbols())

    # --- index cards -------------------------------------------------------
    indices = []
    for card in INDEX_CARDS:
        chosen = None
        proxy = False
        for candidate, as_proxy in ((card["index"], False),
                                    (card["proxy"], True)):
            if candidate and candidate in quotes:
                chosen, proxy = candidate, as_proxy
                break
        if not chosen:
            indices.append({"label": card["label"], "symbol": None,
                            "price": None, "change": None,
                            "change_percent": None, "is_proxy": False,
                            "status": "NO_DATA"})
            continue
        indices.append(_row(card["label"], chosen, quotes, is_proxy=proxy,
                            proxy_for=("index" if proxy else None)))

    # --- sectors -----------------------------------------------------------
    sectors = [_row(s["label"], s["symbol"], quotes) for s in SECTOR_ETFS]
    sectors.sort(key=lambda r: (r["change_percent"] is None,
                                -(r["change_percent"] or 0)))

    # --- universe: heatmap, breadth, movers --------------------------------
    members: list[dict] = []
    for sector, symbols in UNIVERSE.items():
        for symbol in symbols:
            quote = quotes.get(symbol)
            if not quote:
                continue
            members.append({
                "symbol": symbol,
                "sector": sector,
                "price": _num(quote.get("price")),
                "change": _num(quote.get("change")),
                "change_percent": _num(quote.get("change_percent")),
            })

    requested = sum(len(v) for v in UNIVERSE.values())
    advancing = sum(1 for m in members if (m["change_percent"] or 0) > 0)
    declining = sum(1 for m in members if (m["change_percent"] or 0) < 0)
    unchanged = len(members) - advancing - declining

    heatmap = []
    for sector, symbols in UNIVERSE.items():
        rows = [m for m in members if m["sector"] == sector]
        if not rows:
            continue
        rows.sort(key=lambda m: -(m["change_percent"] or 0))
        heatmap.append({
            "sector": sector,
            "members": rows,
            "count": len(rows),
            "average": round(
                sum(m["change_percent"] or 0 for m in rows) / len(rows), 2),
        })
    heatmap.sort(key=lambda s: -s["average"])

    ranked = sorted((m for m in members if m["change_percent"] is not None),
                    key=lambda m: -(m["change_percent"] or 0))

    # --- macro and global --------------------------------------------------
    indicators = [_row(i["label"], i["symbol"], quotes, is_proxy=True,
                       proxy_for=i["proxy_for"]) for i in INDICATORS]
    globals_ = [_row(g["label"], g["symbol"], quotes, is_proxy=True,
                     proxy_for=g["proxy_for"]) for g in GLOBAL_MARKETS]

    # --- options put/call, from the flow tape ------------------------------
    put_call = {"status": "NO_DATA"}
    try:
        import market_flow_service as mflow

        summary = mflow.get_summary()
        if summary.get("status") == "OK":
            put_call = {
                "status": "OK",
                "put_call_ratio": summary.get("put_call_ratio"),
                "call_put_ratio": summary.get("call_put_ratio"),
                "call_premium": summary.get("call_premium"),
                "put_premium": summary.get("put_premium"),
                "sentiment": summary.get("sentiment"),
                "session": summary.get("session"),
            }
    except Exception:  # noqa: BLE001 - the page renders without it
        put_call = {"status": "PROVIDER_OFFLINE"}

    vix = next((i for i in indices if i["label"] == "VIX"), None)

    result = {
        "status": "OK" if members else "NO_DATA",
        "indices": indices,
        "sectors": sectors,
        "heatmap": heatmap,
        "breadth": {
            "advancing": advancing,
            "declining": declining,
            "unchanged": unchanged,
            "measured": len(members),
            "requested": requested,
            "advancing_percent": (round(advancing / len(members) * 100)
                                  if members else None),
            # The denominator, stated. "76% advancing" means nothing without
            # knowing whether that is of 50 names or of 3,000.
            "detail": (f"Across {len(members)} of {requested} large-cap names "
                       "priced right now, not the whole index."),
        },
        "gainers": ranked[:5],
        "losers": ranked[-5:][::-1] if len(ranked) >= 5 else [],
        "indicators": indicators,
        "global": globals_,
        "put_call": put_call,
        "sentiment": _sentiment(advancing, len(members), vix, put_call),
        "market": market.market_clock(),
        "source": "QUOTE_PROVIDERS",
        "detail": (f"{len(quotes)} of {len(_all_symbols())} symbols priced. "
                   "Rows marked as proxies are tracking ETFs, not the index "
                   "itself."),
    }
    market.cache.put("market_pulse", result)
    return result


def _sentiment(advancing: int, measured: int, vix: Optional[dict],
               put_call: dict) -> dict:
    """
    A composite read, built from this app's own measurements.

    Explicitly **not** the CNN Fear & Greed Index: that is a specific published
    series with its own seven inputs, and putting its name on a different
    calculation would be a lie the reader cannot check. This is breadth, the
    volatility level and the options tape, each named with its own
    contribution.
    """
    parts: list[dict] = []

    if measured:
        breadth = advancing / measured * 100
        parts.append({"name": "Advancing share", "score": round(breadth, 1),
                      "detail": f"{advancing} of {measured} names higher."})

    level = _num((vix or {}).get("price"))
    if level is not None:
        # 12 is complacent, 32 is fearful; linear between and clamped outside.
        score = max(0.0, min(100.0, (32.0 - level) / 20.0 * 100.0))
        parts.append({"name": "Volatility", "score": round(score, 1),
                      "detail": f"VIX at {level:.2f}."})

    ratio = _num(put_call.get("put_call_ratio"))
    if ratio:
        # 0.7 is call-heavy, 1.3 put-heavy.
        score = max(0.0, min(100.0, (1.3 - ratio) / 0.6 * 100.0))
        parts.append({"name": "Options tape", "score": round(score, 1),
                      "detail": f"Put/call {ratio:.2f} on premium-weighted flow."})

    if not parts:
        return {"status": "NO_DATA",
                "detail": "None of the sentiment inputs could be measured."}

    value = round(sum(p["score"] for p in parts) / len(parts))
    label = ("Extreme greed" if value >= 80 else "Greed" if value >= 60
             else "Neutral" if value >= 40 else "Fear" if value >= 20
             else "Extreme fear")
    return {
        "status": "OK",
        "value": value,
        "label": label,
        "components": parts,
        "detail": ("A composite of this app's own breadth, volatility and "
                   "options readings -- not the CNN Fear & Greed Index, which "
                   "is a different series with different inputs."),
    }
