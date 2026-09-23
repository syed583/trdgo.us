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
from ibkr_client import IBKRUnavailable, ibkr

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
        return {"status": PROVIDER_OFFLINE, "detail": str(exc)}


def _sec() -> dict:
    """SEC has no ping endpoint; a cached company lookup is the cheapest probe."""
    try:
        from sec_service import get_company_cik

        hit = get_company_cik("AAPL")
        if hit and hit.get("cik"):
            return {"status": OK, "detail": "SEC EDGAR reachable (6h cache)"}
        return {"status": DATA_UNAVAILABLE, "detail": "SEC returned no CIK"}
    except Exception as exc:  # noqa: BLE001
        return {"status": PROVIDER_OFFLINE, "detail": str(exc)}


def _estimates() -> dict:
    """
    Analyst estimates: Alpha Vantage when configured, otherwise whatever the
    database holds. Seed-only data must never read as a real signal.
    """
    import alpha_vantage_estimates_service as av
    import provider_config as pcfg

    if pcfg.ALPHA_VANTAGE.configured:
        return av.provider_status()

    try:
        from models import EstimateSnapshot
        from database import SessionLocal

        db = SessionLocal()
        try:
            rows = db.query(EstimateSnapshot).limit(200).all()
            if not rows:
                return {"status": DATA_UNAVAILABLE,
                        "detail": "No estimate snapshots on record"}
            sources = {(r.source or "").upper() for r in rows}
            if sources.issubset({"TEST", ""}):
                return {
                    "status": TEST_DATA,
                    "detail": (
                        "Only seed rows on record. A real estimates provider "
                        "(e.g. Refinitiv/FactSet/Zacks feed) is required before "
                        "this component can score."
                    ),
                }
            return {"status": OK, "detail": f"Sources: {', '.join(sorted(sources))}"}
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        return {"status": PROVIDER_OFFLINE, "detail": str(exc)}


def _benzinga() -> dict:
    import benzinga_earnings_service as benzinga

    return benzinga.provider_status()


def _alpha_vantage() -> dict:
    import alpha_vantage_estimates_service as av

    return av.provider_status()


def _news() -> dict:
    # Marketaux first: it needs no TWS, so a closed gateway is not the same
    # thing as having no news.
    import marketaux_news_service as marketaux

    if marketaux.configured():
        return marketaux.provider_status()

    from ibkr_news_service import provider_status

    return provider_status()


def _earnings_calendar() -> dict:
    from earnings_calendar_service import calendar_source_status

    return calendar_source_status()


def _ibkr() -> dict:
    status = ibkr.status()
    if not status.get("socket_connected"):
        # Inside the retry window the answer is already known. probe() forces a
        # real connection attempt (~2s on a refused socket), and calling it on
        # every health poll made this the slowest thing on the dashboard while
        # defeating the very back-off that exists to avoid it.
        if ibkr.is_offline():
            return {
                "status": PROVIDER_OFFLINE,
                "detail": status.get("last_error") or "TWS not reachable",
                **{k: status.get(k) for k in ("host", "port", "client_id")},
            }
        probe = ibkr.probe()
        if not probe.get("connected"):
            return {
                "status": PROVIDER_OFFLINE,
                "detail": probe.get("last_error") or "TWS not reachable",
                **{k: probe.get(k) for k in ("host", "port", "client_id")},
            }
        status = probe
    return {
        "status": OK if status.get("connected") else PROVIDER_OFFLINE,
        "detail": status.get("last_error") or "TWS API connected",
        "degraded_note": status.get("degraded_note"),
        "host": status.get("host"),
        "port": status.get("port"),
        "client_id": status.get("client_id"),
        "market_data_type": status.get("market_data_type"),
    }


def _market_data() -> dict:
    """A real quote is the only honest proof that market data is flowing."""
    quote = market.get_quote("SPY")
    if quote.get("status") == "OK" and quote.get("price"):
        source = quote.get("source") or "IBKR"
        detail = f"SPY {quote['price']} via {source}"
        if quote.get("delayed"):
            # Flowing, but end-of-day. Say so on the chip instead of implying
            # a live tick stream.
            detail += f" (delayed, as of {quote.get('as_of') or 'last close'})"
        return {"status": OK, "detail": detail, "source": source,
                "delayed": bool(quote.get("delayed"))}
    if quote.get("status") == "IBKR_UNAVAILABLE":
        return {"status": PROVIDER_OFFLINE, "detail": quote.get("error", "")}
    return {"status": DATA_UNAVAILABLE, "detail": quote.get("status", "")}


def _options() -> dict:
    from live_options_service import load_chain

    chain = load_chain("SPY")
    if chain.get("status") == "OK" and chain.get("rows"):
        priced = sum(1 for r in chain["rows"] if r.get("mid"))
        return {"status": OK,
                "detail": f"{len(chain['rows'])} contracts, {priced} priced"}
    if chain.get("status") == "IBKR_UNAVAILABLE":
        return {"status": PROVIDER_OFFLINE, "detail": chain.get("error", "")}
    if chain.get("status") == "OK":
        return {"status": OK, "detail": f"{len(chain['rows'])} contracts "
                f"via {chain.get('source')}"}
    return {"status": DATA_UNAVAILABLE, "detail": chain.get("status", "")}


def _scanner() -> dict:
    from ibkr_scanner_service import scanner_status

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
                    out[name] = {"status": PROVIDER_OFFLINE, "detail": str(exc)}
        return out

    providers: dict[str, Any] = gather({
        "ibkr": _ibkr,
        "database": _database,
        "market_data": _market_data,
        "estimates": _estimates,
        "earnings_calendar": _earnings_calendar,
        "benzinga": _benzinga,
        "alpha_vantage": _alpha_vantage,
    })

    ib_ok = providers["ibkr"]["status"] == OK
    import marketaux_news_service as _marketaux

    providers["news"] = _news() if (ib_ok or _marketaux.configured()) else {
        "status": PROVIDER_OFFLINE, "detail": "No news provider configured"}
    providers["scanner"] = _scanner() if ib_ok else {
        "status": PROVIDER_OFFLINE, "detail": "IBKR not connected"}

    # Options are no longer IBKR-only: Unusual Whales serves the chain, so
    # the probe has to ask rather than assume a closed TWS means no options.
    import unusualwhales_service as uw

    od_ok = uw.configured()
    if deep:
        providers["options"] = _options() if (ib_ok or od_ok) else {
            "status": PROVIDER_OFFLINE, "detail": "No option chain provider"}
        providers["sec"] = _sec()
    else:
        providers["options"] = {
            "status": OK if (ib_ok or od_ok) else PROVIDER_OFFLINE,
            "detail": ("Not deep-probed" if ib_ok
                       else "Unusual Whales chain available" if od_ok
                       else "No option chain provider"),
        }
        providers["sec"] = {"status": OK, "detail": "Not deep-probed"}

    result = {
        "providers": providers,
        "live": ib_ok,
        "market": market.market_clock(),
        "checked_at": time.time(),
        # Flat shorthand the dashboard badge reads.
        "ibkr": providers["ibkr"]["status"],
        "market_data": providers["market_data"]["status"],
        "options": providers["options"]["status"],
    }
    market.cache.put(key, result)
    return result
