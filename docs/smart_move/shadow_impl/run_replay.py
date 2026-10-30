"""Replay the frozen 40-point engines (continuation and contrarian) on SAVED development-period data only. No request, no database, sealed periods untouched.

    python run_replay.py --candles-dir <folder with the 62 saved candle files> --out-dir <new folder>

Refuses on any pre-registration or candle-hash mismatch, never overwrites, never loads a price after the baseline truncation date, never loads outcome columns.
"""
import argparse
import csv
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
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    out = Path(a.out_dir)
    if (out / "real_data_replay_dev_only.json").exists():
        sys.exit("STOPPED: output exists (never overwritten)")
    base_pre, base_sha = sb.load_prereg()                                     # hash-checked
    pre40, sha40 = sc.load_prereg()                                           # hash-checked
    until = date.fromisoformat(base_pre["sealing"]["candle_truncation_date"][:10])
    dev_start = date.fromisoformat(base_pre["splits"]["dev"][0])
    events = sb.load_events(ROOT / "h1b/baseline/cutoffs.csv", base_pre, "dev")        # sealed rows dropped inside
    syms = sorted({e["symbol"] for e in events} | {"SPY"})
    bad = sb.verify_candle_index(a.candles_dir, ROOT / "h1b/candles_raw_index.json", syms)
    if bad:
        sys.exit("STOPPED: candle files differ from the hash index: %s" % bad)
    idx = json.loads((ROOT / "h1b/candles_raw_index.json").read_text(encoding="utf-8"))
    hist = rp.load_history(ROOT / "h1b/events_h1b.csv", until, dev_start)
    flags = {}
    with open(ROOT / "h1b/events_h1b.csv", newline="", encoding="utf-8") as fh:             # label/vendor columns of dev-window rows only
        for r in csv.DictReader(fh):
            d = date.fromisoformat(r["earnings_date"])
            if dev_start <= d <= until:
                flags[(r["symbol"], r["earnings_date"])] = r
    sec_hist = rp.load_sec_history(ROOT / "h1b/sec_filings.json", until)
    candles = {s: rp.read_candles(Path(a.candles_dir) / (s + ".json"), until) for s in syms}
    rows = []
    for e in sorted(events, key=lambda r: (r["earnings_date"], r["symbol"])):
        sym, ed = e["symbol"], date.fromisoformat(e["earnings_date"])
        meta = flags.get((sym, e["earnings_date"]), {})
        rows.append(rp.replay_event(pre40, sha40, sym, ed, e["provider_kind"], hist.get(sym, []), candles[sym], candles["SPY"], idx[sym + ".json"]["sha256"], idx["SPY.json"]["sha256"],
                                    rp.classify_event({"label_conflict": meta.get("label_conflict"), "provider_kind": e["provider_kind"]})))
    rows_b = []
    for e in sorted(events, key=lambda r: (r["earnings_date"], r["symbol"])):
        sym, ed = e["symbol"], date.fromisoformat(e["earnings_date"])
        meta = flags.get((sym, e["earnings_date"]), {})
        rows_b.append(rp.replay_event(pre40, sha40, sym, ed, e["provider_kind"], sec_hist.get(sym, []), candles[sym], candles["SPY"], idx[sym + ".json"]["sha256"], idx["SPY.json"]["sha256"],
                                      rp.classify_event({"label_conflict": meta.get("label_conflict"), "provider_kind": e["provider_kind"]})))
    summ = rp.summarise(rows)
    summ_b = rp.summarise(rows_b)
    # ---- calendar row formats and vendor flags (labels/flags/dates only; no outcomes)
    fmt = Counter()
    up = Counter()
    with open(ROOT / "h1b/events_h1b.csv", newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            d = date.fromisoformat(r["earnings_date"])
            if r["upcoming"] == "True":
                up[(r["source"], r["provider_label"])] += 1
            elif dev_start <= d <= until:
                fmt[(r["source"], r["provider_label"], r["sec_window_class"])] += 1
    dev_rows = [r for r in flags.values()]
    agree = Counter()
    for r in dev_rows:
        lab, sec = r["provider_label"], r["sec_window_class"]
        if lab in ("premarket", "postmarket") and sec in ("BMO", "AMC"):
            agree["agree" if (lab == "premarket") == (sec == "BMO") else "disagree"] += 1
        else:
            agree["not_comparable"] += 1
    res = {"note": "REPLAY of saved dev-period data; calendar status of the time is unknowable; not a shadow prediction; no outcomes loaded; no accuracy claim", "baseline_prereg_sha256": base_sha,
           "score40_prereg_sha256": sha40, "candle_truncation_date": str(until), "summary_A_provider_history": summ, "summary_B_sec_derived_prior_events": summ_b,
           "vendor_flag_formats": {"dev_window_uw_rows(source,label,sec_window)": {"|".join(k): v for k, v in sorted(fmt.items())},
                                   "upcoming_uw_rows(source,label)": {"|".join(k): v for k, v in sorted(up.items())}},
           "label_vs_sec_filing_agreement_dev_window": dict(agree), "rows_A": rows, "rows_B": rows_b}
    out.mkdir(parents=True, exist_ok=True)
    body = json.dumps(res, indent=1, sort_keys=True, default=str)
    (out / "real_data_replay_dev_only.json").write_text(body, encoding="ascii")
    print(json.dumps({k: res[k] for k in ("summary_A_provider_history", "summary_B_sec_derived_prior_events", "label_vs_sec_filing_agreement_dev_window")}, indent=1, default=str))
    print("sha256", hashlib.sha256(body.encode("ascii")).hexdigest())


if __name__ == "__main__":
    main()
