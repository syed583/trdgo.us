"""Engine20 prediction snapshots: verification against the saved evidence, an immutable historical snapshot, and the outcome comparison. Offline; no provider code and NO scoring of its own.

* verify(): recomputes every row with the FROZEN engine (smart_move_engine20.report -> LiveScorer, score40-v1) on a COPY of the store at the report's own instant and compares field by field; checks that the evidence rows, their payload hashes and
  the request ledger agree. It reads the store and writes nothing into it.
* build_snapshot(): writes one immutable file per generation (never overwrites) plus an append-only index line. A snapshot is a HISTORICAL RECORD with its generation timestamp, never a live feed.
* read_status(): at read time says whether the prediction window is still open; a snapshot is never labelled current/live.
* outcomes(): joins the snapshot with independently verified outcome records (smart_move_outcomes). Without a GRADED record there is no comparison: nothing is estimated or inferred."""
from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

UTC = timezone.utc
SCHEMA = "smart_move.engine20_snapshot.v1"
INDEX = "snapshots.jsonl"
CATEGORY = {"BUY": "BUY", "SELL": "SELL", "NO TRADE (genuine)": "NO_TRADE", "NO VALID PREDICTION": "NO_VALID_PREDICTION", "PENDING FINAL REFRESH": "PENDING_REFRESH"}
CATEGORY_TEXT = {"BUY": "BUY", "SELL": "SELL", "NO_TRADE": "NO TRADE (genuine: all gates passed, margin too small)", "NO_VALID_PREDICTION": "NO VALID PREDICTION (data missing or insufficient - not a neutral view)",
                 "PENDING_REFRESH": "PENDING FINAL REFRESH (evidence stale)"}
COMPARE_FIELDS = ("result", "bull", "bear", "coverage_points", "coverage_max", "liquidity", "window", "reason_codes", "missing_factors")


def _dt(x) -> datetime:
    d = x if isinstance(x, datetime) else datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _jl(p) -> list:
    p = Path(p)
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


# ------------------------------------------------------------------ verification
def verify(report_path, store_dir, state_dir, *, run_day_prefix: str) -> dict:
    """Independent audit. run_day_prefix, e.g. '2026-11-03T08:2', selects this run's ledger/evidence lines. Everything is read-only on the real store (the recomputation runs on a temporary copy)."""
    import smart_move_engine20 as E20
    import smart_move_live as L
    from smvshadow import governor as gv

    rep = json.loads(Path(report_path).read_text(encoding="utf-8"))
    as_of = _dt(rep["as_of"])
    store_dir, state_dir = Path(store_dir), Path(state_dir)
    checks, problems = {}, []

    # 1. recomputation with the frozen engine on a copy
    tmp = Path(tempfile.mkdtemp(prefix="e20verify_"))
    try:
        cp = tmp / "store"
        shutil.copytree(store_dir, cp)
        cfg = L.LiveConfig(mode="SIMULATION", store_dir=cp, refresh_enabled=False, authorized_daily=0, spacing_s=0.0)
        svc = L.RefreshService(cfg, L.Fetcher() if hasattr(L, "Fetcher") else None, gv.FakeClock(as_of))
        caps = {}
        for o in svc.store.read("cal_obs"):
            try:
                caps[o["symbol"]] = float(o["marketcap"])
            except (KeyError, TypeError, ValueError):
                pass
        scored = L.LiveScorer(svc).score_all(as_of)
        order = [r["symbol"] for r in rep["rows"]]
        selected = E20.select_top(scored, caps, as_of, n=len(order), event_date=rep["rows"][0]["event_date"], session=rep["rows"][0]["session"], only=order)
        again = E20.report(svc, selected, as_of)
        mism = []
        by = {r["symbol"]: r for r in again["rows"]}
        for r in rep["rows"]:
            o = by.get(r["symbol"])
            if o is None:
                mism.append((r["symbol"], "missing in recomputation"))
                continue
            for f in COMPARE_FIELDS:
                if json.dumps(r.get(f), sort_keys=True, default=str) != json.dumps(o.get(f), sort_keys=True, default=str):          # JSON round trip turns tuples into lists; compare content
                    mism.append((r["symbol"], f, r.get(f), o.get(f)))
        checks["recomputed_symbols"] = len(again["rows"])
        checks["recomputed_counts"] = again["counts"]
        checks["counts_match_report"] = again["counts"] == rep["counts"]
        checks["field_mismatches"] = mism
        if mism or not checks["counts_match_report"]:
            problems.append("RECOMPUTATION_DIFFERS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 2. ledger, attempts and evidence
    req = [r for r in _jl(store_dir / "requests.jsonl") if r["at"].startswith(run_day_prefix)]
    att = _jl(state_dir / "attempts.jsonl")
    ev = [e for e in _jl(store_dir / "evidence.jsonl") if str(e.get("retrieved_at", "")).startswith(run_day_prefix)]
    ids = [r["request_id"] for r in _jl(store_dir / "requests.jsonl")]
    checks.update(ledger_lines_total=len(ids), ledger_duplicate_ids=len(ids) - len(set(ids)), run_requests=len(req), run_attempts=len(att), run_attempts_ok=sum(1 for a in att if a.get("result") == "OK"),
                  run_evidence_rows=len(ev))
    if len(req) != len(att) or len(ids) != len(set(ids)) or len(ev) != checks["run_attempts_ok"]:
        problems.append("LEDGER_ATTEMPTS_EVIDENCE_DISAGREE")
    bad = 0
    for e in ev:
        sha = e["raw_sha256"]
        p = store_dir / "payloads" / sha[:2] / sha[2:4] / (sha + ".json.gz")
        try:
            if hashlib.sha256(gzip.open(p).read()).hexdigest() != sha:
                bad += 1
        except OSError:
            bad += 1
    checks["payload_hash_failures"] = bad
    if bad:
        problems.append("PAYLOAD_HASH_FAILURE")

    # 3. the report's evidence timestamps exist in the evidence
    stamps = {e["retrieved_at"] for e in _jl(store_dir / "evidence.jsonl")}
    unknown = [(r["symbol"], t) for r in rep["rows"] for t in (r.get("evidence_retrieved") or []) if t not in stamps]
    checks["evidence_timestamps_unknown"] = unknown
    if unknown:
        problems.append("REPORT_TIMESTAMP_NOT_IN_EVIDENCE")
    checks["kill_switch_engaged"] = (store_dir / "KILL").exists()
    if not checks["kill_switch_engaged"]:
        problems.append("KILL_SWITCH_NOT_ENGAGED")
    return {"verdict": "PASS" if not problems else "FAIL", "problems": problems, "checks": checks, "report_sha256": _sha(report_path), "ledger_sha256": _sha(store_dir / "requests.jsonl"), "verified_at": datetime.now(UTC).isoformat()}


# ------------------------------------------------------------------ snapshot
def _row(r: dict) -> dict:
    cat = CATEGORY.get(r["result"])
    if cat is None:
        raise ValueError("unknown result %r" % r["result"])
    return {"symbol": r["symbol"], "company": r.get("company"), "event_date": r["event_date"], "session": r["session"], "category": cat, "category_text": CATEGORY_TEXT[cat], "reason": r.get("reason"),
            "reason_codes": r.get("reason_codes") or [], "bull": r.get("bull"), "bear": r.get("bear"), "coverage_points": r.get("coverage_points"), "coverage_max": r.get("coverage_max"),
            "liquidity": r.get("liquidity"), "adv20_usd": r.get("adv20_usd"), "missing_factors": r.get("missing_factors") or [], "window": r.get("window"), "window_cutoff_utc": r.get("window_cutoff_utc"),
            "evidence_retrieved": r.get("evidence_retrieved") or [], "model_version": r.get("model_version")}


def build_snapshot(report_path, audit: dict, out_dir, *, source: str) -> Path:
    """One immutable snapshot file per generation. Refuses to write when the audit did not PASS, and never overwrites."""
    if audit.get("verdict") != "PASS":
        raise ValueError("audit did not PASS: %s" % audit.get("problems"))
    rep = json.loads(Path(report_path).read_text(encoding="utf-8"))
    rows = [_row(r) for r in rep["rows"]]
    snap = {"schema": SCHEMA, "kind": "HISTORICAL_SNAPSHOT", "generated_at": rep["as_of"], "model_version": rep["model_version"], "counts": {k: sum(1 for r in rows if r["category"] == k) for k in CATEGORY_TEXT},
            "rows": rows, "source": source, "audit": audit, "notes": ["A historical snapshot of the frozen engine's output at its generation time; not a live signal.", "NO TRADE means every gate passed and the margin was too small; "
                                                                                                                             "NO VALID PREDICTION means data was missing or insufficient and is never a neutral view."]}
    body = json.dumps(snap, sort_keys=True, indent=1).encode("utf-8")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    f = out / ("engine20_snapshot_%s_%s.json" % (_dt(rep["as_of"]).strftime("%Y%m%dT%H%M%SZ"), hashlib.sha256(body).hexdigest()[:8]))
    if f.exists():
        raise FileExistsError(f)
    f.write_bytes(body)
    with open(out / INDEX, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"file": f.name, "generated_at": rep["as_of"], "sha256": hashlib.sha256(body).hexdigest(), "counts": snap["counts"]}) + "\n")
    return f


# ------------------------------------------------------------------ read time
def load_latest(snap_dir) -> Optional[dict]:
    idx = _jl(Path(snap_dir) / INDEX)
    if not idx:
        return None
    last = sorted(idx, key=lambda i: i["generated_at"])[-1]
    p = Path(snap_dir) / last["file"]
    if not p.is_file() or _sha(p) != last["sha256"]:
        return {"error": "SNAPSHOT_FILE_MISSING_OR_ALTERED", "file": last["file"]}
    return json.loads(p.read_text(encoding="utf-8"))


def read_status(snap: dict, now) -> dict:
    """Read-time labels. The snapshot is always a historical record; the window flag says only whether its prediction window is still open."""
    now = _dt(now)
    gen = _dt(snap["generated_at"])
    rows = []
    for r in snap["rows"]:
        cut = _dt(r["window_cutoff_utc"]) if r.get("window_cutoff_utc") else None
        ev_times = [_dt(t) for t in r["evidence_retrieved"]]
        rows.append(dict(r, window_open_now=bool(cut and now <= cut), evidence_age_h=round((now - max(ev_times)).total_seconds() / 3600, 2) if ev_times else None,
                         snapshot_label="Historical snapshot generated %s UTC" % gen.strftime("%Y-%m-%d %H:%M")))
    return dict(snap, rows=rows, read_at=now.isoformat(), snapshot_age_h=round((now - gen).total_seconds() / 3600, 2), is_live_signal=False,
                window_open_now=any(r["window_open_now"] for r in rows))


def outcomes(snap: dict, track_dir, now) -> dict:
    """Per row: the outcome state and, only when an independently verified outcome is GRADED, the comparison. BUY/SELL are compared with the sign of the move; NO TRADE and NO VALID PREDICTION are never scored as right or wrong."""
    import smart_move_outcomes as O
    now = _dt(now)
    events = [{"symbol": r["symbol"], "earnings_date": r["event_date"], "kind": r["session"]} for r in snap["rows"]]
    if not track_dir:
        return {"available": False, "detail": "No outcome store is configured (SMART_MOVE_OUTCOMES_DIR): nothing can be compared yet.", "rows": []}
    ready = O.readiness(events, track_dir, now)["by_event"]
    graded = {(o["symbol"], o["earnings_date"]): o for o in O.latest(track_dir) if o.get("status") == "GRADED"}
    out = []
    for r in snap["rows"]:
        st = ready["%s|%s" % (r["symbol"], r["event_date"])]["state"]
        o = graded.get((r["symbol"], r["event_date"]))
        cmp_ = "NOT_AVAILABLE"
        if o is not None and r["category"] in ("BUY", "SELL") and o.get("move_pct") is not None:
            up = o["move_pct"] > 0
            cmp_ = "CORRECT" if (r["category"] == "BUY") == up and o["move_pct"] != 0 else "INCORRECT"
        elif o is not None:
            cmp_ = "NOT_APPLICABLE"
        out.append({"symbol": r["symbol"], "event_date": r["event_date"], "session": r["session"], "category": r["category"], "outcome_state": st, "move_pct": o.get("move_pct") if o else None,
                    "comparison": cmp_, "earliest_fetch_utc": ready["%s|%s" % (r["symbol"], r["event_date"])].get("earliest_fetch_utc")})
    tally = {k: sum(1 for x in out if x["comparison"] == k) for k in ("CORRECT", "INCORRECT", "NOT_APPLICABLE", "NOT_AVAILABLE")}
    return {"available": True, "rows": out, "tally": tally, "note": "Only independently verified GRADED outcomes are compared; a pending or unverified outcome is never guessed."}
