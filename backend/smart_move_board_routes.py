"""Read-only API for the Smart Move earnings board: GET /api/smart-move/board. Saved data only; no provider, no refresh, no write.

Environment (all optional):
  SMART_MOVE_RUN_ROOT   folder holding one-time run folders (run_*/audit_report.json); default C:\\Users\\initi\\SmartMoveRun\\out. Only read, never written.
  SMART_MOVE_BOARD_NOW  ISO time that replaces the clock, for demonstrations only; the response says so.
"""
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Query

import smart_move_board as B
import smart_move_routes as R

router = APIRouter(prefix="/api/smart-move", tags=["smart-move-board"])
DEFAULT_RUN_ROOT = r"C:\Users\initi\SmartMoveRun\out"


def _now():
    forced = os.getenv("SMART_MOVE_BOARD_NOW")
    if forced:
        t = datetime.fromisoformat(forced)
        return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)), True
    return datetime.now(timezone.utc), False


def _live_rows(now):
    """Rows the live store can score right now. Never persists, never refreshes; any failure just means no live rows."""
    try:
        import smart_move_live as L
        svc = R.live_service()
        return L.LiveScorer(svc).score_all(now)
    except Exception:  # noqa: BLE001
        return []


SCHEDULED_TEST = {"task": "SmartMove_OneTime_20261031_1705ET", "planned_start_utc": "2026-10-31T21:05:00+00:00", "latest_start_utc": "2026-11-01T19:00:00+00:00",
                  "max_provider_attempts": 60, "max_stocks": 10, "run_folder": "run_20261031_1705ET", "recurring": False}


def _scheduled_test(root, now):
    """Facts from the run folder only (never the Task Scheduler, never a trigger). The task's own state is verified outside this page."""
    folder = os.path.join(root, SCHEDULED_TEST["run_folder"])
    rep = os.path.join(folder, "audit_report.json")
    start = datetime.fromisoformat(SCHEDULED_TEST["planned_start_utc"])
    state = "REPORT_SAVED" if os.path.isfile(rep) else "RUNNING_OR_INCOMPLETE" if os.path.isdir(folder) else "NOT_STARTED_YET" if now < start else "NOT_FOUND_AFTER_START_TIME"
    return dict(SCHEDULED_TEST, state=state, report_saved=os.path.isfile(rep))


@router.get("/board")
def board(variant: str = Query("continuation", pattern="^(continuation|contrarian)$"), status: Optional[str] = Query(None, pattern="^(BUY|SELL|NO_TRADE|DATA_INCOMPLETE|PENDING)$"),
          kind: Optional[str] = Query(None, pattern="^(BMO|AMC)$"), q: Optional[str] = Query(None, max_length=40)):
    now, forced = _now()
    root = os.getenv("SMART_MOVE_RUN_ROOT") or DEFAULT_RUN_ROOT
    saved = R.current_rows()
    reg = B.derive_registry(saved, B.load_run_reports(root))
    try:
        plan_fn = lambda d, k: R._engine()[3].plan_event(datetime.fromisoformat(d).date(), k)  # noqa: E731
    except Exception:  # noqa: BLE001
        plan_fn = None
    out = B.build_board(saved, _live_rows(now), B.load_run_results(root), now=now, variant=variant, registry=reg, plan_fn=plan_fn)
    rows = out["rows"]
    if status:
        rows = [r for r in rows if r["status"] == status]
    if kind:
        rows = [r for r in rows if r["release"]["kind"] == kind]
    if q:
        t = q.strip().lower()
        rows = [r for r in rows if t in r["symbol"].lower() or t in (r["company"] or "").lower()]
    out.update(scheduled_test=_scheduled_test(root, now), rows=rows, shown=len(rows), clock_override=forced, read_only=True, sends_requests=False,
               notice="Shadow research only; no trade is placed. A status other than BUY or SELL is never a prediction. Missing data is shown as DATA INCOMPLETE, never as neutral.")
    return out


@router.get("/health")
def health():
    """Read-only integrity report for the newest run folder: kill switch, duplicates, evidence hashes, registry reconciliation. Sends nothing."""
    import smart_move_health as H
    now, _ = _now()
    root = os.getenv("SMART_MOVE_RUN_ROOT") or DEFAULT_RUN_ROOT
    reports = B.load_run_reports(root)
    if not reports:
        return {"ok": False, "checks": [{"check": "run_folder_found", "ok": False, "detail": root}], "read_only": True}
    reg = B.derive_registry(R.current_rows(), reports)
    out = H.health(os.path.join(root, reports[-1][0]), reg, now=now)
    return dict(out, read_only=True, sends_requests=False)


@router.get("/ops")
def ops():
    """Operations view, read only: what the next scheduler tick WOULD do (plan only), alerts, provider errors, kill switch, data health. Sends nothing, registers nothing."""
    import smart_move_health as H
    import smart_move_scheduler as SCH
    now, forced = _now()
    root = os.getenv("SMART_MOVE_RUN_ROOT") or DEFAULT_RUN_ROOT
    reports = B.load_run_reports(root)
    saved = R.current_rows()
    reg = B.derive_registry(saved, reports)
    cur = reg.current()
    events = [{"symbol": s, "earnings_date": v["earnings_date"], "kind": v.get("kind")} for s, v in cur.items()]
    captured = set(B.load_run_results(root).keys())
    obs = max((v["observed_ts"] for v in cur.values()), default=None)
    age = round((now - datetime.fromisoformat(obs)).total_seconds() / 3600, 1) if obs else None
    plan = SCH.tick(now, events, captured, set(), SCH.Budget(), calendar_age_h=age, outcome_fetch_enabled=True, collection_enabled=False)
    newest = reports[-1] if reports else None
    rep = newest[1] if newest else {}
    kill = os.path.exists(os.path.join(root, newest[0], "store", "KILL")) if newest else None
    health = H.health(os.path.join(root, newest[0]), reg, now=now) if newest else {"ok": False, "checks": []}
    return {"as_of": now.isoformat(), "clock_override": forced, "read_only": True, "sends_requests": False,
            "scheduler": {"recurring_enabled": False, "registered_by_this_code": False, "mode": "PLAN ONLY (live collection not authorized)", "next_tick_plan": plan,
                          "estimated_daily_requests_for_universe": SCH.estimate_daily_requests(len(events)), "calendar_age_hours": age},
            "alerts": plan["alerts"], "kill_switch": {"engaged": kill, "run": newest[0] if newest else None},
            "provider": {"attempts_total": rep.get("attempts_total"), "by_provider": rep.get("requests_by_provider"), "errors": len(rep.get("errors") or []), "stop_events": len(rep.get("stop_events") or []), "run_status": rep.get("status")},
            "health": health, "event_registry": {"current": len(cur), "removed_or_rescheduled_versions": sum(1 for v in reg.versions if v["status"] != "CURRENT")}}
