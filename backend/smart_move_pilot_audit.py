"""READ-ONLY audit of the Smart Move pilot evidence (request accounting, calendar, Snapshot B, news, completeness, integrity). It reads the saved store and attempt log, makes no provider request, changes
nothing it reads, and writes its report to a NEW folder. Findings are classified PASS / FAIL / WARNING / UNVERIFIED; nothing is scored and no accuracy is calculated.

    python smart_move_pilot_audit.py --store <pilot store> --run <pilot run state folder> --out <NEW folder> [--eval 2026-11-02T16:14:30+00:00]
"""
from __future__ import annotations

import argparse
import difflib
import gzip
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "docs" / "smart_move" / "shadow_impl")]
import smart_move_news as NW  # noqa: E402
import smart_move_pilot_preflight as PF  # noqa: E402

UTC = timezone.utc


def jl(p: Path) -> list:
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()] if p.exists() else []


def sha_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def payload(store: Path, sha: str):
    f = store / "payloads" / sha[:2] / sha[2:4] / (sha + ".json.gz")
    raw = gzip.open(f, "rt", encoding="utf-8").read()
    return raw, json.loads(raw)


def _t(x) -> datetime:
    t = datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def candidate_quarter(rows: list, event_date: str):
    """HEURISTIC only: the latest fiscal-quarter row ending 1 to 150 days before the report. The exact mapping needs the earnings payload's ending_fiscal_quarter, which the pilot did not collect."""
    ed = datetime.fromisoformat(event_date).date()
    c = [r for r in rows if r.get("horizon") == "fiscal quarter" and 1 <= (ed - datetime.fromisoformat(str(r["date"])[:10]).date()).days <= 150]
    if not c:
        return None
    best = max(c, key=lambda r: r["date"])
    return best, (ed - datetime.fromisoformat(str(best["date"])[:10]).date()).days


def near_duplicates(headlines: list, ratio: float = 0.92) -> list:
    """Pairs of headlines of ONE stock that are the same story: identical after normalisation, or very similar text."""
    norm = lambda s: re.sub(r"[^a-z0-9 ]+", "", s.lower()).strip()  # noqa: E731
    out = []
    for i in range(len(headlines)):
        for j in range(i + 1, len(headlines)):
            a, b = norm(headlines[i]["headline"]), norm(headlines[j]["headline"])
            if a == b or difflib.SequenceMatcher(None, a, b).ratio() >= ratio:
                out.append((headlines[i], headlines[j]))
    return out


def audit(store: Path, run: Path, eval_at: datetime) -> dict:
    ev, cal = jl(store / "v2_evidence.jsonl"), jl(store / "cal_obs.jsonl")
    req, att = jl(store / "requests.jsonl"), jl(run / "attempts.jsonl")
    state = json.loads((run / "collection_state.json").read_text(encoding="utf-8"))
    job = list(state["jobs"].values())[-1]
    f = {}

    # ---------------------------------------------------------------- A. request accounting
    kinds = Counter(a["kind"] for a in att)
    keys = Counter((a["kind"], a["symbol"], json.dumps(a["params"], sort_keys=True)) for a in att)
    ts = [_t(a["at"]) for a in att]
    gaps = [(b - a).total_seconds() for a, b in zip(ts, ts[1:])]
    f["A"] = {"attempts_logged": len(att), "all_ok": all(a["result"] == "OK" for a in att), "by_kind": dict(kinds), "requests_jsonl": len(req), "governor_requests": job["requests"], "state_requests_by_day": state["requests_by_day"],
              "duplicate_requests": [k for k, v in keys.items() if v > 1], "chronological": ts == sorted(ts), "completion_gap_s": {"min": round(min(gaps), 2), "median": round(sorted(gaps)[len(gaps) // 2], 2), "max": round(max(gaps), 2)},
              "allocation": {"calendar": job["calendar_attempts"], "snapshot_b": kinds["uw_estimates"], "news": kinds["uw_news"]}, "halted": job.get("halted"), "status": job["status"],
              "records_match_attempts": Counter(r["kind"] for r in req) == kinds,
              "evidence_records": {"%s %s" % k: v for k, v in Counter((r["kind"], r["status"]) for r in ev).items()}, "hidden_retry_check": "retries off in code and tests; provider-side count NOT independently observable (compare the provider dashboard 'used today' delta)"}

    # ---------------------------------------------------------------- B. calendar (evaluated at the capture time)
    cnt = PF.event_counts(store, eval_at)
    cnt.pop("coverage", None)
    svc, tmp = PF._service(store, eval_at)
    try:
        from smvshadow import calendar_confirm as cc
        from smvshadow import cutoff as co
        elig = svc.eligible_events(eval_at)
        reasons, stat = Counter(), Counter()
        for sym, pairs in svc._calobs().items():
            e = cc.evaluate(sym, [o for o, _ in pairs if o.observed_at <= eval_at], eval_at, eval_at.date())
            stat[e["status"]] += 1
            if not e["eligible_for_shadow"]:
                for r in (e["no_trade_reasons"] or e["reasons"]):
                    reasons[r] += 1
        by = Counter((str(e["event_date"]), e["kind"]) for e in elig)
        uniq = {(e["symbol"], str(e["event_date"])) for e in elig}
        raw = defaultdict(lambda: defaultdict(set))
        for r in cal:
            raw[r["symbol"]][r["source"]].add((r["date"], r["label"] or r["clock"]))
        tz = {}
        for e in elig[:1] + [x for x in elig if x["kind"] == "BMO"][:1]:
            p = co.plan_event(e["event_date"], e["kind"])
            tz[e["kind"]] = {"release_utc": p["assumed_release_utc"].isoformat(), "release_et": p["assumed_release_et"].isoformat(), "window_start_utc": p["window_start_utc"].isoformat(), "cutoff_utc": p["cutoff_utc"].isoformat()}
        f["B"] = {"vendor_status": dict(stat), "eligible": len(elig), "eligible_by_date_session": {"%s %s" % k: v for k, v in by.items()}, "duplicates": len(elig) - len(uniq), "not_eligible_reasons": dict(reasons.most_common(6)),
                  "timezone_samples": tz, "calendar_requests": [r["request_id"] for r in cal if r["source"] == "UW_CALENDAR"][:0] or sorted({r["request_id"] for r in cal if r["source"] == "UW_CALENDAR"}),
                  "uw_rows_by_side": {"%s %s" % k: v for k, v in Counter((r["date"], r["label"]) for r in cal if r["source"] == "UW_CALENDAR").items()}, "sources": dict(Counter(r["source"] for r in cal)),
                  "events_by_symbol": {e["symbol"]: {"date": str(e["event_date"]), "kind": e["kind"], "cutoff": e["plan"]["cutoff_utc"].isoformat()} for e in elig}}
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    events = f["B"]["events_by_symbol"]

    # ---------------------------------------------------------------- C. Snapshot B
    snaps, rows_out, problems = [r for r in ev if r["kind"] == "uw_estimates"], [], Counter()
    for r in snaps:
        _, body = payload(store, r["raw_sha256"])
        data = body.get("data") or {}
        est = data.get("estimates") or []
        ticker_ok = str(data.get("ticker", "")).upper() == r["symbol"]
        e = events.get(r["symbol"])
        fq = [x for x in est if x.get("horizon") == "fiscal quarter"]
        dup = len(fq) - len({x["date"] for x in fq})
        cand = candidate_quarter(est, e["date"]) if e else None
        row = {"symbol": r["symbol"], "ticker_in_response_matches": ticker_ok, "event_known": bool(e), "event_date": e and e["date"], "session": e and e["kind"], "retrieved_at": r["retrieved_at"], "before_cutoff": bool(e) and _t(r["retrieved_at"]) < _t(e["cutoff"]),
               "quarter_rows": len(fq), "year_rows": len(est) - len(fq), "duplicate_quarter_rows": dup}
        if cand:
            q, days = cand
            row.update(candidate_quarter_end=q["date"], days_before_report=days, eps_avg=q.get("eps_estimate_average"), revenue_avg=q.get("revenue_estimate_average"), eps_analysts=q.get("eps_estimate_analyst_count"),
                       revenue_analysts=q.get("revenue_estimate_analyst_count"), null_fields=[k for k in ("eps_estimate_average", "revenue_estimate_average", "eps_estimate_analyst_count", "revenue_estimate_analyst_count") if q.get(k) is None],
                       revisions_provided=q.get("eps_estimate_revision_up_last_week") is not None or q.get("eps_estimate_revision_down_last_week") is not None, ambiguous=days < 10 or days > 75)
        else:
            row["candidate_quarter_end"] = None
        for k in ("duplicate_quarter_rows",):
            problems[k] += 1 if row[k] else 0
        problems["ticker_mismatch"] += 0 if ticker_ok else 1
        problems["no_candidate_quarter"] += 1 if cand is None else 0
        problems["null_estimates"] += 1 if row.get("null_fields") else 0
        problems["ambiguous_quarter"] += 1 if row.get("ambiguous") else 0
        problems["after_cutoff"] += 0 if row["before_cutoff"] else 1
        rows_out.append(row)
    f["C"] = {"responses": len(snaps), "problems": dict(problems), "exact_quarter_mapping": "UNVERIFIED: the pilot did not collect the earnings payload (ending_fiscal_quarter); the quarter below is a HEURISTIC", "rows": rows_out}

    # ---------------------------------------------------------------- D. news
    news = [r for r in ev if r["kind"] == "uw_news"]
    names = {r["symbol"]: r["company"] for r in cal if r.get("company")}
    allh, per, probs = [], {}, Counter()
    for r in news:
        _, body = payload(store, r["raw_sha256"])
        hs = body.get("data") or []
        sym, rt = r["symbol"], _t(r["retrieved_at"])
        cls = [NW.classify(h, sym, names.get(sym), rt) for h in hs]
        rec = {"headlines": len(hs), "rows_in_record": r["rows"], "tagged": sum(1 for h in hs if sym in (h.get("tickers") or [])), "multi_ticker": sum(1 for h in hs if len(h.get("tickers") or []) > 1),
               "future_dated": sum(1 for h in hs if _t(h["created_at"]) > rt), "missing_source": sum(1 for h in hs if not h.get("source")), "missing_time": sum(1 for h in hs if not h.get("created_at")),
               "near_duplicates": len(near_duplicates(hs)), "labels": dict(Counter(c["label"] for c in cls)), "categories": dict(Counter(c["category"] for c in cls)),
               "newest": max((h["created_at"] for h in hs), default=None), "oldest": min((h["created_at"] for h in hs), default=None)}
        per[sym] = rec
        allh += [dict(h, _sym=sym) for h in hs]
        for k in ("future_dated", "missing_source", "missing_time", "near_duplicates"):
            probs[k] += rec[k]
        probs["untagged"] += rec["headlines"] - rec["tagged"]
    empties = [s for s, v in per.items() if v["headlines"] == 0]
    probe = {}
    for s in empties:
        name = names.get(s) or s
        first = re.sub(r"(?i)\b(inc|corp|corporation|co|company|holdings|group|plc|ltd|the)\b\.?", "", name).strip().split(" ")[0]
        hits = [h for h in allh if h["_sym"] != s and re.search(r"\b%s\b" % re.escape(first), h["headline"], re.I)] if len(first) >= 4 else []
        probe[s] = {"company": names.get(s), "event": events.get(s), "name_token": first, "mentions_under_other_stocks": len(hits), "examples": [h["headline"][:90] for h in hits[:2]]}
    srcs = Counter(h["source"] for h in allh)
    f["D"] = {"stocks": len(per), "headlines": len(allh), "problems": dict(probs), "sources": dict(srcs), "labels": dict(Counter(c for v in per.values() for c, n in v["labels"].items() for _ in range(n))),
              "empty_stocks": probe, "per_stock_headlines": {s: v["headlines"] for s, v in per.items()}, "ticker_collision_or_unverified": sum(v["categories"].get("ENTITY_MISMATCH", 0) for v in per.values()),
              "low_credibility_source": sum(v["categories"].get("LOW_CREDIBILITY_SOURCE", 0) for v in per.values())}

    # ---------------------------------------------------------------- E. completeness
    snap_syms, news_syms = {r["symbol"] for r in snaps}, {r["symbol"] for r in news}
    allev = set(events)
    both, news_only, snap_only = snap_syms & news_syms, news_syms - snap_syms, snap_syms - news_syms
    f["E"] = {"eligible_events": len(allev), "snapshot_b_and_news": len(both), "news_only": len(news_only), "snapshot_only": len(snap_only), "no_evidence": len(allev - snap_syms - news_syms),
              "collected_by_session": dict(Counter(events[s]["kind"] + " " + events[s]["date"] for s in snap_syms | news_syms)), "snapshot_a": 0, "analyst_revisions_possible": 0, "outcomes": 0,
              "news_only_symbols": sorted(news_only)}

    # ---------------------------------------------------------------- F. integrity and reproducibility
    bad, missing = 0, 0
    for r in ev:
        try:
            raw, _ = payload(store, r["raw_sha256"])
            if hashlib.sha256(raw.encode("utf-8")).hexdigest() != r["raw_sha256"]:
                bad += 1
        except (OSError, ValueError):
            missing += 1
    for r in cal:
        pass
    f["F"] = {"evidence_payloads_checked": len(ev), "hash_mismatch": bad, "payload_missing": missing, "calendar_rows": len(cal),
              "file_sha256": {"v2_evidence.jsonl": sha_file(store / "v2_evidence.jsonl"), "cal_obs.jsonl": sha_file(store / "cal_obs.jsonl"), "requests.jsonl": sha_file(store / "requests.jsonl"), "attempts.jsonl": sha_file(run / "attempts.jsonl"),
                               "collection_state.json": sha_file(run / "collection_state.json")}}
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--eval", default="2026-11-02T16:14:30+00:00", help="the instant at which eligibility is evaluated (the pilot's capture time)")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    f = out / "pilot_audit.json"
    if f.exists():
        print("STOPPED: report exists")
        return 3
    rep = audit(Path(a.store), Path(a.run), _t(a.eval))
    f.write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    print(f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
