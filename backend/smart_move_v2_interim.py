"""Interim V2 data audit of a collection that is STILL RUNNING. Strictly read-only: it copies the collector's store to a temp folder (a consistent snapshot; the collector keeps writing to the
original), scores V2 on the copy, and writes a new JSON + a two-page PDF. It sends no provider request, never touches the collector's files and changes no scoring rule.

    python smart_move_v2_interim.py --run-dir <V1 run> --collect-dir <v2_runs/..> --out-dir <audit folder>
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "docs" / "smart_move" / "shadow_impl"))
import smart_move_live as L  # noqa: E402
import smart_move_v2 as V2  # noqa: E402
import smart_move_v2_collect as C  # noqa: E402
import smart_move_v2_run as RUN  # noqa: E402
from smvshadow import governor as gv  # noqa: E402

UTC = timezone.utc
FACTORS = list(V2.WEIGHTS)


def snapshot(collect_dir, dest):
    """Consistent read-only copy: evidence and request files are append-only JSONL, so a copy taken now is a valid prefix; a half-written last line is dropped."""
    src = Path(collect_dir) / "store"
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("KILL", "*.tmp"))
    for p in Path(dest).glob("*.jsonl"):
        lines = p.read_text(encoding="utf-8").splitlines()
        good = []
        for ln in lines:
            try:
                json.loads(ln)
                good.append(ln)
            except ValueError:
                break
        p.write_text("\n".join(good) + ("\n" if good else ""), encoding="utf-8")
    rep = json.loads((Path(collect_dir) / "report.json").read_text(encoding="utf-8"))
    return rep


def _body(store, sha):
    return json.loads(store.get(sha))


def lookahead_check(store, records, plans):
    """Per evidence record: retrieval inside the capture window, and no row stamped after the retrieval time."""
    res = {"records": 0, "retrieved_outside_window": [], "rows_stamped_after_retrieval": [], "checked_fields": {"uw_analysts": "timestamp", "uw_flow_alerts": "created_at", "uw_short_float": "market_date", "uw_inst_own": "filing_date", "uw_news": "created_at"}}
    for r in records:
        if r["status"] != "OK":
            continue
        res["records"] += 1
        at = datetime.fromisoformat(r["retrieved_at"])
        pl = plans.get(r["symbol"])
        if pl and not (pl["window_start_utc"] <= at <= pl["cutoff_utc"]):
            res["retrieved_outside_window"].append({"symbol": r["symbol"], "kind": r["kind"], "at": r["retrieved_at"]})
        fld = res["checked_fields"].get(r["kind"])
        if fld:
            rows = _body(store, r["raw_sha256"]).get("data")
            rows = rows if isinstance(rows, list) else ([rows] if isinstance(rows, dict) else [])
            bad = 0
            for x in rows:
                t = V2._ts(x.get(fld))
                if t is not None and t > at:
                    bad += 1
            if bad:
                res["rows_stamped_after_retrieval"].append({"symbol": r["symbol"], "kind": r["kind"], "rows": bad})
    return res


def classify_missing(factor, ev, sym, evrec, failed_kinds):
    """Root cause of a missing or partial factor for one stock. Categories: NOT_COLLECTED_YET, API_FAILURE, GENUINE_ABSENCE, FILTERED_BY_RULE, INSUFFICIENT_HISTORY, UNVERIFIED_SEMANTICS, V1_EVIDENCE_GAP."""
    name = factor["factor"]
    reason = factor.get("reason") or ""
    kinds = {"ANALYST": ("uw_analysts", "uw_estimates"), "OPTIONS_FLOW": ("uw_flow_alerts",), "SHORT_INST": ("uw_short_float", "uw_inst_own")}.get(name, ())
    if name == "NEWS_GUIDANCE":
        return "UNVERIFIED_SEMANTICS", "headline `sentiment` meaning is unverified (all 25 sampled headlines read neutral); guidance has no structured field"
    if reason.startswith("V1_FACTOR_MISSING") or name in ("F3", "F5", "F7", "F8"):
        return "V1_EVIDENCE_GAP", "V1 evidence for this factor was missing at capture (insufficient history or not captured live in the window)"
    if name == "IMPLIED_VS_HIST":
        return "INSUFFICIENT_HISTORY", "fewer than 4 reported post-earnings moves, or no implied move in the saved earnings payload"
    if name == "REL_STRENGTH":
        return "INSUFFICIENT_HISTORY", "no common price history for the stock, SPY or the sector ETF"
    if reason in ("NOT_COLLECTED",) or not all(k in evrec for k in kinds):
        if any(k in failed_kinds for k in kinds):
            return "API_FAILURE", "the provider call failed after retries (transient errors)"
        return "NOT_COLLECTED_YET", "this stock has not been reached by the collector yet"
    if name == "OPTIONS_FLOW":
        rows = evrec["uw_flow_alerts"]["rows"]
        if rows == 0:
            return "GENUINE_ABSENCE", "the provider returned no flow alerts for this stock"
        return "FILTERED_BY_RULE", "%d alerts returned, but fewer than 3 passed the rules (single-leg, expiry on/after earnings, volume/OI >= 1, within 5 days) or attributed premium < $500k: thin options activity around earnings" % rows
    if name == "SHORT_INST":
        return "INSUFFICIENT_HISTORY", "short-interest history or 13F ownership rows are insufficient for a change measure"
    if name == "ANALYST":
        return "INSUFFICIENT_HISTORY", "no prior consensus snapshot (needs a second snapshot 7+ days later) and no rating actions in the window"
    return "OTHER", reason


def build(run_dir, collect_dir, now: datetime, final: bool = False) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="sm_v2_interim_"))
    try:
        snap = tmp / "store"
        rep = snapshot(collect_dir, snap)
        store = L.LiveStore(snap)
        records = store.read(C.EVIDENCE)
        coll = {r["symbol"] for r in records if r["status"] == "OK"}                      # every stock with saved V2 evidence (the probe stock included)
        by_sym = defaultdict(dict)
        for r in records:
            if r["status"] == "OK":
                by_sym[r["symbol"]][r["kind"]] = r
        failed = defaultdict(set)
        for f in rep.get("failures") or []:
            failed[f["symbol"]].add(f["kind"])
        # universe at the start of the collection (10:39 UTC): rebuilt from the snapshot's own calendar observations (no request)
        cfg = L.LiveConfig(mode="LIVE", store_dir=snap, refresh_enabled=False, authorized_daily=0, spacing_s=2.0)
        svc = L.RefreshService(cfg, None, gv.FakeClock(datetime(2026, 11, 1, 10, 39, tzinfo=UTC)))
        start_events = svc.eligible_events(datetime(2026, 11, 1, 10, 39, tzinfo=UTC))
        plans = {e["symbol"]: e["plan"] for e in start_events}
        universe = sorted(plans)
        # Each stock is scored AS OF its own capture time (the time its last V2 evidence was retrieved), never as of "now": the frozen calendar-freshness rule (6 h inside D-4) must hold at capture.
        last_ret = {}
        for r in records:
            if r["status"] == "OK":
                last_ret[r["symbol"]] = max(last_ret.get(r["symbol"], ""), r["retrieved_at"])
        buckets = defaultdict(list)
        for sym in coll:
            t = datetime.fromisoformat(last_ret.get(sym) or now.isoformat())
            buckets[t.strftime("%Y%m%dT%H")].append((sym, t))
        events, scored_at = {}, {}
        for key in sorted(buckets):
            as_of = max(t for _, t in buckets[key]) + timedelta(seconds=1)
            run1 = RUN.run(run_dir, as_of, v2_store=store, store_dir=str(snap), v1_report=Path(run_dir) / "audit_report.json")
            want = {sym for sym, _ in buckets[key]}
            for e in run1["events"]:
                if e["symbol"] in want:
                    events[e["symbol"]] = e
                    scored_at[e["symbol"]] = as_of.isoformat()
        look = lookahead_check(store, records, plans)
        per, causes = [], defaultdict(Counter)
        cause_examples = {}
        bands = Counter()
        fac_cov = {f: {"available": 0, "partial": 0, "missing": 0} for f in FACTORS}
        sig = {v: Counter() for v in ("continuation", "contrarian")}
        why = Counter()
        avail_sum = dir_sum = 0
        fabricated = []
        for sym in sorted(events):
            e = events[sym]
            x = e["v2"]["continuation"]
            fl = {f["factor"]: f for f in x["factors"]}
            av, dv = x["available_points"], x["directional_available_points"]
            avail_sum += av
            dir_sum += dv
            bands["100" if av >= 100 else "80-99" if av >= 80 else "60-79" if av >= 60 else "<60"] += 1
            cal_ref = (rep.get("phases") or {}).get("calendar_refresh_2") or (rep.get("phases") or {}).get("calendar_refresh")
            cal_age = (datetime.fromisoformat(scored_at[sym]) - datetime.fromisoformat(cal_ref["at"])).total_seconds() / 3600 if cal_ref else None
            stale_cal = bool(cal_age is not None and cal_age > 6 and "UNKNOWN_TIMING" in e["eligibility"]["v1_gates"])
            row = {"symbol": sym, "calendar_age_h_at_capture": round(cal_age, 2) if cal_age is not None else None, "stale_calendar_at_capture": stale_cal, "scored_as_of": scored_at.get(sym), "available": av, "directional": dv, "missing_points": x["missing_points"], "bull": x["bull"], "bear": x["bear"], "factors": {}}
            for name, f in fl.items():
                pts = f.get("available_points", f["max"]) if f["available"] else 0
                state = "available" if f["available"] and pts >= f["max"] else "partial" if f["available"] else "missing"
                fac_cov[name][state if state != "available" else "available"] += 1
                row["factors"][name] = {"state": state, "points_with_data": pts, "max": f["max"], "bull": f["bull"], "bear": f["bear"]}
                if state != "available":
                    cat, detail = classify_missing(f if state == "missing" else dict(f, reason="PARTIAL"), e, sym, by_sym.get(sym, {}), failed.get(sym, set()))
                    if stale_cal and name in ("F3", "F5", "F7", "F8") and state == "missing":
                        cat, detail = "STALE_CALENDAR_AT_CAPTURE", "the earnings calendar was older than 6 h when this stock was captured (single refresh at 10:38 UTC), so the frozen V1 freshness/eligibility rule invalidated the V1 evidence at capture time"
                    if state == "partial":
                        cat, detail = ("INSUFFICIENT_HISTORY", "estimate-revision half has no prior consensus snapshot (needs a second snapshot 7+ days later)") if name == "ANALYST" else ("INSUFFICIENT_HISTORY", "one of the two halves lacks data (SI change needs two published settlements; 13F rows partial)")
                    causes[name][cat] += 1
                    cause_examples.setdefault((name, cat), detail)
                    row["factors"][name]["cause"] = cat
                # no fabrication / normalisation: a missing factor contributes exactly zero, and no side exceeds the points that have data
                if not f["available"] and (f["bull"] or f["bear"]):
                    fabricated.append({"symbol": sym, "factor": name})
            if x["bull"] + x["bear"] > av + 1e-9:
                fabricated.append({"symbol": sym, "factor": "TOTAL"})
            for v in ("continuation", "contrarian"):
                sig[v][e["v2"][v]["display_status"]] += 1
            gates = e["eligibility"]["v1_gates"]
            if gates:
                cal_age_h = (datetime.fromisoformat(scored_at[sym]) - datetime.fromisoformat(rep["phases"]["calendar_refresh_2"]["at"])).total_seconds() / 3600 if rep.get("phases", {}).get("calendar_refresh_2") else None
                tag = " - calendar was %.1f h old at capture (limit 6 h)" % cal_age_h if cal_age_h is not None and cal_age_h > 6 and "UNKNOWN_TIMING" in gates else ""
                why["blocked by V1 eligibility gate (%s)%s" % (", ".join(sorted(set(gates))), tag)] += 1
            elif dv < 60:
                miss = [n for n, f in row["factors"].items() if f["state"] != "available" and V2.WEIGHTS[n] and n in V2.DIRECTIONAL]
                why["directional evidence %g < 60 (missing: %s)" % (dv, ", ".join(sorted(miss)))] += 1
            else:
                why["60+ directional points but evidence below the BUY/SELL margin" if x["signal"] == "NO_TRADE" else "signal produced (%s)" % x["signal"]] += 1
            row["eligibility"] = e["eligibility"]
            row["signals"] = {v: {"signal": e["v2"][v]["signal"], "display": e["v2"][v]["display_status"], "explanation": e["v2"][v]["explanation"]} for v in ("continuation", "contrarian")}
            row["v1_original"] = {v: (e.get("v1_original") or {}).get(v, {}).get("signal") for v in ("continuation", "contrarian")}
            per.append(row)
        n = len(per)
        not_reached = sorted(set(universe) - coll - set(rep.get("expired_before_collection") or []))
        d60 = sum(1 for r in per if r["directional"] >= 60)
        rec = {"title": "FINAL EVIDENCE AUDIT - COLLECTION COMPLETE" if final else "INTERIM AUDIT - COLLECTION STILL IN PROGRESS", "final": final,
               "v2_file_body": {"model": "V2", "version": V2.VERSION, "status": V2.STATUS, "config_hash": V2.CONFIG_HASH, "source_run": Path(run_dir).name, "as_of": now.isoformat(), "scoring_note": "each stock scored as of its own capture time", "events": [events[s_] for s_ in sorted(events)], "simulation": False}, "generated_utc": now.isoformat(), "snapshot_note": "read-only copy of the collector's store; the collector was not touched",
               "collection": {"universe_at_start": len(universe), "stocks_collected": len(coll), "audited": n, "expired_before_collection": rep.get("expired_before_collection") or [], "not_reached_yet": len(not_reached),
                              "failed_calls": rep.get("failures") or [], "attempts_total": rep.get("attempts_total"), "cap": rep.get("cap"), "stop_events": rep.get("stop_events") or [], "by_kind": rep.get("kinds"),
                              "status": ("RUNNING (no final status yet)" if not rep.get("status_full") else rep["status_full"]) if not final else "COMPLETED: %s" % rep.get("status_full"), "kill_switch": ("ENGAGED" if (Path(collect_dir) / "store" / "KILL").exists() else "NOT ENGAGED") if final else "released while the run works; engages when it ends"},
               "reconciliation": {"universe": len(universe), "collected": len(coll), "expired_before_collection": len(rep.get("expired_before_collection") or []), "not_reached_yet": len(not_reached),
                                  "sum_check": len(coll) + len(rep.get("expired_before_collection") or []) + len(not_reached) == len(universe) or "COLLECTED includes stocks outside the start universe: see notes"},
               "averages": {"available_of_100": round(avail_sum / n, 1) if n else None, "directional_of_100": round(dir_sum / n, 1) if n else None},
               "bands_available_points": dict(bands), "directional_60_plus": d60, "factor_coverage": fac_cov, "missing_causes": {f: dict(c) for f, c in causes.items()},
               "cause_details": {"%s|%s" % k: v for k, v in cause_examples.items()}, "signals": {v: dict(c) for v, c in sig.items()}, "why_no_buy_sell": dict(why),
               "lookahead": look, "fabrication_check": {"violations": fabricated, "rule": "a missing factor contributes 0 bull/bear points; bull+bear never exceeds the points that have data; nothing is normalised to 100"},
               "config_hash": V2.CONFIG_HASH, "v2_version": V2.VERSION, "per_stock": per, "accuracy": "NOT AVAILABLE: no actual outcomes exist; no accuracy is claimed",
               "fixable_now": ["Retry the 6 failed calls (OGS and others) once more after the run ends, within the remaining budget", "Stocks not yet reached: finishing automatically within the cap and window",
                               "Nothing else can be fixed locally: the missing points are history or semantics, not parsing"],
               "needs_future_snapshots": ["Analyst consensus revisions (10 pts): second consensus snapshot at least 7 days after the first", "Short-interest change: newest settlement is already available; 13F net change: re-collect after the 14 Nov filing deadline",
                                          "News/guidance (10 pts): verify the headline sentiment field on a labelled sample or define a structured guidance field", "Options flow on thin names: only more activity close to earnings can fill it"]}
        rec["audit_hash"] = hashlib.sha256(json.dumps({k: v for k, v in rec.items() if k not in ("generated_utc", "audit_hash")}, sort_keys=True, default=str).encode()).hexdigest()
        return rec
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--collect-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--final", action="store_true", help="the collection has completed: titles say FINAL and the kill-switch state is read")
    ap.add_argument("--write-v2-file", help="also write the per-stock V2 results (scored as of each capture time) to this NEW file for the board and the main audit")
    a = ap.parse_args(argv)
    now = datetime.now(UTC)
    rec = build(a.run_dir, a.collect_dir, now, final=a.final)
    body = rec.pop("v2_file_body")
    body["audit_hash"] = hashlib.sha256(json.dumps({k: body[k] for k in ("version", "config_hash", "source_run", "events")}, sort_keys=True, default=str).encode()).hexdigest()
    rec["v2_file_sha256"] = body["audit_hash"]
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    base = out / ("interim_v2_audit_%s" % now.strftime("%Y%m%dT%H%M%SZ"))
    if base.with_suffix(".json").exists():
        raise FileExistsError(base)
    if a.write_v2_file:
        vf = Path(a.write_v2_file)
        if vf.exists():
            raise FileExistsError(vf)
        vf.write_text(json.dumps(body, default=str, indent=1), encoding="utf-8")
    base.with_suffix(".json").write_text(json.dumps(rec, indent=1, default=str), encoding="utf-8")
    import smart_move_v2_interim_pdf as P
    pdf = P.render(rec, base.with_suffix(".pdf"))
    print(json.dumps({"json": str(base.with_suffix(".json")), **pdf, "audited": rec["collection"]["audited"], "averages": rec["averages"], "signals": rec["signals"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
