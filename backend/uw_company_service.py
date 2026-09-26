"""
The company's own record, from Unusual Whales: earnings, dividends,
analyst actions and headlines.

This replaces four feeds that each answered one question badly:

* **Benzinga** earnings, which were only ever as fresh as the last sync
  somebody ran -- an unsynced symbol reported "no earnings history" with
  five reported quarters sitting a call away.
* **Nasdaq** dividends, scraped from a public page with no key and no
  contract; it broke whenever they changed the markup.
* **Alpha Vantage** analyst estimates, on a free tier of twenty-five
  requests a day -- exhausted by lunchtime on a board of thirty-eight.
* **Marketaux and Yahoo** headlines, the first metered at a hundred a day
  and the second an RSS feed with no sentiment at all.

Every shape here is the one the screens already read, so the migration is a
change of source rather than a rewrite of the pages above it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import unusualwhales_service as uw

SOURCE = "UNUSUAL_WHALES"


def configured() -> bool:
    return uw.configured()


def _f(value) -> Optional[float]:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _day(value) -> str:
    return str(value or "")[:10]


# ---------------------------------------------------------------------------
# earnings
# ---------------------------------------------------------------------------


def earnings_history(symbol: str, quarters: int = 8) -> dict:
    """
    Reported quarters, newest first, in the shape the model reads.

    Their feed carries annual rows alongside quarterly ones and rows that
    have not been reported yet. Both are filtered here: an annual figure
    counted as a quarter would double a beat rate, and a forecast is not a
    track record.
    """
    symbol = (symbol or "").upper().strip()
    out = uw.earnings_history(symbol)
    if out["status"] != "OK":
        return {"symbol": symbol, "status": out["status"], "rows": [],
                "detail": out.get("detail"), "source": SOURCE}

    rows = []
    for r in uw._rows(out):
        if str(r.get("report_type") or "").lower() != "quarterly":
            continue
        actual = _f(r.get("reported_eps"))
        when = _day(r.get("report_date"))
        if actual is None or not when:
            continue
        estimate = _f(r.get("estimated_eps"))
        surprise = _f(r.get("surprise"))
        rows.append({
            "date": when,
            "fiscal_period_ending": _day(r.get("fiscal_date_ending")),
            "eps_actual": actual,
            "eps_estimate": estimate,
            "eps_surprise": surprise,
            "eps_surprise_percent": (round(surprise / abs(estimate) * 100, 2)
                                     if surprise is not None and estimate else None),
            "revenue_actual": _f(r.get("revenue")),
            "revenue_estimate": _f(r.get("revenue_estimate")),
            "revenue_surprise_percent": None,
            "price_reaction_pct": None,
            "reporting_time": _reporting_time(r),
            "company": None,
        })

    rows.sort(key=lambda r: r["date"], reverse=True)
    return {
        "symbol": symbol,
        "status": "OK" if rows else "NO_DATA",
        "rows": rows[:quarters],
        "count": len(rows[:quarters]),
        "detail": ("Reported quarters as filed, with the estimate each was "
                   "measured against."),
        "source": SOURCE,
    }


def _reporting_time(row: dict) -> Optional[str]:
    """
    Before the open, after the close, or unknown.

    It matters because the market's answer to an after-close report is the
    *next* session: measured on the report date, NVDA's +8.7% reaction reads
    as -1.6%, which is a real figure for the day before the news.
    """
    raw = str(row.get("report_time") or row.get("time") or "").lower()
    if "pre" in raw or "bmo" in raw or "before" in raw:
        return "BMO"
    if "post" in raw or "amc" in raw or "after" in raw:
        return "AMC"
    return None


def earnings_estimates(symbol: str) -> dict:
    """What analysts expect, by fiscal period -- and how that is being revised."""
    symbol = (symbol or "").upper().strip()
    out = uw.earnings_estimates(symbol)
    if out["status"] != "OK":
        return {"symbol": symbol, "status": out["status"], "rows": [],
                "detail": out.get("detail"), "source": SOURCE}

    rows = []
    for r in uw._rows(out):
        up = _f(r.get("eps_estimate_revision_up_last_week"))
        down = _f(r.get("eps_estimate_revision_down_last_week"))
        rows.append({
            "period_ending": _day(r.get("date")),
            "horizon": r.get("horizon"),
            "eps_estimate": _f(r.get("eps_estimate_average")),
            "eps_low": _f(r.get("eps_estimate_low")),
            "eps_high": _f(r.get("eps_estimate_high")),
            "analysts": _f(r.get("eps_estimate_analyst_count")),
            "revenue_estimate": _f(r.get("revenue_estimate_average")),
            "revisions_up": up,
            "revisions_down": down,
            # Which way the estimate is being moved, which is the part that
            # carries information -- the level itself is mostly consensus.
            "revision_lean": ("UP" if (up or 0) > (down or 0)
                              else "DOWN" if (down or 0) > (up or 0)
                              else "FLAT" if up is not None else None),
        })

    rows.sort(key=lambda r: r["period_ending"] or "")
    return {"symbol": symbol, "status": "OK" if rows else "NO_DATA",
            "rows": rows, "count": len(rows),
            "detail": "Forward estimates and the last week's revisions.",
            "source": SOURCE}


def estimate_revisions(symbol: str) -> dict:
    """
    The current estimate and which way it is being revised.

    Not the same measurement the snapshot-based version makes. That one
    compares stored estimates 7, 30, 60 and 90 days apart, which needs
    months of its own history before it can say anything; this reads the
    provider's own count of analysts revising up and down over the last
    week. Fewer horizons, available immediately, and the direction -- which
    is the part that carries information -- is stated rather than derived.
    """
    symbol = (symbol or "").upper().strip()
    out = earnings_estimates(symbol)
    if out.get("status") != "OK" or not out.get("rows"):
        return {"symbol": symbol, "rows": [], "horizons_available": [],
                "status": out.get("status", "DATA_UNAVAILABLE"),
                "detail": out.get("detail"), "source": SOURCE}

    from datetime import date as _date

    today = _date.today().isoformat()
    ahead = [r for r in out["rows"]
             if (r.get("period_ending") or "") >= today
             and str(r.get("horizon") or "").startswith("fiscal quarter")]
    current = ahead[0] if ahead else out["rows"][-1]

    up = current.get("revisions_up")
    down = current.get("revisions_down")
    return {
        "symbol": symbol,
        "rows": [{
            "label": "Current",
            "days_ago": 0,
            "eps_estimate": current.get("eps_estimate"),
            "eps_low": current.get("eps_low"),
            "eps_high": current.get("eps_high"),
            "revenue_estimate": current.get("revenue_estimate"),
            "analysts": current.get("analysts"),
            "period_ending": current.get("period_ending"),
        }],
        "horizons_available": [0],
        "revisions_up": up,
        "revisions_down": down,
        "revision_lean": current.get("revision_lean"),
        "status": "OK",
        "detail": ("The current estimate and how many analysts moved it up or "
                   "down in the last week. Not a 7/30/60/90-day comparison -- "
                   "that needs months of stored snapshots before it can say "
                   "anything."),
        "source": SOURCE,
    }


def next_report(symbol: str) -> Optional[dict]:
    """The next scheduled report, or None. Shaped like a calendar row."""
    try:
        import uw_earnings_calendar as uwcal

        out = uwcal.preview(symbol)
    except Exception:  # noqa: BLE001
        return None
    if out.get("status") != "OK" or not out.get("next_report"):
        return None
    return {
        "symbol": symbol,
        "date": out["next_report"],
        "date_label": out.get("next_report_label"),
        "reporting_time": out.get("next_report_time"),
        "quarter_label": out.get("quarter_ending"),
        "eps_estimate": out.get("street_estimate"),
        "eps_actual": None,
        "expected_move_percent": out.get("expected_move_percent"),
        # Scheduled and not yet reported: the pipeline has not started.
        "lifecycle": "SCHEDULED",
        "source": SOURCE,
    }


# ---------------------------------------------------------------------------
# dividends
# ---------------------------------------------------------------------------


def dividends(symbol: str, limit: int = 8) -> dict:
    """Declared dividends, newest first, in the Dividends panel's shape."""
    symbol = (symbol or "").upper().strip()
    out = uw.dividends(symbol)
    if out["status"] != "OK":
        return {"symbol": symbol, "status": out["status"], "payments": [],
                "detail": out.get("detail"), "source": SOURCE}

    data = out.get("data") or {}
    raw = data.get("dividends") if isinstance(data, dict) else data
    payments = []
    for r in raw or []:
        amount = _f(r.get("amount"))
        if amount is None:
            continue
        payments.append({
            "amount": amount,
            "type": "Cash",
            # Their older rows carry the string "None" rather than a null,
            # which would render as the word None on the screen.
            "declared": _clean(r.get("declaration_date")),
            "ex_date": _clean(r.get("ex_date")),
            "record_date": _clean(r.get("record_date")),
            "pay_date": _clean(r.get("payment_date")),
            "currency": "USD",
        })

    payments.sort(key=lambda p: p["ex_date"] or "", reverse=True)
    return {
        "symbol": symbol,
        "status": "OK" if payments else "NO_DATA",
        "pays_dividend": bool(payments),
        "payments": payments[:limit],
        "count": len(payments),
        "all_payments": payments,
        "detail": ("Declared dividends as filed, with the dates each one was "
                   "declared, went ex, and was paid."),
        "source": SOURCE,
    }


def _clean(value) -> Optional[str]:
    text = _day(value)
    return None if text in ("", "None", "null") else text


# ---------------------------------------------------------------------------
# analysts
# ---------------------------------------------------------------------------


def analyst_actions(symbol: str = "", limit: int = 50) -> dict:
    """Upgrades, downgrades, initiations and target changes."""
    symbol = (symbol or "").upper().strip()
    out = uw.analyst_actions(symbol, limit)
    if out["status"] != "OK":
        return {"symbol": symbol, "status": out["status"], "rows": [],
                "detail": out.get("detail"), "source": SOURCE}

    rows = []
    for r in uw._rows(out):
        action = str(r.get("action") or "").lower()
        rows.append({
            "symbol": r.get("ticker"),
            "firm": r.get("firm"),
            "analyst": r.get("analyst_name"),
            "action": action.upper() or None,
            "recommendation": str(r.get("recommendation") or "").upper() or None,
            "target": _f(r.get("target")),
            "when": r.get("timestamp"),
            "date": _day(r.get("timestamp")),
            # An upgrade and a raised target are both positive, and a
            # maintained rating is neither -- said as itself rather than
            # rounded toward whichever side the last action took.
            "lean": ("UP" if action in ("upgraded", "initiated", "raised")
                     and _positive(r.get("recommendation"))
                     else "DOWN" if action in ("downgraded", "lowered")
                     or _negative(r.get("recommendation"))
                     else "FLAT"),
        })

    ups = sum(1 for r in rows if r["lean"] == "UP")
    downs = sum(1 for r in rows if r["lean"] == "DOWN")
    return {
        "symbol": symbol, "status": "OK" if rows else "NO_DATA",
        "rows": rows, "count": len(rows),
        "upgrades": ups, "downgrades": downs,
        "net": ups - downs,
        "detail": "Analyst actions as published, newest first.",
        "source": SOURCE,
    }


def _positive(recommendation) -> bool:
    return str(recommendation or "").lower() in (
        "buy", "strong buy", "outperform", "overweight", "positive")


def _negative(recommendation) -> bool:
    return str(recommendation or "").lower() in (
        "sell", "strong sell", "underperform", "underweight", "negative")


# ---------------------------------------------------------------------------
# news
# ---------------------------------------------------------------------------

# Words that carry a direction in a headline. Deliberately small: this is a
# keyword estimate and is labelled as one, never as a provider's sentiment
# model, because the difference matters to anyone reading the number.
_POSITIVE = ("beats", "beat", "raises", "raised", "upgrade", "upgrades",
             "surges", "soars", "jumps", "record", "wins", "approval",
             "outperform", "rally", "strong", "tops", "boosts", "gains",
             "climbs", "rises", "higher", "buyback", "repurchase", "dividend "
             "increase", "expands", "partnership", "launches", "secures",
             "deal", "acquires", "growth", "profit", "bullish", "optimistic",
             "hikes", "accelerates", "breakthrough", "demand")
_NEGATIVE = ("misses", "miss", "cuts", "cut", "downgrade", "downgrades",
             "falls", "plunges", "sinks", "probe", "lawsuit", "recall",
             "warns", "weak", "halts", "delays", "slumps", "drops", "lower",
             "loss", "losses", "layoffs", "investigation", "subpoena",
             "fraud", "bearish", "slowdown", "shortfall", "bankruptcy",
             "resigns", "steps down", "blocked", "banned", "fine", "tumbles")


def _tone(headline: str) -> tuple[Optional[float], str]:
    text = (headline or "").lower()
    up = sum(1 for word in _POSITIVE if word in text)
    down = sum(1 for word in _NEGATIVE if word in text)
    # Lowercase buckets, matching news_desk_service, the sentiment counters and
    # the frontend filter -- which all compare against "positive"/"negative"/
    # "neutral"/"unscored". Returning uppercase here silently broke the Positive/
    # Neutral/Negative tabs (they matched nothing) and the sentiment split.
    if not up and not down:
        return None, "unscored"
    score = round((up - down) / (up + down), 3)
    return score, ("positive" if score > 0.2 else
                   "negative" if score < -0.2 else "neutral")


def news(symbol: str = "", limit: int = 20) -> dict:
    """Headlines, for one symbol or the whole market."""
    symbol = (symbol or "").upper().strip()
    out = uw.headlines(symbol, limit)
    if out["status"] != "OK":
        return {"symbol": symbol, "status": out["status"], "items": [],
                "detail": out.get("detail"), "source": SOURCE}

    items = []
    for r in uw._rows(out):
        headline = str(r.get("headline") or "").strip()
        if not headline:
            continue
        score, bucket = _tone(headline)
        items.append({
            "id": r.get("id") or f"{r.get('created_at')}:{headline[:40]}",
            "headline": headline,
            "summary": None,
            "url": r.get("url") or r.get("link"),
            "provider": r.get("source"),
            "published_at": r.get("created_at"),
            "time": r.get("created_at"),
            "time_label": _ago(r.get("created_at")),
            "symbols": [t for t in (r.get("tickers") or []) if t][:4],
            "tags": r.get("tags") or [],
            "sentiment_score": score,
            "sentiment": bucket,
        })

    items.sort(key=lambda i: str(i.get("published_at") or ""), reverse=True)
    scored = [i["sentiment_score"] for i in items if i["sentiment_score"] is not None]
    return {
        "symbol": symbol,
        "status": "OK" if items else "NO_DATA",
        "items": items[:limit],
        "count": len(items[:limit]),
        "tone": round(sum(scored) / len(scored), 3) if scored else None,
        "tone_basis": ("A keyword estimate over the headline, not a "
                       "provider sentiment model."),
        "detail": "Market headlines as published.",
        "source": SOURCE,
    }


def _ago(stamp) -> Optional[str]:
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    minutes = (datetime.now(timezone.utc) - when).total_seconds() / 60
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{int(minutes)}m ago"
    if minutes < 1440:
        return f"{int(minutes / 60)}h ago"
    return f"{int(minutes / 1440)}d ago"
