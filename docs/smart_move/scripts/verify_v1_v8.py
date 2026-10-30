"""Smart Move provider verification V1-V8: a FROZEN manifest of at most 15 Unusual Whales requests, run once, sequentially, no retries.

    python verify_v1_v8.py freeze --out-dir <dir>
    python verify_v1_v8.py run --manifest <m.json> --manifest-sha256 <hex> [--execute] --out-dir <dir>

freeze: no request; writes the manifest (exact endpoints, parameters, purpose, expected outputs and pass criteria) and its SHA-256.
run: refuses unless the hash matches; dry run by default; --execute sends the requests in order, 2 s apart, never more than 15; stops on 401 or 429
or after 3 consecutive failures; a 403/404/422 on one request is recorded as the answer to that question, not retried. The API key is never printed
or written. Raw responses are kept compressed in a LOCAL folder (not for Git) with their SHA-256; the summary file holds only structure and ranges.
`--test-base` (127.0.0.1 only) exists for the offline test.
"""
import argparse
import gzip
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import provider_probe as pp  # noqa: E402

BUDGET = 15
D60 = "2026-09-02"          # a Wednesday about 58 days before the run
D365 = "2025-10-01"         # about one year back
T = "AAPL"
TIMEKEYS = re.compile(r"(timestamp|created_at|executed_at|updated_at|hr_min|filing_date|report_date|^date$|start_time|end_time|tape_time|published)", re.I)


def R(n, vid, path, params, purpose, expect):
    return {"n": n, "id": vid, "path": path, "params": params, "purpose": purpose, "expect": expect}


def manifest():
    reqs = [
        R(1, "V1", "/api/companies/%s/earnings-estimates" % T, {}, "plan tier for the estimates endpoint; fields; meaning of date/horizon",
          {"status": [200, 403], "fields_if_200": ["date", "horizon", "eps_estimate_average", "revenue_estimate_average"], "question": "does the account include Advanced+ and is there an as-of date per row?"}),
        R(2, "V2a", "/api/option-trades/flow-alerts", {"ticker_symbol": T, "limit": 50}, "latest flow alerts: created_at stamps, payload size",
          {"status": [200], "fields_if_200": ["created_at", "total_ask_side_prem", "total_bid_side_prem", "alert_rule", "option_chain"], "time_field": "created_at"}),
        R(3, "V2b", "/api/option-trades/flow-alerts", {"ticker_symbol": T, "limit": 50, "newer_than": D60 + "T00:00:00Z", "older_than": "2026-09-03T00:00:00Z"},
          "time filter honoured about 58 days back?", {"status": [200], "time_field": "created_at", "window_utc": [D60 + "T00:00:00Z", "2026-09-03T00:00:00Z"], "question": "rows only inside the window, or empty (not retained)?"}),
        R(4, "V3a", "/api/stock/%s/net-prem-ticks" % T, {"date": D60}, "stamped intraday series for a past day", {"status": [200], "time_field": "tape_time|timestamp", "window_day": D60}),
        R(5, "V3b", "/api/stock/%s/greek-flow" % T, {"date": D60}, "per-minute greek flow for a past day", {"status": [200], "time_field": "timestamp", "window_day": D60}),
        R(6, "V3c", "/api/stock/%s/options-pulse" % T, {"date": D60}, "Nasdaq Options Pulse intraday series for a past day", {"status": [200], "time_field": "hr_min", "window_day": D60}),
        R(7, "V4", "/api/institution/%s/ownership" % T, {"start_date": "2025-09-01", "end_date": "2025-10-31", "limit": 20}, "institutional ownership by report-date range; filing_date vs report_date",
          {"status": [200], "fields_if_200": ["report_date", "filing_date", "units_change"], "question": "non-empty, and filing_date later than report_date?"}),
        R(8, "V5a", "/api/news/headlines", {"ticker": T, "limit": 100, "page": 1}, "headline depth: second page", {"status": [200], "time_field": "created_at", "reference_first_page_range": ["2026-10-06T12:10:00Z", "2026-10-29T17:17:00Z"]}),
        R(9, "V5b", "/api/news/headlines", {"ticker": T, "limit": 100, "page": 2}, "headline depth: third page", {"status": [200], "time_field": "created_at"}),
        R(10, "V6a", "/api/stock/%s/volatility/context" % T, {"date": D365}, "dated volatility context one year back (iv30, rank, skew, implied move)", {"status": [200], "fields_if_200": ["iv30", "iv_rank", "iv_percentile", "skew", "term_structure", "date"], "window_day": D365}),
        R(11, "V6b", "/api/stock/%s/interpolated-iv" % T, {"date": D365}, "dated interpolated IV one year back", {"status": [200], "fields_if_200": ["date", "days", "implied_move_perc", "volatility"], "window_day": D365}),
        R(12, "V7a", "/api/darkpool/%s/price-levels" % T, {"date": D60}, "dark-pool price-level concentration for a past day", {"status": [200]}),
        R(13, "V7b", "/api/darkpool/%s" % T, {"date": D60, "newer_than": D60 + "T14:30:00Z", "older_than": D60 + "T15:30:00Z", "limit": 500}, "one-hour window of prints: window filter and prints per hour (cost of a full day)",
          {"status": [200], "time_field": "executed_at", "window_utc": [D60 + "T14:30:00Z", D60 + "T15:30:00Z"]}),
        R(14, "V8a", "/api/screener/analysts", {"ticker": T, "limit": 100}, "latest analyst actions: timestamp, target, firm", {"status": [200], "time_field": "timestamp", "fields_if_200": ["timestamp", "action", "firm", "target", "recommendation"]}),
        R(15, "V8b", "/api/screener/analysts", {"ticker": T, "limit": 100, "older_than": D365 + "T00:00:00Z"}, "analyst depth one year back", {"status": [200], "time_field": "timestamp", "window_before_utc": D365 + "T00:00:00Z"}),
    ]
    assert len(reqs) == BUDGET and len({(r["path"], json.dumps(r["params"], sort_keys=True)) for r in reqs}) == BUDGET
    return {"version": "smv-verify-1", "provider": "Unusual Whales", "base": pp.UW, "symbol": T, "budget": BUDGET, "requests": reqs,
            "evidence_classes": ["VERIFIED_STAMPED_HISTORY (rows for the past date carry their own timestamps inside the requested window)",
                                 "DATED_NOT_STAMPED (rows for the date but no per-row timestamp)", "NOT_AVAILABLE_AS_OF (empty or outside the window)",
                                 "TIER_BLOCKED (403)", "UNVERIFIED (error or ambiguous)"],
            "stop_rules": ["401 or 429 stops the run", "3 consecutive failures stop the run", "no retries; at most 15 requests"],
            "limitations": "one liquid symbol (AAPL) only: thin names are untested"}


def freeze(a):
    body = json.dumps(manifest(), indent=1, sort_keys=True).encode("ascii")
    d = Path(a.out_dir)
    d.mkdir(parents=True, exist_ok=True)
    t = d / "verify_manifest.json"
    if t.exists():
        sys.exit("STOPPED: manifest exists (never overwritten)")
    t.write_bytes(body)
    h = hashlib.sha256(body).hexdigest()
    (d / "verify_manifest.json.sha256").write_text("%s  verify_manifest.json\n" % h, encoding="ascii")
    print("frozen", t, "| requests", BUDGET, "| SHA-256", h)


def find_rows(obj):
    rows = pp.find_rows(obj)
    if rows:
        return rows
    if isinstance(obj, dict):
        for v in obj.values():
            if isinstance(v, dict):
                r = pp.find_rows(v)
                if r:
                    return r
    return []


def summarise(obj, expect):
    rows = [x for x in find_rows(obj) if isinstance(x, dict)]
    s = {"top_level_keys": sorted(obj.keys())[:25] if isinstance(obj, dict) else None, "rows": len(rows)}
    if isinstance(obj, dict) and not rows:
        s["object_keys"] = sorted(obj.keys())[:30]
    if rows:
        keys = sorted({k for r in rows[:50] for k in r})
        s["row_keys"] = keys[:60]
        spans = {}
        for k in keys:
            if TIMEKEYS.search(k):
                vals = [str(r.get(k)) for r in rows if r.get(k) not in (None, "")]
                if vals:
                    spans[k] = {"min": min(vals), "max": max(vals), "n": len(vals)}
        s["time_fields"] = spans
        s["first_row_sample"] = {k: (str(v)[:40]) for k, v in list(rows[0].items())[:12]}
    else:
        s["first_row_sample"] = {k: str(v)[:60] for k, v in list(obj.items())[:12]} if isinstance(obj, dict) else None
    chk = {}
    if "window_day" in expect and s.get("time_fields"):
        day = expect["window_day"]
        chk["all_time_values_inside_requested_day_utc_plus_1"] = all(v["min"][:10] >= day and v["max"][:10] <= day[:8] + "%02d" % (int(day[8:]) + 1) for v in s["time_fields"].values() if re.match(r"\d{4}-\d{2}-\d{2}", v["min"]))
    if "window_utc" in expect and s.get("time_fields"):
        tf = expect.get("time_field", "")
        sp = s["time_fields"].get(tf)
        if sp:
            chk["all_inside_window"] = expect["window_utc"][0] <= sp["min"] and sp["max"] <= expect["window_utc"][1]
    if "window_before_utc" in expect and s.get("time_fields"):
        sp = s["time_fields"].get(expect.get("time_field", ""))
        if sp:
            chk["all_before_cutoff"] = sp["max"] <= expect["window_before_utc"]
    if expect.get("fields_if_200") and rows:
        chk["expected_fields_present"] = {f: f in s["row_keys"] for f in expect["fields_if_200"]}
    s["checks"] = chk
    return s


def run(a):
    raw = Path(a.manifest).read_bytes()
    if hashlib.sha256(raw).hexdigest() != a.manifest_sha256.strip().lower():
        sys.exit("STOPPED: manifest SHA-256 does not match the frozen value")
    m = json.loads(raw.decode("ascii"))
    reqs = m["requests"]
    if len(reqs) > BUDGET:
        sys.exit("STOPPED: more than %d requests" % BUDGET)
    local = bool(a.test_base)
    if local and not re.match(r"^http://127\.0\.0\.1:\d+$", a.test_base):
        sys.exit("STOPPED: --test-base is for 127.0.0.1 only")
    print("verification plan: %d UW requests; manifest %s verified" % (len(reqs), a.manifest_sha256[:12]))
    for r in reqs:
        print("  %2d %-4s %s %s" % (r["n"], r["id"], r["path"], r["params"]))
    if not a.execute:
        print("dry run: nothing sent. key present: %s" % (True if local else bool(pp.api_key())))
        return
    key = "TEST" if local else pp.api_key()
    if not key:
        sys.exit("STOPPED: no key")
    out = Path(a.out_dir)
    (out / "raw").mkdir(parents=True, exist_ok=True)
    lp = out / "verify_results.json"
    if lp.exists():
        sys.exit("STOPPED: results exist (never overwritten)")
    base = a.test_base if local else m["base"]
    res, sent, fails, stop = [], 0, 0, None
    for r in reqs:
        if sent >= BUDGET:
            break
        q = "&".join("%s=%s" % (k, v) for k, v in r["params"].items())
        url = "%s%s%s" % (base, r["path"], ("?" + q) if q else "")
        rec = {"n": r["n"], "id": r["id"], "path": r["path"], "params": r["params"], "requested_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        sent += 1
        try:
            req = pp.urllib.request.Request(url, headers={"Authorization": "Bearer " + key, "Accept": "application/json", "User-Agent": "US-Stock-Reader/1.0"})
            with pp.OPENER.open(req, timeout=90) as resp:
                body = resp.read()
                rec["status"] = resp.status
                rec["limit_headers"] = {k: v for k, v in resp.headers.items() if re.search(r"limit|remaining|retry|reset|concurr", k, re.I)}
            obj = json.loads(body.decode("utf-8", errors="replace"))
            rec["bytes"] = len(body)
            rec["raw_sha256"] = hashlib.sha256(body).hexdigest()
            (out / "raw" / ("%02d_%s.json.gz" % (r["n"], r["id"]))).write_bytes(gzip.compress(body, 6))
            rec["summary"] = summarise(obj, r["expect"])
            fails = 0
        except Exception as e:  # recorded, never retried
            code = getattr(e, "code", None)
            rec["status"] = code
            rec["error"] = type(e).__name__
            fails += 1
            if code in (401, 429):
                stop = "HTTP %s" % code
            elif fails >= 3:
                stop = "3 consecutive failures"
        res.append(rec)
        print("%2d %-4s status=%s rows=%s %s" % (r["n"], r["id"], rec.get("status"), rec.get("summary", {}).get("rows"), ("STOP: " + stop) if stop else ""))
        if stop:
            break
        time.sleep(0.05 if local else 2.0)
    lp.write_text(json.dumps({"manifest_sha256": a.manifest_sha256.lower(), "requests_sent": sent, "budget": BUDGET, "stopped": stop, "results": res}, indent=1), encoding="utf-8")
    print("requests sent:", sent, "| stopped:", stop, "| wrote", lp)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("--out-dir", required=True)
    r = sub.add_parser("run")
    r.add_argument("--manifest", required=True)
    r.add_argument("--manifest-sha256", required=True)
    r.add_argument("--execute", action="store_true")
    r.add_argument("--out-dir", default="")
    r.add_argument("--test-base", default="")
    a = ap.parse_args()
    freeze(a) if a.cmd == "freeze" else run(a)


if __name__ == "__main__":
    main()
