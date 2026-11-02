"""Read-only API for the Smart Move evidence-quality page: GET /api/smart-move/quality/{summary,events}. It serves ONE saved report bundle (smart_move_quality_report.py) and nothing else: no provider, no refresh,
no write. Environment: SMART_MOVE_QUALITY_REPORT = path of the bundle. A missing or unreadable file is an explicit status (NO_REPORT / UNREADABLE) with HTTP 200, so the page shows an empty or error state instead of
failing; the bundle's own `source` / `synthetic` / `label` fields are passed through untouched so a synthetic bundle can never be shown as live."""
import json
import os
from typing import Optional

from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/smart-move/quality", tags=["smart-move-quality"])
SCHEMA = "smart_move.quality_report.v1"


def _load() -> dict:
    path = os.getenv("SMART_MOVE_QUALITY_REPORT")
    if not path:
        return {"status": "NO_REPORT", "detail": "No evidence-quality report is configured (SMART_MOVE_QUALITY_REPORT)."}
    if not os.path.isfile(path):
        return {"status": "NO_REPORT", "detail": "The configured evidence-quality report does not exist yet."}
    try:
        with open(path, encoding="utf-8-sig") as fh:
            b = json.load(fh)
    except (OSError, ValueError) as e:
        return {"status": "UNREADABLE", "detail": "The report could not be read: %s" % type(e).__name__}
    if not isinstance(b, dict) or b.get("schema") != SCHEMA or not isinstance(b.get("events"), list):
        return {"status": "UNREADABLE", "detail": "The file is not a %s bundle." % SCHEMA}
    return {"status": "OK", "bundle": b}


@router.get("/summary")
def summary():
    r = _load()
    if r["status"] != "OK":
        return r
    b = r["bundle"]
    return {"status": "OK", "schema": b["schema"], "generated_at": b.get("generated_at"), "as_of": b.get("as_of"), "source": b.get("source"), "synthetic": bool(b.get("synthetic")), "label": b.get("label"),
            "notes": b.get("notes", []), "summary": b.get("summary", {}), "accuracy": b.get("accuracy", {}), "read_only": True}


@router.get("/events")
def events(quarter_state: Optional[str] = Query(None, pattern="^(KNOWN|QUARTER_UNKNOWN|AMBIGUOUS)$"), eligible: Optional[bool] = None, pairing: Optional[str] = Query(None, pattern="^(PAIRED|UNPAIRED|NOT_PAIRABLE)$"),
           news_state: Optional[str] = Query(None, max_length=40), outcome_state: Optional[str] = Query(None, max_length=60), missing: Optional[str] = Query(None, max_length=60),
           q: Optional[str] = Query(None, max_length=40), limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    r = _load()
    if r["status"] != "OK":
        return dict(r, rows=[], total=0)
    b = r["bundle"]
    rows = b["events"]
    ql = (q or "").strip().lower()
    out = [e for e in rows
           if (quarter_state is None or e.get("quarter_state") == quarter_state) and (eligible is None or bool(e.get("prediction_eligible")) == eligible) and (pairing is None or e.get("pairing_state") == pairing)
           and (news_state is None or e.get("news_state") == news_state) and (outcome_state is None or e.get("outcome_state") == outcome_state) and (missing is None or missing in (e.get("missing") or []))
           and (not ql or ql in str(e.get("symbol", "")).lower() or ql in str(e.get("company") or "").lower())]
    return {"status": "OK", "source": b.get("source"), "synthetic": bool(b.get("synthetic")), "label": b.get("label"), "as_of": b.get("as_of"), "total": len(out), "limit": limit, "offset": offset,
            "rows": out[offset: offset + limit], "read_only": True}
