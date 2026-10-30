"""P1 continuation: the remaining 5 of the approved 10 requests, after the first P1 run used 5 (4 Unusual Whales calls with a wrong page index, pages start at 0; 1 Finviz call
that ended in a 301 because the first runner refused every redirect).

    python p1_continue.py freeze --first-results <p1_results.json> --out-dir <dir>
    python p1_continue.py run --manifest <m.json> --manifest-sha256 <hex> [--execute] --out-dir <dir>

Requests (exactly 5, total with the first run = 10): Unusual Whales premarket and afterhours for the next trading day, page 0 (the 4 extra afterhours rows of page 1 are already
saved), Unusual Whales afterhours for today, page 0; ONE Finviz export (redirects followed only to https://*.finviz.com, at most 2); ONE Finnhub calendar for the two dates.
Today's premarket is skipped (it has already reported). Same safety rules as p1_calendar.py: sequential, 2 s apart, no retries, stop on 401/403/429 or any error, read-only,
raw bodies compressed with SHA-256 and retrieval time, no key or token written, the Finviz URL stored without its auth parameter.
"""
import argparse
import csv
import gzip
import hashlib
import io
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import p1_calendar as p1  # noqa: E402
import provider_probe as pp  # noqa: E402

REMAINING = 5


class FinvizRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 2

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        u = urllib.parse.urlparse(newurl)
        if u.scheme != "https" or not (u.hostname == "finviz.com" or (u.hostname or "").endswith(".finviz.com")):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def manifest(first_results):
    first = json.loads(Path(first_results).read_text(encoding="utf-8"))
    days = ["2026-10-30", "2026-11-02"]
    reqs = [{"n": 1, "id": "UW-2026-11-02-premarket-p0", "provider": "UNUSUAL_WHALES", "path": "/api/earnings/premarket", "params": {"date": days[1], "limit": 100, "page": 0}},
            {"n": 2, "id": "UW-2026-11-02-afterhours-p0", "provider": "UNUSUAL_WHALES", "path": "/api/earnings/afterhours", "params": {"date": days[1], "limit": 100, "page": 0}},
            {"n": 3, "id": "UW-2026-10-30-afterhours-p0", "provider": "UNUSUAL_WHALES", "path": "/api/earnings/afterhours", "params": {"date": days[0], "limit": 100, "page": 0}},
            {"n": 4, "id": "FZ-A", "provider": "FINVIZ_ELITE", "path": "export.ashx", "params": {"v": "152", "c": "1,68", "t": "<=100 largest-market-cap tickers from the UW rows of this and the first run>"}},
            {"n": 5, "id": "FH-A", "provider": "FINNHUB", "path": "/calendar/earnings", "params": {"from": days[0], "to": days[1]}}]
    return {"version": "smv-p1-continue-1", "already_sent_in_first_run": first["requests_sent"], "first_run_manifest_sha256": first["manifest_sha256"], "requests": reqs,
            "max_requests_now": len(reqs), "max_total_with_first_run": first["requests_sent"] + len(reqs), "approved_cap": p1.BUDGET,
            "reason": "first run: UW page index 1 (second page) instead of 0 returned empty pages; Finviz redirect (301) refused by the no-redirect client",
            "stop_rules": ["401, 403 or 429 stops the run", "any error stops the run", "no retries"]}


def freeze(a):
    m = manifest(a.first_results)
    if m["max_total_with_first_run"] > p1.BUDGET:
        sys.exit("STOPPED: total would exceed the approved %d requests" % p1.BUDGET)
    body = json.dumps(m, indent=1, sort_keys=True).encode("ascii")
    d = Path(a.out_dir)
    d.mkdir(parents=True, exist_ok=True)
    t = d / "p1_continue_manifest.json"
    if t.exists():
        sys.exit("STOPPED: manifest exists (never overwritten)")
    t.write_bytes(body)
    h = hashlib.sha256(body).hexdigest()
    (d / "p1_continue_manifest.json.sha256").write_text("%s  p1_continue_manifest.json\n" % h, encoding="ascii")
    print("frozen", t, "| remaining", m["max_requests_now"], "| total", m["max_total_with_first_run"], "| SHA-256", h)


def run(a):
    raw = Path(a.manifest).read_bytes()
    if hashlib.sha256(raw).hexdigest() != a.manifest_sha256.strip().lower():
        sys.exit("STOPPED: manifest SHA-256 does not match the frozen value")
    m = json.loads(raw.decode("ascii"))
    if m["max_total_with_first_run"] > p1.BUDGET or m["max_requests_now"] > REMAINING:
        sys.exit("STOPPED: over the approved cap")
    keys = {"UNUSUAL_WHALES": pp.api_key(), "FINVIZ_ELITE": p1.env_value("FINVIZ_API_TOKEN"), "FINNHUB": p1.env_value("FINNHUB_API_KEY")}
    print("P1 continuation: %d requests (total with the first run %d of %d)" % (m["max_requests_now"], m["max_total_with_first_run"], p1.BUDGET))
    for r in m["requests"]:
        print("  %d %-30s %s %s" % (r["n"], r["id"], r["path"], r["params"]))
    print("credentials present:", {k: bool(v) for k, v in keys.items()})
    if not a.execute:
        print("dry run: nothing sent")
        return
    out = Path(a.out_dir)
    (out / "raw").mkdir(parents=True, exist_ok=True)
    lp = out / "p1_continue_results.json"
    if lp.exists():
        sys.exit("STOPPED: results exist (never overwritten)")
    first_raw = Path(a.first_out) / "raw"
    uw_rows = []
    for f in sorted(first_raw.glob("*UW-*.gz")):
        uw_rows += [x for x in json.loads(gzip.open(f).read()).get("data", []) if isinstance(x, dict)]
    res, sent, stop = [], 0, None
    fz_opener = urllib.request.build_opener(FinvizRedirect)
    uw_headers = {"Authorization": "Bearer " + keys["UNUSUAL_WHALES"], "Accept": "application/json", "User-Agent": "US-Stock-Reader/1.0"}

    def save(rec, status, body, lim, final_host=None):
        rec.update(status=status, bytes=len(body), raw_sha256=hashlib.sha256(body).hexdigest(), limit_headers=lim)
        if final_host:
            rec["final_host"] = final_host
        (out / "raw" / ("%02d_%s.gz" % (sent, rec["id"]))).write_bytes(gzip.compress(body, 6))
        res.append(rec)

    for r in m["requests"]:
        if stop or sent >= REMAINING:
            break
        rec = {"id": r["id"], "provider": r["provider"], "path": r["path"], "params": r["params"], "retrieved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        sent += 1
        try:
            if r["provider"] == "UNUSUAL_WHALES":
                st, body, lim = p1.fetch(pp.UW + r["path"] + "?" + urllib.parse.urlencode(r["params"]), uw_headers)
                rows = json.loads(body.decode("utf-8", errors="replace")).get("data") or []
                rec["rows"] = len(rows)
                uw_rows += [x for x in rows if isinstance(x, dict)]
                save(rec, st, body, lim)
            elif r["provider"] == "FINVIZ_ELITE":
                tickers = p1.pick_tickers(uw_rows)
                rec["params"] = {"v": "152", "c": "1,68", "t": "<%d tickers>" % len(tickers), "tickers": tickers}
                url = p1.FINVIZ + "?" + urllib.parse.urlencode({"v": "152", "c": "1,68", "t": ",".join(tickers)}) + "&auth=" + urllib.parse.quote(keys["FINVIZ_ELITE"])
                with fz_opener.open(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=60) as resp:
                    body = resp.read()
                    rec["rows"] = max(0, len(list(csv.reader(io.StringIO(body.decode("utf-8", errors="replace"))))) - 1)
                    save(rec, resp.status, body, {}, urllib.parse.urlparse(resp.geturl()).hostname)
            else:
                st, body, lim = p1.fetch(p1.FINNHUB + "?" + urllib.parse.urlencode(r["params"]), {"X-Finnhub-Token": keys["FINNHUB"], "Accept": "application/json"})
                obj = json.loads(body.decode("utf-8", errors="replace"))
                rec["rows"] = len(obj.get("earningsCalendar") or []) if isinstance(obj, dict) else None
                save(rec, st, body, lim)
        except Exception as e:  # noqa: BLE001
            code = getattr(e, "code", None)
            rec.update(status=code, error=type(e).__name__)
            res.append(rec)
            stop = "HTTP %s" % code if code else "error %s" % type(e).__name__
        print("%-30s status=%s rows=%s %s" % (rec["id"], rec.get("status"), rec.get("rows"), ("STOP: " + stop) if stop else ""))
        if r["provider"] == "UNUSUAL_WHALES":
            time.sleep(2.0)
        elif not stop:
            time.sleep(2.0)
    lp.write_text(json.dumps({"manifest_sha256": a.manifest_sha256.lower(), "requests_sent_now": sent, "stopped": stop, "results": res}, indent=1), encoding="utf-8")
    print("requests sent now:", sent, "| stopped:", stop)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("--first-results", required=True)
    f.add_argument("--out-dir", required=True)
    r = sub.add_parser("run")
    r.add_argument("--manifest", required=True)
    r.add_argument("--manifest-sha256", required=True)
    r.add_argument("--first-out", required=True)
    r.add_argument("--execute", action="store_true")
    r.add_argument("--out-dir", default="")
    a = ap.parse_args()
    freeze(a) if a.cmd == "freeze" else run(a)


if __name__ == "__main__":
    main()
