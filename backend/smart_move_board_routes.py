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
