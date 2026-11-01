"""V2 (experimental) results and audit generation/retrieval: /api/smart-move/{v2,audits}. Kept apart from the board routes so the board stays provably read-only (it has a test that forbids
file writes there). Reads saved files; the audit POST writes only NEW files in the audit folder and sends no provider request."""
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Query

import smart_move_board as B
from smart_move_board_routes import DEFAULT_RUN_ROOT, _now

router = APIRouter(prefix="/api/smart-move", tags=["smart-move-v2-audit"])


def _dirs():
    base = os.getenv("SMART_MOVE_BASE") or r"C:\Users\initi\SmartMoveRun"
    return {"v2": os.getenv("SMART_MOVE_V2_DIR") or os.path.join(base, "v2"), "audits": os.getenv("SMART_MOVE_AUDIT_DIR") or os.path.join(base, "audits"), "backups": os.getenv("SMART_MOVE_BACKUP_DIR") or os.path.join(base, "backups")}


def _latest_v2():
    import glob
    import json as _j
    files = sorted(glob.glob(os.path.join(_dirs()["v2"], "v2_run_*.json")))
    return (files[-1], _j.load(open(files[-1], encoding="utf-8"))) if files else (None, None)


@router.get("/v2")
def v2_predictions(variant: str = Query("continuation", pattern="^(continuation|contrarian)$")):
    """EXPERIMENTAL V2 (100-point) results for the current universe, from the saved V2 file. UNAVAILABLE for decisions; nothing here is a signal to act on."""
    f, body = _latest_v2()
    if body is None:
        return {"available": False, "status": "UNAVAILABLE", "message": "No V2 run file saved yet.", "events": []}
    root = os.getenv("SMART_MOVE_RUN_ROOT") or DEFAULT_RUN_ROOT
    uni = set(B.load_run_results(root).keys())
    ev = [e for e in body["events"] if (e["symbol"], str(e["earnings_date"])[:10]) in uni]
    out = []
    for e in ev:
        x = e["v2"][variant]
        out.append({"symbol": e["symbol"], "earnings_date": e["earnings_date"], "signal": x["signal"], "display_status": x.get("display_status", x["signal"]), "bull": x["bull"], "bear": x["bear"],
                    "available_points": x["available_points"], "directional_available_points": x.get("directional_available_points"), "missing_points": x.get("missing_points"), "max": 100,
                    "eligibility": e.get("eligibility"), "explanation": x.get("explanation"), "evidence": e.get("evidence"), "capture_ts": e.get("capture_ts"),
                    "validated_real_data_signal": x.get("validated_real_data_signal", False), "reason_codes": x["reason_codes"], "missing": [{"factor": m["factor"], "reason": m["reason"], "data_status": m["data_status"]} for m in x["missing"]],
                    "factors": [{"factor": q["factor"], "bull": q["bull"], "bear": q["bear"], "available": q["available"], "max": q["max"]} for q in x["factors"]]})
    return {"available": True, "status": body["status"], "version": body["version"], "config_hash": body["config_hash"], "audit_hash": body.get("audit_hash"), "source_run": body["source_run"], "as_of": body["as_of"],
            "usable_for_decisions": False, "banner": "EXPERIMENTAL / UNAVAILABLE FOR DECISIONS: weights and thresholds are unvalidated hypotheses; saved evidence covers at most 55 of 100 points, so no V2 BUY/SELL can be produced; missing data is never a signal.",
            "variant": variant, "events": out}


@router.get("/v2/compare")
def v2_compare():
    """V1 versus V2 on the same events with graded real outcomes (none yet: INCONCLUSIVE). Descriptive; never declares a winner."""
    import smart_move_outcomes as O
    import smart_move_tracking as T
    import smart_move_tracking_routes as TR
    import smart_move_v2_compare as CMP
    root = os.getenv("SMART_MOVE_RUN_ROOT") or DEFAULT_RUN_ROOT
    f, body = _latest_v2()
    res = B.load_run_results(root)
    v2e = {(e["symbol"], str(e["earnings_date"])[:10]): e for e in (body or {}).get("events", [])}
    outs = {(o["symbol"], o["earnings_date"]): o["move_pct"] for o in T.load_outcomes(TR._sources()[1]) if o.get("mode") == "LIVE"} if TR._sources()[1] else {}
    rows = []
    for k, r in res.items():
        for v in ("continuation", "contrarian"):
            e2 = v2e.get(k)
            rows.append({"symbol": k[0], "earnings_date": k[1], "kind": r.get("kind"), "sector": None, "variant": v, "move_pct": outs.get(k), "v1_signal": r[v]["signal"], "v2_signal": e2["v2"][v]["signal"] if e2 else None})
    return dict(CMP.compare(rows), v2_file=os.path.basename(f) if f else None, graded_outcomes=len(outs), note="walk-forward by earnings week; INCONCLUSIVE until 100 decided events per variant over 8 weeks for both models")


@router.get("/audits")
def audits_list():
    import smart_move_audit as A
    return {"audits": A.list_audits(_dirs()["audits"]), "read_only": False, "note": "append-only; each audit is a JSON record plus a two-page PDF"}


@router.post("/audits/generate")
def audits_generate():
    """Manual 'Generate audit'. Reads saved data only (no provider request) and writes NEW files; an existing audit is never overwritten."""
    import smart_move_audit_run as AR
    now, _ = _now()
    d = _dirs()
    f, _b = _latest_v2()
    tests = os.path.join(d["audits"], "test_results.json")
    return AR.generate(os.getenv("SMART_MOVE_RUN_ROOT") or DEFAULT_RUN_ROOT, d["audits"], now=now, v2_file=f, tests_file=tests if os.path.exists(tests) else None, backup_root=d["backups"])


@router.get("/audits/{name}.{ext}")
def audits_get(name: str, ext: str):
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    if ext not in ("json", "pdf") or not name.startswith("audit_") or "/" in name or "\\" in name or ".." in name:
        raise HTTPException(404, "unknown audit")
    p = os.path.join(_dirs()["audits"], name + "." + ext)
    if not os.path.isfile(p):
        raise HTTPException(404, "unknown audit")
    return FileResponse(p, media_type="application/pdf" if ext == "pdf" else "application/json", filename=name + "." + ext)
