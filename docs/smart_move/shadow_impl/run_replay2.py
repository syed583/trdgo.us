"""Updated development replay: valid evidence only, SEC-derived F3 as a SEPARATE exploratory experiment, and a labelled F7 sensitivity on the P2 events.
No request, no database, sealed periods untouched, no outcome column loaded.

    python run_replay2.py --candles-dir <62 saved candle files> --p2-dir <p2 out folder> --out-dir <new folder>
"""
import argparse
import csv
import gzip
import hashlib
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT / "baseline")]
import smart_baseline as sb                                                    # noqa: E402
from smvshadow import replay as rp, scorer as sc                               # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candles-dir", required=True)
    ap.add_argument("--p2-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    out = Path(a.out_dir)
    if (out / "replay2.json").exists():
        sys.exit("STOPPED: output exists (never overwritten)")
    base_pre, base_sha = sb.load_prereg()
    pre40, sha40 = sc.load_prereg()
    until = date.fromisoformat(base_pre["sealing"]["candle_truncation_date"][:10])
    dev_start = date.fromisoformat(base_pre["splits"]["dev"][0])
    events = sb.load_events(ROOT / "h1b/baseline/cutoffs.csv", base_pre, "dev")
    syms = sorted({e["symbol"] for e in events} | {"SPY"})
    bad = sb.verify_candle_index(a.candles_dir, ROOT / "h1b/candles_raw_index.json", syms)
    if bad:
        sys.exit("STOPPED: candle files differ from the hash index: %s" % bad)
    idx = json.loads((ROOT / "h1b/candles_raw_index.json").read_text(encoding="utf-8"))
    uw_hist = rp.load_history(ROOT / "h1b/events_h1b.csv", until, dev_start)
    flags = {}
    with open(ROOT / "h1b/events_h1b.csv", newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            d = date.fromisoformat(r["earnings_date"])
            if dev_start <= d <= until:
                flags[(r["symbol"], r["earnings_date"])] = r
    sec_ok, sec_amb = rp.load_sec_tagged(ROOT / "h1b/sec_filings.json", until, uw_hist)
    hist = {"A_provider_history": {s: v for s, v in uw_hist.items()},
            "B_strict_BMO_certain_plus_AMC_reconciled": {s: [(d, l) for d, l, t in v if t in ("BMO_CERTAIN", "AMC_RECONCILED")] for s, v in sec_ok.items()},
            "B_moderate_adds_AMC_unconfirmed": {s: [(d, l) for d, l, t in v] for s, v in sec_ok.items()}}
    candles = {s: rp.read_candles(Path(a.candles_dir) / (s + ".json"), until) for s in syms}
    p2 = {}
    for r in json.loads((Path(a.p2_dir) / "p2_results.json").read_text(encoding="utf-8"))["results"]:
        if r.get("status") == 200:
            body = gzip.open(Path(a.p2_dir) / "raw" / (r["id"] + ".gz")).read()
            p2[(r["event"]["symbol"], r["event"]["earnings_date"])] = (json.loads(body), r["raw_sha256"])
    res = {"note": "development-period replay on saved data; replay not shadow prediction; no outcomes loaded; no accuracy statement", "baseline_prereg_sha256": base_sha, "score40_prereg_sha256": sha40,
           "sec_priors": {"usable_by_tag": dict(Counter(t for v in sec_ok.values() for _, _, t in v)), "ambiguous_excluded_by_reason": dict(Counter(r for v in sec_amb.values() for _, r in v))},
           "experiments": {}}
    ev_sorted = sorted(events, key=lambda r: (r["earnings_date"], r["symbol"]))
    for name, h in hist.items():
        for variant, use_f7 in (("valid_evidence_only", False), ("F7_sensitivity_on_P2_events", True)):
            rows = []
            for e in ev_sorted:
                sym, ed = e["symbol"], date.fromisoformat(e["earnings_date"])
                key = (sym, e["earnings_date"])
                if use_f7 and key not in p2:
                    continue
                meta = flags.get(key, {})
                ctx, sha = p2.get(key, (None, None)) if use_f7 else (None, None)
                rows.append(rp.replay_event(pre40, sha40, sym, ed, e["provider_kind"], h.get(sym, []), candles[sym], candles["SPY"], idx[sym + ".json"]["sha256"], idx["SPY.json"]["sha256"],
                                            rp.classify_event({"label_conflict": meta.get("label_conflict"), "provider_kind": e["provider_kind"]}), f7_context=ctx, f7_sha=sha))
            res["experiments"]["%s | %s" % (name, variant)] = {"summary": rp.summarise(rows), "rows": rows}
    out.mkdir(parents=True, exist_ok=True)
    body = json.dumps(res, indent=1, sort_keys=True, default=str)
    (out / "replay2.json").write_text(body, encoding="ascii")
    print(json.dumps(res["sec_priors"]))
    for k, v in res["experiments"].items():
        s = v["summary"]
        print(k, "| events", s["events_in_dev_scope"], "| cont", s["signals_continuation"], "| contra", s["signals_contrarian"], "| coverage", s["coverage_weight_available"])
    print("sha256", hashlib.sha256(body.encode("ascii")).hexdigest())


if __name__ == "__main__":
    main()
