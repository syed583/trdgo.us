"""Offline analysis of the saved P1 responses (no request): vendor row formats, flags, date and timing agreement, would-be calendar statuses, independence limits.

    python p1_analyze.py --out1 <first run folder> --out2 <continuation folder> --out-dir <new folder>
"""
import argparse
import csv
import gzip
import hashlib
import io
import json
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shadow_impl"))
from smvshadow import calendar_confirm as cc  # noqa: E402
from smvshadow import calendar_validation as cv  # noqa: E402


def load(out1, out2):
    """-> list of (provider, id, retrieved_at, raw_sha256, parsed object)."""
    items = []
    for folder, results in ((out1, "p1_results.json"), (out2, "p1_continue_results.json")):
        res = json.loads((Path(folder) / results).read_text(encoding="utf-8"))["results"]
        for i, r in enumerate(res):
            if r.get("status") != 200:
                continue
            f = [p for p in sorted((Path(folder) / "raw").glob("*.gz")) if p.name.endswith("_" + r["id"] + ".gz")][0]
            body = gzip.open(f).read()
            items.append((r["provider"], r["id"], datetime.fromisoformat(r["retrieved_at_utc"]), hashlib.sha256(body).hexdigest(), body))
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out1", required=True)
    ap.add_argument("--out2", required=True)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    out = Path(a.out_dir)
    if (out / "p1_report.json").exists():
        sys.exit("STOPPED: report exists (never overwritten)")
    uw_obs, fz_obs, fh_obs, uw_raw, fh_raw, fz_raw = [], [], [], [], [], []
    seen = set()
    last = None
    for prov, rid, at, sha, body in load(a.out1, a.out2):
        last = max(last, at) if last else at
        if prov == "UNUSUAL_WHALES":
            for row in json.loads(body).get("data", []):
                key = (row["symbol"], row["report_date"], row["report_time"])
                if key in seen:
                    continue
                seen.add(key)
                uw_raw.append(row)
                uw_obs.append(cc.from_uw_row(row, at, sha, "UW_CALENDAR"))
        elif prov == "FINVIZ_ELITE":
            for r in list(csv.reader(io.StringIO(body.decode("utf-8", errors="replace"))))[1:]:
                fz_raw.append(r)
                o = cc.from_finviz(r[0], r[1], at, sha)
                if o:
                    fz_obs.append(o)
        else:
            for row in json.loads(body).get("earningsCalendar", []):
                fh_raw.append(row)
                if row.get("symbol") and row.get("date"):
                    fh_obs.append(cc.from_finnhub(row, at, sha))
    today = date(2026, 10, 30)
    uw_syms = {o.symbol: o for o in uw_obs if o.date == date(2026, 11, 2)}
    fz = {o.symbol: o for o in fz_obs}
    fh = {o.symbol: o for o in fh_obs if o.date == date(2026, 11, 2)}
    fh_any = {}
    for o in fh_obs:
        fh_any.setdefault(o.symbol, []).append(o)
    rep = {"retrieved_utc_range": [min(o.observed_at for o in uw_obs + fz_obs + fh_obs).isoformat(), last.isoformat()],
           "formats": {
               "uw_row_keys": sorted({k for r in uw_raw for k in r}), "uw_rows": len(uw_raw), "uw_source_flag": dict(Counter(r.get("source") for r in uw_raw)),
               "uw_report_time": dict(Counter(r.get("report_time") for r in uw_raw)), "uw_report_date": dict(Counter(r.get("report_date") for r in uw_raw)),
               "finviz_columns": ["Ticker", "Earnings Date"], "finviz_rows": len(fz_raw), "finviz_clock_values": dict(Counter(o.clock for o in fz_obs)), "finviz_dates": dict(Counter(str(o.date) for o in fz_obs)),
               "finviz_rows_without_time": sum(1 for o in fz_obs if not o.clock), "finnhub_row_keys": sorted({k for r in fh_raw for k in r}), "finnhub_rows": len(fh_raw),
               "finnhub_hour": dict(Counter(r.get("hour") for r in fh_raw)), "finnhub_dates": dict(Counter(r.get("date") for r in fh_raw))}}
    # ---- agreement UW (2026-11-02) against each vendor
    def sess_uw(o):
        return o.session()

    def compare(name, other, other_all=None):
        c = Counter()
        diffs = []
        for s, u in uw_syms.items():
            if s not in other:
                c["not_in_other_vendor_set"] += 1
                continue
            o = other[s]
            if o.date != u.date:
                c["date_differs"] += 1
                diffs.append({"symbol": s, "uw": [str(u.date), u.label, u.vendor_flag], name: [str(o.date), o.label or o.clock]})
                continue
            c["same_date"] += 1
            a_, b_ = sess_uw(u), o.session()
            if a_ in ("BMO", "AMC") and b_ in ("BMO", "AMC"):
                c["timing_agree" if a_ == b_ else "timing_conflict"] += 1
                if a_ != b_:
                    diffs.append({"symbol": s, "uw": [str(u.date), u.label], name: [str(o.date), o.label or o.clock]})
            else:
                c["timing_not_comparable(other_unknown_or_in_hours)" if a_ in ("BMO", "AMC") else "timing_not_comparable(uw_unknown)"] += 1
        return dict(c), diffs[:25]

    rep["uw_vs_finviz"], rep["uw_vs_finviz_diffs"] = compare("finviz", fz)
    rep["uw_vs_finnhub"], rep["uw_vs_finnhub_diffs"] = compare("finnhub", fh)
    both = Counter()
    for s, f in fz.items():
        if s in fh_any:
            same = [o for o in fh_any[s] if o.date == f.date]
            if not same:
                both["finviz_date_not_in_finnhub"] += 1
            else:
                a_, b_ = f.session(), same[0].session()
                both["same_date"] += 1
                both["timing_agree" if a_ == b_ and a_ in ("BMO", "AMC") else "timing_conflict" if a_ in ("BMO", "AMC") and b_ in ("BMO", "AMC") else "timing_not_comparable"] += 1
        else:
            both["finviz_symbol_not_in_finnhub"] += 1
    rep["finviz_vs_finnhub"] = dict(both)
    # ---- label / clock structure
    rep["finviz_clock_vs_uw_label"] = {"%s | %s" % k: v for k, v in Counter((uw_syms[s].label, fz[s].clock) for s in fz if s in uw_syms).items()}
    rep["finviz_time_values_distinct"] = len({o.clock for o in fz_obs})
    # ---- would-be statuses (now = last retrieval; today = 2026-10-30)
    log = cc.ObservationLog()
    for o in uw_obs + fz_obs + fh_obs:
        log.append(o)
    stat, reasons = Counter(), Counter()
    for s in sorted(uw_syms):
        ev = cc.evaluate(s, log.for_symbol(s), last, today)
        stat[ev["status"]] += 1
        for r in ev["no_trade_reasons"]:
            reasons[r] += 1
    rep["would_be_status_uw_symbols_for_2026_11_02"] = dict(stat)
    rep["no_trade_reasons"] = dict(reasons.most_common())
    # ---- only Finnhub + UW as partners (Finviz covers only the 100 largest)
    rep["uw_company_flag_among_2026_11_02_rows"] = dict(Counter(u.vendor_flag for u in uw_syms.values()))
    rep["observation_log_chain_ok"] = log.verify()
    out.mkdir(parents=True, exist_ok=True)
    body = json.dumps(rep, indent=1, sort_keys=True, default=str)
    (out / "p1_report.json").write_text(body, encoding="ascii")
    print(body)


if __name__ == "__main__":
    main()
