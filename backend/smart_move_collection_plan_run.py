"""Read-only report for the collection plan: pagination comparison, stored-headline quality + labelling sheet, snapshot-ledger verification, budgets. Writes new files only; no provider request.

    python smart_move_collection_plan_run.py --out-dir <NEW folder>
"""
from __future__ import annotations

import argparse
import gzip
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import smart_move_collection_plan as CP  # noqa: E402
import smart_move_live as L  # noqa: E402
import smart_move_news_quality as NQ  # noqa: E402
import smart_move_recovery_run as RR  # noqa: E402
import smart_move_v2_snapshots as SN  # noqa: E402

RUN = Path(r"C:\Users\initi\SmartMoveRun")
PROD = RUN / "v2_runs" / "v2_20261101" / "store"
V1STORE = RUN / "out" / "run_20261101_full_universe" / "store"
VERIFY = [RUN / "news_verify_20261102" / "store", RUN / "news_verify_page1_20261102" / "store"]
V2R = RUN / "v2" / "v2r_run_20261102_research.json"
COMPANY = {"AAPL": "Apple Inc", "NVDA": "NVIDIA Corp", "MSFT": "Microsoft Corp", "CE": "Celanese Corp", "PLTR": "Palantir Technologies", "VRTX": "Vertex Pharmaceuticals", "WMB": "Williams Cos"}


def _jsonl(p: Path) -> list:
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()] if p.exists() else []


def _payload(root: Path, sha: str):
    f = root / "payloads" / sha[:2] / sha[2:4] / (sha + ".json.gz")
    return json.loads(gzip.open(f, "rt").read())


def pagination(store0: Path, store1: Path) -> dict:
    def page(store):
        r = [x for x in _jsonl(store / "v2_evidence.jsonl") if x["symbol"] == "MSFT" and x["kind"] == "uw_news"][0]
        return r, _payload(store, r["raw_sha256"])["data"]
    r0, p0 = page(store0)
    r1, p1 = page(store1)
    key = lambda h: (h["created_at"], h["source"], h["headline"].strip().lower())  # noqa: E731
    t0, t1 = sorted(h["created_at"] for h in p0), sorted(h["created_at"] for h in p1)
    return {"page0_rows": len(p0), "page1_rows": len(p1), "overlap": len({key(h) for h in p0} & {key(h) for h in p1}), "duplicates_inside_page1": len(p1) - len({key(h) for h in p1}),
            "page1_params": r1.get("params"), "page0_range": [t0[0], t0[-1]], "page1_range": [t1[0], t1[-1]], "page1_entirely_older_than_page0": t1[-1] <= t0[0],
            "page1_tagged_with_MSFT": sum("MSFT" in (h.get("tickers") or []) for h in p1), "page1_sentiment_values": sorted({str(h.get("sentiment")) for h in p1}),
            "retrieved_page0": r0["retrieved_at"], "retrieved_page1": r1["retrieved_at"]}


def stored_headlines() -> list:
    items = []
    for store in [PROD] + VERIFY:
        for r in _jsonl(store / "v2_evidence.jsonl"):
            if r["kind"] != "uw_news" or r["status"] != "OK":
                continue
            for h in _payload(store, r["raw_sha256"]).get("data") or []:
                items.append(dict(h, symbol=r["symbol"], company=COMPANY.get(r["symbol"]), retrieved_at=r["retrieved_at"]))
    return items


def snapshot_ledger() -> dict:
    events = json.loads(V2R.read_text(encoding="utf-8"))["events"]
    load = RR.make_loader([V1STORE, PROD])
    ev1 = {}
    for r in _jsonl(V1STORE / "evidence.jsonl"):
        if r.get("kind") == "earnings" and r.get("status", "OK") == "OK":
            ev1[r["symbol"]] = r
    quarters = {}
    for e in events:
        r = ev1.get(e["symbol"])
        body = load(r["raw_sha256"]) if r else None
        rows = (body or {}).get("data") if isinstance(body, dict) else None
        q = SN.ending_quarter(rows if isinstance(rows, list) else [], e["earnings_date"])
        if q:
            quarters[e["symbol"]] = q
    tmp = Path(tempfile.mkdtemp(prefix="snapshot_ledger_check_"))
    try:
        for n in ("v2_evidence.jsonl",):
            shutil.copy2(PROD / n, tmp / n)
        shutil.copytree(PROD / "payloads", tmp / "payloads")
        st = L.LiveStore(tmp)
        first = SN.rebuild(st, quarters)
        again = SN.rebuild(st, quarters)                                 # idempotency: a second rebuild adds nothing
        ledger = st.read(SN.LEDGER)
        keys = [(r["symbol"], r["period"], r["raw_sha256"]) for r in ledger]
        stamps = sorted(r["retrieved_at"] for r in ledger)
        per = {s: CP.revision_eligibility(ledger, s, q, datetime(2026, 11, 2, tzinfo=timezone.utc)) for s, q in quarters.items()}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"events": len(events), "stocks_with_exact_quarter": len(quarters), "first_rebuild": first, "second_rebuild": again, "ledger_rows": len(ledger), "duplicate_keys": len(keys) - len(set(keys)),
            "first_snapshot": stamps[0] if stamps else None, "last_snapshot": stamps[-1] if stamps else None, "snapshots_per_stock": sorted({sum(1 for r in ledger if r["symbol"] == s) for s in quarters}),
            "states": {k: sum(1 for v in per.values() if v["state"] == k) for k in ("NO_SNAPSHOT", "WAITING", "REVISION_MEASURABLE")}, "earliest_second_snapshot_useful": CP.revision_eligibility(ledger, next(iter(quarters)), quarters[next(iter(quarters))], datetime(2026, 11, 2, tzinfo=timezone.utc))["eligible_from"] if quarters else None,
            "note": "Computed on a temporary copy; the production store was not written. These quarters belong to events that already reported on 2-4 Nov, so they cannot support a pre-earnings revision for the NEXT report, whose fiscal quarter is different."}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    out = Path(a.out_dir)
    if out.exists():
        print("STOPPED: output folder exists")
        return 3
    out.mkdir(parents=True)
    prod_before = {f.name: f.stat().st_size for f in PROD.glob("*.jsonl")}
    rev = NQ.review(stored_headlines())
    sheet = NQ.labelling_sheet(rev["rows"], out / "news_labelling_sheet.csv")
    res = {"generated_utc": datetime.now(timezone.utc).isoformat(), "provider_requests": 0, "pagination": pagination(VERIFY[0], VERIFY[1]),
           "news_quality": {k: v for k, v in rev.items() if k != "rows"}, "labelling_sheet": str(sheet), "snapshot_ledger": snapshot_ledger(),
           "news_budget": {"observed_open_window_events": CP.news_budget(116), "listed_events_peak_day": CP.news_budget(330), "if_all_listed_peak": CP.news_budget(526)},
           "snapshot_budget": {"daily_for_800_tracked": CP.snapshot_budget(800), "daily_for_1200_tracked": CP.snapshot_budget(1200)},
           "production_store_unchanged": prod_before == {f.name: f.stat().st_size for f in PROD.glob("*.jsonl")}}
    (out / "collection_plan_report.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    print(out / "collection_plan_report.json", "production_store_unchanged =", res["production_store_unchanged"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
