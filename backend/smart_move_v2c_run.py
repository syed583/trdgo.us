"""Builds the V2 candidate file from saved results only (no request): the frozen V2 per-stock file + the evidence audit's per-stock causes.

    python smart_move_v2c_run.py --v2-file <v2_run_*.json> --evidence-audit <interim/final evidence audit .json> --out <NEW v2c_run_*.json>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import smart_move_v2c as C  # noqa: E402


def build(v2_file, evidence_audit) -> dict:
    v2 = json.loads(Path(v2_file).read_text(encoding="utf-8"))
    ea = json.loads(Path(evidence_audit).read_text(encoding="utf-8"))
    stocks = {p["symbol"]: p for p in ea["per_stock"]}
    events = []
    for e in v2["events"]:
        st = stocks.get(e["symbol"])
        if st is None:
            continue
        res = {v: C.classify(e["v2"][v], st) for v in ("continuation", "contrarian")}
        events.append({"symbol": e["symbol"], "earnings_date": e["earnings_date"], "capture_ts": e.get("capture_ts"), "scored_as_of": st.get("scored_as_of"), "eligibility": e.get("eligibility"),
                       "evidence": e.get("evidence"), "v2c": res, "v1_original": e.get("v1_original")})
    summ = C.summarize([e["v2c"] for e in events])
    frozen = {v: {} for v in ("continuation", "contrarian")}
    for e in v2["events"]:
        if e["symbol"] in stocks:
            for v in frozen:
                k = e["v2"][v].get("display_status", e["v2"][v]["signal"])
                frozen[v][k] = frozen[v].get(k, 0) + 1
    body = {"model": "V2C", "version": C.VERSION, "status": C.STATUS, "config_hash": C.CONFIG_HASH, "base_version": C.V2.VERSION, "base_config_hash": C.V2.CONFIG_HASH, "source_v2_file": Path(v2_file).name,
            "events": events, "summary": summ, "frozen_v2_counts": frozen, "accuracy": "NOT VERIFIED: no actual outcomes exist; no accuracy percentage is shown",
            "changes": C.CONFIG["status_logic"], "scoring_changes": C.CONFIG["scoring_changes"], "factor_redistribution": C.CONFIG["factor_redistribution"]}
    body["audit_hash"] = hashlib.sha256(json.dumps({k: body[k] for k in ("version", "config_hash", "source_v2_file", "events")}, sort_keys=True, default=str).encode()).hexdigest()
    return body


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--v2-file", required=True)
    ap.add_argument("--evidence-audit", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    out = Path(a.out)
    if out.exists():
        print("STOPPED: output exists; never overwritten")
        return 3
    body = build(a.v2_file, a.evidence_audit)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(body, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"events": len(body["events"]), "summary": body["summary"], "frozen_v2": body["frozen_v2_counts"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
