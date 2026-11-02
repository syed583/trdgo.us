"""V2 (experimental) results and audit generation/retrieval: /api/smart-move/{v2,audits}. Kept apart from the board routes so the board stays provably read-only (it has a test that forbids
file writes there). Reads saved files; the audit POST writes only NEW files in the audit folder and sends no provider request."""
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Query

import smart_move_board as B
from smart_move_board_routes import DEFAULT_RUN_ROOT, _now

router = APIRouter(prefix="/api/smart-move", tags=["smart-move-v2-audit"])
BASEDIR = r"C:\Users\initi\SmartMoveRun"


def _dirs():
    base = os.getenv("SMART_MOVE_BASE") or r"C:\Users\initi\SmartMoveRun"
    return {"v2": os.getenv("SMART_MOVE_V2_DIR") or os.path.join(base, "v2"), "audits": os.getenv("SMART_MOVE_AUDIT_DIR") or os.path.join(base, "audits"), "backups": os.getenv("SMART_MOVE_BACKUP_DIR") or os.path.join(base, "backups")}


def _latest_v2():
    import glob
    import json as _j
    files = sorted(glob.glob(os.path.join(_dirs()["v2"], "v2_run_*.json")))
    return (files[-1], _j.load(open(files[-1], encoding="utf-8"))) if files else (None, None)


def _latest_collection():
    import glob
    fs = sorted(glob.glob(os.path.join(os.getenv("SMART_MOVE_V2_RUNS_DIR") or os.path.join(os.getenv("SMART_MOVE_BASE") or BASEDIR, "v2_runs"), "*", "report.json")))
    return fs[-1] if fs else None


def _latest_findings():
    import glob
    fs = sorted(glob.glob(os.path.join(_dirs()["audits"], "findings_*.json")))
    return fs[-1] if fs else None


def _latest_v2c():
    import glob
    import json as _j
    files = sorted(glob.glob(os.path.join(_dirs()["v2"], "v2c_run_*.json")))
    return (files[-1], _j.load(open(files[-1], encoding="utf-8"))) if files else (None, None)


@router.get("/v2c")
def v2c_predictions(variant: str = Query("continuation", pattern="^(continuation|contrarian)$")):
    """RESEARCH CANDIDATE (v2c-1) per-stock results from the saved candidate file. Research only; accuracy is NOT VERIFIED and never shown as a percentage."""
    f, body = _latest_v2c()
    if body is None:
        return {"available": False, "status": "UNAVAILABLE", "message": "No V2 candidate file saved yet.", "events": []}
    root = os.getenv("SMART_MOVE_RUN_ROOT") or DEFAULT_RUN_ROOT
    uni = set(B.load_run_results(root).keys())
    out = []
    for e in body["events"]:
        if (e["symbol"], str(e["earnings_date"])[:10]) not in uni:
            continue
        x = e["v2c"][variant]
        out.append({"symbol": e["symbol"], "earnings_date": e["earnings_date"], "signal": x["signal"], "display_status": x["display_status"], "category": x["category"], "primary_cause": x["primary_cause"],
                    "secondary_causes": x["secondary_causes"], "explanation": x["explanation"], "bull": x["bull"], "bear": x["bear"], "available_points": x["available_points"],
                    "directional_available_points": x["directional_available_points"], "missing_points": x["missing_points"], "factor_gaps": x["factor_gaps"], "scored_as_of": e.get("scored_as_of"),
                    "frozen_v2_signal": x["frozen_v2_signal"], "eligibility": e.get("eligibility")})
    return {"available": True, "status": body["status"], "version": body["version"], "config_hash": body["config_hash"], "audit_hash": body.get("audit_hash"), "base_version": body["base_version"],
            "summary": body["summary"], "frozen_v2_counts": body["frozen_v2_counts"], "accuracy": body["accuracy"], "changes": body["changes"], "scoring_changes": body["scoring_changes"],
            "factor_redistribution": body["factor_redistribution"], "usable_for_decisions": False, "variant": variant,
            "banner": "RESEARCH CANDIDATE (v2c-1): scoring, weights and thresholds are identical to the frozen V2; only the status logic names WHY a stock is incomplete or no-trade. No accuracy is verified; research only.", "events": out}


def _latest_v2r():
    import glob
    import json as _j
    files = sorted(glob.glob(os.path.join(_dirs()["v2"], "v2r_run_*.json")))
    return (files[-1], _j.load(open(files[-1], encoding="utf-8"))) if files else (None, None)


@router.get("/recovery")
def recovery_audit():
    """Latest missing-evidence recovery audit (saved file): before/after, categories, per-stock unresolved factors. Read only."""
    import glob
    import json as _j
    d = os.getenv("SMART_MOVE_RECOVERY_DIR") or os.path.join(os.getenv("SMART_MOVE_BASE") or BASEDIR, "audits_recovery")
    fs = sorted(glob.glob(os.path.join(d, "recovery_audit_*.json")))
    if not fs:
        return {"available": False, "message": "no recovery audit saved yet"}
    r = _j.load(open(fs[-1], encoding="utf-8"))
    return {"available": True, "file": os.path.basename(fs[-1]), "generated_utc": r["generated_utc"], "stocks": r["stocks"], "audit_hash": r["audit_hash"], "recovery": r["recovery"], "before_after": r["before_after"],
            "missing_points_by_category": r["missing_points_by_category"], "stocks_by_category": r["stocks_by_category"], "consensus_history": r["consensus_history"], "focus": r["focus"],
            "per_stock": [{"symbol": p["symbol"], "cutoff_passed": p["cutoff_passed"], "unresolved": [{"factor": u["factor"], "points_missing": u["points_missing"], "category": u["category"], "reason": u["reason"], "recoverable_today": u["recoverable_today"]} for u in p["unresolved"]]} for p in r["per_stock"]],
            "can_be_fixed_now": r["can_be_fixed_now"], "needs_future_observations": r["needs_future_observations"]}


@router.get("/v2r")
def v2r_predictions(variant: str = Query("continuation", pattern="^(continuation|contrarian)$")):
    """NEW RESEARCH CANDIDATE (v2r-1): unchanged scoring, transparent options-flow and news layers, named causes. Research only; accuracy NOT VERIFIED."""
    f, body = _latest_v2r()
    if body is None:
        return {"available": False, "status": "UNAVAILABLE", "message": "No v2r candidate file saved yet.", "events": []}
    root = os.getenv("SMART_MOVE_RUN_ROOT") or DEFAULT_RUN_ROOT
    uni = set(B.load_run_results(root).keys())
    out = []
    for e in body["events"]:
        if (e["symbol"], str(e["earnings_date"])[:10]) not in uni:
            continue
        x = e["v2r"][variant]
        out.append({"symbol": e["symbol"], "earnings_date": e["earnings_date"], "signal": x["signal"], "display_status": x["display_status"], "primary_cause": x["primary_cause"], "secondary_causes": x["secondary_causes"],
                    "explanation": x["explanation"], "bull": x["bull"], "bear": x["bear"], "evidence_score": x["evidence_score"], "available_points": x["available_points"],
                    "directional_available_points": x["directional_available_points"], "options_flow": {k: x["options_flow"].get(k) for k in ("direction", "state", "qualifies", "net_ratio", "alerts_used", "attributed_premium", "consistency", "shortfall")},
                    "news": x["news"], "missing_evidence": x["missing_evidence"], "headroom": x["headroom"], "frozen_v2_signal": x["frozen_v2_signal"], "scored_as_of": e.get("scored_as_of")})
    _vf, vbody = _latest_v2()
    keys = {(e["symbol"], str(e["earnings_date"])[:10]) for e in body["events"]}
    frozen = {v: {} for v in ("continuation", "contrarian")}
    for e in (vbody or {}).get("events", []):
        if (e["symbol"], str(e["earnings_date"])[:10]) in keys:
            for v in frozen:
                k = e["v2"][v].get("display_status", e["v2"][v]["signal"])
                frozen[v][k] = frozen[v].get(k, 0) + 1
    return {"available": True, "frozen_v2_counts": frozen, "status": body["status"], "version": body["version"], "config_hash": body["config_hash"], "audit_hash": body.get("audit_hash"), "base_v2": body["base_v2"], "summary": body["summary"],
            "scoring_changes": body["scoring_changes"], "factor_redistribution": body["factor_redistribution"], "accuracy": body["accuracy"], "usable_for_decisions": False, "variant": variant,
            "banner": "NEW RESEARCH CANDIDATE (v2r-1) - RESEARCH ONLY. Scoring, weights and thresholds equal the frozen V2; options flow and news are transparent layers; news earns 0 points until independently validated. Accuracy NOT VERIFIED.", "events": out}


@router.get("/v2/collection")
def v2_collection():
    """Report of the newest one-time V2 collection run (provider access, attempts, errors, kill switch)."""
    import json as _j
    f = _latest_collection()
    return {"available": f is not None, "report": _j.load(open(f, encoding="utf-8")) if f else None}


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
            "usable_for_decisions": False, "banner": "EXPERIMENTAL / NOT VALIDATED: weights and thresholds are unvalidated hypotheses. V2 uses real saved evidence, but news/guidance is BLOCKED and analyst estimate revisions need a second consensus snapshot, so no stock has all 100 points. A V2 BUY/SELL requires 60 directional points and passes the V1 eligibility gates; otherwise DATA INCOMPLETE. Missing data is never a signal.",
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
    return AR.generate(os.getenv("SMART_MOVE_RUN_ROOT") or DEFAULT_RUN_ROOT, d["audits"], now=now, v2_file=f, tests_file=tests if os.path.exists(tests) else None, backup_root=d["backups"], v2_collection_file=_latest_collection(), v2c_file=(_latest_v2r()[0] or _latest_v2c()[0]), findings_file=_latest_findings())


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


@router.get("/v2/repeat-plan")
def v2_repeat_plan():
    """What repeated collections would add information, per stock (plan only; nothing is scheduled or requested)."""
    import glob
    import smart_move_live as L
    import smart_move_v2_collect as C
    now, _ = _now()
    dirs = sorted(glob.glob(os.path.join(os.getenv("SMART_MOVE_V2_RUNS_DIR") or os.path.join(os.getenv("SMART_MOVE_BASE") or BASEDIR, "v2_runs"), "*", "store")))
    if not dirs:
        return {"available": False, "message": "no V2 collection store yet"}
    st = L.LiveStore(dirs[-1])
    syms = sorted({r["symbol"] for r in C._records(st)})
    return dict(C.repeat_plan(st, syms, now), available=True)
