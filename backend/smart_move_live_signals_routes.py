"""Read-only API for the Live Earnings Signals page (local Smart Move server; production registration is a separate, approved deployment step). It serves signals.jsonl produced by the post-batch job and nothing else:
no provider, no scoring, no write. Status is re-evaluated at read time (LIVE ages into STALE, past events become HISTORICAL, a batch whose completion marker no longer verifies becomes UNVERIFIED).
Environment: SMART_MOVE_SIGNALS_DIR = folder with signals.jsonl; SMART_MOVE_BATCH_RUN_DIR = the batch run folder holding batch_complete.json (optional; without it batch rows cannot be LIVE)."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query

import smart_move_batch_marker as BM
import smart_move_live_signals as LS

router = APIRouter(prefix="/api/smart-move/live-signals", tags=["smart-move-live-signals"])


def _load():
    d = os.getenv("SMART_MOVE_SIGNALS_DIR")
    if not d:
        return None, {"status": "NO_SIGNALS", "detail": "No signal store is configured (SMART_MOVE_SIGNALS_DIR)."}
    f = Path(d) / "signals.jsonl"
    if not f.is_file():
        return None, {"status": "NO_SIGNALS", "detail": "No signals have been generated yet: the post-batch job has not processed a validated batch."}
    try:
        rows = [json.loads(ln) for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except ValueError:
        return None, {"status": "UNREADABLE", "detail": "The signal store could not be read."}
    mv = None
    run = os.getenv("SMART_MOVE_BATCH_RUN_DIR")
    if run:
        mv = BM.verify_marker(run)["valid"]
    elif any(r.get("batch_id") for r in rows):
        mv = False
    now = datetime.now(timezone.utc)
    latest: dict = {}
    for r in sorted(rows, key=lambda r: r["generated_at"]):
        latest[(r["symbol"], r["event_date"])] = r                                    # newest generation per event
    return [LS.refresh_status(r, now, mv) for r in latest.values()], {"status": "OK", "marker_valid": mv, "now": now.isoformat()}


@router.get("/summary")
def summary():
    rows, meta = _load()
    if rows is None:
        return meta
    dates = sorted({r["event_date"] for r in rows})
    return {"status": "OK", "as_of": meta["now"], "marker_valid": meta["marker_valid"], "coverage": LS.coverage(rows, dates), "by_data_status": _count(rows, "data_status"), "dates": dates,
            "notes": ["Confidence is not shown: no existing model has a calibrated probability.", "A batch of 50 provider attempts covers only part of the earnings calendar.",
                      "BUY or SELL appears only when every model with valid inputs agrees; NO TRADE says whether inputs were missing, the model was neutral, or models disagreed."], "read_only": True}


def _count(rows, key):
    from collections import Counter
    return dict(Counter(r[key] for r in rows))


@router.get("/rows")
def rows_(signal: Optional[str] = Query(None, pattern="^(BUY|SELL|NO_TRADE)$"), q: Optional[str] = Query(None, max_length=40), event_date: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
          session: Optional[str] = Query(None, pattern="^(BMO|AMC|UNKNOWN)$"), data_status: Optional[str] = Query(None, pattern="^(LIVE|STALE|HISTORICAL|UNVERIFIED|SYNTHETIC)$"),
          limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    rows, meta = _load()
    if rows is None:
        return dict(meta, rows=[], total=0)
    ql = (q or "").strip().lower()
    out = [r for r in rows if (signal is None or r["signal"] == signal) and (event_date is None or r["event_date"] == event_date) and (session is None or r["session"] == session)
           and (data_status is None or r["data_status"] == data_status) and (not ql or ql in r["symbol"].lower() or ql in str(r.get("company") or "").lower())]
    out.sort(key=lambda r: (r["event_date"], {"BUY": 0, "SELL": 1, "NO_TRADE": 2}[r["signal"]], r["symbol"]))
    return {"status": "OK", "as_of": meta["now"], "marker_valid": meta["marker_valid"], "total": len(out), "limit": limit, "offset": offset, "rows": out[offset: offset + limit], "read_only": True}
