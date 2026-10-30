"""Smart Move H1b analysis (offline, no request, no database): reconstructs earnings reactions from the stored daily candles, checks the price data,
compares timing evidence (provider label, application prediction label, SEC filing acceptance time) and writes the evidence files.

    python h1b_analyze.py --h1a-manifest M --h1a-events E --h1b-dir D --backend B --out-dir O

Rules used (Phase 2A, imported unchanged from earnings_outcomes.py): BMO = previous regular close -> earnings-day regular close; AMC = earnings-day
close -> next trading day close; regular session rows only (market_time 'r'); flat band +-0.5 %. Nothing is repaired or imputed.
"""
import argparse
import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 30, 10, 45, tzinfo=timezone.utc)
TODAY = "2026-10-30"
SPLITS = (2, 3, 4, 5, 6, 8, 10, 15, 20)


def d_(s):
    return date.fromisoformat(str(s)[:10])


def main():
    ap = argparse.ArgumentParser()
    for k in ("h1a-manifest", "h1a-events", "h1b-dir", "backend", "out-dir"):
        ap.add_argument("--" + k, required=True)
    a = ap.parse_args()
    sys.path.insert(0, a.backend)
    import earnings_outcomes as eo
    import market_calendar as mc
    h1b = Path(a.h1b_dir)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    man = json.loads(Path(a.h1a_manifest).read_text(encoding="ascii"))
    stratum = {p["symbol"]: p["stratum"][:3] for p in man["selected_symbols"]}
    ev_raw = json.loads(Path(a.h1a_events).read_text(encoding="utf-8"))
    sec = json.loads((h1b / "sec_filings.json").read_text(encoding="utf-8"))
    reqlog = json.loads((h1b / "h1b_request_log.json").read_text(encoding="utf-8"))

    # ---------------- candles
    rows_by, closes, opens, qc = {}, {}, {}, []
    for f in sorted((h1b / "candles_raw").glob("*.json")):
        s = f.stem
        rows = json.loads(f.read_text(encoding="utf-8"))
        rows_by[s] = rows
        reg = defaultdict(list)
        for r in rows:
            day = str(r.get("date") or r.get("start_time") or "")[:10]
            if str(r.get("market_time", "")).lower() == "r":
                reg[day].append(r)
        cl = {d_(k): float(v[-1]["close"]) for k, v in reg.items()}
        op = {d_(k): float(v[-1]["open"]) for k, v in reg.items() if v[-1].get("open") not in (None, "")}
        closes[s], opens[s] = cl, op
        days = sorted(cl)
        exp = [d for d in (days[0] + timedelta(n) for n in range((days[-1] - days[0]).days + 1)) if mc.is_trading_day(d)] if days else []
        missing = [d.isoformat() for d in exp if d not in cl]
        extra = [d.isoformat() for d in days if not mc.is_trading_day(d)]
        jumps = []
        for p, q in zip(days, days[1:]):
            ratio = cl[q] / cl[p] if cl[p] else None
            if ratio and (abs(ratio - 1) > 0.35 or any(abs(ratio * k - 1) < 0.03 or abs(ratio / k - 1) < 0.03 for k in SPLITS)):
                jumps.append({"from": p.isoformat(), "to": q.isoformat(), "ratio": round(ratio, 4)})
        mt = Counter(str(r.get("market_time")) for r in rows)
        qc.append({"symbol": s, "rows": len(rows), "market_time_counts": dict(mt), "regular_days": len(days), "first": days[0].isoformat() if days else None,
                   "last": days[-1].isoformat() if days else None, "dup_regular_rows": sum(1 for v in reg.values() if len(v) > 1),
                   "expected_sessions": len(exp), "missing_sessions": len(missing), "missing_list": missing[:12], "non_trading_day_rows": extra[:6],
                   "jumps": jumps})
    jump_days = {q["symbol"]: [(d_(j["from"]), d_(j["to"])) for j in q["jumps"]] for q in qc}

    # ---------------- events
    evs, undated = [], []
    for r in ev_raw["results"]:
        s = r["symbol"]
        for e in r.get("events", []):
            if not e.get("report_date"):
                undated.append({"symbol": s, "row": e})
                continue
            evs.append((s, e))
    table = []
    for s, e in evs:
        ed = d_(e["report_date"])
        label = str(e.get("report_time"))
        kind = {"premarket": "BMO", "postmarket": "AMC"}.get(label)
        row = {"symbol": s, "earnings_date": ed.isoformat(), "provider_label": label, "provider_kind": kind or "", "prediction_label_kind": stratum[s],
               "label_conflict": bool(kind and kind != stratum[s]), "source": e.get("source"), "upcoming": ed.isoformat() >= TODAY}
        if ed.isoformat() < TODAY:
            o = eo.evaluate(s, ed, kind, NOW, closes.get(s))
            row.update(status=o["status"], base_date=str(o["base_close_date"] or ""), react_date=str(o["reaction_close_date"] or ""),
                       move_pct=o["move_pct"], direction=o["direction"], note=o["note"] or "")
            prov = e.get("post_earnings_move_1d")
            row["provider_1d_pct"] = round(float(prov) * 100, 2) if prov not in (None, "") else ""
            for alt in ("BMO", "AMC"):                      # both windows, to investigate label conflicts
                try:
                    ao = eo.evaluate(s, ed, alt, NOW, closes.get(s))
                    row["move_if_" + alt] = ao["move_pct"] if ao["move_pct"] is not None else ""
                except Exception:
                    row["move_if_" + alt] = ""
            near = any(abs((ed - a_).days) <= 8 or abs((ed - b_).days) <= 8 for a_, b_ in jump_days.get(s, []))
            row["near_price_jump"] = near
            # SEC
            f202 = sec.get(s, {}).get("item_2_02_filings", [])
            best = None
            for f in f202:
                if f.get("filingDate") and abs((d_(f["filingDate"]) - ed).days) <= 3:
                    if best is None or abs((d_(f["filingDate"]) - ed).days) < abs((d_(best["filingDate"]) - ed).days):
                        best = f
            row["sec_filing_date"] = best["filingDate"] if best else ""
            row["sec_accession"] = best["accession"] if best else ""
            if best and best.get("acceptanceDateTime"):
                acc = datetime.fromisoformat(best["acceptanceDateTime"].replace("Z", "+00:00")).astimezone(ET)
                t = acc.time()
                cls = "BMO" if dtime(4, 0) <= t < dtime(9, 30) else "AMC" if dtime(16, 0) <= t < dtime(20, 0) else "DURING_MARKET" if dtime(9, 30) <= t < dtime(16, 0) else "OTHER_HOURS"
                open_et = datetime.combine(ed, dtime(9, 30), ET)
                close_et = datetime.combine(ed, dtime(16, 0), ET)
                # SEC acceptance is only an UPPER BOUND on the release time (a company may file later than it publishes): it can support a
                # BMO label (accepted before the open), contradict an AMC label (accepted before the close) and otherwise says nothing.
                if not kind:
                    ev_ = "UNVERIFIED_LABEL_UNKNOWN"
                elif kind == "BMO":
                    ev_ = "SUPPORTS_BMO_ACCEPTED_BEFORE_OPEN" if acc <= open_et else "UNINFORMATIVE_LATE_FILING"
                else:
                    ev_ = "CONTRADICTS_AMC_ACCEPTED_BEFORE_CLOSE" if acc < close_et else "NOT_CONTRADICTED_AMC"
                row["sec_evidence"] = ev_
                row["sec_acceptance_et"] = acc.strftime("%Y-%m-%d %H:%M:%S")
                row["sec_acceptance_day_offset"] = (acc.date() - ed).days
                row["sec_window_class"] = cls
            else:
                row["sec_acceptance_et"] = row["sec_window_class"] = ""
                row["sec_evidence"] = "UNVERIFIED_NO_SEC_2_02_MATCH" if kind else "UNVERIFIED_LABEL_UNKNOWN"
                row["sec_acceptance_day_offset"] = ""
            row["sec_history_covers"] = bool(sec.get(s) and sec[s].get("oldest_filingDate", "9999") <= "2024-10-01")
        table.append(row)
    cols = sorted({k for r in table for k in r})
    with open(out / "events_h1b.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        w.writerows(table)
    (out / "undated_provider_rows.json").write_text(json.dumps(undated, indent=1), encoding="utf-8")
    (out / "price_coverage_by_symbol.json").write_text(json.dumps(qc, indent=1), encoding="utf-8")

    past = [r for r in table if not r["upcoming"]]
    summ = {"requests": {"uw_sent": reqlog["uw_sent"], "sec_sent": reqlog["sec_sent"], "stopped": reqlog["stopped"]},
            "events": {"dated_total": len(table), "past": len(past), "upcoming": len(table) - len(past), "undated_rows_preserved": len(undated)}}
    summ["status_counts"] = dict(Counter(r["status"] for r in past))
    graded = [r for r in past if r["status"] == "GRADED"]
    summ["graded"] = len(graded)
    summ["direction_counts"] = dict(Counter(r["direction"] for r in graded))
    mv = [r["move_pct"] for r in graded]
    summ["move_pct"] = {"median_abs": round(statistics.median(abs(x) for x in mv), 2), "mean": round(statistics.mean(mv), 2), "p10": round(sorted(mv)[len(mv) // 10], 2), "p90": round(sorted(mv)[9 * len(mv) // 10], 2)}
    # provider comparison
    cmp_ = [r for r in graded if r["provider_1d_pct"] != ""]
    sign_ok = sum(1 for r in cmp_ if (r["move_pct"] > 0) == (r["provider_1d_pct"] > 0) or r["move_pct"] == 0)
    diffs = [abs(r["move_pct"] - r["provider_1d_pct"]) for r in cmp_]
    summ["provider_1d_vs_phase2a"] = {"n": len(cmp_), "same_sign": sign_ok, "median_abs_diff_pct_points": round(statistics.median(diffs), 2),
                                      "within_0.15": sum(1 for x in diffs if x <= 0.15), "within_1.0": sum(1 for x in diffs if x <= 1.0)}
    # alternative conventions
    convs = {"A_phase2a": lambda s, ed, k: None}
    def px(s, d): return closes[s].get(d)
    def alt_moves(r):
        s, ed, k = r["symbol"], d_(r["earnings_date"]), r["provider_kind"]
        out_ = {}
        nxt, prv = mc.next_trading_day(ed), mc.prev_trading_day(ed)
        c0, cn, cp = px(s, ed), px(s, nxt), px(s, prv)
        o0, on = opens[s].get(ed), opens[s].get(nxt)
        if c0 and cn: out_["B_day_close_to_next_close"] = (cn / c0 - 1) * 100
        if c0 and cp: out_["C_prev_close_to_day_close"] = (c0 / cp - 1) * 100
        if k == "AMC" and c0 and on: out_["D_day_close_to_next_open"] = (on / c0 - 1) * 100
        if k == "BMO" and cp and o0: out_["D_prev_close_to_day_open"] = (o0 / cp - 1) * 100
        return out_
    conv = {}
    for kind in ("BMO", "AMC"):
        grp = [r for r in cmp_ if r["provider_kind"] == kind]
        stats = {"n": len(grp), "A_phase2a": {"median_abs_diff": round(statistics.median(abs(r["move_pct"] - r["provider_1d_pct"]) for r in grp), 3), "within_0.15": sum(1 for r in grp if abs(r["move_pct"] - r["provider_1d_pct"]) <= 0.15)}}
        acc = defaultdict(list)
        for r in grp:
            for k_, v in alt_moves(r).items():
                acc[k_].append(abs(v - r["provider_1d_pct"]))
        for k_, v in acc.items():
            stats[k_] = {"n": len(v), "median_abs_diff": round(statistics.median(v), 3), "within_0.15": sum(1 for x in v if x <= 0.15)}
        conv[kind] = stats
    summ["provider_definition_test"] = conv
    # timing evidence
    t_ = Counter()
    for r in past:
        if not r["provider_kind"]:
            t_["provider_unknown"] += 1
        elif not r["sec_window_class"]:
            t_["no_sec_2_02_match"] += 1
        elif r["sec_window_class"] == r["provider_kind"]:
            t_["provider_label_consistent_with_sec_window"] += 1
        else:
            t_["inconsistent:%s_vs_sec_%s" % (r["provider_kind"], r["sec_window_class"])] += 1
    summ["timing_vs_sec"] = dict(t_)
    summ["sec_evidence_tiers"] = dict(Counter(r["sec_evidence"] for r in past))
    summ["sec_offsets"] = dict(Counter(str(r["sec_acceptance_day_offset"]) for r in past if r["sec_window_class"]))
    summ["sec_filings_coverage"] = {"symbols_with_sec_data": len(sec), "symbols_history_reaches_2024_10": sum(1 for v in sec.values() if v.get("oldest_filingDate", "9999") <= "2024-10-01"),
                                    "symbols_with_extra_history_pages": sum(1 for v in sec.values() if v.get("extra_history_pages"))}
    # nine conflicts
    conf = []
    for r in table:
        if r["label_conflict"]:
            conf.append({k: r.get(k, "") for k in ("symbol", "earnings_date", "provider_label", "prediction_label_kind", "sec_acceptance_et", "sec_window_class", "status", "move_pct", "move_if_BMO", "move_if_AMC", "provider_1d_pct", "near_price_jump", "upcoming")})
    (out / "label_conflicts.json").write_text(json.dumps(conf, indent=1), encoding="utf-8")
    summ["label_conflicts"] = len(conf)
    # price coverage
    summ["price"] = {"symbols": len(qc), "with_split_like_jump": [q["symbol"] for q in qc if q["jumps"]], "symbols_with_missing_sessions": [(q["symbol"], q["missing_sessions"]) for q in qc if q["missing_sessions"]][:20],
                     "symbols_first_row_after_2024-09-02": [(q["symbol"], q["first"]) for q in qc if q["first"] > "2024-09-02"], "dup_regular_rows_total": sum(q["dup_regular_rows"] for q in qc),
                     "market_time_counts_example": qc[0]["market_time_counts"], "rows_example": qc[0]["rows"], "row_keys": sorted({k for q in rows_by.values() for r in q[:3] for k in r})}
    summ["pit"] = {"events_with_both_closes": len(graded), "events_near_price_jump": sum(1 for r in graded if r["near_price_jump"]),
                   "events_with_sec_match": sum(1 for r in past if r["sec_window_class"]), "events_with_unknown_provider_label": sum(1 for r in past if not r["provider_kind"])}
    strict = [r for r in graded if r["provider_kind"] and not r["near_price_jump"] and r["sec_evidence"] in ("SUPPORTS_BMO_ACCEPTED_BEFORE_OPEN", "NOT_CONTRADICTED_AMC")]
    summ["strict_candidate_events"] = {"count": len(strict), "symbols": len({r["symbol"] for r in strict}), "by_kind": dict(Counter(r["provider_kind"] for r in strict)),
                                       "by_quarter": dict(sorted(Counter(r["earnings_date"][:4] + "Q" + str((int(r["earnings_date"][5:7]) - 1) // 3 + 1) for r in strict).items()))}
    (out / "summary.json").write_text(json.dumps(summ, indent=1, default=str), encoding="utf-8")
    print(json.dumps(summ, indent=1, default=str)[:9000])


if __name__ == "__main__":
    main()
