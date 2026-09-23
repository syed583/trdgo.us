"""
Screening the market and reading a company's numbers, from Unusual Whales.

Finviz Elite was added to answer "which stocks look like this right now",
because nothing in the app could: it scores a fixed universe of thirty-eight
symbols. Their screener answers it, and so does this one -- with two
differences that matter here.

Their screener carries the option tape alongside the fundamentals: call and
put premium, the side each crossed on, open interest, implied volatility and
the greeks, per name, in the same row as the market cap and the EPS growth.
That is the half Finviz does not publish at all, and it is the half this app
scores on. And the fundamentals come from the filings rather than a screener
table, so a balance sheet is a balance sheet rather than a column.

Finviz stays configured behind this. It is the cheaper subscription and it
answers for names this feed does not carry.
"""

from __future__ import annotations

from typing import Any, Optional

import unusualwhales_service as uw

SOURCE = "Unusual Whales"

# What a screen returns by default: enough to rank a name and see why,
# without shipping two hundred columns to a browser.
SUMMARY_FIELDS = (
    "ticker", "full_name", "sector", "marketcap", "close", "prev_close",
    "volume", "avg30_volume", "call_premium", "put_premium", "call_volume",
    "put_volume", "call_open_interest", "put_open_interest", "iv30d",
    "implied_move", "next_earnings_date", "dividend_yield", "eps_growth_4q",
    "ema_20", "ema_50", "rsi_14", "bullish_premium", "bearish_premium",
)


def configured() -> bool:
    return uw.configured()


def _f(value) -> Optional[float]:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def screen(limit: int = 100, **filters) -> dict:
    """
    Rank the market on the provider's own screen.

    ``filters`` are passed through as they document them (min_marketcap,
    min_volume, sectors[] and so on), so a caller can ask a question this
    module has not anticipated without a change here.
    """
    params = {"limit": min(max(limit, 1), 500)}
    params.update({k: v for k, v in filters.items() if v not in (None, "")})
    out = uw.get("/api/screener/stocks", params)
    if out["status"] != "OK":
        return {"status": out["status"], "rows": [],
                "detail": out.get("detail"), "source": SOURCE}

    rows = []
    for r in uw._rows(out):
        row = {field: r.get(field) for field in SUMMARY_FIELDS if field in r}
        row["ticker"] = r.get("ticker")
        call_prem = _f(r.get("call_premium")) or 0.0
        put_prem = _f(r.get("put_premium")) or 0.0
        total = call_prem + put_prem
        # The reason this screen is worth having: the option tape beside the
        # fundamentals, in the same row.
        row["call_premium_share"] = (round(call_prem / total * 100, 1)
                                     if total else None)
        row["option_lean"] = ("CALLS" if call_prem > put_prem * 1.2
                              else "PUTS" if put_prem > call_prem * 1.2
                              else "BALANCED")
        rows.append(row)

    return {"status": "OK" if rows else "NO_DATA", "rows": rows,
            "count": len(rows),
            "detail": ("Every optionable name, with its option tape beside "
                       "its fundamentals."),
            "source": SOURCE}


def fundamentals(symbol: str) -> dict:
    """
    One company's reported numbers, newest period first.

    From the filings rather than a screener table, so the periods line up
    with what the company actually reported.
    """
    symbol = (symbol or "").upper().strip()
    out = uw.fundamentals(symbol)
    if out["status"] != "OK":
        return {"symbol": symbol, "status": out["status"], "periods": [],
                "detail": out.get("detail"), "source": SOURCE}

    periods = []
    for r in uw._rows(out):
        end = str(r.get("report_period_end_date") or "")[:10]
        if not end:
            continue
        periods.append({
            "period_ending": end,
            "eps": _f(r.get("earnings_per_share")),
            "revenue": _f(r.get("total_revenue") or r.get("revenue")),
            "net_income": _f(r.get("net_income")),
            "gross_profit": _f(r.get("gross_profit")),
            "operating_income": _f(r.get("operating_income")),
            "cash": _f(r.get("cash_and_cash_equivalents")),
            "total_current_assets": _f(r.get("total_current_assets")),
            "total_assets": _f(r.get("total_assets")),
            "total_liabilities": _f(r.get("total_liabilities")),
            "free_cash_flow": _f(r.get("free_cash_flow")),
        })

    periods.sort(key=lambda p: p["period_ending"], reverse=True)
    return {"symbol": symbol, "status": "OK" if periods else "NO_DATA",
            "periods": periods, "count": len(periods),
            "latest": periods[0] if periods else None,
            "detail": "Reported figures as filed, newest period first.",
            "source": SOURCE}


def movers(limit: int = 25) -> dict:
    """The day's biggest movers, as they rank them."""
    out = uw.get("/api/market/movers", {"limit": limit})
    if out["status"] != "OK":
        return {"status": out["status"], "rows": [],
                "detail": out.get("detail"), "source": SOURCE}

    rows = [{
        "symbol": r.get("ticker"),
        "name": r.get("full_name"),
        "price": _f(r.get("close") or r.get("price")),
        "change_percent": _f(r.get("perc_change") or r.get("change_percent")),
        "volume": _f(r.get("volume")),
        "sector": r.get("sector"),
    } for r in uw._rows(out)]
    return {"status": "OK" if rows else "NO_DATA", "rows": rows,
            "count": len(rows), "source": SOURCE}


def short_interest(symbol: str) -> dict:
    """
    Short interest, days to cover and the float behind them.

    New here: nothing the app had published this, and it is the other side
    of an ownership picture that until now was only institutions and
    insiders.
    """
    symbol = (symbol or "").upper().strip()
    out = uw.get(f"/api/shorts/{symbol}/interest-float/v2")
    rows = uw._rows(out)
    if out["status"] != "OK" or not rows:
        return {"symbol": symbol,
                "status": out["status"] if out["status"] != "OK" else "NO_DATA",
                "detail": out.get("detail"), "source": SOURCE}

    latest = rows[-1] if isinstance(rows, list) else rows
    return {
        "symbol": symbol,
        "status": "OK",
        "short_interest": _f(latest.get("short_interest")),
        "float": _f(latest.get("float")),
        "short_percent_of_float": _f(latest.get("percent_of_float")
                                     or latest.get("short_percent_of_float")),
        "days_to_cover": _f(latest.get("days_to_cover")),
        "as_of": latest.get("date") or latest.get("settlement_date"),
        "detail": ("Short interest as reported to the exchanges, twice a "
                   "month -- a slow reading, not a live one."),
        "source": SOURCE,
    }
