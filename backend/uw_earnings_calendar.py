"""
The earnings calendar, and a preview of what the options market expects.

Where the rows come from
------------------------
Unusual Whales publishes the day's reporters split by when they report --
before the open, after the close -- with the street's mean estimate and,
crucially, the **expected move** the options market is pricing for that
report. The calendar this replaces carried the date and the estimate; it had
no way to say how big a move was being priced, which is the first thing
anyone asks about a report they are holding into.

A date that has passed cannot change, so a settled day is fetched once and
kept for a month. Only today and the days ahead are refetched, which is what
keeps a two-week window to a handful of requests.

The preview
-----------
``preview`` answers the question the calendar raises: this company reports on
Tuesday -- what happens when it does? It carries the expected move against
what the stock has actually done after its last reports (one day, three days,
a week), the street estimate, and whether the options market has historically
been paid or run over on that straddle. Every figure is the provider's or
this app's own measurement; none of it is a forecast.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional

import unusualwhales_service as uw

SOURCE = "UNUSUAL_WHALES"

# A finished day is immutable; today and tomorrow are not.
TTL_SETTLED = 30 * 24 * 3600.0
TTL_LIVE = 900.0

# How far a bare "upcoming" ask looks forward.
DEFAULT_FORWARD_DAYS = 14


def configured() -> bool:
    return uw.configured()


def _f(value) -> Optional[float]:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _day(value) -> str:
    return str(value or "")[:10]


def _timing(report_time: str) -> tuple:
    raw = str(report_time or "").lower()
    if "pre" in raw:
        return "BMO", "Before Open", "BMO"
    if "post" in raw or "after" in raw:
        return "AMC", "After Close", "AMC"
    return None, "Time not set", "--"


def _row(r: dict, when: str) -> dict:
    """One reporter, in the shape the calendar screen already renders."""
    code, label, short = _timing(r.get("report_time"))
    move_pct = _f(r.get("expected_move_perc"))
    if move_pct is not None and abs(move_pct) <= 1:
        move_pct *= 100  # theirs is a fraction; the screen talks in percent

    return {
        "symbol": r.get("symbol"),
        "company": r.get("full_name") or r.get("symbol"),
        "sector": r.get("sector"),
        "date": when,
        "date_label": _label(when),
        "reporting_time": code,
        "timing_label": label,
        "short_label": short,
        "report_time": r.get("report_time"),
        "time_label": label,
        "quarter_label": _day(r.get("ending_fiscal_quarter")),
        "eps_estimate": _f(r.get("street_mean_est")),
        "eps_actual": _f(r.get("actual_eps")),
        "revenue_estimate": None,
        "market_cap": _f(r.get("marketcap")),
        "importance": 5 if r.get("is_s_p_500") else 3,
        "importance_label": "S&P 500" if r.get("is_s_p_500") else "Listed",
        "has_options": bool(r.get("has_options")),
        # The part the old calendar could not say: how big a move the option
        # market is pricing into this report.
        "expected_move": _f(r.get("expected_move")),
        "expected_move_percent": round(move_pct, 2) if move_pct is not None else None,
        "prior_close": _f(r.get("pre_earnings_close")),
        # Only present once the market has answered.
        "reaction_percent": _reaction(r),
        "data_status": "OK",
        "source": SOURCE,
    }


def _reaction(r: dict) -> Optional[float]:
    value = _f(r.get("reaction"))
    if value is None:
        return None
    return round(value * 100, 2) if abs(value) <= 1 else round(value, 2)


def _label(when: str) -> str:
    try:
        return datetime.strptime(when, "%Y-%m-%d").strftime("%a %b %d")
    except (TypeError, ValueError):
        return when


def _for_date(when: str) -> list[dict]:
    """Every company reporting on one date, both sessions."""
    settled = when < date.today().isoformat()
    rows: list[dict] = []
    for path in ("/api/earnings/premarket", "/api/earnings/afterhours"):
        key = f"uwcal:{path}:{when}"
        out = uw._cached(key, path, {"date": when, "limit": 500},
                         ttl=TTL_SETTLED if settled else TTL_LIVE)
        if out["status"] != "OK":
            continue
        rows.extend(_row(r, when) for r in uw._rows(out)
                    if str(r.get("country_code") or "US") == "US")
    return rows


def calendar(start: Optional[str] = None, end: Optional[str] = None,
             days: int = DEFAULT_FORWARD_DAYS) -> dict:
    """
    Every scheduled report in a window, newest date first within the window.

    Weekends are skipped rather than requested: no US company reports on a
    Saturday, and asking costs a request to be told so.
    """
    first = start or date.today().isoformat()
    try:
        cursor = datetime.strptime(first, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        cursor = date.today()
    last = (datetime.strptime(end, "%Y-%m-%d").date() if end
            else cursor + timedelta(days=max(days, 1)))

    rows: list[dict] = []
    asked = 0
    while cursor <= last and asked < 40:
        if cursor.weekday() < 5:
            rows.extend(_for_date(cursor.isoformat()))
            asked += 1
        cursor += timedelta(days=1)

    rows.sort(key=lambda r: (r["date"], -(r["market_cap"] or 0)))
    by_day: dict[str, int] = {}
    for row in rows:
        by_day[row["date"]] = by_day.get(row["date"], 0) + 1

    return {
        "status": "OK" if rows else "NO_DATA",
        "rows": rows,
        "count": len(rows),
        "start": first,
        "end": last.isoformat(),
        "days_covered": asked,
        "by_day": by_day,
        "with_expected_move": sum(1 for r in rows
                                  if r["expected_move_percent"] is not None),
        "detail": ("Scheduled reports with the move the options market is "
                   "pricing into each one."),
        "source": SOURCE,
    }


# ---------------------------------------------------------------------------
# the preview
# ---------------------------------------------------------------------------


def preview(symbol: str) -> dict:
    """
    What the options market expects from this company's next report, against
    what its last reports actually did.

    The comparison is the point. An expected move of 7% means nothing on its
    own; it means something beside a stock that has moved 3% on each of its
    last four reports, and something else beside one that has moved 12%.
    """
    symbol = (symbol or "").upper().strip()
    out = uw.get(f"/api/earnings/{symbol}", {"limit": 24})
    if out["status"] != "OK":
        return {"symbol": symbol, "status": out["status"],
                "detail": out.get("detail"), "source": SOURCE}

    rows = uw._rows(out)
    if not rows:
        return {"symbol": symbol, "status": "NO_DATA",
                "detail": f"No earnings record for {symbol}.", "source": SOURCE}

    today = date.today().isoformat()
    upcoming = sorted((r for r in rows if _day(r.get("report_date")) >= today),
                      key=lambda r: _day(r.get("report_date")))
    reported = sorted((r for r in rows
                       if _day(r.get("report_date")) < today
                       and _f(r.get("post_earnings_move_1d")) is not None),
                      key=lambda r: _day(r.get("report_date")), reverse=True)

    next_report = upcoming[0] if upcoming else None
    expected = _f((next_report or {}).get("expected_move_perc"))
    if expected is not None and abs(expected) <= 1:
        expected *= 100

    history = [{
        "date": _day(r.get("report_date")),
        "quarter_ending": _day(r.get("ending_fiscal_quarter")),
        "eps_actual": _f(r.get("actual_eps")),
        "eps_estimate": _f(r.get("street_mean_est")),
        "move_1d": _pct(r.get("post_earnings_move_1d")),
        "move_3d": _pct(r.get("post_earnings_move_3d")),
        "move_1w": _pct(r.get("post_earnings_move_1w")),
        "run_up_1w": _pct(r.get("pre_earnings_move_1w")),
        # What a straddle bought the day before would have returned. Their
        # figure, not a model of ours.
        "straddle_1d": _pct(r.get("long_straddle_1d")),
    } for r in reported[:8]]

    moves = [abs(h["move_1d"]) for h in history if h["move_1d"] is not None]
    typical = round(sum(moves) / len(moves), 2) if moves else None
    beats = [h for h in history
             if h["eps_actual"] is not None and h["eps_estimate"] is not None]
    beaten = sum(1 for h in beats if h["eps_actual"] > h["eps_estimate"])

    return {
        "symbol": symbol,
        "status": "OK",
        "next_report": _day((next_report or {}).get("report_date")) or None,
        "next_report_label": _label(_day((next_report or {}).get("report_date"))),
        "next_report_time": _timing((next_report or {}).get("report_time"))[1],
        "quarter_ending": _day((next_report or {}).get("ending_fiscal_quarter")),
        "street_estimate": _f((next_report or {}).get("street_mean_est")),
        "expected_move_percent": (round(expected, 2)
                                  if expected is not None else None),
        "expected_move": _f((next_report or {}).get("expected_move")),
        "typical_move_percent": typical,
        # The sentence the screen is really there to let someone form.
        "expectation_vs_history": _verdict(expected, typical),
        "reports_measured": len(history),
        "beat_count": beaten,
        "beat_rate": (round(beaten / len(beats) * 100) if beats else None),
        "history": history,
        "detail": ("The move the options market is pricing, against what this "
                   "stock has actually done on its last reports."),
        "source": SOURCE,
    }


def _pct(value) -> Optional[float]:
    figure = _f(value)
    if figure is None:
        return None
    return round(figure * 100, 2) if abs(figure) <= 1 else round(figure, 2)


def _verdict(expected: Optional[float],
             typical: Optional[float]) -> Optional[str]:
    """
    How the priced move compares with this stock's own record.

    Deliberately three buckets and no recommendation: that options look
    expensive against history is an observation, and what to do about it is
    not this app's call.
    """
    if expected is None:
        # Their expected move comes from the front-month straddle, which does
        # not exist yet for a report two months out. Saying so is better than
        # an empty cell that reads like a missing figure.
        return ("The options market has not priced this report yet -- it is "
                "too far out for a front-month straddle."
                + (f" This stock typically moves {typical:.1f}% on results."
                   if typical else ""))
    if not typical:
        return (f"Options are pricing a {expected:.1f}% move. No measured "
                "history of this company's past reactions yet.")
    ratio = expected / typical
    if ratio >= 1.25:
        return (f"Options are pricing {expected:.1f}% against a typical "
                f"{typical:.1f}% -- more than this stock usually moves.")
    if ratio <= 0.8:
        return (f"Options are pricing {expected:.1f}% against a typical "
                f"{typical:.1f}% -- less than this stock usually moves.")
    return (f"Options are pricing {expected:.1f}%, about what this stock "
            f"usually moves ({typical:.1f}%).")
