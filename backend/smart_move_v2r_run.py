"""Builds the v2r-1 research file from SAVED data only (no request): frozen V2 file + evidence audit + the collection store (flow alerts, headlines) + calendar company names.

    python smart_move_v2r_run.py --v2-file <v2_run_*.json> --evidence-audit <final evidence audit .json> --collect-store <v2_runs/../store> --out <NEW v2r_run_*.json>
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "docs" / "smart_move" / "shadow_impl"))
import smart_move_v2r as R  # noqa: E402
from smvshadow import cutoff as co  # noqa: E402


def build(v2_file, evidence_audit, collect_store) -> dict:
    store = Path(collect_store)

    def body(sha):
        return json.loads(gzip.open(store / "payloads" / sha[:2] / sha[2:4] / (sha + ".json.gz"), "rt").read())
    v2 = json.loads(Path(v2_file).read_text(encoding="utf-8"))
    ea = json.loads(Path(evidence_audit).read_text(encoding="utf-8"))
    stocks = {p["symbol"]: p for p in ea["per_stock"]}
    flow, news, company = {}, {}, {}
    for ln in (store / "v2_evidence.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(ln)
        if r["status"] == "OK" and r["kind"] == "uw_flow_alerts":
            flow[r["symbol"]] = r
        elif r["status"] == "OK" and r["kind"] == "uw_news":
            news[r["symbol"]] = r
    for ln in (store / "cal_obs.jsonl").read_text(encoding="utf-8").splitlines():
        o = json.loads(ln)
        if o.get("company"):
            company[o["symbol"]] = o["company"]
    events = []
    for e in v2["events"]:
        st = stocks.get(e["symbol"])
        if st is None:
            continue
        cap = datetime.fromisoformat(st["scored_as_of"])
        kind = e.get("kind")
        try:
            release = co.plan_event(date.fromisoformat(str(e["earnings_date"])[:10]), kind)["assumed_release_utc"] if kind in ("BMO", "AMC") else None
        except Exception:  # noqa: BLE001
            release = None
        alerts = (body(flow[e["symbol"]]["raw_sha256"]).get("data") or []) if e["symbol"] in flow else None
        heads = (body(news[e["symbol"]]["raw_sha256"]).get("data") or []) if e["symbol"] in news else None
        res = R.evaluate(st, e, alerts, heads, company.get(e["symbol"]), cap, release, str(e["earnings_date"])[:10])
        events.append({"symbol": e["symbol"], "earnings_date": e["earnings_date"], "kind": kind, "scored_as_of": st["scored_as_of"], "eligibility": e.get("eligibility"), "v1_original": e.get("v1_original"), "v2r": res,
                       "frozen_v2": {v: e["v2"][v]["signal"] for v in ("continuation", "contrarian")}})
    body_ = {"model": "V2R", "version": R.VERSION, "status": R.STATUS, "config_hash": R.CONFIG_HASH, "base_v2": R.V2.VERSION, "base_v2_hash": R.V2.CONFIG_HASH, "source_v2_file": Path(v2_file).name, "events": events,
             "summary": R.summarize(events), "scoring_changes": [], "factor_redistribution": [], "accuracy": "NOT VERIFIED: no actual outcomes exist; no accuracy percentage is shown", "usable_for_decisions": False}
    body_["audit_hash"] = hashlib.sha256(json.dumps({k: body_[k] for k in ("version", "config_hash", "source_v2_file", "events")}, sort_keys=True, default=str).encode()).hexdigest()
    return body_


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--v2-file", required=True)
    ap.add_argument("--evidence-audit", required=True)
    ap.add_argument("--collect-store", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    out = Path(a.out)
    if out.exists():
        print("STOPPED: output exists; never overwritten")
        return 3
    b = build(a.v2_file, a.evidence_audit, a.collect_store)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(b, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"events": len(b["events"]), **b["summary"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
