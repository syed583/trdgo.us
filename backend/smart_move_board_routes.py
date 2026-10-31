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


@router.get("/board")
def board(variant: str = Query("continuation", pattern="^(continuation|contrarian)$"), status: Optional[str] = Query(None, pattern="^(BUY|SELL|NO_TRADE|DATA_INCOMPLETE|PENDING)$"),
          kind: Optional[str] = Query(None, pattern="^(BMO|AMC)$"), q: Optional[str] = Query(None, max_length=40)):
    now, forced = _now()
    root = os.getenv("SMART_MOVE_RUN_ROOT") or DEFAULT_RUN_ROOT
    out = B.build_board(R.current_rows(), _live_rows(now), B.load_run_results(root), now=now, variant=variant)
    rows = out["rows"]
    if status:
        rows = [r for r in rows if r["status"] == status]
    if kind:
        rows = [r for r in rows if r["release"]["kind"] == kind]
    if q:
        t = q.strip().lower()
        rows = [r for r in rows if t in r["symbol"].lower() or t in (r["company"] or "").lower()]
    out.update(rows=rows, shown=len(rows), clock_override=forced, read_only=True, sends_requests=False,
               notice="Shadow research only; no trade is placed. A status other than BUY or SELL is never a prediction. Missing data is shown as DATA INCOMPLETE, never as neutral.")
    return out
