"""Generate the recovery audit (JSON + two-page PDF) from saved data only.

    python smart_move_recovery_run.py --evidence-audit f --v2r-file f --v1-report f --collect-store d --extra-payloads d [--extra-payloads d2 ...] --out-dir d [--tests-line text]
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import smart_move_recovery as R  # noqa: E402
import smart_move_recovery_pdf as PDF  # noqa: E402


def make_loader(roots):
    def load(sha):
        for root in roots:
            f = Path(root) / "payloads" / sha[:2] / sha[2:4] / (sha + ".json.gz")
            if f.exists():
                try:
                    return json.loads(gzip.open(f, "rt").read())
                except (OSError, ValueError):
                    return None                                    # e.g. the Finviz CSV body
        return None
    return load


def generate(evidence_audit, v2r_file, v1_report, collect_store, extra_roots, out_dir, now=None, tests_line="") -> dict:
    now = now or datetime.now(timezone.utc)
    store = Path(collect_store)
    roots = [store] + [Path(x) for x in extra_roots]
    cal_obs = [json.loads(ln) for ln in (store / "cal_obs.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    rec = R.build(evidence_audit, v2r_file, v1_report, make_loader(roots), cal_obs, now)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    base = out / ("recovery_audit_%s" % now.strftime("%Y%m%dT%H%M%SZ"))
    if base.with_suffix(".json").exists():
        raise FileExistsError(base)
    base.with_suffix(".json").write_text(json.dumps(rec, indent=1, default=str), encoding="utf-8")
    try:
        pdf = PDF.render(rec, base.with_suffix(".pdf"), tests_line)
    except Exception:
        base.with_suffix(".json").unlink(missing_ok=True)
        raise
    return {"json": str(base.with_suffix(".json")), **pdf, "audit_hash": rec["audit_hash"], "recovered_factors": rec["recovery"]["recovered_factors"], "stocks": rec["stocks"]}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence-audit", required=True)
    ap.add_argument("--v2r-file", required=True)
    ap.add_argument("--v1-report", required=True)
    ap.add_argument("--collect-store", required=True)
    ap.add_argument("--extra-payloads", action="append", default=[])
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--tests-line", default="")
    a = ap.parse_args(argv)
    print(json.dumps(generate(a.evidence_audit, a.v2r_file, a.v1_report, a.collect_store, a.extra_payloads, a.out_dir, tests_line=a.tests_line), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
