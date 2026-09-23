"""
Earnings history, statistics and event lifecycle.

Two sources are merged, each labelled:

* Benzinga (`earnings_calendar`) - the real provider cache, when configured.
* The legacy `earnings_events` table - seed rows, flagged TEST_DATA.

Production statistics are computed from verified rows only. A seed row can be
displayed, but it never contributes to a beat rate, an average surprise or a
score - otherwise a demo fixture would quietly become a trading signal.

Post-earnings moves are measured from real IBKR daily bars: the close-to-close
change on the first session after the report, matched to the actual reporting
time (a BMO report moves that same day, an AMC report moves the next).
"""

from __future__ import annotations

import statistics
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import benzinga_earnings_service as benzinga
import live_market_service as market
import provider_config as cfg
from database import SessionLocal
from models import Company, EarningsEvent

HISTORY_TTL = 6 * 3600.0

# A composite result is only half provider data: the moves come from IBKR. If
# that half fails, the merged result must not sit in the cache for six hours
# reporting "no moves measured" long after TWS has recovered.
DEGRADED_TTL = 120.0

# Ordered so a UI can render it as a pipeline.
LIFECYCLE_STAGES = [
    "SCHEDULED", "PRE_READY", "RESULT_DETECTED", "PARTIAL_RESULT",
    "OFFICIAL_VERIFIED", "POST_SCORE_READY", "ENTRY_MONITORING",
    "TRADE_READY", "NO_TRADE",
]

LIFECYCLE_LABELS = {
    "SCHEDULED": "Scheduled",
    "PRE_READY": "Pre-report ready",
    "RESULT_DETECTED": "Report due — awaiting numbers",
    "PARTIAL_RESULT": "Partial result — waiting for complete report",
    "OFFICIAL_VERIFIED": "Official result verified",
    "POST_SCORE_READY": "Post-earnings score ready",
    "ENTRY_MONITORING": "Monitoring for entry",
    "TRADE_READY": "Trade ready",
    "NO_TRADE": "No trade",
}


def _legacy_rows(db, symbol: str) -> list[dict]:
    """Rows from the original earnings_events table, marked as seed data."""
    company = db.query(Company).filter(Company.symbol == symbol.upper()).first()
    if not company:
        return []

    events = (
        db.query(EarningsEvent)
        .filter(EarningsEvent.company_id == company.id)
        .order_by(EarningsEvent.earnings_date.desc())
        .all()
    )

    out = []
    for e in events:
        est = float(e.eps_estimate) if e.eps_estimate else None
        act = float(e.eps_actual) if e.eps_actual else None
        rev_est = float(e.revenue_estimate) if e.revenue_estimate else None
        rev_act = float(e.revenue_actual) if e.revenue_actual else None
        surprise = (
            round((act - est) / abs(est) * 100, 2)
            if act is not None and est else None
        )
        out.append({
            "symbol": symbol.upper(),
            "company": company.company_name,
            "date": e.earnings_date.isoformat() if e.earnings_date else None,
            "date_label": (e.earnings_date.strftime("%b %d, %Y")
                           if e.earnings_date else None),
            "reporting_time": e.reporting_time,
            "quarter_label": (
                f"Q{(e.earnings_date.month - 1) // 3 + 1} {e.earnings_date.year}"
                if e.earnings_date else None
            ),
            "eps_estimate": est, "eps_actual": act,
            "eps_surprise_percent": surprise,
            "revenue_estimate": rev_est, "revenue_actual": rev_act,
            "revenue_surprise_percent": (
                round((rev_act - rev_est) / abs(rev_est) * 100, 2)
                if rev_act is not None and rev_est else None
            ),
            "post_earnings_move_percent": None,
            "lifecycle": _lifecycle_for(act, rev_act, e.earnings_date, e.status),
            "beat": bool(act is not None and est is not None and act > est),
            # This is the crucial flag: seed rows never feed statistics.
            "source": "DATABASE_SEED",
            "data_status": cfg.TEST_DATA,
            "verified": False,
        })
    return out


def _lifecycle_for(eps_actual, revenue_actual, when: Optional[date],
                   status: Optional[str] = None) -> str:
    today = datetime.now(timezone.utc).date()
    if eps_actual is None and revenue_actual is None:
        if when and when <= today:
            return "RESULT_DETECTED"
        if when and when <= today + timedelta(days=1):
            return "PRE_READY"
        return "SCHEDULED"
    if eps_actual is not None and revenue_actual is not None:
        return "OFFICIAL_VERIFIED"
    return "PARTIAL_RESULT"


def _attach_post_earnings_moves(symbol: str, rows: list[dict]) -> tuple[int, str]:
    """
    Measure the real reaction to each report from IBKR daily bars.

    BMO reports move the session they land on; AMC reports move the next
    session. Rows whose date falls outside the available bar history are left
    as None rather than approximated.
    """
    dated = [r for r in rows if r.get("date")]
    if not dated:
        return 0, cfg.OK

    # Daily bars are required: the reaction is a single session, and the 5Y
    # range serves weekly bars, which cannot express it. The 1Y range is the
    # deepest daily window, so reports older than that keep a null move rather
    # than a weekly approximation.
    chart = market.get_chart(symbol, "1Y")
    if chart.get("status") != "OK" or not chart.get("bars"):
        # The moves are missing because the bar source failed, not because the
        # reports fall outside the window. Those are different facts and the
        # caller caches them differently.
        return 0, chart.get("status") or cfg.DATA_UNAVAILABLE

    bars = [{"t": b["t"][:10], "close": b["close"]} for b in chart["bars"]]
    index = {b["t"]: i for i, b in enumerate(bars)}
    dates = sorted(index)
    earliest = dates[0] if dates else None

    filled = 0
    for row in dated:
        when = row["date"]
        timing = (row.get("reporting_time") or "").upper()

        # Outside the daily window there is nothing honest to measure.
        if earliest and when < earliest:
            continue

        # First session at or after the report date.
        after = next((d for d in dates if d >= when), None)
        if not after:
            continue
        i = index[after]

        # An after-close report is reflected in the *next* session.
        if timing == "AMC" and i + 1 < len(bars):
            i += 1
        if i == 0 or i >= len(bars):
            continue

        prev_close = bars[i - 1]["close"]
        close = bars[i]["close"]
        if not prev_close:
            continue

        row["post_earnings_move_percent"] = round(
            (close - prev_close) / prev_close * 100, 2)
        row["post_earnings_date"] = bars[i]["t"][:10]
        filled += 1

    return filled, cfg.OK


def _from_unusual_whales(symbol: str, quarters: int) -> list[dict]:
    """Reported quarters from Unusual Whales, in the model's shape."""
    try:
        import uw_company_service as uwc

        out = uwc.earnings_history(symbol, quarters)
    except Exception:  # noqa: BLE001 - the history stands without it
        return []
    if out.get("status") != cfg.OK:
        return []

    rows = []
    for r in out.get("rows") or []:
        actual, estimate = r.get("eps_actual"), r.get("eps_estimate")
        date_str = r.get("date") or ""
        rows.append({
            "symbol": symbol, "company": r.get("company"), "date": date_str,
            "date_label": _label(date_str), "quarter_label": _label(date_str),
            "eps_estimate": estimate, "eps_actual": actual,
            "eps_surprise_percent": r.get("eps_surprise_percent"),
            "revenue_estimate": r.get("revenue_estimate"),
            "revenue_actual": r.get("revenue_actual"),
            "revenue_surprise_percent": r.get("revenue_surprise_percent"),
            "post_earnings_move_percent": None,
            "eps_prior": None, "revenue_prior": None,
            # Carried through: the reaction to an after-close report is the
            # next session, and dropping the clock measures the day before
            # the news.
            "reporting_time": r.get("reporting_time"),
            "report_time": None,
            "time_label": {"AMC": "After Close", "BMO": "Before Open"}.get(
                r.get("reporting_time")),
            "exchange": None, "importance": None, "importance_label": None,
            "fiscal_period": None, "fiscal_year": None,
            "lifecycle": "REPORTED", "date_confirmed": True,
            "beat": bool(estimate is not None and actual is not None
                         and actual > estimate),
            "verified": True, "data_status": cfg.OK,
            "source": "UNUSUAL_WHALES", "fetched_at": None,
        })
    return rows[:quarters]


def _from_finviz(symbol: str, quarters: int) -> list[dict]:
    """Reported quarters from Finviz, in the shape the model already reads."""
    try:
        import finviz_service as finviz

        out = finviz.earnings_history(symbol, quarters)
    except Exception:  # noqa: BLE001 - the history stands without it
        return []
    if out.get("status") != cfg.OK:
        return []

    rows = []
    for r in out.get("rows") or []:
        actual, estimate = r.get("eps_actual"), r.get("eps_estimate")
        if actual is None:
            continue  # not reported yet; a forecast is not a track record
        date_str = r.get("date") or ""
        rows.append({
            "symbol": symbol, "company": r.get("company"), "date": date_str,
            "date_label": _label(date_str), "quarter_label": _label(date_str),
            "eps_estimate": estimate, "eps_actual": actual,
            "eps_surprise_percent": r.get("eps_surprise_percent"),
            "revenue_estimate": r.get("revenue_estimate"),
            "revenue_actual": r.get("revenue_actual"),
            "revenue_surprise_percent": r.get("revenue_surprise_percent"),
            "post_earnings_move_percent": r.get("price_reaction_pct"),
            "eps_prior": None, "revenue_prior": None,
            # Carried through deliberately: the reaction to an after-close
            # report is the next session, and dropping the clock here
            # measured the day before the news.
            "reporting_time": r.get("reporting_time"),
            "report_time": r.get("report_time"),
            "time_label": {"AMC": "After Close", "BMO": "Before Open",
                           "DMT": "During Market"}.get(r.get("reporting_time")),
            "exchange": None, "importance": None, "importance_label": None,
            "fiscal_period": None, "fiscal_year": None,
            "lifecycle": "REPORTED", "date_confirmed": True,
            "beat": bool(estimate is not None and actual > estimate),
            "verified": True, "data_status": cfg.OK,
            "source": "FINVIZ", "fetched_at": None,
        })
    return rows[:quarters]


def _label(date_str: str) -> Optional[str]:
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%b %d, %Y")
    except (TypeError, ValueError):
        return None


def get_history(symbol: str, quarters: int = 8) -> dict:
    """Reported quarters plus the statistics derived from the verified ones."""
    symbol = symbol.upper()
    cache_key = f"earnhist:{symbol}:{quarters}"
    cached = market.cache.get(cache_key, HISTORY_TTL)
    if cached and cached.get("moves_status") != cfg.OK:
        # Re-read under the short TTL so a result degraded by an IBKR outage
        # is recomputed once the connection is back.
        cached = market.cache.get(cache_key, DEGRADED_TTL)
    if cached:
        return cached

    # Unusual Whales leads. Benzinga's history is a local cache somebody has
    # to sync, and a half-synced one answers with a single quarter -- which
    # the screen then presents as a company's whole track record, beat rate
    # and all. Finviz stays behind both.
    provider = benzinga.get_history(symbol, quarters)
    cached_rows = [
        {**r, "verified": True, "data_status": cfg.OK}
        for r in provider.get("quarters", [])
    ]
    verified = _from_unusual_whales(symbol, quarters)
    if len(verified) < len(cached_rows):
        verified = cached_rows
    if not verified:
        verified = _from_finviz(symbol, quarters)

    db = SessionLocal()
    try:
        seed = _legacy_rows(db, symbol)
    finally:
        db.close()

    # Provider rows win on any date the seed table also covers.
    seen = {r["date"] for r in verified}
    merged = verified + [r for r in seed if r["date"] not in seen]
    merged.sort(key=lambda r: r["date"] or "", reverse=True)
    merged = merged[:quarters]

    moves_filled, moves_status = _attach_post_earnings_moves(symbol, merged)

    stats = _statistics(merged)

    result = {
        "symbol": symbol,
        "quarters": merged,
        "count": len(merged),
        "verified_count": sum(1 for r in merged if r["verified"]),
        "seed_count": sum(1 for r in merged if not r["verified"]),
        "moves_measured": moves_filled,
        "moves_status": moves_status,
        "moves_detail": (
            None if moves_status == cfg.OK else
            "Post-earnings moves could not be measured: the IBKR daily bars "
            f"were unavailable ({moves_status})."
        ),
        "stats": stats,
        "provider": {
            "name": "Benzinga",
            "status": provider.get("status"),
            "detail": provider.get("detail"),
            "configured": cfg.BENZINGA.configured,
            "env_var": cfg.BENZINGA.key_env,
            "signup": cfg.BENZINGA.signup,
        },
        "status": (
            cfg.OK if stats["basis"] == "VERIFIED"
            else cfg.TEST_DATA if merged
            else cfg.DATA_UNAVAILABLE
        ),
        # Named for where the rows actually came from. Reporting
        # "BENZINGA+DATABASE" over quarters Finviz supplied is the kind of
        # small lie that makes every other label worth less.
        "source": "+".join(sorted({r.get("source") or "DATABASE"
                                   for r in merged}) or ["DATABASE"]),
    }
    market.cache.put(cache_key, result)
    return result


def _statistics(rows: list[dict]) -> dict:
    """
    Beat rate and move statistics.

    Computed from verified provider rows only. If the only rows on record are
    seed data, every figure is None and the basis says TEST_DATA - a seed
    fixture must never look like a track record.
    """
    verified = [r for r in rows if r.get("verified")]

    if not verified:
        return {
            "basis": "TEST_DATA" if rows else "NO_DATA",
            "detail": (
                "Only seed rows are on record, so no statistics are computed. "
                "Configure Benzinga to build a verified earnings history."
                if rows else "No earnings history on record."
            ),
            "sample_size": 0,
            "beat_rate": None, "average_surprise": None,
            "revenue_beat_rate": None, "consecutive_beats": None,
            "average_move": None, "median_move": None, "largest_move": None,
        }

    eps_pairs = [r for r in verified
                 if r["eps_actual"] is not None and r["eps_estimate"] is not None]
    beats = [r for r in eps_pairs if r["eps_actual"] > r["eps_estimate"]]
    surprises = [r["eps_surprise_percent"] for r in verified
                 if r["eps_surprise_percent"] is not None]

    rev_pairs = [r for r in verified
                 if r["revenue_actual"] is not None
                 and r["revenue_estimate"] is not None]
    rev_beats = [r for r in rev_pairs
                 if r["revenue_actual"] > r["revenue_estimate"]]

    # Newest-first, so the streak counts forward from the latest report.
    consecutive = 0
    for r in eps_pairs:
        if r["eps_actual"] > r["eps_estimate"]:
            consecutive += 1
        else:
            break

    moves = [abs(r["post_earnings_move_percent"]) for r in verified
             if r.get("post_earnings_move_percent") is not None]

    return {
        "basis": "VERIFIED",
        "detail": f"Computed from {len(verified)} verified quarters.",
        "sample_size": len(verified),
        "beat_rate": round(len(beats) / len(eps_pairs) * 100) if eps_pairs else None,
        "average_surprise": (round(sum(surprises) / len(surprises), 2)
                             if surprises else None),
        "revenue_beat_rate": (round(len(rev_beats) / len(rev_pairs) * 100)
                              if rev_pairs else None),
        "consecutive_beats": consecutive if eps_pairs else None,
        "average_move": round(sum(moves) / len(moves), 2) if moves else None,
        "median_move": round(statistics.median(moves), 2) if moves else None,
        "largest_move": round(max(moves), 2) if moves else None,
        "moves_measured": len(moves),
    }


def as_score_history(payload: dict) -> dict:
    """
    Reshape verified history into the structure the earnings-history scorer
    expects (`{"events": [...], "summary": {...}}`).

    The scorer predates the provider layer and was written against the legacy
    `earnings_events` table. Gating on Benzinga while still scoring that table
    meant a verified 100% beat rate was read from an empty legacy row set and
    scored 0/15 - while reporting OK. This adapter is what connects the gate to
    the data the gate approved.

    Verified rows only, which is the whole point of the gate.
    """
    verified = [r for r in payload.get("quarters", []) if r.get("verified")]

    events = [
        {
            "date": r.get("date"),
            "eps_surprise_percent": r.get("eps_surprise_percent"),
            "revenue_surprise_percent": r.get("revenue_surprise_percent"),
        }
        for r in verified
    ]

    stats = payload.get("stats", {})

    # "Comparable" means both sides of the comparison are present; the scorer
    # derives its sample-size confidence from these counts.
    eps_comparable = sum(
        1 for r in verified
        if r.get("eps_actual") is not None and r.get("eps_estimate") is not None
    )
    revenue_comparable = sum(
        1 for r in verified
        if r.get("revenue_actual") is not None
        and r.get("revenue_estimate") is not None
    )

    return {
        "symbol": payload.get("symbol"),
        "events": events,
        "summary": {
            "total_events": len(events),
            "eps_beat_rate": stats.get("beat_rate"),
            "revenue_beat_rate": stats.get("revenue_beat_rate"),
            "eps_comparable_events": eps_comparable,
            "revenue_comparable_events": revenue_comparable,
        },
    }


def get_event_lifecycle(symbol: str) -> dict:
    """
    Where the next/most recent event sits in the reporting pipeline.

    The states past OFFICIAL_VERIFIED depend on the scoring engine, so they are
    only reached when the score itself is available and gated.
    """
    symbol = symbol.upper()
    today = datetime.now(timezone.utc).date()

    upcoming = benzinga.get_upcoming(today, today + timedelta(days=400), [symbol])
    rows = upcoming.get("rows", [])

    event = rows[0] if rows else None
    source = "BENZINGA"

    if not event:
        db = SessionLocal()
        try:
            seed = _legacy_rows(db, symbol)
        finally:
            db.close()
        future = [r for r in seed if r["date"] and r["date"] >= today.isoformat()]
        event = future[-1] if future else None
        source = "DATABASE_SEED" if event else None

    if not event:
        return {
            "symbol": symbol,
            "stage": None,
            "stages": LIFECYCLE_STAGES,
            "labels": LIFECYCLE_LABELS,
            "status": cfg.DATA_UNAVAILABLE,
            "detail": (
                f"No scheduled earnings event on record for {symbol}. "
                + ("Configure Benzinga to populate the calendar."
                   if not cfg.BENZINGA.configured else "")
            ),
        }

    stage = event.get("lifecycle") or "SCHEDULED"
    waiting = stage == "PARTIAL_RESULT"

    return {
        "symbol": symbol,
        "stage": stage,
        "stage_label": LIFECYCLE_LABELS.get(stage, stage),
        "stage_index": (LIFECYCLE_STAGES.index(stage)
                        if stage in LIFECYCLE_STAGES else 0),
        "stages": LIFECYCLE_STAGES,
        "labels": LIFECYCLE_LABELS,
        "event": event,
        "waiting_for_complete_result": waiting,
        "waiting_detail": (
            "EPS has been reported but revenue has not. The report is "
            "incomplete and is not scored as a finished result."
            if waiting else None
        ),
        "source": source,
        "status": cfg.OK if source == "BENZINGA" else cfg.TEST_DATA,
    }
