"""Generate one audit (JSON + two-page PDF) from saved data. Local and read-only over the run folders; writes only NEW files in the audit folder.

    python smart_move_audit_run.py --run-root <out> --audit-root <audit folder> [--v2-file f] [--tests-file f] [--backup-root d] [--now ISO]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import smart_move_audit as A  # noqa: E402
import smart_move_audit_pdf as PDF  # noqa: E402

DEFAULT_ROOT = r"C:\Users\initi\SmartMoveRun"


def generate(run_root, audit_root, *, now=None, v2_file=None, tests_file=None, backup_root=None, saved_rows=None, plan_fn=None) -> dict:
    now = now or datetime.now(timezone.utc)
    if saved_rows is None:
        import smart_move_routes as R
        saved_rows = R.current_rows()
        try:
            plan_fn = plan_fn or (lambda d, k: R._engine()[3].plan_event(datetime.fromisoformat(d).date(), k))
        except Exception:  # noqa: BLE001
            plan_fn = None
    tests = json.loads(Path(tests_file).read_text(encoding="utf-8")) if tests_file and Path(tests_file).exists() else None
    import smart_move_tracking_routes as TR
    track = Path(TR.DEFAULT_TRACK_DIR)
    import smart_move_outcomes as O
    rec = A.build(run_root, now=now, v2_file=v2_file, tests=tests, backup_root=backup_root, saved_rows=saved_rows, plan_fn=plan_fn, outcomes=O.latest(track) if track.exists() else [])
    if rec.get("ok") is False:
        return rec
    saved = A.save(rec, audit_root)
    pdf = PDF.render(rec, saved["base"] + ".pdf")
    return {"ok": True, "run_id": rec["run_id"], "verdict": rec["verdict"], "audit_hash": rec["audit_hash"], **saved, **pdf}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-root", default=DEFAULT_ROOT + r"\out")
    ap.add_argument("--audit-root", default=DEFAULT_ROOT + r"\audits")
    ap.add_argument("--v2-file")
    ap.add_argument("--tests-file")
    ap.add_argument("--backup-root")
    ap.add_argument("--now")
    a = ap.parse_args(argv)
    out = generate(a.run_root, a.audit_root, now=datetime.fromisoformat(a.now) if a.now else None, v2_file=a.v2_file, tests_file=a.tests_file, backup_root=a.backup_root)
    print(json.dumps(out, indent=1, default=str))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
