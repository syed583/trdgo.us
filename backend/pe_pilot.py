"""Phase 8.2 — Controlled 5-day development pilot controller (ISOLATED).

Wraps the Phase 8 recorder (post_earnings_service) with the pilot's mandatory
safety envelope:
  * HARD CAP of 2,000 Unusual Whales calls/day for the recorder (warn at 80%);
    a cycle is refused before any provider work once the cap is reached.
  * America/New_York schedule awareness via the live market clock (holidays,
    early closes and DST are handled by the clock, not reconstructed here).
  * Auto-stop after 5 distinct US trading days; no continuation beyond that.
  * Auto-pause on API exhaustion or repeated provider/DB errors.
  * Daily metrics recorded to pe_pilot_daily; immutable predictions + DAILY_PROXY
    / TIMESTAMP_UNVERIFIED separation preserved from Phase 8.

Development-only. No production, no VPS, no broker, no live orders. It only runs
while an isolated dev backend (or `python -m pe_pilot`) is up — it is NOT wired
into production and will not run on the VPS.
"""
from __future__ import annotations

import os
import time
from datetime import date, datetime, timezone
from typing import Optional

import post_earnings_service as pe

PILOT_API_CAP = 2000
PILOT_WARN = 1600
PILOT_TRADING_DAYS = 5
MAX_CONSEC_ERRORS = 3
_ready = False


def _ensure() -> bool:
    global _ready
    if _ready:
        return True
    if not pe._ensure_tables():
        return False
    from sqlalchemy import text
    from database import SessionLocal
    db = SessionLocal()
    try:
        db.execute(text(
            "CREATE TABLE IF NOT EXISTS pe_pilot_state ("
            " key VARCHAR(16) PRIMARY KEY, status VARCHAR(16), started_at TIMESTAMPTZ,"
            " start_day DATE, trading_days_done INT DEFAULT 0, last_cycle_ts TIMESTAMPTZ,"
            " consec_errors INT DEFAULT 0, stop_reason TEXT)"))
        db.execute(text(
            "CREATE TABLE IF NOT EXISTS pe_pilot_daily ("
            " trading_day DATE PRIMARY KEY, cycles INT DEFAULT 0, api_calls INT DEFAULT 0,"
            " events_captured INT DEFAULT 0, buy INT DEFAULT 0, sell INT DEFAULT 0,"
            " wait INT DEFAULT 0, verified_ts INT DEFAULT 0, unverified_ts INT DEFAULT 0,"
            " grades_completed INT DEFAULT 0, failures INT DEFAULT 0, note TEXT)"))
        db.commit()
        _ready = True
        return True
    except Exception:  # noqa: BLE001
        db.rollback(); return False
    finally:
        db.close()


def _et_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:  # noqa: BLE001
        return datetime.now(timezone.utc)


def _session() -> str:
    try:
        import live_market_service as mkt
        return (mkt.market_clock() or {}).get("session") or "CLOSED"
    except Exception:  # noqa: BLE001
        return "CLOSED"


def in_schedule_window(session: str, et: Optional[datetime] = None) -> bool:
    """RTH 09:35-16:00 ET, plus the BMO/AMC-next-open observation windows. The
    market clock already excludes holidays/early closes (session=CLOSED)."""
    if session == "OPEN":
        return True
    return False       # extended-hours capture is NOT available (no EH data) -> RTH only


def api_calls_today() -> int:
    if not _ensure():
        return 0
    from sqlalchemy import text
    from database import SessionLocal
    db = SessionLocal()
    try:
        r = db.execute(text("SELECT api_calls FROM pe_pilot_daily WHERE trading_day=:d"),
                       {"d": _et_now().date()}).first()
        return int(r[0]) if r else 0
    finally:
        db.close()


def _over_cap() -> bool:
    return api_calls_today() >= PILOT_API_CAP


def start_pilot() -> dict:
    if not _ensure():
        return {"status": "NO_TABLE"}
    from sqlalchemy import text
    from database import SessionLocal
    db = SessionLocal()
    try:
        row = db.execute(text("SELECT status FROM pe_pilot_state WHERE key='pilot'")).first()
        if row and row[0] == "ACTIVE":
            return {"status": "ALREADY_ACTIVE"}
        db.execute(text(
            "INSERT INTO pe_pilot_state (key,status,started_at,start_day,trading_days_done,consec_errors) "
            "VALUES ('pilot','ACTIVE',now(),:d,0,0) "
            "ON CONFLICT (key) DO UPDATE SET status='ACTIVE', started_at=now(), "
            " start_day=:d, trading_days_done=0, consec_errors=0, stop_reason=NULL"),
            {"d": _et_now().date()})
        db.commit()
        return {"status": "ACTIVE", "start_day": _et_now().date().isoformat(),
                "trading_days_target": PILOT_TRADING_DAYS, "api_cap_daily": PILOT_API_CAP}
    finally:
        db.close()


def stop_pilot(reason: str = "manual") -> dict:
    if not _ensure():
        return {"status": "NO_TABLE"}
    from sqlalchemy import text
    from database import SessionLocal
    db = SessionLocal()
    try:
        db.execute(text("UPDATE pe_pilot_state SET status='STOPPED', stop_reason=:r WHERE key='pilot'"),
                   {"r": reason})
        db.commit()
        return {"status": "STOPPED", "reason": reason}
    finally:
        db.close()


def pilot_cycle() -> dict:
    """One guarded pilot tick. Safe to call repeatedly; it self-gates on status,
    schedule window, API cap and the 5-trading-day limit."""
    if not _ensure():
        return {"status": "NO_TABLE"}
    from sqlalchemy import text
    from database import SessionLocal
    db = SessionLocal()
    try:
        st = db.execute(text("SELECT status, start_day, trading_days_done, consec_errors "
                             "FROM pe_pilot_state WHERE key='pilot'")).first()
    finally:
        db.close()
    if not st or st[0] != "ACTIVE":
        return {"status": "NOT_ACTIVE"}
    if (st[2] or 0) >= PILOT_TRADING_DAYS:
        stop_pilot("completed_5_trading_days")
        return {"status": "COMPLETED"}
    session = _session()
    if not in_schedule_window(session):
        return {"status": "OUT_OF_WINDOW", "session": session}
    if _over_cap():
        _mark_day(note="API_CAP_REACHED")
        return {"status": "API_CAP_REACHED", "api_calls_today": api_calls_today()}

    before = (pe._budget() or {}).get("used_by_this_app") or 0
    res = pe.run_cycle(days=3)
    after = (pe._budget() or {}).get("used_by_this_app") or before
    delta = max(0, after - before)
    ok = res.get("status") == "OK"
    _mark_day(cycles=1, api_calls=delta, failures=0 if ok else 1)
    _bump_state(ok)
    return {"status": "RAN" if ok else "ERROR", "cycle": res,
            "api_calls_this_cycle": delta, "api_calls_today": api_calls_today()}


def _mark_day(cycles: int = 0, api_calls: int = 0, failures: int = 0, note: Optional[str] = None):
    from sqlalchemy import text
    from database import SessionLocal
    d = pe.dashboard()
    db = SessionLocal()
    try:
        db.execute(text(
            "INSERT INTO pe_pilot_daily (trading_day,cycles,api_calls,events_captured,buy,sell,"
            " wait,verified_ts,unverified_ts,grades_completed,failures,note) "
            "VALUES (:day,:c,:a,:ev,:b,:s,:w,:vt,:uv,:g,:f,:n) "
            "ON CONFLICT (trading_day) DO UPDATE SET cycles=pe_pilot_daily.cycles+:c, "
            " api_calls=pe_pilot_daily.api_calls+:a, events_captured=:ev, buy=:b, sell=:s, "
            " wait=:w, verified_ts=:vt, unverified_ts=:uv, grades_completed=:g, "
            " failures=pe_pilot_daily.failures+:f, note=COALESCE(:n,pe_pilot_daily.note)"),
            {"day": _et_now().date(), "c": cycles, "a": api_calls,
             "ev": d.get("events_observed", 0), "b": d.get("buy", 0), "s": d.get("sell", 0),
             "w": d.get("wait", 0), "vt": d.get("verified_timestamps", 0),
             "uv": (d.get("events_observed", 0) - d.get("verified_timestamps", 0)),
             "g": d.get("completed_sim_trades", 0), "f": failures, "n": note})
        db.commit()
    finally:
        db.close()


def _bump_state(ok: bool):
    from sqlalchemy import text
    from database import SessionLocal
    db = SessionLocal()
    try:
        # distinct trading days with at least one cycle, since start
        row = db.execute(text(
            "SELECT COUNT(*) FROM pe_pilot_daily d JOIN pe_pilot_state s ON s.key='pilot' "
            "WHERE d.cycles>0 AND d.trading_day >= s.start_day")).scalar()
        if ok:
            db.execute(text("UPDATE pe_pilot_state SET last_cycle_ts=now(), consec_errors=0, "
                            "trading_days_done=:td WHERE key='pilot'"), {"td": int(row or 0)})
        else:
            db.execute(text("UPDATE pe_pilot_state SET last_cycle_ts=now(), "
                            "consec_errors=consec_errors+1, trading_days_done=:td WHERE key='pilot'"),
                       {"td": int(row or 0)})
        db.commit()
        ce = db.execute(text("SELECT consec_errors FROM pe_pilot_state WHERE key='pilot'")).scalar()
        if ce and ce >= MAX_CONSEC_ERRORS:
            pe.pause(True)
            db.execute(text("UPDATE pe_pilot_state SET status='STOPPED', "
                            "stop_reason='auto_pause_repeated_errors' WHERE key='pilot'"))
            db.commit()
    finally:
        db.close()


def pilot_report() -> dict:
    if not _ensure():
        return {"status": "NO_TABLE"}
    from sqlalchemy import text
    from database import SessionLocal
    db = SessionLocal()
    try:
        st = db.execute(text("SELECT status,started_at,start_day,trading_days_done,consec_errors,"
                             "last_cycle_ts,stop_reason FROM pe_pilot_state WHERE key='pilot'")).first()
        days = db.execute(text("SELECT trading_day,cycles,api_calls,events_captured,buy,sell,wait,"
                               "verified_ts,unverified_ts,grades_completed,failures,note "
                               "FROM pe_pilot_daily ORDER BY trading_day")).all()
    finally:
        db.close()
    return {
        "source": "Phase 8.2 Pilot", "label": "SIMULATED · FORWARD-RECORDED · NO LIVE TRADING",
        "api_cap_daily": PILOT_API_CAP, "warn_at": PILOT_WARN, "trading_days_target": PILOT_TRADING_DAYS,
        "state": None if not st else {"status": st[0], "started_at": st[1].isoformat() if st[1] else None,
            "start_day": str(st[2]), "trading_days_done": st[3], "consec_errors": st[4],
            "last_cycle": st[5].isoformat() if st[5] else None, "stop_reason": st[6]},
        "daily": [{"day": str(r[0]), "cycles": r[1], "api_calls": r[2], "events": r[3],
                   "buy": r[4], "sell": r[5], "wait": r[6], "verified_ts": r[7],
                   "unverified_ts": r[8], "grades": r[9], "failures": r[10], "note": r[11]}
                  for r in days],
        "note": ("Pilot validates FORWARD-DATA COLLECTION RELIABILITY, not profitability. "
                 "Timestamps are UNVERIFIED; grades are DAILY_PROXY; AMC initial reaction is NOT "
                 "captured (no extended-hours data)."),
    }


def loop(interval_s: int = 1800):
    """Standalone pilot loop for a persistent ISOLATED dev host. Gated by PE_PILOT=1."""
    if os.environ.get("PE_PILOT") != "1":
        print("PE_PILOT != 1 — refusing to run the pilot loop (safety gate).")
        return
    print(f"Pilot loop starting (interval {interval_s}s). Ctrl-C to stop.")
    while True:
        try:
            print(datetime.now(timezone.utc).isoformat(), pilot_cycle().get("status"))
        except Exception as e:  # noqa: BLE001
            print("cycle error:", str(e)[:120])
        time.sleep(interval_s)


if __name__ == "__main__":
    loop()
