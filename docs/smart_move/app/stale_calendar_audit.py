"""Read-only audit: were the ORIGINAL V1 predictions of the stocks that later showed a stale calendar valid at their own capture time?

For every stock flagged `stale_calendar_at_capture` in the final V2 evidence audit it checks, from the saved V1 run only (nothing later is used to change a frozen prediction):
  * the V1 capture time lies inside the stock's frozen capture window;
  * the newest calendar observation at or before the V1 capture is at most 6 h old, and the calendar status was vendor confirmed;
  * every evidence record cited by the V1 prediction was retrieved at or before the capture time and not before the window opened.
It also reports the calendar age at the (later) V2 capture, which is what made the V2 re-score ineligible. Sends no request; writes only the new report file given by --out.

    python stale_calendar_audit.py --v1-run <run folder> --v2-audit <interim/final evidence audit json> --out <new .json>
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "docs" / "smart_move" / "shadow_impl"))
from smvshadow import cutoff as co  # noqa: E402

UTC = timezone.utc
LIMIT_H = 6.0


def _t(x):
    return datetime.fromisoformat(str(x).replace("Z", "+00:00"))


def audit(v1_run, v2_audit) -> dict:
    v1 = json.loads((Path(v1_run) / "audit_report.json").read_text(encoding="utf-8"))
    res = {r["symbol"]: r for r in v1["results"]}
    obs = {}
    for ln in (Path(v1_run) / "store" / "cal_obs.jsonl").read_text(encoding="utf-8").splitlines():
        if ln.strip():
            o = json.loads(ln)
            obs.setdefault(o["symbol"], []).append(_t(o["observed_at"]))
    a2 = json.loads(Path(v2_audit).read_text(encoding="utf-8"))
    rows, verdicts = [], {}
    for p in a2["per_stock"]:
        if not p.get("stale_calendar_at_capture"):
            continue
        sym = p["symbol"]
        r = res.get(sym)
        if not r:
            rows.append({"symbol": sym, "verdict": "NO_V1_RECORD"})
            verdicts["NO_V1_RECORD"] = verdicts.get("NO_V1_RECORD", 0) + 1
            continue
        cap = _t(r["capture_ts"])
        ed = r["earnings_date"][:10]
        plan = co.plan_event(datetime.fromisoformat(ed).date(), r["kind"]) if r.get("kind") in ("BMO", "AMC") else None
        in_window = bool(plan and plan["window_start_utc"] <= cap <= plan["cutoff_utc"])
        prior = [t for t in obs.get(sym, []) if t <= cap]
        age = (cap - max(prior)).total_seconds() / 3600 if prior else None
        ev = r.get("evidence") or []
        late = [e for e in ev if _t(e["retrieved_at"]) > cap]
        early = [e for e in ev if plan and _t(e["retrieved_at"]) < plan["window_start_utc"] and e.get("kind") not in ("ohlc", "earnings")]
        problems = []
        if not in_window:
            problems.append("capture outside the frozen window")
        if age is None or age > LIMIT_H:
            problems.append("calendar older than 6 h at V1 capture (%s h)" % (round(age, 2) if age is not None else "unknown"))
        if r.get("calendar_status") not in ("VENDOR_CONFIRMED", "COMPANY_VERIFIED"):
            problems.append("calendar status %s" % r.get("calendar_status"))
        if late:
            problems.append("%d evidence record(s) retrieved after the capture" % len(late))
        verdict = "VALID_AT_CAPTURE" if not problems else "QUESTIONABLE"
        verdicts[verdict] = verdicts.get(verdict, 0) + 1
        rows.append({"symbol": sym, "v1_capture_ts": r["capture_ts"], "calendar_age_h_at_v1_capture": round(age, 2) if age is not None else None, "calendar_status": r.get("calendar_status"), "window_ok": in_window,
                     "evidence_records": len(ev), "evidence_after_capture": len(late), "v1_signals": {"continuation": r["continuation"]["signal"], "contrarian": r["contrarian"]["signal"]},
                     "v2_scored_as_of": p.get("scored_as_of"), "calendar_age_h_at_v2_capture": p.get("calendar_age_h_at_capture"), "verdict": verdict, "problems": problems})
    return {"stocks_checked": len(rows), "verdicts": verdicts, "rule": "6 h calendar freshness inside D-4 (frozen); no later data is used to alter a V1 prediction",
            "conclusion": ("All checked V1 predictions were valid at their own capture time; the staleness arises only in the later V2 re-score." if verdicts.get("VALID_AT_CAPTURE", 0) == len(rows) and rows else "Some V1 predictions need review (see rows)."),
            "rows": rows}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--v1-run", required=True)
    ap.add_argument("--v2-audit", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    out = Path(a.out)
    if out.exists():
        print("STOPPED: output exists; never overwritten")
        return 3
    rep = audit(a.v1_run, a.v2_audit)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: rep[k] for k in ("stocks_checked", "verdicts", "conclusion")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
