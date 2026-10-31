"""Read-only API over saved predictions and outcomes: /api/smart-move/tracking/{status,history,accuracy,report}. No write route, no provider, no refresh.

Sources (environment, all optional):
  SMART_MOVE_PRED_STORES    live-store folders holding predictions.jsonl, separated by the OS path separator
  SMART_MOVE_TRACKING_DIR   folder holding outcomes.jsonl
  SMART_MOVE_TRACKING_DEMO  "1": build SIMULATED history and outcomes in a temp folder (clearly labelled SIMULATION)
"""
import os
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

import smart_move_tracking as T

router = APIRouter(prefix="/api/smart-move/tracking", tags=["smart-move-tracking"])
_demo = {}


def _sources():
    if os.getenv("SMART_MOVE_TRACKING_DEMO") == "1":
        if "dirs" not in _demo:
            import smart_move_tracking_sim as S
            base = Path(tempfile.mkdtemp(prefix="smart_move_tracking_demo_"))
            S.simulate(base / "store", base / "track")
            _demo["dirs"] = ([str(base / "store")], str(base / "track"))
        return _demo["dirs"]
    stores = [s for s in (os.getenv("SMART_MOVE_PRED_STORES") or "").split(os.pathsep) if s]
    return stores, os.getenv("SMART_MOVE_TRACKING_DIR") or ""


def _data():
    stores, track = _sources()
    return T.load_predictions(stores), (T.load_outcomes(track) if track else []), stores, track


@router.get("/status")
def status():
    preds, outs, stores, track = _data()
    return {"read_only": True, "sends_requests": False, "demo": os.getenv("SMART_MOVE_TRACKING_DEMO") == "1", "prediction_stores": len(stores), "tracking_dir_set": bool(track),
            "prediction_versions": len(preds), "outcomes": len(outs),
            "by_mode": {m: {"prediction_versions": sum(1 for p in preds if (p.get("mode") or "LIVE") == m), "outcomes": sum(1 for o in outs if o.get("mode") == m)} for m in ("LIVE", "SIMULATION")}, "modes": sorted({(p.get("mode") or "LIVE") for p in preds}),
            "message": None if preds else "No stored predictions yet. They appear here after the live layer or the scheduled run saves them."}


@router.get("/history")
def history(mode: Optional[str] = Query(None, pattern="^(LIVE|SIMULATION)$"), symbol: Optional[str] = Query(None, max_length=12), signal: Optional[str] = Query(None, pattern="^(BUY|SELL|NO_TRADE)$"),
            limit: int = Query(200, ge=1, le=1000), offset: int = Query(0, ge=0)):
    preds, outs, _, _ = _data()
    rows = T.history(preds)
    out_map = {(o["symbol"], o["earnings_date"], o["mode"]): o for o in outs}
    if mode:
        rows = [r for r in rows if r["mode"] == mode]
    if symbol:
        rows = [r for r in rows if r["symbol"] == symbol.upper().strip()]
    if signal:
        rows = [r for r in rows if signal in {r["latest"][v]["signal"] for v in T.VARIANTS}]
    for r in rows:
        o = out_map.get((r["symbol"], r["earnings_date"], r["mode"]))
        r["outcome"] = {"move_pct": o["move_pct"], "class": T.grader.outcome_class(o["move_pct"]), "source": o["source"]} if o else None
    return {"total": len(rows), "rows": rows[offset:offset + limit], "notice": "History only; predictions are never edited, a change is a new version."}


@router.get("/accuracy")
def accuracy(mode: str = Query("LIVE", pattern="^(LIVE|SIMULATION)$")):
    preds, outs, _, _ = _data()
    return T.accuracy(preds, outs, mode=mode)


@router.get("/report")
def report():
    preds, outs, _, _ = _data()
    rep = T.build_report(preds, outs)
    rep["markdown"] = T.report_markdown(rep)
    return rep


@router.get("/{rest:path}")
def unknown(rest: str):
    raise HTTPException(404, "unknown tracking route")
