"""Tab B -- Short Straddle Scanner.

Ranks upcoming-earnings names by how inflated their option premium / IV is
before the report (the bigger the expected IV crush, the better the sell). Four
banded filters (100 pts): IV Rank 40, Implied Move % 25, Implied-vs-historical
20, Liquidity 15. Action SELL >= 70 / WATCH 50-69 / SKIP, plus a bullish/
bearish/neutral tilt and hard exclusions. It does NOT predict direction.

Reuses the warmed earn:trade:default entry for the implied move %, the average
historical earnings move and the tilt; reads IV rank (uw_volatility_service)
and the ATM straddle spread (earnings_options_service) fresh, cached per symbol
as tabb:{SYM}.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

import tab_settings as cfg

SOURCE = "Tab B — Straddle Scanner"

_log_ready = False


def _f(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def _ensure_log_table() -> bool:
    """Daily option-chain snapshot per watchlist name, so the `lb`-sessions-ago
    ATM premium and IV for the same strike are available for the inflation
    ranking (the spec's 'store daily snapshots' requirement)."""
    global _log_ready
    if _log_ready:
        return True
    try:
        from sqlalchemy import text
        from database import SessionLocal
        db = SessionLocal()
        try:
            db.execute(text(
                "CREATE TABLE IF NOT EXISTS straddle_snap_v2 ("
                "  symbol        VARCHAR(20) NOT NULL,"
                "  snapshot_date DATE        NOT NULL,"
                "  report_date   DATE,"
                "  event_expiry  DATE,"
                "  atm_strike    DOUBLE PRECISION,"
                "  straddle      DOUBLE PRECISION,"
                "  front_iv      DOUBLE PRECISION,"
                "  back_iv       DOUBLE PRECISION,"
                "  rank          INTEGER,"
                "  status        VARCHAR(20),"
                "  PRIMARY KEY (symbol, snapshot_date)"
                ")"))
            db.commit()
            _log_ready = True
            return True
        except Exception:  # noqa: BLE001
            db.rollback()
            return False
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        return False


def _snapshot_prev(symbol: str, lb: int) -> Optional[dict]:
    """Stored snapshot closest to ~`lb` trading sessions ago (~lb*7/5 calendar
    days). Returns {straddle, front_iv, event_expiry, atm_strike} or None."""
    if not _ensure_log_table():
        return None
    try:
        from sqlalchemy import text
        from database import SessionLocal
        target = date.today() - timedelta(days=round(lb * 7 / 5))
        db = SessionLocal()
        try:
            row = db.execute(text(
                "SELECT straddle, front_iv, event_expiry, atm_strike FROM straddle_snap_v2 "
                "WHERE symbol = :s AND snapshot_date <= :d AND straddle IS NOT NULL "
                "ORDER BY snapshot_date DESC LIMIT 1"),
                {"s": symbol.upper(), "d": target}).fetchone()
            if row:
                return {"straddle": _f(row[0]), "front_iv": _f(row[1]),
                        "event_expiry": row[2], "atm_strike": _f(row[3])}
            return None
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        return None


def _last_report_date(symbol: str) -> Optional[str]:
    """The report date stored on the most recent snapshot, to detect a changed
    earnings date (spec: flip to NOT DUE / tag DATE CHANGED when it moves)."""
    if not _ensure_log_table():
        return None
    try:
        from sqlalchemy import text
        from database import SessionLocal
        db = SessionLocal()
        try:
            row = db.execute(text(
                "SELECT report_date FROM straddle_snap_v2 WHERE symbol = :s "
                "AND report_date IS NOT NULL ORDER BY snapshot_date DESC LIMIT 1"),
                {"s": symbol.upper()}).fetchone()
            return str(row[0]) if (row and row[0]) else None
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        return None


def log_scores(rows: list[dict]) -> int:
    """Record today's report date + result expiry per name, so the date re-check
    can tell when an earnings date has moved."""
    scored = [r for r in (rows or []) if r.get("result_date")]
    if not scored or not _ensure_log_table():
        return 0
    try:
        from sqlalchemy import text
        from database import SessionLocal
        db = SessionLocal()
        try:
            today = date.today()
            for r in scored:
                db.execute(text(
                    "INSERT INTO straddle_snap_v2 "
                    "(symbol, snapshot_date, report_date, event_expiry) "
                    "VALUES (:s, :d, :rd, :ee) "
                    "ON CONFLICT (symbol, snapshot_date) DO UPDATE SET "
                    "report_date = :rd, event_expiry = :ee"),
                    {"s": r["symbol"].upper(), "d": today,
                     "rd": r.get("result_date"), "ee": r.get("expiry")})
            db.commit()
            return len(scored)
        except Exception:  # noqa: BLE001
            db.rollback()
            return 0
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        return 0


def _next_session(report_date, reporting_time) -> bool:
    """True if the result is due the NEXT US session: today after close (AMC) or
    the next trading day before open (BMO). This is the only window a short
    straddle qualifies in -- never hold one over an unknown later session."""
    if not report_date:
        return False
    try:
        rd = datetime.fromisoformat(str(report_date)[:10]).date()
    except Exception:  # noqa: BLE001
        return False
    today = date.today()
    nd = today + timedelta(days=1)
    while nd.weekday() >= 5:            # skip Sat/Sun to the next trading day
        nd += timedelta(days=1)
    rt = (reporting_time or "").upper()
    if rd == today and rt == "AMC":
        return True
    if rd == nd and rt in ("BMO", ""):  # next-day before open (unknown time -> allow)
        return True
    return False


def _within_universe(report_date, max_td: int) -> bool:
    """True if the result is due today or within `max_td` trading sessions from
    today -- the IV Crush universe: the next session plus the next ~10 trading
    days. Past dates drop out."""
    if not report_date:
        return False
    try:
        rd = datetime.fromisoformat(str(report_date)[:10]).date()
    except Exception:  # noqa: BLE001
        return False
    today = date.today()
    if rd < today:
        return False
    td = _trading_days(today, rd)
    return td is not None and td <= max_td


def _trading_days(d0, d1) -> Optional[int]:
    """Trading sessions (weekdays) from d0 (exclusive) to d1 (inclusive)."""
    try:
        a = d0 if isinstance(d0, date) else datetime.fromisoformat(str(d0)[:10]).date()
        b = datetime.fromisoformat(str(d1)[:10]).date()
    except Exception:  # noqa: BLE001
        return None
    if b <= a:
        return 0
    n, cur = 0, a
    while cur < b:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            n += 1
    return n


def _reaction_date(report_date, code) -> Optional[str]:
    """The earnings reaction session: BMO -> report day; AMC -> next trading day."""
    if not report_date:
        return None
    try:
        rd = datetime.fromisoformat(str(report_date)[:10]).date()
    except Exception:  # noqa: BLE001
        return None
    if (code or "").upper() == "AMC":
        nd = rd + timedelta(days=1)
        while nd.weekday() >= 5:
            nd += timedelta(days=1)
        return nd.isoformat()
    return rd.isoformat()


def _report_meta(symbol: str) -> tuple:
    """(report_date, code BMO/AMC) from the earnings calendar preview."""
    try:
        import uw_earnings_calendar as uwcal
        pv = uwcal.preview(symbol) or {}
        rt = (pv.get("next_report_time") or "").lower()
        code = ("BMO" if ("before" in rt or "bmo" in rt or "pre" in rt)
                else "AMC" if ("after" in rt or "amc" in rt or "post" in rt) else "")
        return pv.get("next_report"), code
    except Exception:  # noqa: BLE001
        return None, ""


def compute(symbol: str) -> dict:
    """IV Crush Check row: the ATM call and put TODAY and 10 trading sessions ago
    (rate + IV each) for the result expiry. Nothing else -- no ranking, score,
    status or ratios (cached as tabb:{SYM})."""
    import swr
    import earnings_options_service as opt
    import unusualwhales_service as uw

    symbol = symbol.upper()
    d = swr.peek(f"earn:trade:default:{symbol}") or {}
    em = d.get("expected_move") or {}
    spot = _f(em.get("current")) or _f(d.get("price"))
    if spot is None:
        try:
            import live_market_service as market
            spot = _f((market.get_quote(symbol) or {}).get("price"))
        except Exception:  # noqa: BLE001
            spot = None
    lb = cfg.SS_LOOKBACK_SESSIONS

    report_date, report_code = _report_meta(symbol)
    reaction = _reaction_date(report_date, report_code)

    # The trading day `lb` NYSE sessions ago, and the spot that day. Daily candles
    # ARE actual trading sessions (the provider skips weekends/holidays), but it
    # can return several rows per day, so collapse to one close per date first.
    date_10d = spot_10d = None
    try:
        per_day: dict = {}
        for b in uw._rows(uw.candles(symbol, "1d", limit=120)):
            dd = str(b.get("date"))[:10]
            c = _f(b.get("close"))
            if dd and c is not None:
                per_day[dd] = c            # last row for the date wins
        sessions = sorted(per_day)          # oldest -> newest, one per NYSE session
        if len(sessions) >= lb + 1:
            date_10d = sessions[-(lb + 1)]
            spot_10d = per_day[date_10d]
    except Exception:  # noqa: BLE001
        pass

    q = opt.iv_crush_quote(symbol, spot, reaction, date_10d, spot_10d) or {}
    return {
        "symbol": symbol, "spot": spot,
        "result_date": report_date, "result_code": report_code,
        "expiry": q.get("expiry"), "date_10d": date_10d, "strike": q.get("strike"),
        "call_then": q.get("call_then"), "call_now": q.get("call_now"),
        "put_then": q.get("put_then"), "put_now": q.get("put_now"),
    }


def _cached(symbol: str) -> Optional[dict]:
    import swr
    return swr.serve(f"tabb:{symbol.upper()}", lambda: compute(symbol), 21600.0)


def universe_rows(days: int = 15) -> list[dict]:
    """The IV Crush / Unusual Activity universe: calendar rows for every name
    reporting within the next SS_UNIVERSE_TDAYS trading sessions (next session +
    next ~10 trading days), de-duplicated and sorted soonest-first."""
    import uw_earnings_calendar as cal
    max_td = int(getattr(cfg, "SS_UNIVERSE_TDAYS", 10))
    rows = (cal.calendar(days=max(days, max_td + 4)) or {}).get("rows") or []
    seen: set[str] = set()
    uniq: list[dict] = []
    for r in rows:
        s = (r.get("symbol") or "").upper()
        if s and s not in seen and _within_universe(r.get("date"), max_td):
            seen.add(s)
            uniq.append(r)
    uniq.sort(key=lambda r: (str(r.get("date") or ""),
                             0 if (r.get("reporting_time") == "BMO") else 1,
                             (r.get("symbol") or "").upper()))
    return uniq


def universe_symbols(days: int = 15) -> list[str]:
    """Just the tickers of universe_rows(), soonest-first."""
    return [r["symbol"].upper() for r in universe_rows(days)]


def scan(days: int = 15, warm: int = 0) -> dict:
    """The IV Crush Check table: EVERY upcoming-earnings name reporting within the
    next SS_UNIVERSE_TDAYS trading sessions (the next session plus the next ~10
    trading days) -- from the earnings calendar, not the straddle watchlist --
    each with the ATM call/put today vs 10 sessions ago. Sorted by result time,
    then ticker. No ranking, scores or status."""
    import swr

    # Soonest reporters first so the rows -- and the warm queue -- fill the
    # next-session names before the far ones.
    uniq = universe_rows(days)
    seen = {r["symbol"].upper() for r in uniq}
    cached = swr.peek_many([f"tabb:{s}" for s in seen])

    out: list[dict] = []
    missing: list[str] = []
    computed: list[dict] = []
    for r in uniq:
        s = r["symbol"].upper()
        meta = {"company": r.get("company"), "date": r.get("date"),
                "date_label": r.get("date_label"), "result_time": r.get("time_label"),
                "result_code": r.get("reporting_time")}
        t = cached.get(f"tabb:{s}")
        if t:
            row = {**t, **meta}
            out.append(row)
            computed.append(row)
        else:
            missing.append(s)
            out.append({"symbol": s, "status": "PENDING", "spot": None,
                        "expiry": None, "strike": None, "call_then": None,
                        "call_now": None, "put_then": None, "put_now": None, **meta})

    for s in missing[:max(0, warm)]:
        try:
            _cached(s)
        except Exception:  # noqa: BLE001
            pass

    # Only log the names that actually computed -- a 500-name universe must not
    # upsert a snapshot row per PENDING name every minute (prod DB is conn-capped).
    try:
        log_scores(computed)
    except Exception:  # noqa: BLE001
        pass
    return {"status": "OK", "source": SOURCE, "rows": out,
            "universe": len(seen), "missing": missing[:120]}
