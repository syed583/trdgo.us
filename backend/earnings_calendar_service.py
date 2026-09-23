"""
Upcoming-earnings calendar.

Source decision, made by probing rather than assuming: this account has no
Wall Street Horizon entitlement - reqWshMetaData returns an empty payload - so
IBKR cannot supply an earnings calendar here. The database is therefore the
only permitted source, and every response says so explicitly.

Nothing is scraped and no date is inferred. A symbol with no row on record is
reported as having no known date, not as "no earnings".
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional

import ib_bootstrap  # noqa: F401  (must precede ib_insync)
from ib_insync import IB

import live_market_service as market
from database import SessionLocal
from ibkr_client import IBKRUnavailable, ibkr
from models import Company, EarningsEvent

WSH_TTL = 6 * 3600.0

RANGES = ("TODAY", "TOMORROW", "THIS_WEEK", "NEXT_WEEK", "THIS_MONTH", "ALL")

# Scheduled earnings dates move a handful of times a day, and results land
# after the close. Minutes of staleness cost nothing; re-querying a remote
# database on every dashboard load costs seconds.
CALENDAR_TTL_OPEN = 300.0
CALENDAR_TTL_CLOSED = 900.0


def wsh_available() -> dict:
    """
    Probe IBKR's Wall Street Horizon event feed once and cache the answer.

    Kept as its own check so that if the entitlement is ever added, the
    calendar can switch source without any other code changing.
    """
    cached = market.cache.get("wsh:available", WSH_TTL)
    if cached is not None:
        return cached

    async def job(ib: IB):
        return await ib.reqWshMetaDataAsync()

    try:
        meta = ibkr.run(job, timeout=45)
        ok = bool(meta and len(str(meta)) > 2)
        result = {
            "available": ok,
            "status": "OK" if ok else "ENTITLEMENT_REQUIRED",
            "detail": (
                "Wall Street Horizon event data available"
                if ok else
                "IBKR returned no WSH metadata for this account. A Wall Street "
                "Horizon subscription is required for an IBKR-sourced earnings "
                "calendar."
            ),
        }
    except IBKRUnavailable as exc:
        result = {"available": False, "status": "PROVIDER_OFFLINE",
                  "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001
        result = {"available": False, "status": "ENTITLEMENT_REQUIRED",
                  "detail": f"{type(exc).__name__}: {exc}"}

    market.cache.put("wsh:available", result)
    return result


def calendar_source_status() -> dict:
    """Health entry describing where calendar rows come from."""
    # Benzinga first: with a cached provider calendar the screen is healthy,
    # and saying "IBKR cannot supply a calendar" would be both wrong and
    # actionable-looking. Only fall through to the IBKR/WSH story when no
    # provider rows exist at all.
    try:
        import benzinga_earnings_service as benzinga

        status = benzinga.provider_status()
        cached = status.get("cached_rows") or 0
        symbols = status.get("cached_symbols") or 0
    except Exception:  # noqa: BLE001
        cached = symbols = 0

    if cached:
        return {
            "status": "OK",
            "detail": f"{cached} cached events across {symbols} companies "
                      f"(Benzinga)",
            "source": "BENZINGA",
            "companies_tracked": symbols,
            "scheduled_events": cached,
        }

    wsh = wsh_available()
    db = SessionLocal()
    try:
        tracked = db.query(Company).count()
        scheduled = (
            db.query(EarningsEvent)
            .filter(EarningsEvent.status == "scheduled")
            .count()
        )
    except Exception as exc:  # noqa: BLE001
        db.close()
        return {"status": "PROVIDER_OFFLINE", "detail": str(exc)}
    finally:
        db.close()

    if scheduled == 0:
        return {
            "status": "DATA_UNAVAILABLE",
            "detail": (
                f"No scheduled earnings rows in the database "
                f"({tracked} companies tracked). "
                "IBKR cannot supply a calendar on this account: " + wsh["detail"]
            ),
            "source": "DATABASE",
            "ibkr_wsh": wsh["status"],
            "companies_tracked": tracked,
            "scheduled_events": 0,
            "required_provider": (
                "An earnings-calendar feed (Wall Street Horizon via IBKR, or a "
                "third-party calendar API) populating earnings_events."
            ),
        }

    return {
        "status": "OK",
        "detail": f"{scheduled} scheduled events across {tracked} companies",
        "source": "DATABASE",
        "ibkr_wsh": wsh["status"],
        "companies_tracked": tracked,
        "scheduled_events": scheduled,
    }


def _window(range_key: str) -> tuple[Optional[date], Optional[date], str]:
    today = datetime.now(market.EASTERN).date()
    monday = today - timedelta(days=today.weekday())

    if range_key == "TODAY":
        return today, today, "Today"
    if range_key == "TOMORROW":
        d = today + timedelta(days=1)
        return d, d, "Tomorrow"
    if range_key == "THIS_WEEK":
        return monday, monday + timedelta(days=6), "This Week"
    if range_key == "NEXT_WEEK":
        start = monday + timedelta(days=7)
        return start, start + timedelta(days=6), "Next Week"
    if range_key == "THIS_MONTH":
        start = today.replace(day=1)
        nxt = (start + timedelta(days=32)).replace(day=1)
        return start, nxt - timedelta(days=1), "This Month"
    return None, None, "All Upcoming"


SORTS = {
    "DATE": lambda r: (r["date"] or "9999-99-99", r["symbol"]),
    "AZ": lambda r: r["symbol"],
    "ZA": lambda r: r["symbol"],
    "SCORE": lambda r: -(r["score"] if r["score"] is not None else -1e9),
    "CONFIDENCE": lambda r: -(r["confidence"] if r["confidence"] is not None else -1e9),
}


def _provider_rows(start, end, query: Optional[str], limit: int) -> list[dict]:
    """
    Cached Benzinga calendar rows shaped like the legacy ones.

    Never triggers a provider call; returns [] when the cache is empty so the
    caller can fall back to the seed table.
    """
    import benzinga_earnings_service as benzinga

    if start is None:
        start = datetime.now(market.EASTERN).date()
    try:
        raw = benzinga.get_upcoming(start, end).get("rows") or []
    except Exception:  # noqa: BLE001 - the screen must render without a provider
        return []

    raw = _drop_superseded(raw)

    needle = (query or "").upper()
    out = []
    for row in raw:
        symbol = str(row.get("symbol") or "").upper()
        if needle and needle not in symbol:
            continue
        timing = row.get("reporting_time")
        out.append({
            "symbol": symbol,
            "company": row.get("company") or symbol,
            "sector": row.get("sector"),
            "date": row.get("date"),
            "date_label": row.get("date_label"),
            "reporting_time": timing,
            "timing_label": {"AMC": "After Market Close",
                             "BMO": "Before Market Open"}.get(
                                 timing, "Unconfirmed"),
            "short_label": ("After Close" if timing == "AMC"
                            else "Before Open" if timing == "BMO"
                            else "Unconfirmed"),
            "report_time": row.get("report_time"),
            "time_label": row.get("time_label"),
            "exchange": row.get("exchange"),
            "importance": row.get("importance"),
            "importance_label": row.get("importance_label"),
            "quarter_label": row.get("quarter_label"),
            "date_confirmed": row.get("date_confirmed"),
            "eps_estimate": row.get("eps_estimate"),
            "eps_prior": row.get("eps_prior"),
            "revenue_prior": row.get("revenue_prior"),
            "revenue_estimate": row.get("revenue_estimate"),
            "price": None, "change": None, "change_percent": None,
            "score": None, "confidence": None, "expected_move": None,
            "data_status": "OK",
            "source": "BENZINGA",
        })
        if len(out) >= limit:
            break
    return out


def _profiles_for(symbols: list[str]) -> dict[str, dict]:
    """
    EDGAR profiles for the rows on screen, never fatally.

    The calendar is a schedule; a sector lookup that fails must cost the screen
    nothing. A miss leaves the columns blank rather than raising.
    """
    try:
        import company_profile_service as profiles

        return profiles.get_profiles(symbols)
    except Exception:  # noqa: BLE001 - decoration, not the point of the screen
        return {}


def _market_cap(shares: Optional[float], price: Optional[float]) -> Optional[float]:
    if not shares or not price or shares <= 0 or price <= 0:
        return None
    return round(float(shares) * float(price), 2)


def _drop_superseded(rows: list[dict]) -> list[dict]:
    """
    Remove placeholder dates that a confirmed date has replaced.

    The provider publishes an estimated date for a quarter and then, once the
    company announces, a second row with the real one. Both stay on file, so
    the same quarter appeared twice -- TRV showed on both Oct 15 and Oct 16,
    with different prior-year figures, which reads as two separate reports.

    Only an *unconfirmed* row is dropped, and only when a confirmed row exists
    for the same company and fiscal quarter. Two genuinely scheduled reports
    in one quarter would both survive.
    """
    confirmed = {
        (r.get("symbol"), r.get("fiscal_period"), r.get("fiscal_year"))
        for r in rows if r.get("date_confirmed")
    }
    return [
        r for r in rows
        if r.get("date_confirmed")
        or (r.get("symbol"), r.get("fiscal_period"), r.get("fiscal_year"))
        not in confirmed
    ]


def _unusual_whales_rows(start, end, query, limit) -> list[dict]:
    """Scheduled reports from the paid feed, filtered as the screen asks."""
    try:
        import uw_earnings_calendar as uwcal

        if not uwcal.configured():
            return []
        out = uwcal.calendar(
            start=str(start) if start else None,
            end=str(end) if end else None)
    except Exception:  # noqa: BLE001 - the screen renders without it
        return []
    if out.get("status") != "OK":
        return []

    rows = out["rows"]
    if query:
        needle = str(query).upper()
        rows = [r for r in rows if needle in str(r.get("symbol") or "").upper()
                or needle in str(r.get("company") or "").upper()]
    return rows[:limit] if limit else rows


def get_calendar(
    range_key: str = "THIS_WEEK",
    sort: str = "DATE",
    query: Optional[str] = None,
    with_quotes: bool = True,
    limit: int = 60,
) -> dict:
    range_key = (range_key or "THIS_WEEK").upper()
    if range_key not in RANGES:
        range_key = "THIS_WEEK"
    sort = (sort or "DATE").upper()

    # This was uncached, so every dashboard load re-ran a provider read plus
    # several queries against a remote database -- about four seconds for a
    # table of scheduled dates that changes a few times a day. The key carries
    # every argument, because a search query or a different range is a
    # different answer.
    cache_key = (f"calendar:{range_key}:{sort}:{query or ''}"
                 f":{int(with_quotes)}:{limit}")
    cached = market.cache.get(
        cache_key, market.session_ttl(CALENDAR_TTL_OPEN, CALENDAR_TTL_CLOSED))
    if cached:
        return cached

    start, end, label = _window(range_key)
    source = calendar_source_status()

    # Benzinga is the verified source and is consulted first. The legacy
    # earnings_events table is seed data joined to `companies`, and on an
    # install where only Benzinga has run both are empty -- which is why this
    # screen reported "0 companies tracked" and blamed IBKR while 57 real
    # provider rows sat in earnings_calendar.
    # Unusual Whales first: it carries the move the options market is pricing
    # into each report, which is the first thing anyone asks about a date they
    # are holding into and which neither of the sources below publishes.
    # Benzinga's cached calendar and the seed table stay behind it.
    rows = _unusual_whales_rows(start, end, query, limit)
    if rows:
        # Named for where these rows came from. The header read "from
        # BENZINGA - 97 cached events" over rows the live feed supplied,
        # which is the kind of small mislabel that makes the next label
        # worth less.
        source = {
            "status": "OK",
            "source": "UNUSUAL_WHALES",
            "provider": "UNUSUAL_WHALES",
            "detail": (f"{len(rows)} scheduled reports, with the move the "
                       "options market is pricing into each."),
        }

    if not rows:
        rows = _provider_rows(start, end, query, limit)
        if rows:
            source = {**source, "status": "OK", "provider": "BENZINGA"}

    # The seed table is only consulted when no provider answered. Reaching
    # for it anyway is how a calendar that already had its rows came back
    # empty on a server whose database was unreachable: the provider had
    # answered, and then a query nobody needed threw the answer away.
    db = SessionLocal() if not rows else None
    try:
        q = None if db is None else (
            db.query(EarningsEvent, Company)
            .join(Company, Company.id == EarningsEvent.company_id)
            .filter(EarningsEvent.status == "scheduled")
        )
        if q is not None and start:
            q = q.filter(EarningsEvent.earnings_date >= start)
        if q is not None and end:
            q = q.filter(EarningsEvent.earnings_date <= end)
        if q is not None and query:
            like = f"%{query.upper()}%"
            q = q.filter(Company.symbol.like(like))

        pairs = [] if q is None else (
            q.order_by(EarningsEvent.earnings_date.asc()).limit(limit).all())

        for event, company in pairs:
            timing = {"AMC": "After Market Close",
                      "BMO": "Before Market Open"}.get(
                          event.reporting_time, "Unconfirmed")
            rows.append({
                "symbol": company.symbol,
                "company": company.company_name,
                "sector": company.sector,
                "date": event.earnings_date.isoformat() if event.earnings_date else None,
                "date_label": (event.earnings_date.strftime("%b %d, %Y")
                               if event.earnings_date else None),
                "reporting_time": event.reporting_time,
                "timing_label": timing,
                "short_label": ("After Close" if event.reporting_time == "AMC"
                                else "Before Open" if event.reporting_time == "BMO"
                                else "Unconfirmed"),
                "industry": None, "sic": None, "market_cap": None,
                "shares_outstanding": None, "shares_as_of": None,
                "shares_status": None, "market_cap_detail": None,
                "report_time": None, "time_label": None, "exchange": None,
                "importance": None, "importance_label": None,
                "quarter_label": None, "eps_prior": None, "revenue_prior": None,
                "eps_estimate": float(event.eps_estimate) if event.eps_estimate else None,
                "revenue_estimate": (float(event.revenue_estimate)
                                     if event.revenue_estimate else None),
                "price": None, "change": None, "change_percent": None,
                "score": None, "confidence": None, "expected_move": None,
                "data_status": "OK",
            })
    except Exception:  # noqa: BLE001
        # A database that cannot answer must not cost us rows a provider
        # already supplied.
        pairs = []
    finally:
        if db is not None:
            db.close()

    # Attach live quotes in one batched pass for the rows actually returned.
    if with_quotes and rows:
        try:
            batch = market.get_batch([r["symbol"] for r in rows][:25])
        except Exception:  # noqa: BLE001
            batch = {}
        quotes = batch.get("symbols", {})
        for r in rows:
            hit = quotes.get(r["symbol"])
            if hit:
                r["price"] = hit.get("price")
                r["change"] = hit.get("change")
                r["change_percent"] = hit.get("change_percent")
                if hit.get("status") != "OK":
                    r["data_status"] = hit.get("status")

    # Sector and share count come from EDGAR, cached per company; the calendar
    # provider carries neither. Previous EPS is the prior-year quarter, which
    # is what makes the estimate on this row mean anything.
    if rows:
        symbols = [r["symbol"] for r in rows]
        # Sector, prior EPS and the share count are decoration on top of a
        # row that is already complete. Each reads a local table, and on a
        # server whose database is not up yet that turned a working calendar
        # into "Data unavailable" -- losing the provider's answer to enrich
        # it with something optional.
        try:
            sectors = _sector_map(symbols)
        except Exception:  # noqa: BLE001
            sectors = {}
        try:
            priors = _prior_eps(symbols,
                                start or datetime.now(market.EASTERN).date())
        except Exception:  # noqa: BLE001
            priors = {}
        try:
            profiles = _profiles_for(symbols)
        except Exception:  # noqa: BLE001
            profiles = {}

        for r in rows:
            profile = profiles.get(r["symbol"]) or {}
            if not r.get("sector"):
                r["sector"] = profile.get("sector") or sectors.get(r["symbol"])
            r["industry"] = profile.get("industry")
            r["sic"] = profile.get("sic")
            r["shares_outstanding"] = profile.get("shares_outstanding")
            r["shares_as_of"] = profile.get("shares_as_of")
            r["shares_status"] = profile.get("shares_status")
            # Market cap is computed here rather than stored: the share count
            # is quarterly but the price is live, so a stored figure would be
            # wrong within the hour.
            r["market_cap"] = (
                _market_cap(profile.get("shares_outstanding"), r.get("price"))
                if profile.get("shares_status") == "OK" else None
            )
            # Say which half is missing. "No share count on file" while the
            # share count is right there and the quote feed is down sends the
            # reader to the wrong problem entirely.
            if r.get("market_cap"):
                r["market_cap_detail"] = (
                    f"{profile['shares_outstanding']:,.0f} shares as of "
                    f"{profile.get('shares_as_of')} x live price")
            elif profile.get("shares_status") != "OK":
                r["market_cap_detail"] = (
                    profile.get("detail") or "No usable share count on file.")
            elif r.get("price") is None:
                r["market_cap_detail"] = (
                    f"{profile['shares_outstanding']:,.0f} shares on file as of "
                    f"{profile.get('shares_as_of')}, but no live price to "
                    "multiply them by.")
            else:
                r["market_cap_detail"] = "Market cap could not be computed."

            # The provider's prior-year figure is the right comparison and is
            # labelled as such; the last row on file is only a fallback, and
            # may be a different fiscal quarter.
            r["eps_previous"] = (
                r.get("eps_prior")
                if r.get("eps_prior") is not None
                else priors.get(r["symbol"])
            )
            r["eps_previous_basis"] = (
                "Same quarter last year (provider)"
                if r.get("eps_prior") is not None
                else ("Last reported quarter on file"
                      if priors.get(r["symbol"]) is not None else None)
            )

    reverse = sort == "ZA"
    rows.sort(key=SORTS.get(sort, SORTS["DATE"]), reverse=reverse)

    empty_window = not rows and source["status"] == "OK"

    result = {
        "range": range_key,
        "range_label": label,
        "empty_window": empty_window,
        "empty_detail": (
            f"No earnings scheduled in {label.lower()} for the "
            f"{source.get('companies_tracked', 0)} companies covered by "
            f"{source.get('source', 'the calendar provider').title()}. "
            "Try a wider range."
            if empty_window else None
        ),
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "sort": sort,
        "query": query,
        "rows": rows,
        "count": len(rows),
        "source": source,
        "status": "OK" if rows else source["status"],
        "market": market.market_clock(),
    }
    market.cache.put(cache_key, result)
    return result


# ===========================================================================
# Calendar context: the counts, breakdowns and history the calendar screen
# shows around the table.
#
# Every number here is read from rows already on record -- the Benzinga
# calendar table and the companies table. Nothing is estimated, and a figure
# that cannot be sourced is returned as None rather than filled in, so the
# screen can say "not available" instead of showing a plausible invention.
# ===========================================================================

CONTEXT_TTL = 900.0
IMPACT_DAYS = 30
UNCLASSIFIED = "Unclassified"


def _sector_map(symbols: list[str]) -> dict[str, str]:
    """Sector per symbol from the companies table, for rows that have one."""
    if not symbols:
        return {}
    db = SessionLocal()
    try:
        hits = (
            db.query(Company.symbol, Company.sector)
            .filter(Company.symbol.in_(list({s for s in symbols if s})))
            .all()
        )
        return {sym: sector for sym, sector in hits if sector}
    finally:
        db.close()


def _prior_eps(symbols: list[str], before: date) -> dict[str, float]:
    """
    Last reported EPS per symbol, for the "vs previous" column.

    Reported means ``eps_actual`` is present on an earlier row -- an estimate
    from a past quarter is not a result and is not used here.
    """
    from models_earnings import EarningsCalendarEntry

    if not symbols:
        return {}
    db = SessionLocal()
    try:
        rows = (
            db.query(EarningsCalendarEntry.symbol,
                     EarningsCalendarEntry.earnings_date,
                     EarningsCalendarEntry.eps_actual)
            .filter(EarningsCalendarEntry.symbol.in_(list({s for s in symbols if s})))
            .filter(EarningsCalendarEntry.earnings_date < before)
            .filter(EarningsCalendarEntry.eps_actual.isnot(None))
            .order_by(EarningsCalendarEntry.earnings_date.desc())
            .all()
        )
    finally:
        db.close()

    latest: dict[str, float] = {}
    for symbol, _when, eps in rows:
        # Rows arrive newest first, so the first sighting of a symbol is its
        # most recent report.
        if symbol not in latest:
            latest[symbol] = float(eps)
    return latest


def _count_between(db, start: date, end: date) -> int:
    from models_earnings import EarningsCalendarEntry

    return (
        db.query(EarningsCalendarEntry)
        .filter(EarningsCalendarEntry.earnings_date >= start)
        .filter(EarningsCalendarEntry.earnings_date <= end)
        .count()
    )


def _delta_percent(now: int, before: int) -> Optional[float]:
    """Percentage change, undefined rather than infinite against a zero base."""
    if not before:
        return None
    return round((now - before) / before * 100.0, 1)


def _count_between_cached(start: date, end: date) -> int:
    """``_count_between`` with its own session, for callers outside the block."""
    db = SessionLocal()
    try:
        return _count_between(db, start, end)
    finally:
        db.close()


def _provider_counts(today, monday, sunday, month_start, month_end,
                     prev_month_start, prev_month_end):
    """
    The same six counts, from the live calendar.

    Returns None when the feed cannot answer, so the stored counts still get
    their turn rather than the screen showing zeros.
    """
    try:
        import uw_earnings_calendar as uwcal

        if not uwcal.configured():
            return None, None
        # Two windows rather than one long one: the fortnight around today
        # and the current month. Reaching back over a previous month as well
        # ran past the per-call day budget and returned a month total with
        # zeros for today and this week -- the near dates, which are the ones
        # anyone is actually looking at.
        near = uwcal.calendar(start=(monday - timedelta(days=7)).isoformat(),
                              end=sunday.isoformat())
        month = uwcal.calendar(start=month_start.isoformat(),
                               end=month_end.isoformat())
    except Exception:  # noqa: BLE001
        return None, None
    if near.get("status") != "OK" and month.get("status") != "OK":
        return None, None

    by_day = {**(month.get("by_day") or {}), **(near.get("by_day") or {})}

    def between(start, end) -> int:
        return sum(n for day, n in by_day.items()
                   if start.isoformat() <= day <= end.isoformat())

    # The previous month is deliberately not fetched: it is only used for a
    # "versus last month" percentage, and thirty more requests for one
    # comparison is not a trade worth making. None means no baseline, which
    # the screen already knows how to say.
    counts = (
        between(today, today),
        between(today - timedelta(days=1), today - timedelta(days=1)),
        between(monday, sunday),
        between(monday - timedelta(days=7), sunday - timedelta(days=7)),
        between(month_start, month_end),
        None,
    )
    # The week's per-day counts travel with them: the strip under the header
    # is the same measurement, sliced by day.
    week_days = {day: n for day, n in by_day.items()
                 if monday.isoformat() <= day <= sunday.isoformat()}
    return counts, week_days


def get_context() -> dict:
    """Counts, per-day totals, sector mix, beat rate and post-earnings moves."""
    from models_earnings import EarningsCalendarEntry

    cached = market.cache.get("calendar:context", CONTEXT_TTL)
    if cached:
        return cached

    today = datetime.now(market.EASTERN).date()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    month_start = today.replace(day=1)
    month_end = (month_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    prev_month_end = month_start - timedelta(days=1)
    prev_month_start = prev_month_end.replace(day=1)

    # The counts have to come from the same source the table does. They were
    # read from the Benzinga cache while the table moved to the live feed, so
    # the screen showed "0 THIS WEEK" above twenty-two scheduled companies --
    # a header disagreeing with the rows underneath it.
    provider, provider_by_day = _provider_counts(
        today, monday, sunday, month_start, month_end,
        prev_month_start, prev_month_end)

    db = SessionLocal()
    try:
        if provider:
            (today_n, yesterday_n, week_n, last_week_n,
             month_n, last_month_n) = provider
        else:
            today_n = _count_between(db, today, today)
            yesterday_n = _count_between(db, today - timedelta(days=1),
                                         today - timedelta(days=1))
            week_n = _count_between(db, monday, sunday)
            last_week_n = _count_between(db, monday - timedelta(days=7),
                                         sunday - timedelta(days=7))
            month_n = _count_between(db, month_start, month_end)
            last_month_n = _count_between(db, prev_month_start, prev_month_end)

        week_rows = (
            db.query(EarningsCalendarEntry.symbol,
                     EarningsCalendarEntry.earnings_date)
            .filter(EarningsCalendarEntry.earnings_date >= monday)
            .filter(EarningsCalendarEntry.earnings_date <= sunday)
            .all()
        )

        # Beat rate is measured on reported quarters only. A scheduled row with
        # no actual yet is not "in line" -- it is not a result at all.
        #
        # Scoped to every reported quarter on record rather than the calendar
        # month: reporting clusters into four short seasons, so a month-scoped
        # beat rate is 0 of 0 for most of the year.
        month_results = (
            db.query(EarningsCalendarEntry.eps_actual,
                     EarningsCalendarEntry.eps_estimate)
            .filter(EarningsCalendarEntry.eps_actual.isnot(None))
            .filter(EarningsCalendarEntry.eps_estimate.isnot(None))
            .all()
        )

        # EPS surprise on every reported quarter on record. This is the one
        # post-report measure that is actually populated -- the close-to-close
        # move column is never written by the sync, so a "market impact" panel
        # built on it would be permanently blank.
        surprise_rows = (
            db.query(EarningsCalendarEntry.symbol,
                     EarningsCalendarEntry.earnings_date,
                     EarningsCalendarEntry.eps_surprise_percent,
                     EarningsCalendarEntry.eps_actual,
                     EarningsCalendarEntry.eps_estimate)
            .filter(EarningsCalendarEntry.eps_surprise_percent.isnot(None))
            .order_by(EarningsCalendarEntry.earnings_date.desc())
            .limit(40)
            .all()
        )

        # Every measured reaction on record rather than a 30-day slice:
        # reporting clusters into seasons, so a trailing month is empty for
        # most of the year and the panel would read as broken.
        impact = (
            db.query(EarningsCalendarEntry.symbol,
                     EarningsCalendarEntry.earnings_date,
                     EarningsCalendarEntry.post_earnings_move_percent,
                     EarningsCalendarEntry.post_earnings_drift_percent,
                     EarningsCalendarEntry.reaction_date)
            .filter(EarningsCalendarEntry.post_earnings_move_percent.isnot(None))
            .order_by(EarningsCalendarEntry.earnings_date.asc())
            .all()
        )
        measurable = (
            db.query(EarningsCalendarEntry)
            .filter(EarningsCalendarEntry.eps_actual.isnot(None))
            .count()
        )
    finally:
        db.close()

    # --- per day of the current week -------------------------------------
    # Counted from the live feed where it can answer. Reading the strip from
    # the stored table while the table above it came from the provider showed
    # "none" under every weekday of a week holding twenty-two reports.
    per_day: dict[str, int] = dict(provider_by_day or {})
    if not per_day:
        for _symbol, when in week_rows:
            if when:
                per_day[when.isoformat()] = per_day.get(when.isoformat(), 0) + 1
    days = []
    for offset in range(7):
        d = monday + timedelta(days=offset)
        days.append({
            "date": d.isoformat(),
            "weekday": d.strftime("%a"),
            "label": d.strftime("%b %d"),
            "count": per_day.get(d.isoformat(), 0),
            "is_today": d == today,
        })

    # --- sector mix for the week ------------------------------------------
    # EDGAR first, since it actually has a sector for every filer; the legacy
    # companies table is the fallback and is usually empty.
    week_symbols = [s for s, _ in week_rows]
    profiles = _profiles_for(week_symbols)
    sectors = {**_sector_map(week_symbols),
               **{sym: p["sector"] for sym, p in profiles.items()
                  if p.get("sector")}}
    tally: dict[str, int] = {}
    for symbol, _when in week_rows:
        name = sectors.get(symbol) or UNCLASSIFIED
        tally[name] = tally.get(name, 0) + 1
    total = sum(tally.values())
    heatmap = [
        {"sector": name, "count": n,
         "percent": round(n / total * 100.0, 1) if total else 0.0}
        for name, n in sorted(tally.items(), key=lambda kv: -kv[1])
    ]
    classified = total - tally.get(UNCLASSIFIED, 0)

    # --- beat / in line / missed ------------------------------------------
    beat = inline = missed = 0
    for actual, estimate in month_results:
        a, e = float(actual), float(estimate)
        if a > e:
            beat += 1
        elif a < e:
            missed += 1
        else:
            inline += 1
    reported = beat + inline + missed

    def share(n: int) -> Optional[float]:
        return round(n / reported * 100.0, 1) if reported else None

    # --- post-earnings moves ----------------------------------------------
    moves = [
        {"symbol": symbol,
         "date": when.isoformat() if when else None,
         "date_label": when.strftime("%b %d") if when else None,
         "reaction_date": reaction,
         "move": round(float(pct), 2),
         "drift": round(float(drift), 2) if drift is not None else None}
        for symbol, when, pct, drift, reaction in impact
    ]
    # The average *size* of the reaction, which is what "expect a move like
    # this" means. Averaging the signed moves would net winners against losers
    # and report a number close to zero for a set of violent reactions.
    avg_abs = (round(sum(abs(m["move"]) for m in moves) / len(moves), 2)
               if moves else None)
    drifts = [m["drift"] for m in moves if m["drift"] is not None]
    # Drift is averaged signed, on purpose: the question there is direction,
    # not size -- does the market keep going after the gap, or give it back.
    avg_drift = (round(sum(drifts) / len(drifts), 2) if drifts else None)

    # The narrowest window that actually has something in it, so the screen
    # opens on data instead of on an empty week. Reporting clusters into four
    # short seasons; for most of the year "this week" is genuinely empty, and
    # landing there made the calendar look broken rather than quiet.
    default_range = "ALL"
    for key in ("TODAY", "TOMORROW", "THIS_WEEK", "NEXT_WEEK", "THIS_MONTH"):
        win_start, win_end, _label = _window(key)
        if win_start and win_end and _count_between_cached(win_start, win_end):
            default_range = key
            break

    week_label = (monday.strftime("%b %d") + " - "
                  + sunday.strftime("%b %d, %Y"))

    surprises = [
        {"symbol": symbol,
         "date": when.isoformat() if when else None,
         "date_label": when.strftime("%b %d") if when else None,
         "surprise": round(float(pct), 1),
         "actual": float(actual) if actual is not None else None,
         "estimate": float(est) if est is not None else None}
        for symbol, when, pct, actual, est in surprise_rows
    ]
    avg_surprise = (round(sum(s["surprise"] for s in surprises) / len(surprises), 1)
                    if surprises else None)

    result = {
        "today": today.isoformat(),
        "week_start": monday.isoformat(),
        "week_end": sunday.isoformat(),
        "week_label": week_label,
        "default_range": default_range,
        "counts": {
            "today": today_n,
            "week": week_n,
            "month": month_n,
            "today_vs_yesterday": _delta_percent(today_n, yesterday_n),
            "week_vs_last_week": _delta_percent(week_n, last_week_n),
            "month_vs_last_month": _delta_percent(month_n, last_month_n),
        },
        "days": days,
        "heatmap": heatmap,
        "heatmap_total": total,
        "heatmap_classified": classified,
        "heatmap_detail": (
            f"{classified} of {total} companies this week have a SIC sector "
            f"on file; the rest are grouped as {UNCLASSIFIED}."
            if total else "No earnings on record for this week."
        ),
        "results": {
            "reported": reported,
            "beat": beat, "in_line": inline, "missed": missed,
            "beat_percent": share(beat),
            "in_line_percent": share(inline),
            "missed_percent": share(missed),
        },
        "surprises": {
            "samples": len(surprises),
            "average": avg_surprise,
            "rows": surprises,
        },
        "impact": {
            "samples": len(moves),
            "measurable": measurable,
            "drift_samples": len(drifts),
            "drift_sessions": 5,
            "avg_abs_move": avg_abs,
            "avg_drift": avg_drift,
            "moves": moves[-90:],
            "detail": (
                f"Measured on {len(moves)} of {measurable} reported quarters. "
                "The rest have no daily history from the chart provider."
                if measurable else "No reported quarters on record."
            ),
        },
        "status": "OK",
    }
    market.cache.put("calendar:context", result)
    return result
