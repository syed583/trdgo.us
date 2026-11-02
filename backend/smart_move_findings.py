"""Evidence findings behind the V2 candidate decisions, computed from SAVED data only (no request): analyst point-in-time history, news sentiment schema, options-flow lookback backtest.

    python smart_move_findings.py --collect-store <v2_runs/../store> --run-out <out folder> --out <NEW findings .json>
"""
from __future__ import annotations

import argparse
import collections
import gzip
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import smart_move_flow_backtest as FB  # noqa: E402


def _loader(stores):
    def load(sha):
        for st in stores:
            f = Path(st) / "payloads" / sha[:2] / sha[2:4] / (sha + ".json.gz")
            if f.exists():
                return json.loads(gzip.open(f, "rt").read())
        return None
    return load


def analyst_history(collect_store, run_out, event_date="2026-11-02") -> dict:
    """Every PIT consensus observation we saved for the event: the UW calendar's `street_mean_est` at each calendar request, plus the estimates endpoint's own revision fields."""
    stores = [Path(collect_store)] + [p / "store" for p in Path(run_out).glob("run_*")]
    load = _loader(stores)
    obs = [json.loads(ln) for ln in (Path(collect_store) / "cal_obs.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    uw = [r for r in obs if r["source"] == "UW_CALENDAR" and str(r["date"]) == event_date]
    cache, snaps = {}, collections.defaultdict(dict)
    for r in uw:
        if r["raw_sha256"] not in cache:
            cache[r["raw_sha256"]] = load(r["raw_sha256"])
        b = cache[r["raw_sha256"]]
        for x in (b.get("data") if isinstance(b, dict) else b) or []:
            if x.get("symbol") == r["symbol"] and str(x.get("report_date"))[:10] == event_date and x.get("street_mean_est") not in (None, ""):
                snaps[r["symbol"]][r["observed_at"]] = float(x["street_mean_est"])
    spans, changed, multi = [], 0, 0
    for s, v in snaps.items():
        pts = sorted(v.items())
        if len(pts) >= 2:
            multi += 1
            spans.append((datetime.fromisoformat(pts[-1][0]) - datetime.fromisoformat(pts[0][0])).total_seconds() / 3600)
            changed += abs(pts[-1][1] - pts[0][1]) > 1e-9
    est_total = est_null = 0
    for ln in (Path(collect_store) / "v2_evidence.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(ln)
        if r["kind"] == "uw_estimates" and r["status"] == "OK":
            for x in (load(r["raw_sha256"]) or {}).get("data", {}).get("estimates", []):
                if x.get("horizon") == "fiscal quarter":
                    est_total += 1
                    est_null += x.get("eps_estimate_revision_up_last_week") is None and x.get("eps_estimate_revision_down_last_week") is None
    max_span = max(spans) if spans else None
    return {"source": "UW calendar street_mean_est at each saved calendar request (point-in-time, EPS only)", "stocks_with_observations": len(snaps), "stocks_with_two_or_more": multi, "max_span_hours": round(max_span, 1) if max_span else None,
            "stocks_with_a_changed_value": changed, "required_gap": "7 days (frozen V2 band)", "provider_revision_fields": {"quarter_rows": est_total, "null": est_null},
            "usable_revisions": 0, "conclusion": "NO genuine revision history of at least 7 days exists in any saved record (longest %s h; %d changes). The revision factor stays unavailable; nothing is manufactured and today's consensus is never used as an older one." % (round(max_span, 1) if max_span else None, changed)}


def news_schema(collect_store) -> dict:
    load = _loader([collect_store])
    sent, major, src, nt = collections.Counter(), collections.Counter(), collections.Counter(), []
    syms, n = set(), 0
    for ln in (Path(collect_store) / "v2_evidence.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(ln)
        if r["kind"] == "uw_news" and r["status"] == "OK":
            syms.add(r["symbol"])
            for h in (load(r["raw_sha256"]) or {}).get("data", []):
                n += 1
                sent[h.get("sentiment")] += 1
                major[h.get("is_major")] += 1
                src[h.get("source")] += 1
                nt.append(len(h.get("tickers") or []))
    return {"stocks_sampled": sorted(syms), "headlines": n, "sentiment": dict(sent), "is_major": {str(k): v for k, v in major.items()}, "sources": dict(src.most_common(6)), "tickers_per_headline_mean": round(statistics.mean(nt), 2) if nt else None,
            "documented_semantics": "NONE: api_spec.yaml shows only example values (positive, neutral) and does not define who assigns the label or whether it refers to the headline or to a listed ticker",
            "conclusion": "UNVERIFIED: %d identical labels carry no information; the factor stays BLOCKED and unscored. Analyst actions are already used in the ANALYST factor, so they are not reused here (no double counting). No timestamped guidance field exists in the saved payloads." % n}


def news_classifier() -> dict:
    """news-x1 on the 56 saved headlines against the AUTHOR's labels (not independent: a regression check, not validation) and against the provider label."""
    import smart_move_news as NW
    f = HERE.parent / "docs" / "smart_move" / "news_labels_author.json"
    data = json.loads(f.read_text(encoding="utf-8"))
    cap = datetime.fromisoformat(data["capture_ts"])
    conf = collections.Counter()
    for r in data["rows"]:
        out = NW.classify({"headline": r["headline"], "source": r["source"], "created_at": r["created_at"]}, r["symbol"], r["company"], cap, None, lookback_days=3650)
        conf["%s>%s" % (r["label"], out["label"])] += 1
    agree = sum(n for k, n in conf.items() if k.split(">")[0] == k.split(">")[1])
    return {"version": NW.VERSION, "headlines": len(data["rows"]), "agreement_with_author_labels": "%d/%d" % (agree, len(data["rows"])), "confusion": dict(conf), "independent_validation": "NONE: labels are the author's; production points stay 0",
            "provider_label_vs_classifier": "provider says neutral for 56/56; classifier: " + ", ".join("%s %d" % (k, v) for k, v in collections.Counter(k.split(">")[1] for k in sum(([k] * n for k, n in conf.items()), [])).items()),
            "required_for_points": "an independently labelled set (>= 100 headlines, >= 90% agreement, positive and negative examples present) and your approval"}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--collect-store", required=True)
    ap.add_argument("--run-out", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    out = Path(a.out)
    if out.exists():
        print("STOPPED: output exists; never overwritten")
        return 3
    rep = {"generated_utc": datetime.now().astimezone().isoformat(), "analyst_history": analyst_history(a.collect_store, a.run_out), "news_schema": news_schema(a.collect_store), "flow_lookback_backtest": FB.run(a.collect_store), "news_classifier": news_classifier(),
           "outcomes_available": 0, "accuracy": "NOT VERIFIED: no actual outcome exists for any prediction made in this system; no accuracy percentage is reported"}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"analyst": rep["analyst_history"]["conclusion"], "news": rep["news_schema"]["conclusion"][:80], "flow": rep["flow_lookback_backtest"]["verdict"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
