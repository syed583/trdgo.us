"""
The earnings feed the rest of the app already knows how to read.

Several screens were written against the Benzinga client's shape --
``get_upcoming`` returning rows, ``get_history`` returning quarters -- and
that client is gone. Rather than rewrite each caller around a new
vocabulary, this answers in the old shape from the one remaining feed.

It is a translation layer and nothing more: no logic lives here that is not
about making Unusual Whales look like what the callers expect.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

SOURCE = "UNUSUAL_WHALES"


def configured() -> bool:
    import unusualwhales_service as uw

    return uw.configured()


def _label(when: str) -> Optional[str]:
    try:
        return datetime.strptime(str(when)[:10], "%Y-%m-%d").strftime("%b %d, %Y")
    except (TypeError, ValueError):
        return None


def _row(row: dict) -> dict:
    """One scheduled report, in the shape the calendar callers read."""
    when = str(row.get("date") or "")[:10]
    timing = row.get("reporting_time")
    return {
        "symbol": row.get("symbol"),
        "company": row.get("company"),
        "date": when,
        "date_label": _label(when),
        "reporting_time": timing,
        "report_time": row.get("report_time"),
        "time_label": row.get("time_label") or row.get("timing_label"),
        "quarter_label": row.get("quarter_label"),
        "exchange": None,
        "importance": row.get("importance"),
        "importance_label": row.get("importance_label"),
        "eps_estimate": row.get("eps_estimate"),
        "eps_actual": row.get("eps_actual"),
        "eps_prior": None,
        "eps_surprise_percent": None,
        "revenue_estimate": row.get("revenue_estimate"),
        "revenue_actual": None,
        "revenue_prior": None,
        "revenue_surprise_percent": None,
        "post_earnings_move_percent": row.get("reaction_percent"),
        "expected_move_percent": row.get("expected_move_percent"),
        "market_cap": row.get("market_cap"),
        "sector": row.get("sector"),
        # Scheduled until the numbers land; the lifecycle strip reads this.
        "lifecycle": "REPORTED" if row.get("eps_actual") is not None
                     else "SCHEDULED",
        "date_confirmed": True,
        "beat": None,
        "source": SOURCE,
        "data_status": "OK",
    }


def get_upcoming(date_from: date, date_to: Optional[date] = None,
                 symbols: Optional[list] = None) -> dict:
    """Scheduled reports in a window, optionally filtered to some symbols."""
    import uw_earnings_calendar as cal

    if not configured():
        return {"rows": [], "count": 0, "status": "PROVIDER_NOT_CONFIGURED",
                "source": SOURCE}

    out = cal.calendar(start=str(date_from), end=str(date_to) if date_to else None)
    rows = [_row(r) for r in (out.get("rows") or [])]
    if symbols:
        wanted = {str(s).upper() for s in symbols}
        rows = [r for r in rows if str(r["symbol"] or "").upper() in wanted]

        # A window is capped at forty trading days, so a symbol reporting
        # beyond it would read as "no date on record". Ask that symbol
        # directly rather than let the window decide the answer.
        missing = wanted - {str(r["symbol"] or "").upper() for r in rows}
        for symbol in sorted(missing):
            import uw_company_service as uwc

            nxt = uwc.next_report(symbol)
            if nxt:
                rows.append(_row({**nxt, "company": symbol,
                                  "quarter_label": nxt.get("quarter_label")}))

    rows.sort(key=lambda r: r["date"] or "")
    return {"rows": rows, "count": len(rows),
            "status": "OK" if rows else "NO_DATA", "source": SOURCE}


def get_history(symbol: str, quarters: int = 8) -> dict:
    """Reported quarters, newest first, in the history callers' shape."""
    import provider_config as cfg
    import uw_company_service as uwc

    if not configured():
        return {"symbol": symbol, "quarters": [], "count": 0,
                "status": "PROVIDER_NOT_CONFIGURED", "source": SOURCE}

    out = uwc.earnings_history(symbol, quarters)
    rows = []
    for r in out.get("rows") or []:
        estimate, actual = r.get("eps_estimate"), r.get("eps_actual")
        rows.append({
            "symbol": symbol.upper(),
            "company": r.get("company"),
            "date": r.get("date"),
            "date_label": _label(r.get("date") or ""),
            "quarter_label": _label(r.get("date") or ""),
            "reporting_time": r.get("reporting_time"),
            "report_time": None,
            "time_label": {"AMC": "After Close", "BMO": "Before Open"}.get(
                r.get("reporting_time")),
            "exchange": None, "importance": None, "importance_label": None,
            "fiscal_period": None, "fiscal_year": None,
            "eps_estimate": estimate,
            "eps_actual": actual,
            "eps_prior": None,
            "eps_surprise_percent": r.get("eps_surprise_percent"),
            "revenue_estimate": r.get("revenue_estimate"),
            "revenue_actual": r.get("revenue_actual"),
            "revenue_prior": None,
            "revenue_surprise_percent": r.get("revenue_surprise_percent"),
            "post_earnings_move_percent": None,
            "lifecycle": "REPORTED",
            "date_confirmed": True,
            "beat": bool(estimate is not None and actual is not None
                         and actual > estimate),
            "source": SOURCE,
            "data_status": cfg.OK,
            "fetched_at": None,
        })

    return {"symbol": symbol.upper(), "quarters": rows, "count": len(rows),
            "status": cfg.OK if rows else cfg.DATA_UNAVAILABLE,
            "detail": None if rows else f"No reported quarters for {symbol}.",
            "source": SOURCE}


def provider_status() -> dict:
    import unusualwhales_service as uw

    return uw.provider_status()


def sync_calendar(*args: Any, **kwargs: Any) -> dict:
    """
    Nothing to sync.

    The old client kept a local copy of the calendar that had to be filled
    before the screens could read it -- and an unsynced install reported
    "no earnings on record" for companies reporting that week. This feed is
    read on demand, so the concept no longer exists; the function stays so
    the startup hook that called it does not have to know that.
    """
    return {"status": "OK", "synced": 0,
            "detail": "Read on demand; nothing is cached to sync.",
            "source": SOURCE}
