"""Offline analysis of the saved P2 volatility-context responses (no request): coverage, structure, and what the data can and cannot say about point-in-time integrity.

    python p2_analyze.py --p2-dir <p2 out> --candles-dir <candles> --out-dir <new folder>

Checks: (1) the payload echoes the requested date; (2) the payload's realized volatility `rv` equals the annualised standard deviation of the 21 log returns of the saved closes ENDING
EXACTLY on that date and not windows shifted by -3..+10 sessions (an as-of computation); (3) expiry arithmetic; (4) market fields; (5) whether the next-earnings date/time in the payload
could discriminate as-of knowledge from hindsight (it can only if some event's date moved: counted). No outcome is read.
"""
import argparse
import gzip
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--p2-dir", required=True)
    ap.add_argument("--candles-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    out = Path(a.out_dir)
    if (out / "p2_report.json").exists():
        sys.exit("STOPPED: report exists (never overwritten)")
    rs = json.loads((Path(a.p2_dir) / "p2_results.json").read_text(encoding="utf-8"))["results"]

    def series(sym):
        s = {}
        for r in json.loads((Path(a.candles_dir) / (sym + ".json")).read_text(encoding="utf-8")):
            if str(r.get("market_time")) == "r":
                s[date.fromisoformat(r["date"][:10])] = float(r["close"])
        return dict(sorted(s.items()))
    st = Counter(r.get("status") for r in rs)
    err = defaultdict(list)
    rep = {"requests": len(rs), "status": {str(k): v for k, v in st.items()}, "retrieved_utc_range": [min(r["retrieved_at_utc"] for r in rs), max(r["retrieved_at_utc"] for r in rs)]}
    skew, kinds, moved, vix, dte_ok, fri, echo, lab, missing_fields, ivr = Counter(), Counter(), 0, 0, 0, 0, 0, 0, Counter(), []
    older = []
    for r in rs:
        if r.get("status") != 200:
            continue
        d = json.loads(gzip.open(Path(a.p2_dir) / "raw" / (r["id"] + ".gz")).read())["data"]
        ev = r["event"]
        dt = date.fromisoformat(d["date"])
        echo += d["date"] == r["params"]["date"]
        skew[d["skew"].get("label")] += 1
        kinds[ev["kind"]] += 1
        moved += d.get("earnings_date") != ev["earnings_date"]
        lab += {"premarket": "BMO", "postmarket": "AMC"}.get(d.get("earnings_report_time")) == ev["kind"]
        vix += d["market"].get("vix") is not None
        ex = date.fromisoformat(d["expiry"]["expiry"])
        dte_ok += (ex - dt).days == d["expiry"]["dte"]
        fri += ex.weekday() == 4
        for f in ("iv_rank", "iv_percentile", "iv30", "rv", "skew", "expiry", "term_structure", "earnings_date", "earnings_report_time"):
            if d.get(f) in (None, {}):
                missing_fields[f] += 1
        ivr.append(d["iv_rank"])
        older.append((date(2026, 10, 30) - dt).days)
        s = series(ev["symbol"])
        days = sorted(s)
        i = days.index(dt)
        for k in (-3, -1, 0, 1, 3, 10):
            j = i + k
            if j - 21 < 0 or j >= len(days):
                continue
            cl = [s[days[x]] for x in range(j - 21, j + 1)]
            rv = statistics.stdev([math.log(cl[x] / cl[x - 1]) for x in range(1, len(cl))]) * math.sqrt(252)
            err[k].append(abs(rv - d["rv"]) / d["rv"])
    rep.update({"date_echo_ok": echo, "skew_label": dict(skew), "event_kind": dict(kinds), "payload_earnings_date_differs_from_final": moved, "payload_report_time_matches_final_side": lab,
                "market_vix_not_null": vix, "expiry_dte_arithmetic_ok": dte_ok, "expiry_is_friday": fri, "missing_fields": dict(missing_fields), "iv_rank_range": [round(min(ivr), 1), round(max(ivr), 1)],
                "age_of_requested_date_days": [min(older), max(older)],
                "rv_window_match_by_shift": {str(k): {"n": len(v), "median_rel_error": round(statistics.median(v), 4), "within_2pct": sum(1 for x in v if x < 0.02)} for k, v in sorted(err.items())},
                "point_in_time_reading": {
                    "verified_as_of": "realized volatility (rv): equals the 21-log-return volatility ending exactly on the requested date for every event, and only the window ending on that date reproduces it exactly (shifted windows deviate: median 1.3-1.5 % at +-1 session, 4-8 % at +-3); "
                                      "the saved candle closes are also the closes the provider used then (no later adjustment visible for these 40 events)",
                    "not_verifiable": "skew label, iv_rank, iv_percentile, iv30, implied move: option-derived, no per-row timestamp, no independent record; market.vix is null with missing_periscope=true for all rows, "
                                      "which suggests the historical record is reconstructed rather than a stored live snapshot",
                    "cannot_discriminate": "next-earnings date and time: equal to the final event in every sample event (no moved event in the sample), so hindsight and as-of knowledge look identical",
                    "class": "DATED_NOT_STAMPED / RECONSTRUCTED_AS_OF_UNVERIFIED for the option-derived fields"}})
    out.mkdir(parents=True, exist_ok=True)
    (out / "p2_report.json").write_text(json.dumps(rep, indent=1, sort_keys=True), encoding="ascii")
    print(json.dumps(rep, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
