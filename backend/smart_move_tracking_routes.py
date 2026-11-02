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
    if not stores:                                       # default: the real collection-run stores, read only
        root = _run_root()
        if os.path.isdir(root):
            stores = [str(d / "store") for d in sorted(Path(root).iterdir()) if d.name.startswith("run_") and (d / "store" / "predictions.jsonl").exists()]
    return stores, os.getenv("SMART_MOVE_TRACKING_DIR") or DEFAULT_TRACK_DIR


DEFAULT_TRACK_DIR = r"C:\Users\initi\SmartMoveRun\tracking"


def _run_root():
    return os.getenv("SMART_MOVE_RUN_ROOT") or r"C:\Users\initi\SmartMoveRun\out"


def _universe_keys():
    """(symbol, date) pairs the board counts as real events: everything a real run report evaluated."""
    import smart_move_board as B
    if os.getenv("SMART_MOVE_PRED_STORES"):                       # explicitly configured stores are shown as they are, without a universe scope
        return set()
    return set(B.load_run_results(_run_root()).keys())          # empty when no run report exists: then no scope filter applies and every saved event is shown


def _data():
    stores, track = _sources()
    return T.load_predictions(stores), (T.load_outcomes(track) if track else []), stores, track


@router.get("/status")
def status():
    preds, outs, stores, track = _data()
    uni = (_universe_keys() or None) if os.getenv("SMART_MOVE_TRACKING_DEMO") != "1" else None
    in_uni = [p for p in preds if uni is None or (p["symbol"], str(p["earnings_date"])[:10]) in uni]
    return {"universe_events": len({(p["symbol"], str(p["earnings_date"])[:10]) for p in in_uni}), "universe_versions": len(in_uni), "saved_events_total": len({(p["symbol"], str(p["earnings_date"])[:10]) for p in preds}),
            "read_only": True, "sends_requests": False, "demo": os.getenv("SMART_MOVE_TRACKING_DEMO") == "1", "prediction_stores": len(stores), "tracking_dir_set": bool(track),
            "prediction_versions": len(preds), "outcomes": len(outs),
            "by_mode": {m: {"prediction_versions": sum(1 for p in preds if (p.get("mode") or "LIVE") == m), "outcomes": sum(1 for o in outs if o.get("mode") == m)} for m in ("LIVE", "SIMULATION")}, "modes": sorted({(p.get("mode") or "LIVE") for p in preds}),
            "message": None if preds else "No stored predictions yet. They appear here after the live layer or the scheduled run saves them."}


@router.get("/history")
def history(scope: str = Query("universe", pattern="^(universe|all)$"), mode: Optional[str] = Query(None, pattern="^(LIVE|SIMULATION)$"), symbol: Optional[str] = Query(None, max_length=12), signal: Optional[str] = Query(None, pattern="^(BUY|SELL|NO_TRADE)$"),
            limit: int = Query(200, ge=1, le=1000), offset: int = Query(0, ge=0)):
    preds, outs, _, _ = _data()
    rows = T.history(preds)
    uni = _universe_keys()
    for r in rows:
        r["in_universe"] = (r["symbol"], r["earnings_date"]) in uni
    all_n = len(rows)
    if scope == "universe" and uni and os.getenv("SMART_MOVE_TRACKING_DEMO") != "1":
        rows = [r for r in rows if r["in_universe"]]
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
    return {"total": len(rows), "all_saved_events": all_n, "scope": scope, "rows": rows[offset:offset + limit], "notice": "History only; predictions are never edited, a change is a new version."}


@router.get("/accuracy")
def accuracy(mode: str = Query("LIVE", pattern="^(LIVE|SIMULATION)$")):
    preds, outs, _, _ = _data()
    return T.accuracy(preds, outs, mode=mode)


@router.get("/analytics")
def analytics(mode: str = Query("LIVE", pattern="^(LIVE|SIMULATION)$")):
    """Breakdowns with sample sizes and Wilson intervals. SIMULATION is returned only as an explicitly labelled demo and never as performance."""
    import smart_move_analytics as A
    import smart_move_board as B
    preds, outs, _, _ = _data()
    kinds = {k: v.get("kind") for k, v in B.load_run_results(_run_root()).items()}
    sectors = {}
    try:
        import smart_move_routes as R
        sectors = {r["symbol"]: r.get("sector") for r in R.current_rows() if r.get("sector")}      # saved Unusual Whales calendar rows only
    except Exception:  # noqa: BLE001
        pass
    uni = (_universe_keys() or None) if os.getenv("SMART_MOVE_TRACKING_DEMO") != "1" else None
    if uni is not None:
        preds = [p for p in preds if (p["symbol"], str(p["earnings_date"])[:10]) in uni]
    rows = A.rows_from_records(preds, outs, mode=mode, sector_lookup=sectors, kind_lookup=kinds)
    if mode == "SIMULATION":
        for r in rows:
            r["mode"] = "LIVE"                           # only so the breakdown can run on demo data; the response is labelled below
    out = A.breakdown(rows)
    if mode == "SIMULATION":
        out.update(simulated=True, claim_text="SIMULATED data: tests the reporting only and says nothing about the model")
    known = {"with_liquidity": sum(1 for r in rows if r["adv20"] is not None), "with_sector": sum(1 for r in rows if r["sector"]),
             "with_kind": sum(1 for r in rows if r["kind"]), "rows": len(rows)}
    return dict(out, mode=mode, metadata_known=known, graded_rows=sum(1 for r in rows if r["move_pct"] is not None),
                note="Metadata is read from the stored record when present; older records lack liquidity and sector and show as unknown (never backfilled by guess).")


@router.get("/outcomes")
def outcomes():
    import smart_move_outcomes as O
    _, _, _, track = _data()
    rows = O.latest(track) if track else []
    return {"total": len(rows), "rows": rows, "read_only": True, "sends_requests": False}


@router.get("/report")
def report():
    preds, outs, _, _ = _data()
    rep = T.build_report(preds, outs)
    rep["markdown"] = T.report_markdown(rep)
    return rep


@router.get("/events")
def events(mode: str = Query("LIVE", pattern="^(LIVE|SIMULATION)$")):
    """Per-event PASS / FAIL / FLAT rows, continuation vs contrarian_partial_factor comparison, calendar freshness and accuracy status. Saved data only."""
    from datetime import datetime, timezone
    import smart_move_report as RP
    preds, outs, stores, _ = _data()
    newest = None
    for sd in stores:
        for r in T._read_jsonl(Path(sd) / "cal_obs.jsonl"):
            try:
                t = datetime.fromisoformat(r["observed_at"])
            except (KeyError, ValueError, TypeError):
                continue
            newest = t if newest is None or t > newest else newest
    age = round((datetime.now(timezone.utc) - newest).total_seconds() / 3600, 3) if newest else None
    return RP.build(preds, outs, mode=mode, calendar_age_h=age)


@router.get("/{rest:path}")
def unknown(rest: str):
    raise HTTPException(404, "unknown tracking route")
