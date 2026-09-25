"""
One place that answers "what can this installation actually do right now?".

Every panel in the UI keys off these statuses, so the distinction between a
provider being down, an entitlement being absent, and a genuine application
bug stays visible instead of collapsing into "error".
"""

from __future__ import annotations

import time
from typing import Any, Optional

from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import text

import live_market_service as market
from database import engine

# Status vocabulary shared with the frontend.
OK = "OK"
PROVIDER_OFFLINE = "PROVIDER_OFFLINE"
ENTITLEMENT_REQUIRED = "ENTITLEMENT_REQUIRED"
DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
TEST_DATA = "TEST_DATA"
PROVIDER_NOT_CONFIGURED = "PROVIDER_NOT_CONFIGURED"
PARTIAL_DATA = "PARTIAL_DATA"

HEALTH_TTL = 30.0


def _database() -> dict:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": OK, "detail": "PostgreSQL reachable"}
    except Exception as exc:  # noqa: BLE001
        return {"status": PROVIDER_OFFLINE, "detail": type(exc).__name__}


def _sec() -> dict:
    """SEC has no ping endpoint; a cached company lookup is the cheapest probe."""
    try:
        from sec_service import get_company_cik

        hit = get_company_cik("AAPL")
        if hit and hit.get("cik"):
            return {"status": OK, "detail": "SEC EDGAR reachable (6h cache)"}
        return {"status": DATA_UNAVAILABLE, "detail": "SEC returned no CIK"}
    except Exception as exc:  # noqa: BLE001
        return {"status": PROVIDER_OFFLINE, "detail": type(exc).__name__}


def _estimates() -> dict:
    """Analyst estimates, from the one feed that carries them."""
    import uw_company_service as uwc

    out = uwc.estimate_revisions("AAPL")
    return {"status": OK if out.get("rows") else DATA_UNAVAILABLE,
            "detail": out.get("detail"), "source": "UNUSUAL_WHALES"}


def _earnings() -> dict:
    """Can the feed name the next report for a company that has one?"""
    import uw_company_service as uwc

    if not uwc.configured():
        return {"status": PROVIDER_NOT_CONFIGURED,
                "detail": "UNUSUAL_WHALES_API_KEY is not set."}
    nxt = uwc.next_report("AAPL")
    return {"status": OK if nxt else DATA_UNAVAILABLE,
            "detail": (f"Next AAPL report {nxt.get('date')}" if nxt
                       else "No scheduled report returned."),
            "source": "UNUSUAL_WHALES"}


def _news() -> dict:
    import uw_news_adapter as uwnews

    return uwnews.provider_status()


def _earnings_calendar() -> dict:
    from earnings_calendar_service import calendar_source_status

    return calendar_source_status()


def _feed() -> dict:
    """
    Is the one feed answering, and how much of today's budget is left?

    This asks it something rather than reporting the status of the last call
    somebody else happened to make. On a freshly started server nothing has
    called it yet, and "UNKNOWN" on the chip for the feed the whole app now
    runs on reads as broken.
    """
    import unusualwhales_service as uw

    if not uw.configured():
        return {"status": PROVIDER_NOT_CONFIGURED,
                "detail": "UNUSUAL_WHALES_API_KEY is not set."}

    quote = uw.get_quote("SPY")
    budget = uw.budget()
    status = uw.provider_status()
    left, cap = budget.get("app_left"), budget.get("app_budget")
    priced = bool(quote and quote.get("price"))

    if priced:
        eff, detail = OK, f"Answering; {left} of {cap} requests left today"
    elif left == 0:
        # Status and detail must agree: a spent budget is not "OK".
        eff = RATE_LIMITED
        detail = (f"Today's budget of {cap} requests is spent; it resets at "
                  "midnight UTC. Everything from the feed is paused until then.")
    else:
        eff = status.get("status") or DATA_UNAVAILABLE
        detail = "The feed did not return a quote."
    return {**status, "status": eff, "detail": detail}


def _market_data() -> dict:
    """A real quote is the only honest proof that market data is flowing."""
    quote = market.get_quote("SPY")
    if quote.get("status") == "OK" and quote.get("price"):
        source = quote.get("source") or "UNUSUAL_WHALES"
        detail = f"SPY {quote['price']} via {source}"
        if quote.get("delayed"):
            # Flowing, but end-of-day. Say so on the chip instead of implying
            # a live tick stream.
            detail += f" (delayed, as of {quote.get('as_of') or 'last close'})"
        return {"status": OK, "detail": detail, "source": source,
                "delayed": bool(quote.get("delayed"))}
    return {"status": DATA_UNAVAILABLE, "detail": quote.get("status", "")}


def _options() -> dict:
    from live_options_service import load_chain

    chain = load_chain("SPY")
    if chain.get("status") == "OK" and chain.get("rows"):
        priced = sum(1 for r in chain["rows"] if r.get("mid"))
        return {"status": OK,
                "detail": f"{len(chain['rows'])} contracts, {priced} priced"}
    if chain.get("status") == "OK":
        return {"status": OK, "detail": f"{len(chain['rows'])} contracts "
                f"via {chain.get('source')}"}
    return {"status": DATA_UNAVAILABLE, "detail": chain.get("status", "")}


def _scanner() -> dict:
    from uw_scanner_service import scanner_status

    return scanner_status()


def get_health(deep: bool = False) -> dict:
    """
    Provider matrix. ``deep`` additionally exercises the slow providers
    (options chain, SEC); the default keeps the badge cheap enough to poll.
    """
    key = f"health:{deep}"
    cached = market.cache.get(key, HEALTH_TTL)
    if cached:
        return cached

    # Every probe is an independent network or database round trip, and run
    # one after another they were the slowest thing on the dashboard. Nothing
    # here depends on anything else, so fan them out.
    def gather(jobs: dict) -> dict:
        out: dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=min(8, len(jobs)),
                                thread_name_prefix="health") as pool:
            futures = {pool.submit(fn): name for name, fn in jobs.items()}
            for future in futures:
                name = futures[future]
                try:
                    out[name] = future.result()
                except Exception as exc:  # noqa: BLE001
                    out[name] = {"status": PROVIDER_OFFLINE, "detail": type(exc).__name__}
        return out

    providers: dict[str, Any] = gather({
        "feed": _feed,
        "database": _database,
        "market_data": _market_data,
        "estimates": _estimates,
        "earnings_calendar": _earnings_calendar,
        "earnings": _earnings,
    })

    import unusualwhales_service as uw

    feed_ok = uw.configured()
    providers["news"] = _news() if feed_ok else {
        "status": PROVIDER_OFFLINE, "detail": "No news provider configured"}
    providers["scanner"] = _scanner()

    if deep:
        providers["options"] = _options() if feed_ok else {
            "status": PROVIDER_OFFLINE, "detail": "No option chain provider"}
        providers["sec"] = _sec()
    else:
        providers["options"] = {
            "status": OK if feed_ok else PROVIDER_OFFLINE,
            "detail": ("Not deep-probed" if feed_ok
                       else "No option chain provider"),
        }
        providers["sec"] = {"status": OK, "detail": "Not deep-probed"}

    result = {
        "providers": providers,
        "live": feed_ok,
        "market": market.market_clock(),
        "checked_at": time.time(),
        # Flat shorthand the dashboard badge reads.
        "feed": providers["feed"]["status"],
        "market_data": providers["market_data"]["status"],
        "options": providers["options"]["status"],
    }
    market.cache.put(key, result)
    return result
