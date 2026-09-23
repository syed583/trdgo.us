"""
What the market actually did after each report.

Two measurements, deliberately kept apart:

* **The reaction** -- the close-to-close move across the first session that
  could price the report. Which session that is depends on the timing: a
  before-open report is priced the same day, an after-close report the next.
  Treating them the same measures the wrong day roughly half the time.

* **The drift** -- the move over the sessions *after* the reaction, from the
  reaction close onward. This is the part that answers "did the market keep
  going once it had read the filing", and folding it into the reaction would
  hide exactly that.

Bars come from whatever chart provider is answering, so this works without a
broker connection. Nothing is estimated: a quarter whose reaction session is
not in the bar history is left unmeasured rather than approximated from a
neighbouring day.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

import live_market_service as market
from database import SessionLocal
from models_earnings import EarningsCalendarEntry

# Sessions after the reaction used to measure drift. One week of trading: long
# enough for the move to be something other than noise, short enough that it is
# still attributable to the report.
DRIFT_SESSIONS = 5

# How far back to measure. The calendar only holds a few quarters, so this is
# generous; it exists to stop a future backfill walking years of history.
LOOKBACK_DAYS = 400

# A move beyond this is almost certainly a split or a bad bar rather than an
# earnings reaction, and one of them would dominate every average on the page.
IMPLAUSIBLE_MOVE_PCT = 60.0


def _bar_date(bar: dict) -> Optional[date]:
    raw = str(bar.get("t") or bar.get("date") or "")[:10]
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def _closes(symbol: str) -> list[tuple[date, float]]:
    """Daily closes, oldest first, from any provider that answers."""
    try:
        chart = market.get_chart(symbol, "1Y")
    except Exception:  # noqa: BLE001 - one symbol must not stop a backfill
        return []
    out: list[tuple[date, float]] = []
    for bar in chart.get("bars") or []:
        when = _bar_date(bar)
        close = bar.get("close")
        if when and close:
            out.append((when, float(close)))
    out.sort(key=lambda row: row[0])
    return out


def _reaction_index(closes: list[tuple[date, float]],
                    when: date, timing: Optional[str]) -> Optional[int]:
    """
    Index of the session that prices the report.

    Before the open, the report is in that day's close. After the close (or
    when the timing is unknown, which is the safer assumption), it is in the
    next session's close.
    """
    same_day = (timing or "").upper() == "BMO"
    for i, (bar_date, _close) in enumerate(closes):
        if bar_date < when:
            continue
        if bar_date == when:
            return i if same_day else (i + 1 if i + 1 < len(closes) else None)
        # The report date itself is not a trading session -- a weekend or a
        # holiday. The next session prices it either way.
        return i
    return None


def measure(symbol: str, when: date, timing: Optional[str],
            closes: Optional[list[tuple[date, float]]] = None) -> dict:
    """Reaction and drift for one report, or a reason there is neither."""
    closes = closes if closes is not None else _closes(symbol)
    if len(closes) < 2:
        return {"status": "NO_BARS",
                "detail": f"No daily history available for {symbol}."}

    index = _reaction_index(closes, when, timing)
    if index is None or index < 1 or index >= len(closes):
        return {"status": "NO_SESSION",
                "detail": (f"No trading session on record prices a report "
                           f"dated {when.isoformat()}.")}

    before = closes[index - 1][1]
    reaction_close = closes[index][1]
    if before <= 0:
        return {"status": "NO_SESSION", "detail": "Prior close was not usable."}

    move = (reaction_close - before) / before * 100.0
    if abs(move) > IMPLAUSIBLE_MOVE_PCT:
        return {"status": "IMPLAUSIBLE",
                "detail": (f"A {move:.0f}% close-to-close move is a split or a "
                           "bad bar, not an earnings reaction.")}

    drift = None
    tail = index + DRIFT_SESSIONS
    if tail < len(closes):
        drift = (closes[tail][1] - reaction_close) / reaction_close * 100.0
        if abs(drift) > IMPLAUSIBLE_MOVE_PCT:
            drift = None

    return {
        "status": "OK",
        "symbol": symbol,
        "reaction_date": closes[index][0].isoformat(),
        "move": round(move, 2),
        "drift": round(drift, 2) if drift is not None else None,
    }


def backfill(limit: int = 200, refresh: bool = False) -> dict:
    """
    Measure every reported quarter that has no reaction on file.

    Bars are fetched once per symbol and reused across that symbol's quarters:
    the history covers all of them, and one request per quarter would be four
    requests for the same year of data.
    """
    cutoff = date.today().toordinal() - LOOKBACK_DAYS
    db = SessionLocal()
    try:
        query = (
            db.query(EarningsCalendarEntry)
            .filter(EarningsCalendarEntry.eps_actual.isnot(None))
            .filter(EarningsCalendarEntry.earnings_date
                    >= date.fromordinal(max(cutoff, 1)))
            .filter(EarningsCalendarEntry.earnings_date <= date.today())
        )
        if not refresh:
            query = query.filter(
                EarningsCalendarEntry.post_earnings_move_percent.is_(None))
        rows = query.order_by(
            EarningsCalendarEntry.symbol,
            EarningsCalendarEntry.earnings_date).limit(limit).all()

        measured = skipped = 0
        reasons: dict[str, int] = {}
        cache: dict[str, list[tuple[date, float]]] = {}

        for entry in rows:
            if entry.symbol not in cache:
                cache[entry.symbol] = _closes(entry.symbol)
            result = measure(entry.symbol, entry.earnings_date,
                             entry.reporting_time, cache[entry.symbol])
            if result.get("status") != "OK":
                skipped += 1
                reasons[result.get("status", "UNKNOWN")] = (
                    reasons.get(result.get("status", "UNKNOWN"), 0) + 1)
                continue
            entry.post_earnings_move_percent = result["move"]
            entry.post_earnings_drift_percent = result["drift"]
            entry.reaction_date = result["reaction_date"]
            measured += 1

        if measured:
            db.commit()
        return {
            "status": "OK",
            "considered": len(rows),
            "measured": measured,
            "skipped": skipped,
            "reasons": reasons,
            "symbols": len(cache),
        }
    finally:
        db.close()
