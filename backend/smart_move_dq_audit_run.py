"""Runs the read-only data-quality audit on the saved V2 collection and writes ONE new JSON file (never overwrites, never touches a store).

    python smart_move_dq_audit_run.py --out-dir <folder>
Defaults point at the saved one-time V2 collection; every path can be overridden. No provider request is made.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import smart_move_dq_audit as DQ  # noqa: E402
import smart_move_recovery_run as RR  # noqa: E402

RUN = Path(r"C:\Users\initi\SmartMoveRun")
STORE = RUN / "v2_runs" / "v2_20261101" / "store"
V2R = RUN / "v2" / "v2r_run_20261102_research.json"
V1STORE = RUN / "out" / "run_20261101_full_universe" / "store"


def _jsonl(p: Path) -> list:
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _hash_tree(root: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(root.rglob("*.jsonl")):
        h.update(f.name.encode())
        h.update(f.read_bytes())
    return h.hexdigest()


def run(store: Path = STORE, v2r: Path = V2R, v1store: Path = V1STORE, recovery: Path | None = None, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    before = _hash_tree(store)
    events = json.loads(v2r.read_text(encoding="utf-8"))["events"]
    evidence = _jsonl(store / "v2_evidence.jsonl")
    load = RR.make_loader([store, v1store])
    companies = {}
    for r in _jsonl(store / "cal_obs.jsonl"):
        if r.get("company"):
            companies[r["symbol"]] = r["company"]
    rec = json.loads((recovery or sorted((RUN / "audits_recovery").glob("recovery_audit_*.json"))[-1]).read_text(encoding="utf-8"))
    rp = store.parent / "report.json"
    failed = json.loads(rp.read_text(encoding="utf-8")).get("failures", []) if rp.exists() else []
    snaps = [{"symbol": r["symbol"], "retrieved_at": r["retrieved_at"]} for r in evidence if r.get("kind") == "uw_estimates" and r.get("status") == "OK"]
    out = {"generated_utc": now.isoformat(), "read_only": True, "provider_requests": 0, "events": len(events),
           "flow": DQ.flow_audit(events, evidence, load), "news": DQ.news_audit(events, evidence, load, companies), "news_coverage": DQ.news_coverage(events, evidence, load, companies, failed), "root_causes": DQ.root_cause_table(rec),
           "analyst_readiness": DQ.analyst_readiness(snaps, [e["symbol"] for e in events], now)}
    out["store_unchanged"] = _hash_tree(store) == before
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    res = run()
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    f = out / ("dq_audit_%s.json" % datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    if f.exists():
        raise FileExistsError(f)
    f.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    print(f, "store_unchanged =", res["store_unchanged"])


if __name__ == "__main__":
    main()
