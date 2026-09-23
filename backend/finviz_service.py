"""
Finviz Elite: the whole market as a screen, exported as CSV.

What this is for. The app scores a fixed universe of thirty-eight symbols
because nothing in it could answer "which stocks look like this right now"
across the market. Finviz can: an Elite subscription exports any screener as
CSV, with seventy-odd filters and the fundamentals the rest of this app does
not carry -- valuation, margins, growth, float, short interest.

What it is not. Finviz's own footer says futures and options are delayed by
fifteen minutes, and it publishes no per-print options tape at all, so it
cannot replace Unusual Whales for options flow. It replaces
guessing at a universe.

Access. Elite exports are authenticated by a token on the URL, not a header,
and the path is ``/export/screener``. The legacy ``.ashx`` URLs still answer
but with a 301, so anything that does not follow redirects reads an empty
body -- this client follows them.

One subscription, one person: the token is read from backend/.env, never
logged, and never returned by the status endpoint.
"""

from __future__ import annotations

import csv
import io
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from dotenv import load_dotenv

from live_market_service import cache

load_dotenv()

SOURCE = "FINVIZ"
EXPORT = "https://elite.finviz.com/export"

# Every export Finviz documents, by the name this app calls it. Kept in one
# table so adding one of theirs is a line here rather than another copy of the
# fetch-and-parse code.
PATHS = {
    "screener": "/screener",
    "portfolio": "/portfolio",
    "stock": "/stock",                      # price history for one ticker
    "groups": "/groups",                    # sector / industry / country
    "options": "/options",                  # option chain for one ticker
    "filings": "/latest-filings",
    "news": "/news",
    "insiders": "/insiders",
    "managers": "/managers",
    "funds": "/funds",
    "calendar_economic": "/calendar/economic",
    "calendar_earnings": "/calendar/earnings",
    "calendar_dividends": "/calendar/dividends",
    "futures": "/futures/performance",
    "forex": "/forex/performance",
    "crypto": "/crypto/performance",
}

BASE = f"{EXPORT}/screener"
PORTFOLIO = f"{EXPORT}/portfolio"
ENV_KEY = "FINVIZ_AUTH_TOKEN"
TIMEOUT = 45.0

# Screens change with the market, not with the minute.
TTL_OPEN = 900.0
TTL_CLOSED = 3600.0

# Tables that move daily at most: fund holdings, managers, calendars.
SLOW_TTL = 6 * 3600.0

# A sanity ceiling rather than their limit. It has to clear the widest table
# the app asks for: a year of the earnings calendar is four thousand rows, and
# a lower cap silently truncated it -- MSFT came back with one quarter of the
# four it had reported, because the rest were past the cut.
MAX_ROWS = 20000

# The view that carries the overview columns. v=111 is the default screener
# table; the app names it explicitly so a change of theirs is visible here.
DEFAULT_VIEW = "111"

# Columns their CSV writes in millions, unsuffixed. Everything else is in the
# unit its name implies.
MILLIONS = {"Market Cap", "Sales", "Income", "Float", "Shares Outstanding"}

_last_status: Optional[str] = None


def auth_token() -> str:
    return (os.environ.get(ENV_KEY) or "").strip()


def configured() -> bool:
    return bool(auth_token())


def _ttl() -> float:
    import live_market_service as market

    return market.session_ttl(TTL_OPEN, TTL_CLOSED)


def _number(value: Any) -> Any:
    """
    "12.5%" -> 12.5, "1.2B" -> 1200000000.0, "-" -> None.

    Their CSV is written for a spreadsheet: percentages carry a sign, large
    numbers carry a suffix, and a missing value is a dash. Leaving those as
    text would push the parsing into every caller.
    """
    if value is None:
        return None
    text = str(value).strip()
    if text in ("", "-", "--", "N/A"):
        return None
    cleaned = text.replace(",", "").replace("$", "").replace("%", "")
    multiplier = 1.0
    if cleaned and cleaned[-1] in "KMBT":
        multiplier = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[cleaned[-1]]
        cleaned = cleaned[:-1]
    try:
        return float(cleaned) * multiplier
    except ValueError:
        return text


def _export(url: str, params: dict, key: str, limit: int, ttl: float,
            detail: str, require_ticker: bool = False) -> dict:
    """
    Fetch one Finviz export and read the CSV back as rows.

    Shared by the screener and the portfolio because the two differ only in
    path and parameters: same authentication on the URL, same 301 from the
    legacy paths, same spreadsheet formatting, and the same silent empty body
    when the token is not accepted.
    """
    global _last_status

    if not configured():
        return {"status": "PROVIDER_NOT_CONFIGURED", "rows": [],
                "detail": f"Add {ENV_KEY} to backend/.env to use Finviz.",
                "source": SOURCE}

    hit = cache.get(key, ttl)
    if hit:
        return hit

    request = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode({**params, 'auth': auth_token()})}",
        headers={
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/140.0 Safari/537.36"),
            "Accept": "text/csv,*/*",
        })

    try:
        # urllib follows the 301 from their legacy paths by default, which is
        # the behaviour their docs tell curl users to ask for with -L.
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = response.read().decode("utf-8-sig", "ignore")
    except urllib.error.HTTPError as exc:
        _last_status = ("ENTITLEMENT_REQUIRED" if exc.code in (401, 403)
                        else "PROVIDER_OFFLINE")
        return {"status": _last_status, "rows": [],
                "detail": f"HTTP {exc.code} from Finviz.", "source": SOURCE}
    except Exception as exc:  # noqa: BLE001
        _last_status = "PROVIDER_OFFLINE"
        return {"status": "PROVIDER_OFFLINE", "rows": [],
                "detail": type(exc).__name__, "source": SOURCE}

    if not body.strip():
        # What an unauthenticated or redirected export looks like: 200, empty.
        _last_status = "ENTITLEMENT_REQUIRED"
        return {"status": "ENTITLEMENT_REQUIRED", "rows": [],
                "detail": ("Finviz returned an empty export. The token may be "
                           "missing, expired, or not an Elite subscription -- "
                           "or, for a portfolio, the id may not be yours."),
                "source": SOURCE}
    if body.lstrip()[:1] == "<":
        _last_status = "ENTITLEMENT_REQUIRED"
        return {"status": "ENTITLEMENT_REQUIRED", "rows": [],
                "detail": ("Finviz returned a web page rather than CSV; the "
                           "token was probably rejected."),
                "source": SOURCE}

    rows = []
    for row in csv.DictReader(io.StringIO(body)):
        shaped = {(k or "").strip(): _number(v) for k, v in row.items()}
        # Their export writes these in millions with no suffix, so NVDA's
        # market cap arrives as 5,500,827 -- a number that looks like five
        # million dollars and is five and a half trillion. Converted here,
        # once, rather than in every screen that reads a column.
        for column in MILLIONS:
            value = shaped.get(column)
            if isinstance(value, (int, float)):
                shaped[column] = value * 1e6
        symbol = shaped.get("Ticker")
        if isinstance(symbol, str):
            shaped["Ticker"] = symbol.strip().upper()
        # Most of these exports are tables of stocks; some -- news, the
        # calendars, futures -- have no ticker column at all, and dropping
        # their rows for lacking one would return an empty table that looks
        # like "no data" rather than a table read wrongly.
        if require_ticker and not re.fullmatch(
                r"[A-Z][A-Z0-9.\-]{0,9}", str(shaped.get("Ticker") or "")):
            continue
        rows.append(shaped)
        if len(rows) >= min(limit, MAX_ROWS):
            break

    _last_status = "OK"
    result = {
        "status": "OK", "rows": rows, "count": len(rows),
        "columns": list(rows[0].keys()) if rows else [],
        "detail": detail,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": SOURCE,
    }
    cache.put(key, result)
    return result


def portfolio(pid: str, order: str = "", limit: int = 500) -> dict:
    """
    One of your own Finviz portfolios, exported.

    ``pid`` is the id in the portfolio URL. The rows are whatever columns that
    portfolio is configured to show, so the app does not assume a shape --
    which also means it never invents a cost basis or a position size that
    your portfolio does not actually carry.
    """
    pid = str(pid or "").strip()
    if not pid.isdigit():
        return {"status": "INVALID_REQUEST", "rows": [],
                "detail": "A Finviz portfolio id is the number in its URL.",
                "source": SOURCE}

    params: dict = {"pid": pid}
    if order:
        params["o"] = order
    return {**_export(PORTFOLIO, params, f"finviz:pf:{pid}:{order}:{limit}",
                      limit, _ttl(),
                      "Exported from your own Finviz portfolio. Quotes are "
                      "delayed unless your plan says otherwise.",
                      require_ticker=True),
            "portfolio_id": pid, "order": order or None}


def screen(filters: str = "", view: str = DEFAULT_VIEW, order: str = "",
           tickers: str = "", limit: int = 200) -> dict:
    """
    Run one screen and return its rows.

    ``filters`` is Finviz's own filter string, copied from the screener URL --
    for example ``fa_div_pos,sec_technology``. Keeping their vocabulary rather
    than inventing one means a screen built by hand on their site can be
    pasted here and behave identically.
    """
    params: dict = {"v": view}
    if filters:
        params["f"] = filters
    if order:
        params["o"] = order
    if tickers:
        params["t"] = tickers

    out = _export(BASE, params,
                  f"finviz:{view}:{filters}:{order}:{tickers}:{limit}",
                  limit, _ttl(),
                  "Exported from your own Finviz Elite screen. Quotes are "
                  "delayed unless your plan says otherwise; futures and "
                  "options are fifteen minutes behind.",
                  require_ticker=True)
    return {**out, "filters": filters, "view": view}


def symbols(filters: str = "", order: str = "", limit: int = 200) -> list[str]:
    """Just the tickers a screen returns -- what a universe is built from."""
    out = screen(filters=filters, order=order, limit=limit)
    return [r["Ticker"] for r in out.get("rows") or [] if r.get("Ticker")]


def fundamentals(symbol: str) -> dict:
    """
    One company's overview row: valuation, margins, growth, float, short
    interest -- the part of a company the rest of this app does not read.
    """
    symbol = (symbol or "").upper().strip()
    out = screen(tickers=symbol, limit=1)
    if out.get("status") != "OK":
        return {"symbol": symbol, **out}
    rows = out.get("rows") or []
    if not rows:
        return {"symbol": symbol, "status": "NO_DATA", "row": None,
                "detail": f"Finviz returned no row for {symbol}.",
                "source": SOURCE}
    return {"symbol": symbol, "status": "OK", "row": rows[0],
            "detail": out["detail"], "source": SOURCE}


def provider_status() -> dict:
    """Configuration state for the settings screen. Never returns the token."""
    if not configured():
        return {"provider": "Finviz Elite", "configured": False,
                "status": "NOT_CONFIGURED", "env_var": ENV_KEY,
                "purpose": "market-wide screening and fundamentals",
                "signup": "https://elite.finviz.com/",
                "detail": (f"Add {ENV_KEY} to backend/.env. It is the auth "
                           "token on your Elite export URL.")}

    probe = screen(tickers="AAPL", limit=1)
    return {
        "provider": "Finviz Elite",
        "configured": True,
        "status": probe.get("status"),
        "env_var": ENV_KEY,
        "purpose": "market-wide screening and fundamentals",
        "detail": (probe.get("detail") if probe.get("status") != "OK"
                   else "Token accepted; screener export available."),
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


# ---------------------------------------------------------------------------
# the rest of the exports, in Finviz's own vocabulary
# ---------------------------------------------------------------------------
#
# Each takes the parameters that appear in the corresponding page's URL, so a
# view built by hand on their site can be reproduced here by copying the query
# string. Everything shares one fetch, one CSV reader and one set of failure
# statuses.


def _simple(name: str, params: dict, ttl: float = 0.0, limit: int = MAX_ROWS,
            detail: str = "", require_ticker: bool = False) -> dict:
    key = "finviz:" + name + ":" + ":".join(
        f"{k}={v}" for k, v in sorted(params.items()))
    return _export(EXPORT + PATHS[name], params, key, limit,
                   ttl or _ttl(), detail or f"Finviz {name} export.",
                   require_ticker=require_ticker)


def stock_history(symbol: str, period: str = "d", limit: int = 500) -> dict:
    """
    Daily, weekly or monthly price history for one ticker.

    ``period`` is their own ``p``: d, w or m.
    """
    symbol = (symbol or "").upper().strip()
    return {"symbol": symbol,
            **_simple("stock", {"t": symbol, "p": period}, limit=limit,
                      detail="Price history exported from Finviz.")}


def groups(group: str = "sector", view: str = "152", order: str = "") -> dict:
    """Sector, industry or country performance tables."""
    params = {"g": group, "v": view}
    if order:
        params["o"] = order
    return _simple("groups", params,
                   detail="Group performance exported from Finviz.")


def options_chain(symbol: str, expiry: str = "", limit: int = 1000) -> dict:
    """
    The option chain for one ticker, as Finviz publishes it.

    Their own site says options are fifteen minutes delayed, so this is a
    second opinion on the chain rather than a live feed -- useful when TWS is
    down, not a replacement for it.
    """
    symbol = (symbol or "").upper().strip()
    params = {"t": symbol, "ty": "oc"}
    if expiry:
        params["e"] = expiry
    return {"symbol": symbol, "expiry": expiry or None,
            **_simple("options", params, limit=limit,
                      detail=("Option chain exported from Finviz; their "
                              "options data is fifteen minutes delayed."))}


def latest_filings(symbol: str = "", order: str = "-filingDate",
                   limit: int = 100) -> dict:
    """SEC filings as Finviz lists them, newest first by default."""
    params = {"o": order}
    if symbol:
        params["t"] = (symbol or "").upper().strip()
    return _simple("filings", params, limit=limit,
                   detail="Latest filings exported from Finviz.")


def news(view: str = "1", limit: int = 200) -> dict:
    """The market news table."""
    return _simple("news", {"v": view}, limit=limit,
                   detail="News exported from Finviz.")


def insiders(transaction: str = "", symbol: str = "", limit: int = 200) -> dict:
    """
    Insider trades. ``transaction`` is their ``tc`` -- 7 is open-market buys.

    Kept beside the app's own Form 4 reading rather than replacing it: this
    arrives as a table, the app's own reader knows which transactions were
    discretionary.
    """
    params: dict = {}
    if transaction:
        params["tc"] = transaction
    if symbol:
        params["t"] = (symbol or "").upper().strip()
    return _simple("insiders", params, limit=limit,
                   detail="Insider trades exported from Finviz.")


def managers(search: str = "", limit: int = 200) -> dict:
    """13F managers, by partial name search."""
    params = {"search": search} if search else {}
    return _simple("managers", params, limit=limit,
                   detail="Managers exported from Finviz.")


def funds(search: str = "", limit: int = 200) -> dict:
    """Funds, by partial name search."""
    params = {"search": search} if search else {}
    return _simple("funds", params, limit=limit,
                   detail="Funds exported from Finviz.")


def fund_holdings(investor_id: str, limit: int = 1000) -> dict:
    """
    One fund's holdings. ``investor_id`` is the id in its URL (e.g. S000007195),
    with or without the name in front of it.
    """
    investor_id = (investor_id or "").strip().strip("/")
    if not investor_id:
        return {"status": "INVALID_REQUEST", "rows": [],
                "detail": "A fund id looks like S000007195.", "source": SOURCE}
    return {"investor_id": investor_id,
            **_export(f"{EXPORT}/funds/{investor_id}/holdings", {},
                      f"finviz:fundholdings:{investor_id}:{limit}", limit,
                      SLOW_TTL, "Fund holdings exported from Finviz.")}


def calendar(kind: str = "earnings", date_from: str = "",
             limit: int = 500) -> dict:
    """The economic, earnings or dividends calendar."""
    name = f"calendar_{kind}"
    if name not in PATHS:
        return {"status": "INVALID_REQUEST", "rows": [],
                "detail": "Calendar kind is economic, earnings or dividends.",
                "source": SOURCE}
    # Their calendars reject a request with no window -- HTTP 400, "one or
    # more validation errors" -- so "from today" is the default rather than
    # an error the caller has to learn about.
    params = {"dateFrom": date_from or time.strftime("%Y-%m-%d")}
    return {"kind": kind,
            **_simple(name, params, limit=limit,
                      detail=f"{kind.title()} calendar exported from Finviz.")}


def performance(market: str = "futures") -> dict:
    """Futures, forex or crypto performance tables."""
    if market not in ("futures", "forex", "crypto"):
        return {"status": "INVALID_REQUEST", "rows": [],
                "detail": "Market is futures, forex or crypto.",
                "source": SOURCE}
    return {"market": market,
            **_simple(market, {}, detail=f"{market.title()} performance "
                                         "exported from Finviz.")}


# How far back the shared earnings window reaches. A year covers four
# quarters for every company whatever its fiscal calendar.
EARNINGS_WINDOW_DAYS = 400


def earnings_window(days: int = EARNINGS_WINDOW_DAYS) -> dict:
    """
    Every earnings report in the window, indexed by ticker.

    Their calendar ignores a ticker filter -- t, tickers and symbols all
    return the whole window -- so asking per symbol would download four
    thousand rows each time. One request serves every symbol instead, the
    same trick the options scan uses.
    """
    from datetime import date, timedelta

    start = date.today() - timedelta(days=days)
    key = f"finviz:earnwin:{start.isoformat()}"
    hit = cache.get(key, SLOW_TTL)
    if hit:
        return hit

    # Their calendar caps a response at a few thousand rows and fills it from
    # the start of the range, so one request for a year returned only its
    # first two months -- every company came back with a single quarter. The
    # year is fetched in slices small enough to sit under that cap.
    by_symbol: dict = {}
    rows_seen = 0
    failures = 0
    slice_days = 45
    cursor = start
    today = date.today()
    while cursor < today:
        end = min(cursor + timedelta(days=slice_days), today)
        out = _simple("calendar_earnings",
                      {"dateFrom": cursor.isoformat(), "dateTo": end.isoformat()},
                      ttl=SLOW_TTL, limit=MAX_ROWS,
                      detail="Earnings calendar exported from Finviz.")
        if out.get("status") != "OK":
            failures += 1
            if failures >= 3:
                return out
        for row in out.get("rows") or []:
            symbol = str(row.get("Ticker") or "").upper()
            if symbol:
                by_symbol.setdefault(symbol, []).append(row)
                rows_seen += 1
        cursor = end + timedelta(days=1)

    result = {"status": "OK" if by_symbol else "NO_DATA", "symbols": by_symbol,
              "count": rows_seen, "companies": len(by_symbol),
              "window_from": start.isoformat(), "slices_failed": failures,
              "detail": "Earnings calendar exported from Finviz.",
              "source": SOURCE}
    cache.put(key, result)
    return result


def _reporting_time(clock: str) -> Optional[str]:
    """
    Before the open, after the close, or during the session.

    Finviz puts the clock in the date ("2026-08-26 16:30"), and this is not
    cosmetic: the reaction to an after-close report is the *next* session.
    Reading NVDA's 16:30 report as a morning one measured the day before the
    news -- -1.6% on a report the market answered with +8.7%.
    """
    try:
        hour, minute = (int(p) for p in clock.split(":"))
    except (AttributeError, TypeError, ValueError):
        return None
    if (hour, minute) >= (16, 0):
        return "AMC"
    if (hour, minute) <= (9, 30):
        return "BMO"
    return "DMT"


def earnings_history(symbol: str, quarters: int = 8) -> dict:
    """
    Reported quarters for one company, newest first.

    Shaped like the Benzinga history the model already reads, so switching
    provider is a change of source rather than a change of parameter.
    """
    symbol = (symbol or "").upper().strip()
    window = earnings_window()
    if window.get("status") != "OK":
        return {"symbol": symbol, "rows": [], **window}

    rows = []
    for row in window["symbols"].get(symbol, []):
        stamp = str(row.get("Date") or "")
        rows.append({
            "date": stamp[:10],
            "report_time": stamp[11:16] or None,
            "reporting_time": _reporting_time(stamp[11:16]),
            "eps_estimate": row.get("EPS Estimate"),
            "eps_actual": row.get("EPS Actual"),
            "eps_surprise_percent": row.get("EPS Surprise"),
            "revenue_estimate": row.get("Revenue Estimate"),
            "revenue_actual": row.get("Revenue Actual"),
            "revenue_surprise_percent": row.get("Revenue Surprise"),
            "price_reaction_pct": row.get("1-Day Price Reaction"),
            "company": row.get("Company"),
        })
    rows.sort(key=lambda r: r["date"], reverse=True)

    return {
        "symbol": symbol,
        "status": "OK" if rows else "NO_DATA",
        "rows": rows[:quarters],
        "count": len(rows[:quarters]),
        "detail": ("Reported quarters from Finviz, including the one-day "
                   "price reaction to each report."
                   if rows else
                   f"Finviz reported no results for {symbol} in the last "
                   f"{EARNINGS_WINDOW_DAYS} days."),
        "source": SOURCE,
    }
