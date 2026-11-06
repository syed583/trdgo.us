"""
Earnings reaction outcomes: when an earnings event can be graded, and how.

One row per (symbol, earnings_date) in `earnings_event_outcomes`, shared by every model and every
snapshot of that event. The rules:

  * Before Open  : reaction session = the earnings-day session (or the next trading day when the
                   date is not a trading day). Base = the previous trading day's close.
  * After Close  : reaction session = the next trading day after the earnings date. Base = the
                   earnings-day close (or the last close before it).
  * An event is only graded once the reaction session has CLOSED (13:00 ET on early-close days,
    16:00 ET otherwise, America/New_York with daylight saving) plus a short availability buffer,
    AND both closes are present in the price history. The move is (reaction close - base close) /
    base close.
  * Until then the event is PENDING_REACTION. If the reaction session has closed but a price is not
    available yet the event is PENDING_PRICE and is retried on a back-off schedule that never stops;
    it is never marked permanently unverifiable. Unknown report timing is PENDING_TIMING.
  * A GRADED outcome is never rewritten.

Original rows in `model_predictions` are not rewritten either: `sync_legacy_columns` only fills the
legacy outcome columns of rows that have not been evaluated yet.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

import market_calendar as mc

log = logging.getLogger(__name__)

METHOD_VERSION = "2A-1"
FLAT_PCT = 0.5                      # |move| inside this band counts as a flat reaction
READY_BUFFER = timedelta(minutes=45)  # time after the closing bell for the daily close to be published

GRADED = "GRADED"
PENDING_REACTION = "PENDING_REACTION"
PENDING_PRICE = "PENDING_PRICE"
PENDING_TIMING = "PENDING_TIMING"

BMO = "BMO"
AMC = "AMC"


def timing_kind(label: Optional[str]) -> Optional[str]:
    """'BMO' / 'AMC' from a calendar label such as 'Before Open' / 'After Close'; None if unknown."""
    s = (label or "").strip().lower()
    if not s:
        return None
    if "before" in s or "pre" in s or s.startswith("bmo"):
        return BMO
    if "after" in s or "post" in s or s.startswith("amc"):
        return AMC
    return None


def direction_of(move_pct: float) -> str:
    if move_pct > FLAT_PCT:
        return "BUY"
    if move_pct < -FLAT_PCT:
        return "SELL"
    return "NEUTRAL"


def reaction_window(earnings_date: date, kind: str) -> tuple[date, date]:
    """(base close date, reaction close date) for an event."""
    if kind == BMO:
        reaction = mc.on_or_next_trading_day(earnings_date)
        return mc.prev_trading_day(reaction), reaction
    if kind == AMC:
        return mc.on_or_prev_trading_day(earnings_date), mc.next_trading_day(earnings_date)
    raise ValueError(f"unknown timing kind {kind!r}")


def ready_at(reaction_date: date) -> datetime:
    """UTC instant from which the reaction-session close may be graded."""
    return mc.session_close_utc(reaction_date) + READY_BUFFER


def evaluate(symbol: str, earnings_date: date, kind: Optional[str], now: datetime,
             closes: Optional[dict]) -> dict:
    """The outcome state of one event at `now`. `closes` maps date -> daily close (may be None)."""
    out = {"symbol": symbol, "earnings_date": earnings_date, "timing": kind, "status": PENDING_TIMING,
           "base_close_date": None, "base_close": None, "reaction_close_date": None, "reaction_close": None,
           "move_pct": None, "direction": None, "ready_at": None, "note": None}
    if kind not in (BMO, AMC):
        out["note"] = "report timing unknown"
        return out
    base_d, react_d = reaction_window(earnings_date, kind)
    out.update(base_close_date=base_d, reaction_close_date=react_d, ready_at=ready_at(react_d))
    if now < out["ready_at"]:
        out["status"] = PENDING_REACTION
        out["note"] = f"reaction session {react_d} has not closed"
        return out
    base_c = (closes or {}).get(base_d)
    react_c = (closes or {}).get(react_d)
    if not base_c or not react_c or base_c <= 0 or react_c <= 0:
        missing = [str(d) for d, c in ((base_d, base_c), (react_d, react_c)) if not c or c <= 0]
        out["status"] = PENDING_PRICE
        out["note"] = "close not available yet: " + ", ".join(missing)
        return out
    move = round((react_c - base_c) / base_c * 100.0, 2)
    out.update(status=GRADED, base_close=float(base_c), reaction_close=float(react_c), move_pct=move,
               direction=direction_of(move), note=None)
    return out


def retry_due(prev: Optional[dict], ready: datetime, now: datetime) -> bool:
    """Back-off for incomplete events: hourly for 2 days after the session closed, 6-hourly for two
    weeks, daily for two months, then weekly. It never stops, so late data is always picked up."""
    if prev is None:
        return True
    if prev.get("status") == GRADED:
        return False
    last = prev.get("last_attempt_at")
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    age = now - ready
    if age < timedelta(days=2):
        gap = timedelta(minutes=55)
    elif age < timedelta(days=14):
        gap = timedelta(hours=6)
    elif age < timedelta(days=60):
        gap = timedelta(hours=24)
    else:
        gap = timedelta(days=7)
    return now - last >= gap


# --------------------------------------------------------------------------------------------- #
# Storage (portable SQL: PostgreSQL in production, SQLite in tests)                              #
# --------------------------------------------------------------------------------------------- #
_DDL = (
    "CREATE TABLE IF NOT EXISTS earnings_event_outcomes ("
    " symbol VARCHAR(20) NOT NULL, earnings_date DATE NOT NULL, timing VARCHAR(8),"
    " status VARCHAR(20) NOT NULL, base_close_date DATE, base_close DOUBLE PRECISION,"
    " reaction_close_date DATE, reaction_close DOUBLE PRECISION, move_pct DOUBLE PRECISION,"
    " direction VARCHAR(8), method_version VARCHAR(12), attempts INTEGER NOT NULL DEFAULT 0,"
    " last_attempt_at TIMESTAMP, graded_at TIMESTAMP, note VARCHAR(200),"
    " PRIMARY KEY (symbol, earnings_date))")

_UPSERT = (
    "INSERT INTO earnings_event_outcomes (symbol, earnings_date, timing, status, base_close_date, base_close,"
    " reaction_close_date, reaction_close, move_pct, direction, method_version, attempts, last_attempt_at,"
    " graded_at, note) VALUES (:symbol, :earnings_date, :timing, :status, :base_close_date, :base_close,"
    " :reaction_close_date, :reaction_close, :move_pct, :direction, :method_version, :attempts,"
    " :last_attempt_at, :graded_at, :note)"
    " ON CONFLICT (symbol, earnings_date) DO UPDATE SET timing = excluded.timing, status = excluded.status,"
    " base_close_date = excluded.base_close_date, base_close = excluded.base_close,"
    " reaction_close_date = excluded.reaction_close_date, reaction_close = excluded.reaction_close,"
    " move_pct = excluded.move_pct, direction = excluded.direction, method_version = excluded.method_version,"
    " attempts = excluded.attempts, last_attempt_at = excluded.last_attempt_at, graded_at = excluded.graded_at,"
    " note = excluded.note WHERE earnings_event_outcomes.status <> 'GRADED'")


def _utc_naive(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _d(v) -> Optional[date]:
    if v is None or isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    return date.fromisoformat(str(v)[:10])


def _dt(v) -> Optional[datetime]:
    if v is None or isinstance(v, datetime):
        return v
    s = str(v).replace("Z", "+00:00")
    return datetime.fromisoformat(s)


def ensure_table(db) -> None:
    from sqlalchemy import text
    db.execute(text(_DDL))
    db.commit()


def load_outcomes(db) -> dict:
    from sqlalchemy import text
    rows = db.execute(text(
        "SELECT symbol, earnings_date, timing, status, base_close_date, base_close, reaction_close_date,"
        " reaction_close, move_pct, direction, attempts, last_attempt_at, graded_at, note"
        " FROM earnings_event_outcomes")).all()
    out = {}
    for r in rows:
        out[(r.symbol, _d(r.earnings_date))] = {
            "symbol": r.symbol, "earnings_date": _d(r.earnings_date), "timing": r.timing, "status": r.status,
            "base_close_date": _d(r.base_close_date), "base_close": r.base_close,
            "reaction_close_date": _d(r.reaction_close_date), "reaction_close": r.reaction_close,
            "move_pct": r.move_pct, "direction": r.direction, "attempts": r.attempts or 0,
            "last_attempt_at": _dt(r.last_attempt_at), "graded_at": _dt(r.graded_at), "note": r.note}
    return out


def _save(db, o: dict, prev: Optional[dict], now: datetime) -> None:
    from sqlalchemy import text
    db.execute(text(_UPSERT), {
        "symbol": o["symbol"], "earnings_date": o["earnings_date"], "timing": o["timing"], "status": o["status"],
        "base_close_date": o["base_close_date"], "base_close": o["base_close"],
        "reaction_close_date": o["reaction_close_date"], "reaction_close": o["reaction_close"],
        "move_pct": o["move_pct"], "direction": o["direction"], "method_version": METHOD_VERSION,
        "attempts": (prev or {}).get("attempts", 0) + 1, "last_attempt_at": _utc_naive(now),
        "graded_at": _utc_naive(now) if o["status"] == GRADED else None,
        "note": (o.get("note") or "")[:200] or None})


def events_to_review(db, today: date) -> dict:
    """Every (symbol, earnings_date) with a recorded prediction up to `today`, with its report timing
    (the label from the most recent snapshot that has one)."""
    from sqlalchemy import text
    rows = db.execute(text(
        "SELECT symbol, earnings_date, report_timing, MAX(predicted_at) AS last_at FROM model_predictions"
        " WHERE earnings_date IS NOT NULL AND earnings_date <= :t GROUP BY symbol, earnings_date, report_timing"),
        {"t": today}).all()
    best: dict = {}
    for r in rows:
        key = (r.symbol, _d(r.earnings_date))
        label = (r.report_timing or "").strip()
        at = str(r.last_at or "")
        cur = best.get(key)
        if cur is None or (label and (not cur[0] or at > cur[1])):
            best[key] = (label, at)
    return {k: (v[0] or None) for k, v in best.items()}


def regular_session_closes(rows) -> dict:
    """date -> REGULAR-session close from raw daily rows (fail closed).

    The provider's daily feed can send more than one row for the same date -- pre-market ('pr'), regular ('r')
    and post-market ('po') -- and the app's own overview code keeps the regular one. An earnings reaction is
    measured close to close, so a pre- or post-market row must never stand in for the close: after an After
    Close report the post-market row already contains the earnings reaction and would corrupt the base price.
    Only a row explicitly labelled market_time 'r' is a regular close. A date without one (only 'pr' / 'po' rows,
    or rows with no label at all) has NO regular close and is left out: the event stays pending and is retried.
    Unlabelled rows are never guessed at -- on real data the highest-volume row can be the post-market one."""
    by_date: dict = {}
    for r in rows or []:
        raw_day = r.get("date") or r.get("start_time")
        try:
            d = date.fromisoformat(str(raw_day)[:10])
            c = float(r.get("close"))
        except (TypeError, ValueError):
            continue
        if str(r.get("market_time") or "").strip().lower() == "r":
            by_date.setdefault(d, []).append(c)
    return {d: v[-1] for d, v in by_date.items()}


def default_bars_fetcher(symbol: str) -> dict:
    """Regular-session daily closes from the same price source the earnings history already uses."""
    import unusualwhales_service as uw
    return regular_session_closes(uw._rows(uw.candles(symbol, "1d", limit=400)))


def run(now: Optional[datetime] = None, fetcher: Optional[Callable[[str], dict]] = None,
        max_symbols: int = 120, db=None) -> dict:
    """Evaluate every due event once and persist the result. Idempotent and safe to run repeatedly."""
    from zoneinfo import ZoneInfo
    now = now or datetime.now(timezone.utc)
    own = db is None
    if own:
        from database import SessionLocal
        db = SessionLocal()
    uw_prev = None
    uw_mod = None
    if fetcher is None:
        fetcher = default_bars_fetcher
        try:                                  # background priority: never compete with live trading
            import unusualwhales_service as uw_mod
            uw_prev = uw_mod.is_background()
            uw_mod.set_background(True)
        except Exception:  # noqa: BLE001
            uw_mod = None
    summary = {"events": 0, "due": 0, "graded_new": 0, "pending_reaction": 0, "pending_price": 0,
               "pending_timing": 0, "symbols_fetched": 0, "fetch_errors": 0}
    try:
        ensure_table(db)
        today_et = now.astimezone(ZoneInfo("America/New_York")).date()
        events = events_to_review(db, today_et)
        prev = load_outcomes(db)
        summary["events"] = len(events)
        by_symbol: dict[str, list] = {}
        for (sym, ed), label in sorted(events.items()):
            p = prev.get((sym, ed))
            if p and p["status"] == GRADED:
                continue
            kind = timing_kind(label)
            if kind is None:
                o = evaluate(sym, ed, None, now, None)
                if p is None or p["status"] != PENDING_TIMING:
                    _save(db, o, p, now)
                summary["pending_timing"] += 1
                continue
            base_d, react_d = reaction_window(ed, kind)
            rdy = ready_at(react_d)
            if now < rdy:
                summary["pending_reaction"] += 1       # not stored: computed on the fly, nothing to retry yet
                continue
            if not retry_due(p, rdy, now):
                summary["pending_price"] += 1
                continue
            by_symbol.setdefault(sym, []).append((ed, kind, p))
        summary["due"] = sum(len(v) for v in by_symbol.values())
        for sym in sorted(by_symbol)[:max_symbols]:
            try:
                closes = fetcher(sym)
                summary["symbols_fetched"] += 1
            except Exception as exc:  # noqa: BLE001
                closes = None
                summary["fetch_errors"] += 1
                log.warning("earnings outcome fetch failed for %s: %s", sym, type(exc).__name__)
            for ed, kind, p in by_symbol[sym]:
                o = evaluate(sym, ed, kind, now, closes)
                if closes is None:
                    o["note"] = "price history fetch failed"
                _save(db, o, p, now)
                if o["status"] == GRADED:
                    summary["graded_new"] += 1
                else:
                    summary["pending_price"] += 1
            db.commit()
        db.commit()
        return summary
    except Exception:  # noqa: BLE001
        db.rollback()
        raise
    finally:
        if uw_mod is not None:
            try:
                uw_mod.set_background(uw_prev)
            except Exception:  # noqa: BLE001
                pass
        if own:
            db.close()


def sync_legacy_columns(db, now: Optional[datetime] = None) -> int:
    """Fill the legacy outcome columns of model_predictions rows that were never evaluated, from the
    graded event outcome. Rows already stamped (graded or not) are not touched."""
    from sqlalchemy import text
    from zoneinfo import ZoneInfo
    now = now or datetime.now(timezone.utc)
    today_et = now.astimezone(ZoneInfo("America/New_York")).date()
    outcomes = {k: v for k, v in load_outcomes(db).items() if v["status"] == GRADED}
    if not outcomes:
        return 0
    pend = db.execute(text(
        "SELECT DISTINCT symbol, earnings_date FROM model_predictions WHERE evaluated_at IS NULL"
        " AND earnings_date IS NOT NULL AND earnings_date <= :t"), {"t": today_et}).all()
    n = 0
    for r in pend:
        o = outcomes.get((r.symbol, _d(r.earnings_date)))
        if not o:
            continue
        res = db.execute(text(
            "UPDATE model_predictions SET actual_move_pct = :mv, actual_direction = CAST(:dr AS VARCHAR(10)),"
            " correct = CASE WHEN signal = CAST(:dr2 AS VARCHAR(10)) THEN 1 ELSE 0 END, evaluated_at = :now"
            " WHERE symbol = :s AND earnings_date = :d AND evaluated_at IS NULL"),
            {"mv": o["move_pct"], "dr": o["direction"], "dr2": o["direction"], "now": now,
             "s": r.symbol, "d": r.earnings_date})
        n += res.rowcount or 0
    db.commit()
    return n
