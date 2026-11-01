"""Smart Move audit record: ONE machine-readable JSON assembled from saved data only (run reports, stores, registry, V2 file, health, backups, recorded test results).
Nothing is requested from a provider and nothing is invented: anything not verifiable is written as NOT VERIFIED / NOT AVAILABLE. The PDF (smart_move_audit_pdf.py) is a
summary of this record; the record is the evidence. Audits are append-only files named by run id and time; an existing file is never overwritten.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import smart_move_board as B
import smart_move_health as H
import smart_move_scheduler as SCH
import smart_move_v2 as V2

UTC = timezone.utc
NV = "NOT VERIFIED"
NA = "NOT AVAILABLE"

# factor -> source/endpoint/status, from the data availability audit (docs/smart_move/V2_DATA_AVAILABILITY.md). Status is about DATA, not about the factor's validity.
FACTOR_SOURCES = {
    "F3": ("UW /api/earnings/{t} + /ohlc/1d", "READY", "saved in every run"),
    "F5": ("UW /api/stock/{t}/ohlc/1d", "READY", "saved in every run"),
    "F7": ("UW /volatility/context + /iv-rank", "READY", "collected live in window only"),
    "F8": ("UW ohlc SPY + sector ETF", "READY", "saved in every run"),
    "ANALYST": ("UW /api/screener/analysts; /companies/{t}/earnings-estimates (current only)", "PARTIAL", "rating actions only; estimate revisions need dated snapshots"),
    "OPTIONS_FLOW": ("UW /api/stock/{t}/net-prem-ticks, /flow-alerts", "PARTIAL", "adapter built; never collected, no history saved"),
    "IMPLIED_VS_HIST": ("UW /api/earnings/{t} (expected_move_perc, post_earnings_move_1d)", "PARTIAL", "ratio is READY from saved data; direction undefined, magnitude only"),
    "SHORT_INST": ("UW /api/shorts/{t}/interest-float/v2, /institution/{t}/ownership", "PARTIAL", "never collected; FINRA/13F data is delayed"),
    "REL_STRENGTH": ("derived from saved ohlc (stock, SPY, sector ETF)", "READY", "overlaps V1 F5/F8 evidence"),
    "NEWS_GUIDANCE": ("UW /api/news/headlines (sentiment field semantics unverified)", "BLOCKED", "no structured guidance field"),
}


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def build(run_root, *, now: datetime, v2_file=None, tests: Optional[dict] = None, backup_root=None, saved_rows=None, plan_fn=None, outcomes: Optional[list] = None) -> dict:
    reports = B.load_run_reports(run_root)
    if not reports:
        return {"ok": False, "error": "no run report found", "generated_utc": now.isoformat()}
    name, rep = reports[-1]
    run_dir = Path(run_root) / name
    saved = saved_rows if saved_rows is not None else []
    reg = B.derive_registry(saved, reports)
    runres = B.load_run_results(run_root)
    boards = {v: B.build_board(saved, [], runres, now=now, variant=v, registry=reg, plan_fn=plan_fn) for v in ("continuation", "contrarian")}
    rows = {r["symbol"]: r for r in boards["continuation"]["rows"]}
    res = {r["symbol"]: r for r in (rep.get("results") or [])}
    summary = boards["continuation"]["summary"]
    counts = {v: boards[v]["counts"] for v in boards}
    # ---- events and evidence
    cov = Counter()
    for r in boards["continuation"]["rows"]:
        a = r["points"]["available"]
        cov["complete (40/40)" if a == 40 else "partial" if a else "missing (not evaluated)"] += 1
    kinds = Counter(r["release"]["kind"] for r in boards["continuation"]["rows"])
    # ---- top signals and disagreements
    def top(variant, sig, key, n=3):
        c = [(x["symbol"], x[variant]) for x in res.values() if x[variant]["signal"] == sig and x["symbol"] in rows and rows[x["symbol"]]["variants"][variant]["status"] == sig]
        c.sort(key=lambda t: -t[1][key])
        return [{"symbol": s, "variant": variant, "signal": sig, "score": e[key], "available": res[s]["available_points"], "evidence": "40/40" if res[s]["available_points"] == 40 else "%s/40" % res[s]["available_points"],
                 "reason": ", ".join(e["reason_codes"])} for s, e in c[:n]]
    tops = [t for v in ("continuation", "contrarian") for sig, key in (("BUY", "bull"), ("SELL", "bear")) for t in top(v, sig, key)]
    dis = [s for s, r in rows.items() if {r["variants"]["continuation"]["status"], r["variants"]["contrarian"]["status"]} == {"BUY", "SELL"}]
    # ---- provider
    ev_times = [e["retrieved_at"] for r in res.values() for e in (r.get("evidence") or []) if e.get("retrieved_at")]
    by = rep.get("requests_by_provider") or {}
    stops = rep.get("stop_events") or []
    provider = {"attempts_total": rep.get("attempts_total"), "by_provider": by, "errors": len(rep.get("errors") or []), "stop_events_401_429": len(stops), "cap": rep.get("cap_total"),
                "budget_utilisation_pct": round(100 * rep["attempts_total"] / rep["cap_total"], 1) if rep.get("attempts_total") and rep.get("cap_total") else None,
                "latest_evidence_retrieved_utc": max(ev_times) if ev_times else None, "run_updated_utc": rep.get("updated_utc"), "run_status": rep.get("status"),
                "subscription": NV + " (needs the Unusual Whales dashboard; last seen: Whale Bundle Advanced, renews 1 Nov 2026, 3 concurrent requests)", "limits": "3 in-flight per token (UW support 23 Oct); daily quota unlimited per dashboard; both NOT RE-VERIFIED today"}
    # ---- windows, liquidity
    codes = Counter(c for r in res.values() for c in r["continuation"]["reason_codes"])
    windows = {"late_or_expired": summary["window_closed_missed"], "open_without_prediction": summary["window_open_no_prediction"], "ahead": summary["window_ahead"], "evaluated": summary["evaluated"]}
    liquidity = {"NOT_TRADABLE (entry session already passed)": codes.get("NOT_TRADABLE", 0), "ILLIQUID_OR_UNKNOWN (20-day avg dollar volume < $20M)": codes.get("ILLIQUID_OR_UNKNOWN", 0), "INSUFFICIENT_COVERAGE": codes.get("INSUFFICIENT_COVERAGE", 0)}
    # ---- V2
    v2 = {"status": V2.STATUS, "version": V2.VERSION, "config_hash": V2.CONFIG_HASH, "readiness": "UNAVAILABLE: saved evidence covers at most 55 of 100 points (60 required); 4 factors have no saved data and 1 is BLOCKED", "factors": [], "signals": None}
    v2_events = None
    if v2_file and Path(v2_file).exists():
        v2b = json.loads(Path(v2_file).read_text(encoding="utf-8"))
        v2_events = {e["symbol"]: e for e in v2b["events"] if e["symbol"] in res}
        v2["audit_hash"] = v2b.get("audit_hash")
        v2["source_run"] = v2b.get("source_run")
        v2["signals"] = {v: dict(Counter(e["v2"][v]["signal"] for e in v2_events.values())) for v in ("continuation", "contrarian")}
        v2["available_points_max"] = max((e["v2"]["continuation"]["available_points"] for e in v2_events.values()), default=None)
        av = Counter()
        for e in v2_events.values():
            for f in e["v2"]["continuation"]["factors"]:
                av[f["factor"]] += 1 if f["available"] else 0
        for k, w in V2.WEIGHTS.items():
            src = FACTOR_SOURCES[k]
            v2["factors"].append({"factor": k, "points": w, "stocks_with_data": av.get(k, 0), "stocks": len(v2_events), "data_status": src[1], "source": src[0], "note": src[2]})
    else:
        for k, w in V2.WEIGHTS.items():
            src = FACTOR_SOURCES[k]
            v2["factors"].append({"factor": k, "points": w, "stocks_with_data": None, "stocks": None, "data_status": src[1], "source": src[0], "note": src[2] + "; V2 run file " + NA})
    # ---- outcomes / accuracy
    outs = outcomes or []
    outcome = {"graded": len(outs), "accuracy": NA + " (no graded outcomes yet; no accuracy is claimed)" if not outs else "see tracking analytics", "claim_allowed": False}
    comparison = {"verdict": "INCONCLUSIVE: no graded outcomes; minimum sample is 100 decided events per variant over 8 earnings weeks"}
    # ---- operations
    health = H.health(run_dir, reg, now=now)
    kill = (run_dir / "store" / "KILL").exists()
    backups = []
    if backup_root and Path(backup_root).is_dir():
        for d in sorted(Path(backup_root).iterdir()):
            if d.is_dir() and d.name.startswith(name) and (d / "MANIFEST.sha256.json").exists():
                backups.append({"folder": d.name, **H.verify_backup(d)})
    ops = {"scheduler": "PLAN ONLY; recurring live collection disabled; no task registered", "kill_switch": "ENGAGED" if kill else "NOT ENGAGED", "health_ok": health["ok"], "health_problems": [c for c in health["checks"] if not c["ok"]],
           "backup": backups[-1] if backups else NV + " (no backup folder for this run)", "estimated_daily_requests": SCH.estimate_daily_requests(summary["total"])}
    # ---- defects
    defects = []
    def d(sev, comp, what, action):
        defects.append({"severity": sev, "component": comp, "defect": what, "action": action})
    if not outs:
        d("HIGH", "outcomes", "No outcome has ever been collected or graded; accuracy is unknown", "Run outcome collection after the reaction sessions close (needs separate live approval)")
    d("HIGH", "V2 data", "Saved evidence reaches at most 55 of 100 V2 points; 4 factors have no saved data, news/guidance is BLOCKED", "Authorize collection of analyst, flow and short/institutional data; define guidance field")
    d("MED", "scheduler", "scheduler_tick --execute is tested with mocks only; never run live", "Review seed-store convention, then a supervised first execution")
    d("MED", "provider", "Subscription, renewal (1 Nov) and 3-concurrent limit not re-verified today", "Owner to confirm in the Unusual Whales dashboard")
    if not backups:
        d("MED", "backup", "No verified backup for this run", "Create and verify a backup")
    for c in health["checks"]:
        if not c["ok"]:
            d("CRITICAL" if c["check"] in ("kill_switch_engaged", "no_duplicate_symbols", "evidence_hashes_present", "no_provider_stop") else "HIGH", "health", "%s: %s" % (c["check"], c["detail"]), "Investigate before relying on the board")
    for f in (tests or {}).get("failures", []):
        d(f.get("severity", "LOW"), "tests", f["name"] + ": " + f["why"], f.get("action", "see test notes"))
    verdict = "PARTIAL" if not any(x["severity"] == "CRITICAL" for x in defects) else "BLOCKED"
    rec = {
        "schema": "smart-move-audit/1", "title": "TRDGO-US SMART MOVE - EARNINGS PREDICTION AUDIT", "generated_utc": now.isoformat(), "run_id": name, "mode": "REAL" if not rep.get("simulation") else "SIMULATION",
        "event": {"dates": sorted({r["earnings_date"] for r in boards["continuation"]["rows"]}), "timing": dict(kinds)},
        "models": {"V1": {"points": 40, "status": "FROZEN, in use for the board (shadow research)"}, "V2": {"points": 100, "status": V2.STATUS + " / UNAVAILABLE for decisions", "version": V2.VERSION, "config_hash": V2.CONFIG_HASH}},
        "events": {"calendar_events": summary["total"] + len(boards["continuation"]["removed_events"]), "eligible": summary["total"], "evaluated": summary["evaluated"], "pending": counts["continuation"].get("PENDING", 0),
                   "incomplete": counts["continuation"].get("DATA_INCOMPLETE", 0), "excluded_removed": len(boards["continuation"]["removed_events"]), "removed": boards["continuation"]["removed_events"]},
        "signals": counts, "evidence_coverage": dict(cov), "top_signals": tops, "disagreements": {"count": len(dis), "symbols": dis},
        "provider": provider, "windows": windows, "liquidity": liquidity, "v2": v2, "outcomes": outcome, "v1_vs_v2": comparison, "operations": ops,
        "tests": tests or {"recorded": NV}, "defects": defects, "verdict": verdict,
        "verdict_reasons": [x["defect"] for x in defects if x["severity"] in ("HIGH", "CRITICAL")][:4],
        "evidence_timestamp_utc": provider["latest_evidence_retrieved_utc"], "data_labels": "REAL saved evidence; no simulated or mock data in this record" if not rep.get("simulation") else "SIMULATED",
        "board_counts_source": "board build over the registry universe at generation time",
    }
    rec["audit_hash"] = _sha({k: v for k, v in rec.items() if k not in ("generated_utc", "audit_hash")})
    return rec


def save(rec: dict, audit_root) -> dict:
    root = Path(audit_root)
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromisoformat(rec["generated_utc"]).strftime("%Y%m%dT%H%M%SZ")
    base = root / ("audit_%s_%s" % (rec["run_id"], stamp))
    j = base.with_suffix(".json")
    if j.exists():
        raise FileExistsError("audit exists; never overwritten: %s" % j)
    j.write_text(json.dumps(rec, indent=1, default=str), encoding="utf-8")
    return {"json": str(j), "base": str(base)}


def list_audits(audit_root) -> list:
    root = Path(audit_root)
    out = []
    for p in sorted(root.glob("audit_*.json")) if root.is_dir() else []:
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({"file": p.stem, "run_id": r.get("run_id"), "generated_utc": r.get("generated_utc"), "verdict": r.get("verdict"), "audit_hash": r.get("audit_hash"), "pdf": p.with_suffix(".pdf").exists()})
    return out
