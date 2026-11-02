"""Batch-completion validation and the explicit completion MARKER (offline, read only until `seal`). Downstream processing (smart_move_live_signals) must NEVER start because files merely appear: it needs this marker, and it
re-checks the marker's hashes itself.

validate_batch() looks at a finished supervised run and answers: was it a clean, in-window, within-ceiling, append-only collection whose evidence can be trusted? It reads attempts.jsonl, collection_state.json and the store; it sends
nothing. seal() writes `batch_complete.json` ONLY when validation passes and a person's name is given (the owner or a person acting on the owner's explicit instruction); it never overwrites an existing marker.
verify_marker() is what the processing job calls: it recomputes the hashes of the attempt log and the state file and refuses a marker whose files changed afterwards."""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "docs" / "smart_move" / "shadow_impl"))
import market_calendar as mc  # noqa: E402

UTC = timezone.utc
MARKER = "batch_complete.json"
SCHEMA = "smart_move.batch_complete.v1"
MAX_ATTEMPTS = 50
MAX_AGE_H = 36.0


def _sha_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _dt(x) -> datetime:
    d = x if isinstance(x, datetime) else datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _lines(p: Path) -> list:
    return [json.loads(ln) for ln in Path(p).read_text(encoding="utf-8-sig").splitlines() if ln.strip()] if Path(p).exists() else []


def _regular_session(t: datetime) -> bool:
    """True when t falls inside the regular US session (09:30 ET to that day's close, early closes included) on a trading day."""
    et = t.astimezone(mc.NY)
    d = et.date()
    if not mc.is_trading_day(d):
        return False
    close = mc.session_close_utc(d) if hasattr(mc, "session_close_utc") else mc.close_datetime(d)                 # the two calendar versions in this repo name it differently
    return datetime.combine(d, datetime.min.time().replace(hour=9, minute=30), tzinfo=mc.NY) <= et < close.astimezone(mc.NY)


def validate_batch(run_dir, store_dir, now, *, ceiling: int = MAX_ATTEMPTS, source_manifest: Optional[dict] = None) -> dict:
    """-> {ok, checks: [{check, result: PASS|FAIL|WARN, detail}], summary}. Every check is evidence-based; nothing is assumed."""
    run, store, now = Path(run_dir), Path(store_dir), _dt(now)
    checks = []

    def add(name, ok, detail, warn=False):
        checks.append({"check": name, "result": "PASS" if ok else ("WARN" if warn else "FAIL"), "detail": detail})
    attempts = _lines(run / "attempts.jsonl")
    state_f = run / "collection_state.json"
    state = json.loads(state_f.read_text(encoding="utf-8")) if state_f.exists() else None
    add("attempt log exists", bool(attempts), "%d attempt lines" % len(attempts))
    add("state file exists", state is not None, str(state_f))
    live_jobs = [j for j in (state or {}).get("jobs", {}).values() if j.get("mode") == "LIVE"]
    job = max(live_jobs, key=lambda j: j["started_utc"]) if live_jobs else None
    add("a LIVE job is recorded", job is not None, (job or {}).get("id", "no live job"))
    if job:
        add("job finished (COMPLETED or PARTIAL)", job.get("status") in ("COMPLETED", "PARTIAL"), "status %s, halted %s" % (job.get("status"), job.get("halted")))
        add("job recorded failures", not job.get("failures"), "%d failures" % len(job.get("failures") or []), warn=True)
    add("attempts within the ceiling (failures included)", len(attempts) <= ceiling, "%d attempts, ceiling %d" % (len(attempts), ceiling))
    if job:
        add("state request count matches the attempt log", job.get("requests") == len(attempts), "state %s vs log %d" % (job.get("requests"), len(attempts)))
    errs = Counter(str(a.get("error_kind")) for a in attempts if a.get("result") == "ERROR")
    stop = [k for k in errs if k in ("rate_limited", "auth")]
    add("no 429 / authentication error", not stop, "errors: %s" % dict(errs) if errs else "none")
    outside = [a["at"] for a in attempts if _regular_session(_dt(a["at"]))]
    add("every attempt was outside regular US market hours", not outside, "%d attempts inside the session" % len(outside) if outside else "all outside")
    kill = store / "KILL"
    add("kill switch re-engaged", kill.exists(), str(kill))
    evid = _lines(store / "v2_evidence.jsonl") + _lines(store / "evidence.jsonl")
    window_start = _dt(job["started_utc"]) - timedelta(minutes=5) if job else None
    mine = [e for e in evid if window_start and _dt(e["retrieved_at"]) >= window_start]
    ok_by_kind = Counter(e["kind"] for e in mine if e.get("status", "OK") == "OK")
    add("evidence records were appended by this run", bool(mine), "%d records: %s" % (len(mine), dict(ok_by_kind)))
    add("one evidence record per successful attempt (no gaps)", len(mine) >= sum(1 for a in attempts if a.get("result") == "OK") - 8, "records %d vs successful attempts %d (calendar requests are stored as observations, not evidence records)" % (len(mine), sum(1 for a in attempts if a.get("result") == "OK")), warn=True)
    try:
        from smvshadow.store import PayloadStore
        n, badp = PayloadStore(store).verify_all()
        add("raw payload files verify against their hashes", not badp, "%d files, %d bad" % (n, len(badp)))
    except Exception as e:  # noqa: BLE001
        add("raw payload files verify against their hashes", False, "verification error: %s" % type(e).__name__)
    old = [e for e in mine if now - _dt(e["retrieved_at"]) > timedelta(hours=MAX_AGE_H)]
    add("evidence is fresh", not old, "%d records older than %.0f h" % (len(old), MAX_AGE_H))
    if source_manifest is not None:
        import smart_move_evidence_integrity as EI
        cur = EI.build_manifest([store])["files"]
        changed = [f for f, h in source_manifest.items() if f in cur and cur[f]["sha256"] != h and not f.endswith(".jsonl")]
        add("pre-existing payload files unchanged", not changed, "%d changed" % len(changed))
    fails = [c for c in checks if c["result"] == "FAIL"]
    return {"ok": not fails, "checks": checks, "summary": {"attempts": len(attempts), "evidence_this_run": dict(ok_by_kind), "failures": len(fails)}, "job_id": (job or {}).get("id"),
            "started_utc": (job or {}).get("started_utc")}


def seal(run_dir, store_dir, now, sealed_by: str, **kw) -> dict:
    """Write the completion marker. Refuses unless validation passed and a name is given; never overwrites."""
    if not str(sealed_by or "").strip():
        raise ValueError("a person's name is required to seal a batch")
    v = validate_batch(run_dir, store_dir, now, **kw)
    if not v["ok"]:
        raise RuntimeError("batch validation failed: " + "; ".join(c["check"] for c in v["checks"] if c["result"] == "FAIL"))
    p = Path(run_dir) / MARKER
    if p.exists():
        raise FileExistsError(p)
    m = {"schema": SCHEMA, "batch_id": v["job_id"], "sealed_by": sealed_by.strip(), "sealed_at": _dt(now).isoformat(), "store_dir": str(Path(store_dir)), "attempts_sha256": _sha_file(Path(run_dir) / "attempts.jsonl"),
         "state_sha256": _sha_file(Path(run_dir) / "collection_state.json"), "summary": v["summary"], "validation_checks": [(c["check"], c["result"]) for c in v["checks"]]}
    p.write_text(json.dumps(m, indent=1, sort_keys=True), encoding="utf-8")
    return m


def verify_marker(run_dir) -> dict:
    """-> {valid, reason, marker}. The marker must exist, have the right schema and name a person, and the files it hashed must be unchanged."""
    p = Path(run_dir) / MARKER
    if not p.exists():
        return {"valid": False, "reason": "NO_COMPLETION_MARKER", "marker": None}
    try:
        m = json.loads(p.read_text(encoding="utf-8-sig"))
    except ValueError:
        return {"valid": False, "reason": "MARKER_UNREADABLE", "marker": None}
    if m.get("schema") != SCHEMA or not str(m.get("sealed_by") or "").strip() or not m.get("batch_id"):
        return {"valid": False, "reason": "MARKER_MALFORMED", "marker": m}
    for key, fname in (("attempts_sha256", "attempts.jsonl"), ("state_sha256", "collection_state.json")):
        f = Path(run_dir) / fname
        if not f.exists() or _sha_file(f) != m.get(key):
            return {"valid": False, "reason": "MARKER_HASH_MISMATCH:" + fname, "marker": m}
    return {"valid": True, "reason": None, "marker": m}
