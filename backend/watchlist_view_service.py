"""
The watchlist screen's data, assembled from what this app can actually source.

Rows carry a live quote, the issuer's sector and market cap from EDGAR, and the
next scheduled report from the calendar provider. The screen-level figures --
today's move, gainers and losers, the sector split -- are computed from those
same rows rather than fetched again, so the summary and the table can never
disagree.

Two deliberate absences:

* **No trade action.** This application does not place orders, by design, and a
  disabled button that looks like one is an invitation to a mistake.
* **No P/E, dividend yield or beta.** They need a fundamentals feed, and every
  configured provider either rejects the request or gates it behind a plan.
  A blank field is honest; a stale or guessed one is not.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

import live_market_service as market

VIEW_TTL_OPEN = 45.0
VIEW_TTL_CLOSED = 900.0

# Wall-clock ceiling for the whole assembly, so one slow provider cannot hold
# the screen. Sections that miss it are reported absent, not blank.
BUILD_BUDGET = 60.0


def _num(value: Any) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _quotes(symbols: list[str]) -> dict[str, dict]:
    """A quote per symbol through the normal fallback chain, in parallel."""
    from concurrent.futures import ThreadPoolExecutor

    def one(symbol: str) -> tuple[str, Optional[dict]]:
        try:
            return symbol, market.get_quote(symbol)
        except Exception:  # noqa: BLE001 - one symbol must not stop the screen
            return symbol, None

    if not symbols:
        return {}
    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=min(10, len(symbols)),
                            thread_name_prefix="watchlist") as pool:
        for symbol, quote in pool.map(one, symbols):
            if quote:
                out[symbol] = quote
    return out


def _earnings_dates(symbols: list[str]) -> dict[str, dict]:
    """Next scheduled report per symbol, from the calendar provider's table."""
    if not symbols:
        return {}
    try:
        from database import SessionLocal
        from models_earnings import EarningsCalendarEntry
    except Exception:  # noqa: BLE001
        return {}

    db = SessionLocal()
    try:
        rows = (
            db.query(EarningsCalendarEntry)
            .filter(EarningsCalendarEntry.symbol.in_(symbols))
            .filter(EarningsCalendarEntry.earnings_date >= date.today())
            .order_by(EarningsCalendarEntry.earnings_date.asc())
            .all()
        )
    except Exception:  # noqa: BLE001
        return {}
    finally:
        db.close()

    out: dict[str, dict] = {}
    for row in rows:
        # Rows arrive soonest first, so the first sighting is the next report.
        if row.symbol in out:
            continue
        out[row.symbol] = {
            "date": row.earnings_date.isoformat(),
            "date_label": row.earnings_date.strftime("%b %d, %Y"),
            "reporting_time": row.reporting_time,
            "time_label": (row.report_time or None),
            "confirmed": row.data_status != "ESTIMATED_DATE",
        }
    return out


def get_view() -> dict:
    """Rows, headline figures, sector split and the next reports."""
    cached = market.cache.get(
        "watchlist_view", market.session_ttl(VIEW_TTL_OPEN, VIEW_TTL_CLOSED))
    if cached:
        return cached

    import workspace_service as workspace

    saved = workspace.list_watchlist(with_quotes=False).get("rows") or []
    symbols = [r["symbol"].upper() for r in saved]
    if not symbols:
        return {
            "status": "EMPTY",
            "rows": [], "totals": {}, "sectors": [], "earnings": [],
            "detail": "No symbols saved yet. Add one to start the list.",
        }

    quotes = _quotes(symbols)
    events = _earnings_dates(symbols)

    profiles: dict[str, dict] = {}
    try:
        import company_profile_service as cp

        profiles = cp.get_profiles(symbols)
    except Exception:  # noqa: BLE001 - names and sectors are decoration
        profiles = {}

    rows = []
    for index, saved_row in enumerate(saved, start=1):
        symbol = saved_row["symbol"].upper()
        quote = quotes.get(symbol) or {}
        profile = profiles.get(symbol) or {}
        shares = _num(profile.get("shares_outstanding"))
        price = _num(quote.get("price"))

        rows.append({
            "rank": index,
            "id": saved_row.get("id"),
            "symbol": symbol,
            "company": (profile.get("company_name")
                        or saved_row.get("company") or symbol),
            "note": saved_row.get("note"),
            "price": price,
            "change": _num(quote.get("change")),
            "change_percent": _num(quote.get("change_percent")),
            "volume": _num(quote.get("volume")),
            "sector": profile.get("sector"),
            "industry": profile.get("industry"),
            "shares_outstanding": shares,
            # Computed here rather than stored: the share count is quarterly
            # but the price is live, so a stored cap is wrong within the hour.
            "market_cap": (round(shares * price, 2)
                           if shares and price else None),
            "shares_as_of": profile.get("shares_as_of"),
            "earnings": events.get(symbol),
            "status": quote.get("status") or ("OK" if price else "NO_DATA"),
            "source": quote.get("source"),
        })

    priced = [r for r in rows if r["change_percent"] is not None]
    gainers = [r for r in priced if r["change_percent"] > 0]
    losers = [r for r in priced if r["change_percent"] < 0]

    # An equal-weight average, and labelled as one. A portfolio move needs
    # position sizes, which a watchlist does not have -- presenting this as
    # "today's P/L" would imply money that was never committed.
    average = (round(sum(r["change_percent"] for r in priced) / len(priced), 2)
               if priced else None)

    tally: dict[str, int] = {}
    for row in rows:
        key = row["sector"] or "Unclassified"
        tally[key] = tally.get(key, 0) + 1
    sectors = [
        {"sector": name, "count": n,
         "percent": round(n / len(rows) * 100.0, 1)}
        for name, n in sorted(tally.items(), key=lambda kv: -kv[1])
    ]

    upcoming = sorted(
        ({"symbol": r["symbol"], "company": r["company"], **r["earnings"]}
         for r in rows if r.get("earnings")),
        key=lambda e: e["date"],
    )

    result = {
        "status": "OK",
        "rows": rows,
        "totals": {
            "symbols": len(rows),
            "priced": len(priced),
            "gainers": len(gainers),
            "losers": len(losers),
            "unchanged": len(priced) - len(gainers) - len(losers),
            "average_change": average,
            "average_basis": ("Equal-weight average of the "
                              f"{len(priced)} priced symbols, not a position "
                              "return -- a watchlist holds no sizes."),
            "upcoming_earnings": len(upcoming),
        },
        "sectors": sectors,
        "earnings": upcoming[:8],
        "market": market.market_clock(),
        "detail": (f"{len(priced)} of {len(rows)} symbols priced. Sector and "
                   "market cap come from SEC filings; the cap is shares "
                   "outstanding times the live price."),
    }
    market.cache.put("watchlist_view", result)
    return result
