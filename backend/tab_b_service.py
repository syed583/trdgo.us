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
    """Upsert today's event-expiry snapshot per name (feeds the 10-session multiples)."""
    scored = [r for r in (rows or []) if r.get("straddle") is not None]
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
                    "(symbol, snapshot_date, report_date, event_expiry, atm_strike, "
                    " straddle, front_iv, back_iv, rank, status) "
                    "VALUES (:s, :d, :rd, :ee, :k, :pm, :fiv, :biv, :rk, :st) "
                    "ON CONFLICT (symbol, snapshot_date) DO UPDATE SET "
                    "report_date = :rd, event_expiry = :ee, atm_strike = :k, "
                    "straddle = :pm, front_iv = :fiv, back_iv = :biv, rank = :rk, status = :st"),
                    {"s": r["symbol"].upper(), "d": today, "rd": r.get("report_date"),
                     "ee": r.get("event_expiry"), "k": r.get("atm_strike"),
                     "pm": r.get("straddle"), "fiv": r.get("front_iv"),
                     "biv": r.get("back_iv"), "rk": r.get("rank"), "st": r.get("status")})
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
    """Per-name short-straddle metrics for the change spec (cached as tabb:{SYM}):
    event & back expiry prices + IV breakdown, implied-vs-historical move, the
    10-session multiples (from snapshots) and the status. Rank is set in scan()."""
    import swr
    import earnings_options_service as opt

    symbol = symbol.upper()
    d = swr.peek(f"earn:trade:default:{symbol}") or {}
    em = d.get("expected_move") or {}
    spot = _f(em.get("current")) or _f(d.get("price"))
    lb = cfg.SS_LOOKBACK_SESSIONS

    report_date, report_code = _report_meta(symbol)
    reaction = _reaction_date(report_date, report_code)
    next_session = _next_session(report_date, report_code)
    prev_report = _last_report_date(symbol)
    date_changed = bool(prev_report and report_date and str(prev_report)[:10] != str(report_date)[:10])
    date_status = "CHANGED" if date_changed else ("CONFIRMED" if report_date else "UNCONFIRMED")

    q = opt.short_straddle_quote(symbol, spot, reaction, cfg.SS_ATM_MODE,
                                 cfg.SS_BACK_GAP_DAYS, cfg.SS_BACK_MAX_DAYS) or {}
    call = q.get("call") or {}
    put = q.get("put") or {}
    front_iv = q.get("front_iv")
    back_iv = q.get("back_iv")
    straddle = q.get("straddle")
    atm_strike = q.get("atm_strike")
    event_expiry = q.get("event_expiry")
    event_dte = q.get("event_dte")
    back_expiry = q.get("back_expiry")
    wide_spread = bool(q.get("wide_spread"))
    back_far = bool(q.get("back_far"))

    implied_move = round(straddle / spot * 100, 2) if (straddle and spot) else None
    front_back = round(front_iv / back_iv, 2) if (front_iv and back_iv) else None

    # Event-only move: strip the normal (back) vol out of the front vol.
    event_move = None
    if front_iv and back_iv and event_dte and event_dte > 0:
        t = event_dte / 365.0
        ev_var = ((front_iv / 100.0) ** 2 - (back_iv / 100.0) ** 2) * t
        event_move = round((max(ev_var, 0.0) ** 0.5) * 100, 2)

    # Historical move: avg abs reaction-session move over the last N reports.
    hist_move = None
    hist_n = 0
    try:
        import earnings_trade_service as ets
        moves = [abs(_f(m.get("move_pct"))) for m in (ets._historical_moves(symbol) or [])
                 if _f(m.get("move_pct")) is not None]
        moves = moves[-cfg.SS_HIST_REPORTS:]
        hist_n = len(moves)
        if hist_n:
            hist_move = round(sum(moves) / hist_n, 2)
    except Exception:  # noqa: BLE001
        pass
    low_history = hist_n < cfg.SS_HIST_MIN
    move_ratio = round(implied_move / hist_move, 2) if (implied_move and hist_move) else None

    # 10-session multiples from stored snapshots (method 10S); else term structure.
    iv_10s = straddle_10s = iv_mult = prem_mult = None
    snap = _snapshot_prev(symbol, lb)
    if snap:
        straddle_10s = _f(snap.get("straddle"))
        iv_10s = _f(snap.get("front_iv"))
        if straddle and straddle_10s:
            prem_mult = round(straddle / straddle_10s, 2)
        if front_iv and iv_10s:
            iv_mult = round(front_iv / iv_10s, 2)

    if prem_mult is not None and iv_mult is not None:
        method = "10S"
        inflation = round((prem_mult + iv_mult) / 2, 2)
    elif front_back is not None:
        method = "TS"
        inflation = front_back
    else:
        method = None
        inflation = None

    tags = []
    if wide_spread:
        tags.append("WIDE SPREAD")
    if low_history:
        tags.append("LOW HISTORY")
    if back_far:
        tags.append("BACK FAR")
    if date_changed:
        tags.append("DATE CHANGED")

    # Status (section 5 precedence).
    if (not next_session) or date_status in ("UNCONFIRMED", "CHANGED"):
        status = "NOT DUE"
    elif move_ratio is not None and move_ratio < cfg.SS_MOVE_RATIO_FLOOR:
        status = "NO EDGE"
    elif inflation is None or inflation < cfg.SS_MIN_INFLATION:
        status = "NO REAL INFLATION"
    elif move_ratio is not None and move_ratio >= cfg.SS_MOVE_RATIO_SELL and not wide_spread:
        status = "SELL STRADDLE"
    else:
        status = "WATCH"

    return {
        "symbol": symbol, "spot": spot,
        "report_date": report_date, "report_code": report_code, "date_status": date_status,
        "event_expiry": event_expiry, "event_dte": event_dte, "back_expiry": back_expiry,
        "atm_strike": atm_strike,
        "call": call, "put": put, "straddle": straddle,
        "iv_call": call.get("iv"), "iv_put": put.get("iv"),
        "iv_front": front_iv, "iv_back": back_iv, "front_back": front_back,
        "event_move": event_move,
        "iv_10s_ago": iv_10s, "iv_multiple": iv_mult,
        "straddle_10s_ago": straddle_10s, "prem_multiple": prem_mult,
        "implied_move": implied_move, "hist_move": hist_move, "hist_n": hist_n,
        "move_ratio": move_ratio,
        "inflation": inflation, "method": method,
        "wide_spread": wide_spread, "tags": tags, "status": status,
    }


def _cached(symbol: str) -> Optional[dict]:
    import swr
    return swr.serve(f"tabb:{symbol.upper()}", lambda: compute(symbol), 21600.0)


def scan(days: int = 5, warm: int = 0) -> dict:
    """Rank the watchlist names reporting the NEXT session by inflation. Primary
    (method 10S, when snapshots exist) = avg(premium multiple, IV multiple);
    fallback (TS) = Front/Back IV. All 10S rows rank above TS rows; ties broken by
    the higher Move ratio. `days` only bounds the calendar lookahead."""
    import swr
    import uw_earnings_calendar as cal

    rows = (cal.calendar(days=max(days, 3)) or {}).get("rows") or []
    wl = set(cfg.TAB_B_WATCHLIST)
    seen: set[str] = set()
    uniq: list[dict] = []
    for r in rows:
        s = (r.get("symbol") or "").upper()
        if (s and s in wl and s not in seen
                and _next_session(r.get("date"), r.get("reporting_time"))):
            seen.add(s)
            uniq.append(r)
    cached = swr.peek_many([f"tabb:{s}" for s in seen])

    out: list[dict] = []
    missing: list[str] = []
    for r in uniq:
        s = r["symbol"].upper()
        meta = {"company": r.get("company"), "date": r.get("date"),
                "date_label": r.get("date_label"), "result_time": r.get("time_label"),
                "result_code": r.get("reporting_time")}
        t = cached.get(f"tabb:{s}")
        if t:
            out.append({**t, **meta})
        else:
            missing.append(s)
            out.append({"symbol": s, "status": "PENDING", "spot": None,
                        "method": None, "inflation": None, "rank": None, **meta})

    for s in missing[:max(0, warm)]:
        try:
            _cached(s)
        except Exception:  # noqa: BLE001
            pass

    # Rank: 10S rows (highest inflation) above TS rows, ties by higher Move ratio.
    rankable = [r for r in out if r.get("status") != "PENDING" and r.get("method")
                and r.get("inflation") is not None]

    def _key(r):
        method_order = 0 if r["method"] == "10S" else 1
        return (method_order, -r["inflation"], -(r.get("move_ratio") or 0))

    rankable.sort(key=_key)
    for i, r in enumerate(rankable, 1):
        r["rank"] = i

    def _order(r):
        if r.get("rank"):
            return (0, r["rank"])
        return (2, 0) if r.get("status") == "PENDING" else (1, r.get("symbol") or "")

    out.sort(key=_order)
    try:
        log_scores(out)
    except Exception:  # noqa: BLE001
        pass
    sellable = sum(1 for r in out if r.get("status") == "SELL STRADDLE")
    return {"status": "OK", "source": SOURCE, "rows": out,
            "universe": len(seen), "sellable": sellable, "missing": missing[:60]}
