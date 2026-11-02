"""Build the REAL evidence-quality report from the saved Smart Move stores (offline, read only). Nothing is fetched; the source stores are only read (a before/after integrity manifest proves it); everything new is
written into ONE new output folder. Point-in-time: event records are reconstructed from the saved calendar rows at the times given below, each with only the earnings payloads retrieved by that time.

    python smart_move_real_quality_report.py --out <new folder>            (defaults point at the saved stores under C:\\Users\\initi\\SmartMoveRun)"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "docs" / "smart_move" / "shadow_impl"))
import smart_move_evidence_integrity as EI  # noqa: E402
import smart_move_event_records as ER  # noqa: E402
import smart_move_news_audit as NA  # noqa: E402
import smart_move_news_review as NR  # noqa: E402
import smart_move_outcome_audit as OA  # noqa: E402
import smart_move_phase13_audits as P13  # noqa: E402
import smart_move_quality_report as QR  # noqa: E402
from smvshadow.store import PayloadStore  # noqa: E402

UTC = timezone.utc
RUN = Path(r"C:\Users\initi\SmartMoveRun")
V1 = RUN / "out" / "run_20261101_full_universe" / "store"
PILOT = RUN / "pilot_store_20261102"
V2RUN = RUN / "v2_runs" / "v2_20261101" / "store"
NEWS_VERIFY = [RUN / "news_verify_20261102" / "store", RUN / "news_verify_page1_20261102" / "store"]
AS_OF = "2026-11-02T18:00:00+00:00"
V1_TIMES = ["2026-10-31T21:33:37+00:00", "2026-10-31T21:54:04+00:00", "2026-10-31T22:00:00+00:00"]
PILOT_TIMES = ["2026-11-02T16:14:30+00:00"]


def _jsonl(p) -> list:
    p = Path(p)
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8-sig").splitlines() if ln.strip()] if p.exists() else []


def build_records(store: Path, times: list, path: Path) -> "ER.EventRecords":
    """Reconstruct point-in-time event records: at each time, the saved calendar rows observed by then and the earnings payloads retrieved by then."""
    ps = PayloadStore(store)

    def load(sha):
        try:
            return json.loads(ps.get(sha))
        except Exception:  # noqa: BLE001
            return None
    cal, evid = _jsonl(store / "cal_obs.jsonl"), _jsonl(store / "evidence.jsonl") + _jsonl(store / "v2_evidence.jsonl")
    rs = ER.EventRecords(path)
    for t in times:
        t = datetime.fromisoformat(t)
        visible = [r for r in cal if datetime.fromisoformat(r["observed_at"]) <= t]
        rs.observe_snapshot(ER.records_from_saved(visible, t, ER.earnings_payloads(evid, load, t)), observed=t)
    return rs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    out = Path(a.out)
    if out.exists():
        print("refusing to overwrite", out)
        return 2
    sources = [V1, PILOT, V2RUN] + NEWS_VERIFY
    before = EI.build_manifest([s for s in sources if s.exists()])
    out.mkdir(parents=True)
    rsets = [build_records(V1, V1_TIMES, out / "event_records_v1.jsonl"), build_records(PILOT, PILOT_TIMES, out / "event_records_pilot.jsonl")]
    stores = [str(s) for s in sources if s.exists()]
    ps_all = [PayloadStore(s) for s in sources if s.exists()]

    def est_rows(sha):
        for ps in ps_all:
            try:
                body = json.loads(ps.get(sha))
            except Exception:  # noqa: BLE001
                continue
            d = body.get("data") if isinstance(body, dict) else None
            return d.get("estimates") if isinstance(d, dict) else d
        return None
    est = {}
    for s in sources:
        for r in _jsonl(Path(s) / "v2_evidence.jsonl"):
            if r.get("kind") == "uw_estimates":
                est[r["raw_sha256"]] = r
    news = NA.load_news_records(stores)
    preds = [p for p in _jsonl(V1 / "predictions.jsonl")]
    companies = P13.companies([V1, PILOT])
    ev_for_audit = [{"event_id": rs.as_of(i, AS_OF)["event_id"], "symbol": rs.as_of(i, AS_OF)["symbol"], "event_date": rs.as_of(i, AS_OF)["event_date"], "session": rs.as_of(i, AS_OF)["session"],
                     "cutoff_utc": rs.as_of(i, AS_OF)["cutoff_utc"], "ending_fiscal_quarter": rs.as_of(i, AS_OF)["ending_fiscal_quarter"], "quarter_state": rs.as_of(i, AS_OF)["quarter_state"],
                     "company": companies.get(rs.as_of(i, AS_OF)["symbol"])} for rs in rsets for i in rs.event_ids() if rs.as_of(i, AS_OF)]
    audit = NA.audit(ev_for_audit, news, companies)
    queue = NR.build_queue(audit, reviewable_only=False)
    store = NR.LabelStore()
    bundle = QR.build(rsets, news_records=news, companies=companies, bars_by_symbol=OA.load_bars([str(V1)]), predictions=preds, estimate_recs=list(est.values()), load_estimate_rows=est_rows, as_of=AS_OF,
                      source="SAVED_EVIDENCE", synthetic=False, review_by_event=NR.per_event_review(queue, store),
                      notes=["Built offline from the saved V1, pilot, V2-run and news-verification stores; nothing was fetched.",
                             "Event records are RECONSTRUCTED point-in-time from saved calendar rows (V1 at %s; pilot at %s)." % (", ".join(V1_TIMES), ", ".join(PILOT_TIMES)),
                             "Release times are the nominal 06:00 / 16:00 ET; none is verified.",
                             "Missing, unverified or ineligible evidence is never a positive or negative trading signal."])
    (out / "news_review").mkdir()
    (out / "news_review" / "queue.json").write_text(json.dumps(queue, indent=1, sort_keys=True), encoding="utf-8")
    QR.write(bundle, out / "quality_report.json")
    after = EI.build_manifest([s for s in sources if s.exists()])
    cmp_ = EI.compare(before, after)
    (out / "source_integrity.json").write_text(json.dumps({"ok": cmp_["ok"], "files_checked": len(before["files"]), "missing": cmp_["missing"], "changed": cmp_["changed"], "truncated": cmp_["truncated"],
                                                           "appended": cmp_["appended"]}, indent=1), encoding="utf-8")
    print(json.dumps({"events": bundle["summary"]["events"], "source_integrity_ok": cmp_["ok"], "files_checked": len(before["files"]), "queue_items": len(queue), "evidence_classes": bundle["summary"]["evidence_classes"],
                      "capture": bundle["summary"]["by_capture_status"], "predictions": bundle["summary"]["predictions"], "pairing": bundle["summary"]["by_pairing_state"],
                      "outcomes": bundle["summary"]["outcomes"]["by_state"], "news": bundle["summary"]["news"]["by_news_state"], "accuracy_pairs": bundle["accuracy"]["comparable_pairs"]}, indent=1))
    return 0 if cmp_["ok"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
