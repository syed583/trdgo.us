"""Smart Move health, integrity and backup checks. Read-only over run folders (the backup writes only to a NEW destination folder). No provider, no scoring, no broker.

health(): kill switch, report/rows consistency, prediction-count reconciliation against the event registry, calendar age, alerts.
backup(): copies a run folder's reports and stores (not payload blobs' contents are altered) into a new dated folder with a SHA-256 manifest; verify_backup() re-hashes it.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

UTC = timezone.utc


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def health(run_dir, registry=None, *, now: datetime | None = None, calendar_age_h: float | None = None) -> dict:
    now = now or datetime.now(UTC)
    d = Path(run_dir)
    checks, ok = [], True

    def add(name, passed, detail):
        nonlocal ok
        ok = ok and passed
        checks.append({"check": name, "ok": bool(passed), "detail": detail})
    rep_p = d / "audit_report.json"
    try:
        rep = json.loads(rep_p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": False, "checks": [{"check": "report_readable", "ok": False, "detail": str(rep_p)}], "as_of": now.isoformat()}
    add("report_readable", True, rep.get("status"))
    add("kill_switch_engaged", (d / "store" / "KILL").exists(), "KILL file present" if (d / "store" / "KILL").exists() else "KILL file missing: collection could start")
    res = rep.get("results") or []
    syms = [r["symbol"] for r in res]
    add("no_duplicate_symbols", len(syms) == len(set(syms)), "%d results, %d unique" % (len(syms), len(set(syms))))
    add("universe_matches_results", rep.get("universe") in (None, len(res)) and not rep.get("not_evaluated"), "universe=%s results=%d not_evaluated=%d" % (rep.get("universe"), len(res), len(rep.get("not_evaluated") or [])))
    add("no_provider_stop", not rep.get("stop_events"), "%d stop events" % len(rep.get("stop_events") or []))
    add("every_result_has_capture_ts", all(r.get("capture_ts") for r in res), "")
    add("evidence_hashes_present", all((r.get("evidence") or []) and all(e.get("raw_sha256") for e in r["evidence"]) for r in res), "")
    if registry is not None:
        cur = registry.current()
        rec = registry.reconcile([{"symbol": r["symbol"], "earnings_date": str(r["earnings_date"])[:10]} for r in res])
        add("predictions_reconcile_with_registry", not rec["orphan"], "live=%d historical=%d orphan=%d, registry universe=%d, current without prediction=%s"
            % (len(rec["live"]), len(rec["historical"]), len(rec["orphan"]), len(cur), rec["current_without_prediction"]))
    if calendar_age_h is not None:
        add("calendar_fresh", calendar_age_h <= 5.0, "%.1f h old" % calendar_age_h)
    return {"ok": ok, "checks": checks, "as_of": now.isoformat(), "run": d.name}


def backup(run_dir, dest_root, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    src = Path(run_dir)
    dest = Path(dest_root) / ("%s_%s" % (src.name, now.strftime("%Y%m%dT%H%M%SZ")))
    if dest.exists():
        raise FileExistsError("backup folder exists; never overwritten: %s" % dest)
    manifest = {}
    for p in sorted(x for x in src.rglob("*") if x.is_file()):
        rel = p.relative_to(src)
        t = dest / rel
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, t)
        manifest[rel.as_posix()] = _sha(p)
    (dest / "MANIFEST.sha256.json").write_text(json.dumps({"source": str(src), "created_utc": now.isoformat(), "files": manifest}, indent=1), encoding="utf-8")
    return {"dest": str(dest), "files": len(manifest)}


def verify_backup(backup_dir) -> dict:
    b = Path(backup_dir)
    m = json.loads((b / "MANIFEST.sha256.json").read_text(encoding="utf-8"))
    bad = [rel for rel, h in m["files"].items() if not (b / rel).exists() or _sha(b / rel) != h]
    return {"ok": not bad, "files": len(m["files"]), "mismatched_or_missing": bad}
