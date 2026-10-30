"""Smart Move history-depth probe: a FIXED plan of 6 Unusual Whales + 2 SEC EDGAR requests, no retries, no extra requests.

Default is a dry run (prints the plan, makes no request). `--execute` sends exactly the planned requests once each, one at a time,
2 s apart, and writes a summary (never the API key, never raw bodies) to a NEW json file; it refuses to overwrite.
Reads the key from backend/.env (name UNUSUAL_WHALES_API_KEY) and never prints it. Does not touch any database.
"""
import argparse
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent
UW = "https://api.unusualwhales.com"
SEC_AGENT = "US-Stock Reader personal research (jeetubajaj@gmail.com)"
AAPL_CIK = "0000320193"

PLAN = [
    ("UW", "/api/stock/AAPL/iv-rank", {"date": "2026-06-01"}, "cat7 IV rank: depth 5 months back"),
    ("UW", "/api/stock/AAPL/volatility/term-structure", {"date": "2026-09-02"}, "cat7 term structure: depth 2 months back"),
    ("UW", "/api/option-activity/unusual", {"ticker_symbol": "AAPL", "date": "2026-09-02", "limit": 50, "unusual": "true"}, "cat1 unusual contracts: depth 2 months back"),
    ("UW", "/api/darkpool/AAPL", {"date": "2026-09-02", "limit": 50}, "cat2 dark pool: depth 2 months back"),
    ("UW", "/api/institution/AAPL/ownership", {"date": "2026-09-02", "limit": 20}, "cat2 institutional holdings as of a past date"),
    ("UW", "/api/news/headlines", {"ticker": "AAPL", "limit": 50}, "cat6 headlines: how far back 50 rows reach, timestamps"),
    ("SEC", "https://data.sec.gov/submissions/CIK%s.json" % AAPL_CIK, {}, "8-K / Form 4 acceptanceDateTime and 8-K items"),
    ("SEC", "https://data.sec.gov/api/xbrl/companyfacts/CIK%s.json" % AAPL_CIK, {}, "'filed' dates of reported EPS / revenue facts"),
]
assert sum(1 for p in PLAN if p[0] == "UW") == 6 and sum(1 for p in PLAN if p[0] == "SEC") == 2


def api_key():
    env = pathlib.Path("C:/Users/initi/Downloads/US-STOCKS/backend/.env")  # dev machine key file (git-ignored)
    for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.match(r"\s*UNUSUAL_WHALES_API_KEY\s*=\s*(.*)$", line)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return ""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


OPENER = urllib.request.build_opener(NoRedirect)
TIMEKEY = re.compile(r"(date|time|_at$|executed|filed|created|published|period)", re.I)


def find_rows(obj):
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for k in ("data", "results", "rows", "items"):
            if isinstance(obj.get(k), list):
                return obj[k]
        for v in obj.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    return []


def summarise(obj):
    rows = find_rows(obj)
    out = {"rows": len(rows), "top_level_keys": sorted(obj.keys())[:20] if isinstance(obj, dict) else None}
    if rows and isinstance(rows[0], dict):
        out["row_keys"] = sorted(rows[0].keys())[:40]
        spans = {}
        for k in rows[0]:
            if TIMEKEY.search(k):
                vals = [str(r.get(k)) for r in rows if isinstance(r, dict) and r.get(k) not in (None, "")]
                if vals:
                    spans[k] = {"min": min(vals), "max": max(vals), "n": len(vals)}
        out["time_fields"] = spans
    return out


def summarise_sec(url, obj):
    if "submissions" in url:
        recent = obj.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        acc = recent.get("acceptanceDateTime", [])
        items = recent.get("items", [])
        eight = [i for i, f in enumerate(forms) if f == "8-K"]
        e202 = [i for i in eight if i < len(items) and "2.02" in str(items[i])]
        return {"recent_filings": len(forms), "with_acceptanceDateTime": sum(1 for a in acc if a),
                "form_8K": len(eight), "8K_item_2.02": len(e202),
                "latest_2.02_acceptance": [acc[i] for i in e202[:4]],
                "latest_2.02_filingDate": [recent.get("filingDate", [])[i] for i in e202[:4]],
                "oldest_recent_filingDate": min(recent.get("filingDate", [""]) or [""]),
                "form4_with_acceptance": sum(1 for f, a in zip(forms, acc) if f == "4" and a)}
    facts = obj.get("facts", {}).get("us-gaap", {})
    res = {}
    for name in ("EarningsPerShareDiluted", "Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"):
        units = facts.get(name, {}).get("units", {})
        pts = [p for u in units.values() for p in u]
        if pts:
            filed = sorted(p.get("filed", "") for p in pts if p.get("filed"))
            res[name] = {"facts": len(pts), "with_filed": len(filed), "filed_min": filed[0] if filed else None,
                         "filed_max": filed[-1] if filed else None, "has_form_and_fp": all(("form" in p and "fp" in p) for p in pts[:50])}
    return res


def fetch(kind, path, params):
    if kind == "UW":
        q = "&".join("%s=%s" % (k, urllib.request.quote(str(v))) for k, v in params.items())
        req = urllib.request.Request(UW + path + ("?" + q if q else ""),
                                     headers={"Authorization": "Bearer " + api_key(), "Accept": "application/json", "User-Agent": "US-Stock-Reader/1.0"})
        shown = path + ("?" + q if q else "")
    else:
        req = urllib.request.Request(path, headers={"User-Agent": SEC_AGENT, "Accept": "application/json"})
        shown = path
    t0 = datetime.now(timezone.utc)
    rec = {"kind": kind, "endpoint": shown, "requested_at_utc": t0.isoformat(timespec="seconds")}
    try:
        with OPENER.open(req, timeout=60) as r:
            body = r.read()
            rec["status"] = r.status
            rec["bytes"] = len(body)
            hdrs = {k: v for k, v in r.headers.items() if re.search(r"limit|remaining|retry|reset|concurr", k, re.I)}
            rec["limit_headers"] = hdrs
            obj = json.loads(body.decode("utf-8", errors="replace"))
            rec["coverage"] = summarise_sec(path, obj) if kind == "SEC" else summarise(obj)
    except urllib.error.HTTPError as e:
        rec["status"] = e.code
        rec["limit_headers"] = {k: v for k, v in e.headers.items() if re.search(r"limit|remaining|retry|reset|concurr", k, re.I)}
        rec["error"] = "HTTP %s" % e.code
    except Exception as e:  # network / parse: recorded, never retried
        rec["status"] = None
        rec["error"] = type(e).__name__
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    print("PLAN (%d requests: 6 UW + 2 SEC):" % len(PLAN))
    for i, (k, p, q, why) in enumerate(PLAN, 1):
        print("  %d. %s %s %s  -- %s" % (i, k, p, q or "", why))
    if not a.execute:
        print("dry run: nothing sent. key present in backend/.env: %s" % bool(api_key()))
        return
    if not api_key():
        sys.exit("STOPPED: UNUSUAL_WHALES_API_KEY not found in backend/.env")
    out = pathlib.Path(a.out) if a.out else HERE.parent / ("probe_results_%s.json" % datetime.now().strftime("%Y%m%d-%H%M%S"))
    if out.exists():
        sys.exit("STOPPED: %s exists (never overwritten)" % out)
    results = []
    for i, (k, p, q, why) in enumerate(PLAN, 1):
        rec = fetch(k, p, q)
        rec["purpose"] = why
        results.append(rec)
        print("%d. %s status=%s %s" % (i, k, rec.get("status"), rec.get("error", "")))
        time.sleep(2.0)
    out.write_text(json.dumps({"requests_sent": len(results), "results": results}, indent=1), encoding="utf-8")
    print("wrote", out)


if __name__ == "__main__":
    main()
