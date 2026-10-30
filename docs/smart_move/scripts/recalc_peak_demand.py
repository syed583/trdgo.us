"""Independent offline recalculation of the Smart Move shadow-pilot peak demand and coverage (no request, no database).

Uses only the dated historical events of the 50-name pilot sample (h1b/events_h1b.csv, past events) as an empirical picture of how one reporting season spreads
over days, scaled to a universe of N events per season. Two estimators are compared and a symbol-resampling bootstrap gives uncertainty:
  A) scaling: demand(day) = count(day in sample season) * N / sample size   (the earlier method)
  B) symbol bootstrap: resample the 50 symbols with replacement, rebuild the season day counts, scale to N
Coverage(cap) = sum_d min(demand_d, cap) / sum_d demand_d. The sample consists of names that reported inside the 13-30 October window, so it describes the
timing pattern of exactly those names; it is NOT a random draw of the whole universe, which is why the real counts (event_count_universe.py) are still required.
"""
import csv
import json
import random
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROWS = [r for r in csv.DictReader(open(HERE.parent / "h1b" / "events_h1b.csv", encoding="utf-8")) if r["upcoming"] == "False"]
CORE, FULL_EXTRA, SHARED = 5, 13, 24
CAPS = (30, 70, 115, 170, 230)
NS = (590, 878, 1000, 1200)
SEED = 20261031


def season_of(d):
    return (int(d[:4]), (int(d[5:7]) - 1) // 3)


def counts_by_season(rows):
    by = defaultdict(Counter)
    for r in rows:
        by[season_of(r["earnings_date"])][r["earnings_date"]] += 1
    return {k: v for k, v in by.items() if sum(v.values()) >= 30}


def coverage(day_counts, scale, cap):
    d = [v * scale for v in day_counts.values()]
    return sum(min(x, cap) for x in d) / sum(d)


def needed_cap(by, scales, target):
    for cap in range(10, 800, 5):
        if statistics.mean(coverage(by[k], scales[k], cap) for k in by) >= target:
            return cap
    return None


def main():
    by = counts_by_season(ROWS)
    out = {"method": "see module docstring", "sample_seasons": {"%d-Q%d" % (k[0], k[1] + 1): {"events": sum(v.values()), "active_days": len(v), "peak_day_events": max(v.values())} for k, v in sorted(by.items())}}
    res = {}
    syms = sorted({r["symbol"] for r in ROWS})
    by_sym = defaultdict(list)
    for r in ROWS:
        by_sym[r["symbol"]].append(r)
    rnd = random.Random(SEED)
    for N in NS:
        scales = {k: N / sum(v.values()) for k, v in by.items()}
        peaks = [max(v.values()) * scales[k] for k, v in by.items()]
        entry = {"estimator_A_scaling": {"peak_day_events_mean": round(statistics.mean(peaks)), "peak_day_events_range": [round(min(peaks)), round(max(peaks))],
                                         "coverage_by_core_cap": {str(c): round(statistics.mean(coverage(by[k], scales[k], c) for k in by), 3) for c in CAPS},
                                         "cap_for_80pct": needed_cap(by, scales, 0.80), "cap_for_95pct": needed_cap(by, scales, 0.95)}}
        boots = {c: [] for c in CAPS}
        peak_b = []
        for _ in range(1000):
            pick = [rnd.choice(syms) for _ in syms]
            rows = [r for s in pick for r in by_sym[s]]
            b = counts_by_season(rows)
            if not b:
                continue
            sc = {k: N / sum(v.values()) for k, v in b.items()}
            for c in CAPS:
                boots[c].append(statistics.mean(coverage(b[k], sc[k], c) for k in b))
            peak_b.append(statistics.mean(max(v.values()) * sc[k] for k, v in b.items()))
        def q(xs, p):
            xs = sorted(xs)
            return xs[int(p * (len(xs) - 1))]
        entry["estimator_B_symbol_bootstrap_95pct_interval"] = {"peak_day_events": [round(q(peak_b, .025)), round(q(peak_b, .975))],
                                                                 "coverage_by_core_cap": {str(c): [round(q(boots[c], .025), 3), round(q(boots[c], .975), 3)] for c in CAPS}}
        req = {}
        for ev_core, ev_full in ((30, 30), (70, 0), (115, 0), (115, 30), (170, 30)):
            if ev_core == 30 and ev_full == 30:
                req["approved_30_full_capture_only"] = 30 * (CORE + FULL_EXTRA) + SHARED
            else:
                req["core%d+full%d" % (ev_core, ev_full)] = ev_core * CORE + ev_full * FULL_EXTRA + SHARED
        entry["peak_requests_per_day_by_plan"] = req
        res["N=%d" % N] = entry
    out["scenarios"] = res
    out["arithmetic_check"] = {"tiered_115_core_30_full": 115 * CORE + 30 * FULL_EXTRA + SHARED, "approved_30_full": 30 * (CORE + FULL_EXTRA) + SHARED}
    (HERE.parent / "shadow" / "peak_demand_recalc.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
