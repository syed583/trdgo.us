"""ONE development-period pipeline check of the pre-registered offline baseline. Validation and test stay sealed.

    python run_dev.py --candles-dir <h1b out>/candles_raw --out-dir <new dir>

Refuses to run if the pre-registration hash, an input hash or a candle file hash differs, and refuses a second run (a record file is written).
"""
import argparse
import json
import random
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import smart_baseline as sb  # noqa: E402

ROOT = HERE.parent                                   # docs/smart_move


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candles-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    pre, pre_hash = sb.load_prereg()
    out = Path(a.out_dir)
    record = out / "dev_run_record.json"
    if record.exists():
        sys.exit("STOPPED: the one development run has already been made (%s exists)" % record)
    for rel, want in pre["inputs_sha256"].items():
        if sb.sha256_file(ROOT / rel) != want:
            sys.exit("STOPPED: input %s differs from the pre-registered hash" % rel)
    buf = pre["cutoff"]["availability_buffer_minutes"]
    until = date.fromisoformat(pre["sealing"]["candle_truncation_date"][:10])
    events = sb.load_events(ROOT / "h1b/baseline/cutoffs.csv", pre, "dev")
    syms = sorted({e["symbol"] for e in events} | {"SPY"})
    bad = sb.verify_candle_index(a.candles_dir, ROOT / "h1b/candles_raw_index.json", syms)
    if bad:
        sys.exit("STOPPED: candle files differ from the hash index: %s" % bad)
    series = sb.load_series(a.candles_dir, syms, until)
    band = pre["label"]["flat_band_pct"]
    mc, _ = sb._cal()

    # ---- reconstruct every dev event independently, with the entry/cutoff checks
    recon, excl = [], []
    for e in sorted(events, key=lambda r: (r["earnings_date"], r["symbol"])):
        sym, ed, kind = e["symbol"], date.fromisoformat(e["earnings_date"]), e["provider_kind"]
        cutoff = sb.utc_dt(e["cutoff_utc"])
        last = sb.last_usable_session(cutoff, buf)
        why = None
        if last.isoformat() != e["last_session"]:
            why = "LAST_SESSION_RECOMPUTE_MISMATCH"
        mv, base, react = sb.reaction_return(series.get(sym, {}), ed, kind)
        if why is None and mv is None:
            why = "RETURN_NOT_CALCULABLE_MISSING_REGULAR_CLOSE"
        if why is None and abs(mv - float(e["move_pct"])) > 0.005:
            why = "RECONSTRUCTED_RETURN_DIFFERS_FROM_STORED"
        if why is None and react > until:
            why = "REACTION_AFTER_TRUNCATION"
        if why:
            excl.append({"symbol": sym, "earnings_date": e["earnings_date"], "reason": why})
            continue
        tradable = sb.order_deadline_ok(cutoff, base, pre["entry_exit"]["order_deadline_minutes_before_close"])
        spy_mv, _, _ = sb.reaction_return(series["SPY"], ed, kind)
        recon.append({"sym": sym, "ed": ed, "kind": kind, "cutoff": cutoff, "last": last, "base": base, "react": react, "move": mv, "spy_move": spy_mv or 0.0,
                      "tradable": tradable, "week": "%d-W%02d" % ed.isocalendar()[:2], "tier": e["category"], "flags": e["cutoff_flags"]})
    react_sessions = defaultdict(set)
    for r in recon:
        react_sessions[r["sym"]].add(r["react"])
    thr = pre["suspect_rule"]["threshold_pct"]
    suspects = {s: sb.find_suspects(series[s], react_sessions[s], thr) for s in series if s != "SPY"}
    spy_sus = sb.find_suspects(series["SPY"], set(), thr)
    ev_returns = defaultdict(list)
    for r in recon:
        ev_returns[r["sym"]].append((r["ed"], r["base"], r["react"], r["move"]))

    # ---- features, votes, decisions
    rows, miss_reasons, leak = [], Counter(), {"events": 0, "max_source_after_last": 0, "last_close_after_cutoff": 0, "entry_deadline_fail": 0,
                                               "entry_not_after_cutoff": 0, "entry_on_last_session": 0}
    for r in recon:
        f, why, maxsrc = sb.features(series[r["sym"]], r["last"], suspects[r["sym"]], series["SPY"], spy_sus)
        m4, n4 = sb.prior_mean4(r["sym"], r["cutoff"], {r["sym"]: [x for x in ev_returns[r["sym"]] if x[0] < r["ed"]]}, suspects[r["sym"]], buf)
        f["mean4"] = m4
        if m4 is None:
            why["mean4"] = "FEWER_THAN_3_USABLE_PRIOR_EVENTS(%d)" % n4
        for k, v in why.items():
            miss_reasons[(k, v.split("(")[0])] += 1
        leak["events"] += 1
        leak["max_source_after_last"] += bool(maxsrc and maxsrc > r["last"])
        leak["last_close_after_cutoff"] += not (mc.session_close_utc(r["last"]) + timedelta(minutes=buf) <= r["cutoff"])
        leak["entry_deadline_fail"] += not r["tradable"]
        leak["entry_not_after_cutoff"] += not (mc.session_close_utc(r["base"]) > r["cutoff"])
        leak["entry_on_last_session"] += r["base"] <= r["last"]
        r["f"], r["why"] = f, why
        r["adv"] = f.get("adv20")
        r["outcome"] = sb.cls_of(r["move"], band)
        prior = [x for x in ev_returns[r["sym"]] if x[0] < r["ed"] and mc.session_close_utc(x[2]) + timedelta(minutes=buf) <= r["cutoff"] and not any(x[1] < q <= x[2] for q in suspects[r["sym"]])]
        r["prior_last"] = sorted(prior)[-1][3] if prior else None
        rows.append(r)

    def calls_for(fn):
        out_ = []
        for r in rows:
            sig = fn(r) if r["tradable"] else "NO_TRADE"
            out_.append({"sig": sig, "move": r["move"], "spy_move": r["spy_move"], "adv": r["adv"], "week": r["week"], "outcome": r["outcome"], "r": r})
        return out_

    def rule(direction, scale=1.0):
        def fn(r):
            v, bull, bear = sb.votes(r["f"], pre, scale)
            avail = sum(x is not None for x in v.values())
            sig, reason = sb.decide(bull, bear, avail, r["adv"], r["tradable"], pre, direction)
            r.setdefault("decision", {})[(direction, scale)] = {"votes": v, "bull": bull, "bear": bear, "available": avail, "reason": reason}
            return sig
        return fn

    results = {"continuation": sb.summarise(calls_for(rule("continuation")), pre, "continuation (primary)"),
               "contrarian": sb.summarise(calls_for(rule("contrarian")), pre, "contrarian mirror")}
    results["always_BUY"] = sb.summarise(calls_for(lambda r: "BUY"), pre, "always BUY")
    results["always_SELL"] = sb.summarise(calls_for(lambda r: "SELL"), pre, "always SELL")
    results["momentum_sign"] = sb.summarise(calls_for(lambda r: "NO_TRADE" if r["f"]["ret20"] is None else ("BUY" if r["f"]["ret20"] > 0 else "SELL")), pre, "momentum sign")
    results["previous_reaction_sign"] = sb.summarise(calls_for(lambda r: "NO_TRADE" if r["prior_last"] is None else ("BUY" if r["prior_last"] > band else "SELL" if r["prior_last"] < -band else "NO_TRADE")), pre, "previous reaction sign")
    sens = {"vote_threshold_x0.6": sb.summarise(calls_for(rule("continuation", 0.6)), pre, "continuation, thresholds x0.6"),
            "vote_threshold_x1.4": sb.summarise(calls_for(rule("continuation", 1.4)), pre, "continuation, thresholds x1.4")}
    # coverage-matched random
    cont = results["continuation"]
    n_dec = cont["decided"]
    pool = [r for r in rows if r["tradable"]]
    rnd = random.Random(pre["random_baseline"]["seed"])
    rh, rm = [], []
    for _ in range(pre["random_baseline"]["draws"]):
        pick = rnd.sample(pool, min(n_dec, len(pool)))
        sg = [rnd.choice((1, -1)) for _ in pick]
        rh.append(sum(1 for r, s in zip(pick, sg) if (s > 0 and r["outcome"] == "UP") or (s < 0 and r["outcome"] == "DOWN")) / max(len(pick), 1))
        rm.append(statistics.mean(sb.net_return(s, r["move"], r["adv"], 1.0) for r, s in zip(pick, sg)) if pick else 0)
    rh.sort(); rm.sort()
    n = len(rh)
    results["random_coverage_matched"] = {"draws": n, "calls_per_draw": n_dec, "strict_hit_rate_mean": round(statistics.mean(rh), 3), "strict_hit_rate_p2.5_p97.5": [round(rh[int(.025 * n)], 3), round(rh[int(.975 * n)], 3)],
                                          "net_mean_pct_cost_x1_mean": round(statistics.mean(rm), 3), "net_mean_p2.5_p97.5": [round(rm[int(.025 * n)], 3), round(rm[int(.975 * n)], 3)]}

    # ---- diagnostics
    by_tier = Counter(r["tier"] for r in rows)
    by_kind = Counter(r["kind"] for r in rows)
    bucket = Counter("liquid" if (r["adv"] or 0) >= 200e6 else "mid" if (r["adv"] or 0) >= 20e6 else "thin_or_unknown" for r in rows)
    reasons = Counter()
    for r in rows:
        reasons[r["decision"][("continuation", 1.0)]["reason"]] += 1
    votes_av = Counter(r["decision"][("continuation", 1.0)]["available"] for r in rows)
    cont_calls = [c for c in calls_for(rule("continuation")) if c["sig"] != "NO_TRADE"]
    cont_by_kind = {k: Counter(c["sig"] for c in cont_calls if c["r"]["kind"] == k) for k in ("BMO", "AMC")}
    suspect_events = [{"symbol": r["sym"], "earnings_date": r["ed"].isoformat(), "missing": {k: v for k, v in r["why"].items() if "SUSPECT" in v}} for r in rows if any("SUSPECT" in v for v in r["why"].values())]
    ftv = ftv_evidence(series, pre)
    corp = [{"symbol": s, "session": q.isoformat(), "ret_pct": round((series[s][q][3] / series[s][max(d for d in series[s] if d < q)][3] - 1) * 100, 1)} for s in sorted(suspects) for q in sorted(suspects[s]) if q >= date(2024, 4, 1)]
    full = {"prereg_sha256": pre_hash, "code_sha256": {"smart_baseline.py": sb.sha256_file(HERE / "smart_baseline.py"), "run_dev.py": sb.sha256_file(HERE / "run_dev.py")},
            "run_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "split": "dev (validation and test sealed, never loaded)",
            "event_set_label": "timing-uncertain exploratory events; none is a verified release time",
            "dev_events_in_input": len(events), "eligible_events": len(rows), "excluded_events": excl, "events_by_tier": dict(by_tier), "events_by_timing": dict(by_kind),
            "liquidity_buckets_adv20": dict(bucket), "leakage_checks_on_real_events": leak,
            "missing_feature_counts": {"%s:%s" % k: v for k, v in sorted(miss_reasons.items())}, "continuation_NO_TRADE_and_RULE_reasons": dict(reasons),
            "votes_available_distribution": dict(sorted(votes_av.items())), "continuation_calls_by_timing": {k: dict(v) for k, v in cont_by_kind.items()},
            "suspect_window_events_in_dev": suspect_events, "non_earnings_jumps_over_threshold_in_dev_series": corp, "ftv_corporate_action_evidence": ftv,
            "results": results, "threshold_sensitivity": sens}
    out.mkdir(parents=True, exist_ok=True)
    (out / "dev_run_results.json").write_text(json.dumps(full, indent=1, default=str), encoding="utf-8")
    record.write_text(json.dumps({"prereg_sha256": pre_hash, "code_sha256": full["code_sha256"], "run_at_utc": full["run_at_utc"], "eligible_events": len(rows),
                                  "note": "the one development-period pipeline check; validation and test were not loaded"}, indent=1), encoding="utf-8")
    print(json.dumps({k: full[k] for k in ("prereg_sha256", "eligible_events", "excluded_events", "leakage_checks_on_real_events")}, indent=1))
    for k in ("continuation", "contrarian", "always_BUY", "always_SELL", "momentum_sign", "previous_reaction_sign"):
        r_ = results[k]
        print("%-24s BUY %3d SELL %3d NO_TRADE %3d coverage %s strict %s %s" % (k, r_["BUY"], r_["SELL"], r_["NO_TRADE"], r_["coverage"], r_["strict_hit_rate"], r_["strict_hit_ci95"]))


def ftv_evidence(series, pre):
    s = series.get("FTV", {})
    days = sorted(s)
    q = date(2025, 6, 30)
    if q not in s:
        return None
    p = max(d for d in days if d < q)
    prior_vol = [s[d][4] for d in days if d < q][-20:]
    h1a = json.loads((ROOT / "h1a/h1a_events.json").read_text(encoding="utf-8"))
    rows = [e for r in h1a["results"] if r["symbol"] == "FTV" for e in r["events"] if e.get("report_date") and e["report_date"] <= "2025-09-30"]
    eps = [(e["report_date"], e.get("street_mean_est"), e.get("actual_eps")) for e in sorted(rows, key=lambda e: e["report_date"])]
    sec = json.loads((ROOT / "h1b/sec_filings.json").read_text(encoding="utf-8")).get("FTV", {})
    off_cycle = [(f["filingDate"], f["acceptanceDateTime"]) for f in sec.get("item_2_02_filings", []) if "2025-06-01" <= f["filingDate"] <= "2025-07-15"]
    return {"session": q.isoformat(), "prev_close": s[p][3], "open": s[q][0], "close": s[q][3], "open_gap_pct": round((s[q][0] / s[p][3] - 1) * 100, 1),
            "day_range_pct": round((s[q][1] / s[q][2] - 1) * 100, 1), "volume_vs_20d_avg": round(s[q][4] / statistics.mean(prior_vol), 2),
            "consensus_eps_and_actual_by_event_(dev_dates_only)": eps, "item_2_02_filings_between_2025-06-01_and_07-15": off_cycle,
            "assessment": "probable corporate action (separation/distribution) with unadjusted prices; cause NOT verified from an external source; features whose windows contain this session are missing, reaction returns of events whose base and reaction closes both lie on one side are unaffected"}


if __name__ == "__main__":
    main()
