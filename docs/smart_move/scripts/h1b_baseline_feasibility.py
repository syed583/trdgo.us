"""Smart Move offline baseline feasibility (no provider request, no database): reconciliation of every historical event, per-event conservative
24-hour cutoffs, investigation of large price jumps, chronological split sizes and descriptive naive baselines on the DEVELOPMENT period only.

    python h1b_baseline_feasibility.py --h1b-dir D --analysis-dir A --h1a-events E --backend B --out-dir O

The validation and test periods are only counted, never described, so that they stay untouched for the pre-registered rules. No model is fitted
and no accuracy of any prediction rule is computed.
"""
import argparse
import csv
import json
import math
import random
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = timezone.utc
TODAY = "2026-10-30"
AVAIL = timedelta(minutes=45)                    # daily close is usable 45 minutes after the bell (same buffer as Phase 2A)
SPLITS = {"dev": ("2024-10-01", "2025-09-30"), "validation": ("2025-10-01", "2026-03-31"), "test": ("2026-04-01", "2026-10-31")}
SECTOR = {"CNC": "XLV", "NVCR": "XLV", "LRN": "XLY", "PRLB": "XLI", "RDDT": "XLC", "FTV": "XLI", "VRT": "XLI", "LBRT": "XLE", "PI": "XLK"}
SEED = 20261030


def d_(s):
    return date.fromisoformat(str(s)[:10])


def wilson(k, n, z=1.96):
    if n == 0:
        return (None, None)
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (round(c - h, 3), round(c + h, 3))


def boot_mean(vals, groups, reps=2000):
    by = defaultdict(list)
    for v, g in zip(vals, groups):
        by[g].append(v)
    keys = list(by)
    rnd = random.Random(SEED)
    means = []
    for _ in range(reps):
        pick = [by[rnd.choice(keys)] for _ in keys]
        flat = [x for grp in pick for x in grp]
        means.append(sum(flat) / len(flat))
    means.sort()
    return round(statistics.mean(vals), 3), round(means[int(0.025 * reps)], 3), round(means[int(0.975 * reps)], 3)


def main():
    ap = argparse.ArgumentParser()
    for k in ("h1b-dir", "analysis-dir", "h1a-events", "backend", "out-dir"):
        ap.add_argument("--" + k, required=True)
    a = ap.parse_args()
    sys.path.insert(0, a.backend)
    import market_calendar as mc
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = list(csv.DictReader(open(Path(a.analysis_dir) / "events_h1b.csv", encoding="utf-8")))
    undated = json.loads((Path(a.analysis_dir) / "undated_provider_rows.json").read_text(encoding="utf-8"))
    raw = {f.stem: json.loads(f.read_text(encoding="utf-8")) for f in (Path(a.h1b_dir) / "candles_raw").glob("*.json")}
    close, vol, opn, high, low = {}, {}, {}, {}, {}
    for s, rs in raw.items():
        close[s], vol[s], opn[s], high[s], low[s] = {}, {}, {}, {}, {}
        for r in rs:
            if str(r.get("market_time")) == "r":
                dd = d_(r["date"])
                close[s][dd], opn[s][dd], high[s][dd], low[s][dd] = float(r["close"]), float(r["open"]), float(r["high"]), float(r["low"])
                vol[s][dd] = float(r["volume"]) if r.get("volume") not in (None, "") else None

    # ---------------- A. reconciliation (single category per event, fixed precedence)
    cat = {}
    for r in rows:
        key = (r["symbol"], r["earnings_date"])
        if r["upcoming"] == "True":
            c = "UPCOMING_NOT_SCORED"
        elif r["status"] == "PENDING_REACTION":
            c = "PENDING_REACTION"
        elif not r["provider_kind"]:
            c = "UNKNOWN_TIMING"
        elif r["sec_evidence"] in ("UNVERIFIED_NO_SEC_2_02_MATCH", "UNINFORMATIVE_LATE_FILING"):
            c = "EXCLUDED_NO_DEFENSIBLE_CUTOFF_EVIDENCE"
        elif r["sec_evidence"] == "CONTRADICTS_AMC_ACCEPTED_BEFORE_CLOSE":
            c = "EXCLUDED_TIMING_CONTRADICTED"
        elif r["sec_evidence"] == "SUPPORTS_BMO_ACCEPTED_BEFORE_OPEN":
            c = "EXPLORATORY_UNCERTAIN_BMO_FILING_BOUND"
        else:
            c = "EXPLORATORY_UNCERTAIN_AMC_NOT_CONTRADICTED"
        cat[key] = c
        r["category"] = c
    # ---------------- per-event conservative cutoff
    past = [r for r in rows if r["upcoming"] == "False"]
    cut_rows, viol = [], 0
    for r in past:
        r["cutoff_utc"] = r["cutoff_et"] = r["last_session"] = r["entry_session"] = ""
        r["cutoff_flags"] = ""
        if r["category"] not in ("EXPLORATORY_UNCERTAIN_BMO_FILING_BOUND", "EXPLORATORY_UNCERTAIN_AMC_NOT_CONTRADICTED"):
            continue
        ed = d_(r["earnings_date"])
        nominal = datetime.combine(ed, dtime(6, 0) if r["provider_kind"] == "BMO" else dtime(16, 0), ET).astimezone(UTC)
        acc = datetime.strptime(r["sec_acceptance_et"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=ET).astimezone(UTC) if r["sec_acceptance_et"] else None
        e = min(nominal, acc) if acc else nominal
        cutoff = e - timedelta(hours=24)                     # at least 24 absolute hours before the earliest plausible release
        d0 = cutoff.astimezone(ET).date()
        last = d0
        while not (mc.is_trading_day(last) and mc.session_close_utc(last) + AVAIL <= cutoff):
            last -= timedelta(days=1)
        base = d_(r["base_date"])
        flags = []
        if not mc.is_trading_day(d0):
            flags.append("CUTOFF_ON_NON_TRADING_DAY")
        if any(not mc.is_trading_day(last + timedelta(n)) for n in range(1, (d0 - last).days + 1)):
            flags.append("WEEKEND_OR_HOLIDAY_BETWEEN_LAST_SESSION_AND_CUTOFF")
        if e.astimezone(ET).utcoffset() != cutoff.astimezone(ET).utcoffset():
            flags.append("DST_TRANSITION_BETWEEN_CUTOFF_AND_RELEASE")
        if acc and acc < nominal:
            flags.append("RELEASE_BOUND_BY_SEC_ACCEPTANCE_EARLIER_THAN_NOMINAL")
        if not cutoff < mc.session_close_utc(base):
            viol += 1
            flags.append("CUTOFF_NOT_BEFORE_ENTRY")
        r.update(cutoff_utc=cutoff.strftime("%Y-%m-%d %H:%M:%S"), cutoff_et=cutoff.astimezone(ET).strftime("%Y-%m-%d %H:%M:%S %Z"), last_session=last.isoformat(),
                 entry_session=base.isoformat(), cutoff_flags="|".join(flags))
        r["assumed_release_utc"] = e.strftime("%Y-%m-%d %H:%M:%S")
        r["assumed_release_basis"] = "SEC_ACCEPTANCE_UPPER_BOUND" if acc and acc < nominal else "NOMINAL_" + r["provider_kind"]
        cut_rows.append(r)
    for r in cut_rows:
        if "CUTOFF_NOT_BEFORE_ENTRY" in r["cutoff_flags"]:
            r["category"] = "EXCLUDED_ENTRY_BEFORE_CUTOFF_NOT_TRADABLE"
            cat[(r["symbol"], r["earnings_date"])] = r["category"]
    counts = Counter(cat.values())
    counts["VERIFIED_PUBLIC_RELEASE"] = 0
    counts["UNDATED_PROVIDER_ROWS_MISSING"] = len(undated)
    past = [r for r in rows if r["upcoming"] == "False"]
    recon = {"dated_events": len(rows), "past_dated": len(past), "upcoming": len(rows) - len(past), "undated_rows": len(undated),
             "total_provider_rows": len(rows) + len(undated), "categories": dict(counts),
             "sum_of_past_categories": sum(v for k, v in counts.items() if k not in ("UPCOMING_NOT_SCORED", "VERIFIED_PUBLIC_RELEASE", "UNDATED_PROVIDER_ROWS_MISSING")),
             "exploratory_total": counts["EXPLORATORY_UNCERTAIN_BMO_FILING_BOUND"] + counts["EXPLORATORY_UNCERTAIN_AMC_NOT_CONTRADICTED"]}
    assert recon["sum_of_past_categories"] == recon["past_dated"], "double counting or loss"
    with open(out / "cutoffs.csv", "w", newline="", encoding="utf-8") as fh:
        keys = ["symbol", "earnings_date", "provider_kind", "category", "assumed_release_basis", "assumed_release_utc", "cutoff_utc", "cutoff_et", "last_session", "entry_session", "cutoff_flags", "status", "move_pct", "direction"]
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(cut_rows)
    flag_counts = Counter(f for r in cut_rows for f in r["cutoff_flags"].split("|") if f)
    recon["cutoffs"] = {"events": len(cut_rows), "cutoff_not_before_entry": viol, "flags": dict(flag_counts)}
    (out / "reconciliation.json").write_text(json.dumps(recon, indent=1), encoding="utf-8")

    # ---------------- B. price jumps
    jumps, scan = [], []
    for s in sorted(close):
        days = sorted(close[s])
        for p, q in zip(days, days[1:]):
            ret = close[s][q] / close[s][p] - 1
            if abs(ret) > 0.25:
                scan.append((s, p.isoformat(), q.isoformat(), round(ret * 100, 1)))
        bad_ohlc = sum(1 for d in days if not (low[s][d] <= min(opn[s][d], close[s][d]) + 1e-9 and high[s][d] >= max(opn[s][d], close[s][d]) - 1e-9))
        zero_vol = sum(1 for d in days if not vol[s].get(d))
        stale = max((len(list(g)) for _, g in __import__("itertools").groupby(close[s][d] for d in days)), default=0)
        if bad_ohlc or zero_vol > 3 or stale > 4:
            scan.append((s, "QC", "bad_ohlc=%d zero_volume_days=%d max_stale_run=%d" % (bad_ohlc, zero_vol, stale), None))
    for s, p, q, ret in [x for x in scan if x[3] is not None and x[0] in SECTOR]:
        p_, q_ = d_(p), d_(q)
        days = sorted(close[s])
        i = days.index(q_)
        prior_vol = [vol[s][d] for d in days[max(0, i - 20):i] if vol[s].get(d)]
        etf = SECTOR[s]
        spy_ret = close["SPY"][q_] / close["SPY"][p_] - 1 if q_ in close["SPY"] and p_ in close["SPY"] else None
        etf_ret = close[etf][q_] / close[etf][p_] - 1 if q_ in close[etf] and p_ in close[etf] else None
        after = {n: round((close[s][days[min(i + n, len(days) - 1)]] / close[s][q_] - 1) * 100, 1) for n in (5, 20)}
        ev_near = [r for r in rows if r["symbol"] == s and r["upcoming"] == "False" and abs((d_(r["earnings_date"]) - q_).days) <= 3]
        jumps.append({"symbol": s, "from": p, "to": q, "close_ratio": round(close[s][q_] / close[s][p_], 4), "ret_pct": ret, "open_gap_pct": round((opn[s][q_] / close[s][p_] - 1) * 100, 1),
                      "day_range_pct": round((high[s][q_] / low[s][q_] - 1) * 100, 1), "volume_vs_20d_avg": round(vol[s][q_] / statistics.mean(prior_vol), 2) if prior_vol and vol[s].get(q_) else None,
                      "spy_ret_pct": round(spy_ret * 100, 2) if spy_ret is not None else None, "sector_etf": etf, "sector_ret_pct": round(etf_ret * 100, 2) if etf_ret is not None else None,
                      "drift_after_pct": after, "integer_split_ratio": any(abs(close[s][q_] / close[s][p_] * k - 1) < 0.03 or abs(close[s][q_] / close[s][p_] / k - 1) < 0.03 for k in (2, 3, 4, 5, 6, 8, 10, 15, 20)),
                      "earnings_event_within_3_days": [(r["earnings_date"], r["provider_label"], r["move_pct"], r["provider_1d_pct"]) for r in ev_near]})
    (out / "price_jumps.json").write_text(json.dumps({"jumps_over_25pct": scan, "investigated": jumps}, indent=1, default=str), encoding="utf-8")

    # ---------------- E/F. split sizes + descriptive naive baselines on the development period only
    ex = [r for r in cut_rows if r["status"] == "GRADED" and r["category"].startswith("EXPLORATORY")]
    def period(r):
        for k, (a_, b_) in SPLITS.items():
            if a_ <= r["earnings_date"] <= b_:
                return k
    sizes = {k: sum(1 for r in ex if period(r) == k) for k in SPLITS}
    by_q = Counter((period(r), r["earnings_date"][:4] + "Q" + str((int(r["earnings_date"][5:7]) - 1) // 3 + 1)) for r in ex)
    sym_split = {k: len({r["symbol"] for r in ex if period(r) == k}) for k in SPLITS}
    # history needed by price-only features
    need = 61
    hist_ok = {}
    for r in ex:
        n = sum(1 for d in close[r["symbol"]] if d <= d_(r["last_session"]))
        hist_ok[(r["symbol"], r["earnings_date"])] = n >= need
    dev = [r for r in ex if period(r) == "dev"]
    cls = lambda r: "UP" if float(r["move_pct"]) > 0.5 else "DOWN" if float(r["move_pct"]) < -0.5 else "FLAT"
    mat = Counter(cls(r) for r in dev)
    n = len(dev)
    def adv(r):
        s, lsd = r["symbol"], d_(r["last_session"])
        ds = [d for d in sorted(close[s]) if d <= lsd][-20:]
        v = [close[s][d] * vol[s][d] for d in ds if vol[s].get(d)]
        return statistics.mean(v) if v else 0
    def cost_bps(r):
        a_ = adv(r)
        side = 3 if a_ >= 200e6 else 8 if a_ >= 20e6 else 20
        return 2 * (side + 1)                               # half-spread+slippage plus ~1 bp commission, both sides
    buckets = Counter("liquid" if adv(r) >= 200e6 else "mid" if adv(r) >= 20e6 else "thin" for r in dev)
    weeks = [(d_(r["earnings_date"]) - timedelta(days=d_(r["earnings_date"]).weekday())).isoformat() for r in dev]
    gross_buy = [float(r["move_pct"]) for r in dev]
    base = {"n_dev": n, "liquidity_buckets": dict(buckets), "outcome_counts": dict(mat), "outcome_share": {k: round(v / n, 3) for k, v in mat.items()}}
    for name, sign in (("always_BUY", 1), ("always_SELL", -1)):
        hit = sum(1 for r in dev if cls(r) == ("UP" if sign > 0 else "DOWN"))
        dir_ex = sum(1 for r in dev if cls(r) != "FLAT")
        entry = {"confusion": {"%s_vs" % name.split("_")[1]: dict(mat)}, "strict_hit_rate": round(hit / n, 3), "strict_hit_ci95": wilson(hit, n),
                 "direction_only_hit_rate_ex_flat": round(hit / dir_ex, 3), "direction_only_ci95": wilson(hit, dir_ex), "n_ex_flat": dir_ex}
        for mult in (0, 1, 2, 3):
            net = [sign * float(r["move_pct"]) - mult * (cost_bps(r) + (1.0 if sign < 0 else 0)) / 100 for r in dev]
            m, lo, hi = boot_mean(net, weeks)
            entry["net_mean_pct_cost_x%d" % mult] = {"mean": m, "ci95_cluster_bootstrap_by_week": [lo, hi]}
        base[name] = entry
    base["NO_TRADE_all"] = {"coverage": 0.0, "note": "abstains on every event; return 0"}
    base["median_roundtrip_cost_bps"] = statistics.median(cost_bps(r) for r in dev)
    splits = {"definition": SPLITS, "events_by_split": sizes, "symbols_by_split": sym_split, "events_by_split_quarter": {"%s %s" % k: v for k, v in sorted(by_q.items())},
              "events_with_>=61_sessions_history_at_cutoff": sum(hist_ok.values()), "events_total": len(ex), "validation_and_test": "counted only; outcomes not inspected"}
    mde = {}
    for k, v in sizes.items():
        mde[k] = {"n": v, "wilson_halfwidth_at_p0.5": round(1.96 * math.sqrt(0.25 / v), 3) if v else None}
    (out / "splits_and_baselines_dev_only.json").write_text(json.dumps({"splits": splits, "interval_precision": mde, "naive_baselines_dev": base}, indent=1), encoding="utf-8")
    print(json.dumps({"reconciliation": recon, "splits": splits, "precision": mde, "dev_baselines": base}, indent=1)[:7000])
    print("jumps > 25%:", len([x for x in scan if x[3] is not None]), "| investigated", len(jumps))


if __name__ == "__main__":
    main()
