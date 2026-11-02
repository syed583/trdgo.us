"""Offline driver for the Phase 13 news-quality and outcome-validation audits. Reads saved event records, news, bars and predictions; writes JSON reports into --out (a new folder). No request is made.
    python smart_move_phase13_audits.py --events <event_records.jsonl>... --stores <store dir>... --cal-stores <store dir>... --out <dir> --as-of <ISO>"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import smart_move_news_audit as NA  # noqa: E402
import smart_move_outcome_audit as OA  # noqa: E402


def latest_events(paths) -> list:
    last: dict = {}
    for p in paths:
        for ln in Path(p).read_text(encoding="utf-8").splitlines():
            if ln.strip():
                r = json.loads(ln)
                k = (r["event_id"], p)
                if k not in last or r["version"] > last[k]["version"] or (r["version"] == last[k]["version"] and r["status"] != "CURRENT"):
                    last[k] = r
    return [{"event_id": r["event_id"], "symbol": r["symbol"], "event_date": r["event_date"], "session": r["session"], "cutoff_utc": r["cutoff_utc"], "window_start_utc": r["window_start_utc"],
             "ending_fiscal_quarter": r["ending_fiscal_quarter"], "quarter_state": r["quarter_state"], "version": r["version"], "status": r["status"], "vendors": r.get("vendors") or []} for r in last.values()]


def companies(store_dirs) -> dict:
    out = {}
    for d in store_dirs:
        f = Path(d) / "cal_obs.jsonl"
        if f.exists():
            for ln in f.read_text(encoding="utf-8-sig").splitlines():
                if ln.strip():
                    r = json.loads(ln)
                    if r.get("company"):
                        out[r["symbol"]] = r["company"]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", nargs="+", required=True)
    ap.add_argument("--stores", nargs="+", required=True, help="stores holding news / bars / predictions")
    ap.add_argument("--cal-stores", nargs="+", required=True, help="stores holding cal_obs (company names)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--as-of", required=True)
    a = ap.parse_args(argv)
    out = Path(a.out)
    if out.exists():
        print("refusing to overwrite", out)
        return 2
    out.mkdir(parents=True)
    ev = latest_events(a.events)
    news = NA.audit(ev, NA.load_news_records(a.stores), companies(a.cal_stores))
    outc = OA.audit(ev, OA.load_bars(a.stores), OA.load_predictions(a.stores), a.as_of)
    rules = OA.rule_audit()
    for name, obj in (("news_quality_audit.json", news), ("outcome_validation_audit.json", outc), ("outcome_rule_audit.json", rules)):
        (out / name).write_text(json.dumps(obj, indent=1, sort_keys=True, default=str), encoding="utf-8")
    print(json.dumps({"news": news["summary"], "outcomes": outc["summary"], "rule_audit_passed": rules["passed"], "session_mismatches": len(rules["session_mismatches"])}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
