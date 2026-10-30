"""Smart Move H1b: freeze a request manifest, then run it (sequential, hard budget, stop conditions). Research only.

    python h1b.py freeze --h1a-manifest <h1a_manifest.json> --h1a-events <h1a_events.json> --out-dir <dir>
    python h1b.py run --manifest <h1b_manifest.json> --manifest-sha256 <hex> [--execute] --out-dir <dir>

freeze: no request. Builds the exact list of Unusual Whales daily-candle requests (benchmark SPY first, the 50 sample symbols, then 11 sector
SPDR ETFs: 62 in total) and decides which SEC submissions files are needed: the application's own disk cache (read only) is used where it
already holds a usable copy (it is newer than the symbol's last past event + 2 days); only the missing ones are requested. The ticker->CIK map is
read from the same cache (no request). Budgets: UW <= 62, SEC <= 51.
run: refuses unless the manifest SHA-256 matches; dry run by default; --execute sends the requests one at a time, 2 s apart, no retries, and
stops on any HTTP error, permission error, 429, non-JSON answer, empty/malformed rows, SPY coverage failure or a response at the 2,500 row cap.
Neither mode prints or writes the API key. `--test-base` (127.0.0.1 only) exists for the offline test with a fake server.
"""
import argparse
import gzip
import hashlib
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import provider_probe as pp  # noqa: E402

SEC_CACHE = Path("C:/Users/initi/Downloads/US-STOCKS/backend/.sec_cache")
SEC_AGENT = pp.SEC_AGENT
UW_MAX, SEC_MAX = 62, 51
ETFS = ["XLK", "XLF", "XLV", "XLY", "XLP", "XLE", "XLI", "XLB", "XLU", "XLRE", "XLC"]
PARAMS = {"timeframe": "3Y", "end_date": "2026-10-29", "limit": 2500}
TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
FROM_DAY = "2024-09-02"
TODAY = "2026-10-30"


def ck(url):
    return hashlib.sha256(url.encode()).hexdigest()


def sha(b):
    return hashlib.sha256(b).hexdigest()


def cache_file(url):
    return SEC_CACHE / (ck(url) + ".json.gz")


def utc(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def freeze(a):
    raw = Path(a.h1a_manifest).read_bytes()
    man = json.loads(raw.decode("ascii"))
    syms = [p["symbol"] for p in man["selected_symbols"]]
    ev = json.loads(Path(a.h1a_events).read_text(encoding="utf-8"))
    last_past = {}
    for r in ev["results"]:
        days = [str(e["report_date"])[:10] for e in r.get("events", []) if e.get("report_date") and str(e["report_date"])[:10] < TODAY]
        last_past[r["symbol"]] = max(days) if days else None
    tf = cache_file(TICKER_URL)
    if not tf.exists():
        sys.exit("STOPPED: ticker map not in the application's cache")
    tm = json.loads(gzip.decompress(tf.read_bytes()))
    t2c = {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in tm.values()}
    mapping = {s: t2c.get(s) for s in syms}
    unmapped = [s for s, c in mapping.items() if not c]
    uw = [("benchmark", "SPY")] + [("sample", s) for s in syms] + [("sector_etf", e) for e in ETFS]
    uw_req = [{"n": i, "role": r, "symbol": s, "path": "/api/stock/%s/ohlc/1d" % s, "params": PARAMS} for i, (r, s) in enumerate(uw, 1)]
    cached, need = [], []
    for s in syms:
        c = mapping[s]
        if not c:
            continue
        url = "https://data.sec.gov/submissions/CIK%s.json" % c
        f = cache_file(url)
        usable = False
        if f.exists() and last_past.get(s):
            day = datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).date().isoformat()
            usable = day >= _plus2(last_past[s])
        if usable:
            cached.append({"symbol": s, "cik": c, "cache_file": f.name, "cache_file_sha256": sha(f.read_bytes()), "cache_mtime_utc": utc(f.stat().st_mtime), "last_past_event": last_past[s]})
        else:
            need.append({"symbol": s, "cik": c, "url": url, "reason": "no cached copy" if not f.exists() else "cached copy older than last past event + 2 days", "last_past_event": last_past.get(s)})
    for i, r in enumerate(need, 1):
        r["n"] = i
    if len(uw_req) > UW_MAX or len(need) > SEC_MAX:
        sys.exit("STOPPED: budget exceeded")
    out = {"version": "smv-h1b-1", "frozen_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "h1a_manifest_sha256": sha(raw), "budget": {"uw_max": UW_MAX, "sec_max": SEC_MAX},
           "totals": {"uw_requests": len(uw_req), "sec_requests": len(need), "sec_from_cache": len(cached), "sec_unmapped": len(unmapped)},
           "uw_requests": uw_req,
           "uw_rules": {"base": pp.UW, "endpoint": "GET /api/stock/{ticker}/ohlc/1d", "params": PARAMS,
                        "row_order": "oldest first (spec)", "rows_kept": "all rows, with market_time; strict analysis uses market_time 'r' only (regular session)",
                        "adjustment": "NOT documented by the provider; no adjustment applied by us; splits/discontinuities are detected afterwards and the affected events flagged, never repaired",
                        "required_coverage_spy": {"first_row_on_or_before": FROM_DAY, "last_row_on_or_after": "2026-10-27"}},
           "sec_ticker_map": {"source": "application disk cache (read only)", "file": tf.name, "sha256": sha(tf.read_bytes()), "mtime_utc": utc(tf.stat().st_mtime),
                              "mapping": mapping, "unmapped": unmapped},
           "sec_cached": cached, "sec_requests": need,
           "sec_rules": {"agent": SEC_AGENT, "pace_seconds": 2, "recorded_as": "SEC_ACCEPTANCE_TIME (a filing fact, never a public release timestamp)",
                         "kept": "8-K filings with Item 2.02: accession, filingDate, acceptanceDateTime, items; the earliest filingDate in the recent list; tickers field"},
           "stop_conditions": ["HTTP status other than 200", "401/403/429 or concurrency refusal", "answer is not JSON", "UW rows empty or a row without close/date",
                               "UW answer with 2,500 rows (cap reached)", "SPY first row after %s or last row before 2026-10-27" % FROM_DAY,
                               "SEC answer without filings.recent or whose tickers do not contain the symbol (recorded; stops after 3)"]}
    body = json.dumps(out, indent=1, sort_keys=True).encode("ascii")
    d = Path(a.out_dir)
    d.mkdir(parents=True, exist_ok=True)
    t = d / "h1b_manifest.json"
    if t.exists():
        sys.exit("STOPPED: manifest exists (never overwritten)")
    t.write_bytes(body)
    (d / "h1b_manifest.json.sha256").write_text("%s  h1b_manifest.json\n" % sha(body), encoding="ascii")
    print("frozen:", t)
    print("UW requests", len(uw_req), "| SEC requests", len(need), "| SEC from app cache", len(cached), "| unmapped", unmapped)
    print("MANIFEST SHA-256:", sha(body))


def _plus2(day):
    from datetime import date, timedelta
    return (date.fromisoformat(day) + timedelta(days=2)).isoformat()


def get(url, headers, timeout=90):
    req = urllib.request.Request(url, headers=headers)
    with pp.OPENER.open(req, timeout=timeout) as r:
        return r.status, r.read()


def run(a):
    raw = Path(a.manifest).read_bytes()
    if sha(raw) != a.manifest_sha256.strip().lower():
        sys.exit("STOPPED: manifest SHA-256 does not match the frozen value")
    m = json.loads(raw.decode("ascii"))
    uw, sec = m["uw_requests"], m["sec_requests"]
    if len(uw) > UW_MAX or len(sec) > SEC_MAX:
        sys.exit("STOPPED: manifest exceeds the approved budget")
    local = bool(a.test_base)
    if local and not re.match(r"^http://127\.0\.0\.1:\d+$", a.test_base):
        sys.exit("STOPPED: --test-base is for 127.0.0.1 only")
    print("H1b plan: %d UW + %d SEC requests (%d SEC files read from the application cache); manifest %s verified" % (len(uw), len(sec), len(m["sec_cached"]), a.manifest_sha256[:12]))
    if not a.execute:
        print("dry run: nothing sent. UW key present: %s" % (True if local else bool(pp.api_key())))
        return
    key = "TEST" if local else pp.api_key()
    if not key:
        sys.exit("STOPPED: no key")
    out = Path(a.out_dir)
    (out / "candles_raw").mkdir(parents=True, exist_ok=True)
    log_path = out / "h1b_request_log.json"
    if log_path.exists():
        sys.exit("STOPPED: output exists (never overwritten)")
    log, sent_uw, sent_sec, stop = [], 0, 0, None
    uwb = a.test_base if local else m["uw_rules"]["base"]
    for r in uw:
        if sent_uw >= UW_MAX:
            break
        q = "&".join("%s=%s" % (k, v) for k, v in r["params"].items())
        url = "%s%s?%s" % (uwb, r["path"], q)
        rec = {"provider": "UW", "n": r["n"], "symbol": r["symbol"], "role": r["role"], "path": r["path"], "params": r["params"],
               "requested_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        sent_uw += 1
        try:
            st, body = get(url, {"Authorization": "Bearer " + key, "Accept": "application/json", "User-Agent": "US-Stock-Reader/1.0"})
            rec["status"] = st
            obj = json.loads(body.decode("utf-8", errors="replace"))
            rows = pp.find_rows(obj)
            rec["rows"] = len(rows)
            days = [str(x.get("date") or x.get("start_time") or "")[:10] for x in rows if isinstance(x, dict)]
            ok = rows and all(isinstance(x, dict) and x.get("close") not in (None, "") and len(d) == 10 for x, d in zip(rows, days))
            if not ok:
                stop = "UW %s: empty rows or a row without close/date" % r["symbol"]
            elif len(rows) >= 2500:
                stop = "UW %s: 2,500 row cap reached" % r["symbol"]
            else:
                rec["first_row"], rec["last_row"] = min(days), max(days)
                rec["market_time_counts"] = {k: sum(1 for x in rows if x.get("market_time") == k) for k in sorted({str(x.get("market_time")) for x in rows})}
                rec["row_keys"] = sorted({k for x in rows for k in x})
                keep = [{k: x.get(k) for k in ("date", "start_time", "end_time", "market_time", "open", "high", "low", "close", "volume", "total_volume") if k in x} for x in rows]
                (out / "candles_raw" / (r["symbol"] + ".json")).write_text(json.dumps(keep), encoding="utf-8")
                if r["role"] == "benchmark" and (min(days) > m["uw_rules"]["required_coverage_spy"]["first_row_on_or_before"] or max(days) < m["uw_rules"]["required_coverage_spy"]["last_row_on_or_after"]):
                    stop = "SPY coverage %s..%s does not meet the required range" % (min(days), max(days))
        except Exception as e:  # never retried
            code = getattr(e, "code", None)
            rec["status"] = code
            rec["error"] = type(e).__name__
            stop = "UW %s: %s %s" % (r["symbol"], type(e).__name__, code or "")
        log.append(rec)
        print("UW %2d %-5s status=%s rows=%s %s" % (r["n"], r["symbol"], rec.get("status"), rec.get("rows"), ("STOP: " + stop) if stop else ""))
        if stop:
            break
        time.sleep(0.05 if local else 2.0)
    filings, mism = {}, 0
    for r in sec if not stop else []:
        if sent_sec >= SEC_MAX:
            break
        url = (a.test_base + "/submissions/CIK%s.json" % r["cik"]) if local else r["url"]
        rec = {"provider": "SEC", "n": r["n"], "symbol": r["symbol"], "url": r["url"], "requested_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        sent_sec += 1
        try:
            st, body = get(url, {"User-Agent": SEC_AGENT, "Accept": "application/json"})
            rec["status"] = st
            obj = json.loads(body.decode("utf-8", errors="replace"))
            filings[r["symbol"]] = extract(obj)
            rec["recent_filings"] = filings[r["symbol"]]["recent_count"]
            if r["symbol"] not in [t.upper() for t in obj.get("tickers", [])]:
                mism += 1
                rec["ticker_mismatch"] = True
                if mism >= 3:
                    stop = "SEC: 3 ticker/CIK mismatches"
        except Exception as e:
            rec["status"] = getattr(e, "code", None)
            rec["error"] = type(e).__name__
            stop = "SEC %s: %s %s" % (r["symbol"], type(e).__name__, rec["status"] or "")
        log.append(rec)
        print("SEC %2d %-5s status=%s %s" % (r["n"], r["symbol"], rec.get("status"), ("STOP: " + stop) if stop else ""))
        if stop:
            break
        time.sleep(0.05 if local else 2.0)
    for c in m["sec_cached"]:
        f = SEC_CACHE / c["cache_file"] if not local else Path(a.test_base_cache) / c["cache_file"]
        if not f.exists():
            continue
        if sha(f.read_bytes()) != c["cache_file_sha256"]:
            stop = stop or "cached SEC file changed since the freeze: " + c["symbol"]
            continue
        obj = json.loads(gzip.decompress(f.read_bytes()))
        filings[c["symbol"]] = dict(extract(obj), source="application cache " + c["cache_file"][:12], cache_mtime_utc=c["cache_mtime_utc"])
    (out / "sec_filings.json").write_text(json.dumps(filings, indent=1), encoding="utf-8")
    log_path.write_text(json.dumps({"manifest_sha256": a.manifest_sha256.lower(), "uw_sent": sent_uw, "sec_sent": sent_sec, "uw_budget": UW_MAX, "sec_budget": SEC_MAX,
                                    "stopped": stop, "requests": log}, indent=1), encoding="utf-8")
    print("UW sent:", sent_uw, "| SEC sent:", sent_sec, "| stopped:", stop, "| wrote", log_path)


def extract(obj):
    rec = obj.get("filings", {}).get("recent", {})
    forms, items, acc = rec.get("form", []), rec.get("items", []), rec.get("acceptanceDateTime", [])
    out = []
    for i, f in enumerate(forms):
        if f == "8-K" and i < len(items) and "2.02" in str(items[i]):
            out.append({"accession": rec.get("accessionNumber", [None] * len(forms))[i], "filingDate": rec.get("filingDate", [None] * len(forms))[i],
                        "acceptanceDateTime": acc[i] if i < len(acc) else None, "items": items[i]})
    return {"tickers": obj.get("tickers"), "recent_count": len(forms), "oldest_filingDate": min(rec.get("filingDate", [""]) or [""]),
            "extra_history_pages": len(obj.get("filings", {}).get("files", [])), "item_2_02_filings": out}


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("--h1a-manifest", required=True)
    f.add_argument("--h1a-events", required=True)
    f.add_argument("--out-dir", required=True)
    r = sub.add_parser("run")
    r.add_argument("--manifest", required=True)
    r.add_argument("--manifest-sha256", required=True)
    r.add_argument("--execute", action="store_true")
    r.add_argument("--out-dir", default="")
    r.add_argument("--test-base", default="")
    r.add_argument("--test-base-cache", default="")
    a = ap.parse_args()
    freeze(a) if a.cmd == "freeze" else run(a)


if __name__ == "__main__":
    main()
