"""P1 calendar comparison: a FROZEN manifest of at most 10 read-only requests (Unusual Whales, Finviz Elite, Finnhub), run once, sequentially, no retries.

    python p1_calendar.py freeze --out-dir <dir> [--today YYYY-MM-DD]
    python p1_calendar.py run --manifest <m.json> --manifest-sha256 <hex> [--execute] --out-dir <dir>

freeze: no request. The nearest trading days (calendar distance 0-3 from today) are fixed in the manifest. Requests: Unusual Whales /api/earnings/premarket and /afterhours,
page 1 per day (4); a page 2 only when a first page returns exactly the page limit (at most 4 more); ONE Finviz export for the 100 largest-market-cap tickers seen in the
Unusual Whales rows (rule recorded, chosen without any price or outcome); ONE Finnhub /calendar/earnings for the same dates. Hard cap 10.
run: refuses unless the hash matches; dry run by default; --execute sends in order, 2 s apart; stops on 401/403/429 or any transport error; records retrieval time, status,
size, SHA-256 and rate-limit headers; the raw bodies are kept compressed in a local folder (not for Git). No key or token is ever printed or written; the Finviz URL is saved
without its auth parameter.
"""
import argparse
import csv
import gzip
import hashlib
import io
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import provider_probe as pp  # noqa: E402

ENV = Path("C:/Users/initi/Downloads/US-STOCKS/backend/.env")
BUDGET, PAGE_LIMIT, FINVIZ_TICKERS = 10, 100, 100
FINVIZ = "https://elite.finviz.com/export.ashx"
FINNHUB = "https://finnhub.io/api/v1/calendar/earnings"
TICKER = re.compile(r"^[A-Z]{1,5}$")


def env_value(name):
    for line in ENV.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.match(r"\s*%s\s*=\s*(.*)$" % name, line)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return ""


def trading_days(today: date):
    sys.path.insert(0, str(HERE.parents[2] / "backend"))
    import market_calendar as mc
    return [(today + timedelta(days=k)).isoformat() for k in range(0, 4) if mc.is_trading_day(today + timedelta(days=k))]


def manifest(today: date):
    days = trading_days(today)
    reqs, n = [], 0
    for d in days:
        for side in ("premarket", "afterhours"):
            n += 1
            reqs.append({"n": n, "id": "UW-%s-%s-p1" % (d, side), "provider": "UNUSUAL_WHALES", "path": "/api/earnings/" + side, "params": {"date": d, "limit": PAGE_LIMIT, "page": 1},
                         "page2_if_rows_equal": PAGE_LIMIT, "max_requests": 2})
    reqs.append({"n": n + 1, "id": "FZ-A", "provider": "FINVIZ_ELITE", "path": "export.ashx", "params": {"v": "152", "c": "1,68", "t": "<%d largest-market-cap tickers from the UW rows above>" % FINVIZ_TICKERS},
                 "ticker_rule": "unique symbols matching [A-Z]{1,5} from the Unusual Whales rows, ordered by marketcap descending then symbol, first %d" % FINVIZ_TICKERS, "max_requests": 1})
    reqs.append({"n": n + 2, "id": "FH-A", "provider": "FINNHUB", "path": "/calendar/earnings", "params": {"from": days[0], "to": days[-1]}, "max_requests": 1})
    cap = sum(r["max_requests"] for r in reqs)
    assert cap <= BUDGET + 0 or cap == BUDGET, cap
    return {"version": "smv-p1-1", "frozen_for_today": today.isoformat(), "trading_days": days, "budget_cap": BUDGET, "requests": reqs, "max_possible_requests": cap,
            "stop_rules": ["401, 403 or 429 stops the run", "any transport error stops the run", "no retries", "never more than %d requests" % BUDGET],
            "read_only": "GET only; no account, billing or configuration endpoint; nothing is written to any database",
            "storage": "raw bodies gzip-compressed in a new local folder with SHA-256 and retrieval time; keys and tokens are never stored"}


def pick_tickers(rows):
    best = {}
    for r in rows:
        s = str(r.get("symbol") or "").upper()
        if not TICKER.match(s):
            continue
        try:
            mc = float(r.get("marketcap"))
        except (TypeError, ValueError):
            continue
        best[s] = max(mc, best.get(s, 0.0))
    return [s for s, _ in sorted(best.items(), key=lambda kv: (-kv[1], kv[0]))][:FINVIZ_TICKERS]


def freeze(a):
    today = date.fromisoformat(a.today) if a.today else datetime.now(timezone.utc).astimezone(__import__("zoneinfo").ZoneInfo("America/New_York")).date()
    body = json.dumps(manifest(today), indent=1, sort_keys=True).encode("ascii")
    d = Path(a.out_dir)
    d.mkdir(parents=True, exist_ok=True)
    t = d / "p1_manifest.json"
    if t.exists():
        sys.exit("STOPPED: manifest exists (never overwritten)")
    t.write_bytes(body)
    h = hashlib.sha256(body).hexdigest()
    (d / "p1_manifest.json.sha256").write_text("%s  p1_manifest.json\n" % h, encoding="ascii")
    print("frozen", t, "| max requests", json.loads(body)["max_possible_requests"], "| SHA-256", h)


def fetch(url, headers, timeout=60):
    req = urllib.request.Request(url, headers=headers)
    with pp.OPENER.open(req, timeout=timeout) as resp:
        body = resp.read()
        lim = {k: v for k, v in resp.headers.items() if re.search(r"limit|remaining|retry|reset|concurr|usage|quota", k, re.I)}
        return resp.status, body, lim


def run(a):
    raw = Path(a.manifest).read_bytes()
    if hashlib.sha256(raw).hexdigest() != a.manifest_sha256.strip().lower():
        sys.exit("STOPPED: manifest SHA-256 does not match the frozen value")
    m = json.loads(raw.decode("ascii"))
    if m["max_possible_requests"] > BUDGET:
        sys.exit("STOPPED: more than %d requests" % BUDGET)
    keys = {"UNUSUAL_WHALES": pp.api_key(), "FINVIZ_ELITE": env_value("FINVIZ_API_TOKEN"), "FINNHUB": env_value("FINNHUB_API_KEY")}
    print("P1 plan: max %d requests; manifest %s verified; trading days %s" % (m["max_possible_requests"], a.manifest_sha256[:12], m["trading_days"]))
    for r in m["requests"]:
        print("  %2d %-26s %s %s (max %d)" % (r["n"], r["id"], r["path"], r["params"], r["max_requests"]))
    print("credentials present (values never shown):", {k: bool(v) for k, v in keys.items()})
    if not a.execute:
        print("dry run: nothing sent")
        return
    if not all(keys.values()):
        sys.exit("STOPPED: a credential is missing")
    out = Path(a.out_dir)
    (out / "raw").mkdir(parents=True, exist_ok=True)
    lp = out / "p1_results.json"
    if lp.exists():
        sys.exit("STOPPED: results exist (never overwritten)")
    res, sent, stop, uw_rows = [], 0, None, []

    def record(rec, status, body, lim, name):
        rec.update(status=status, bytes=len(body), raw_sha256=hashlib.sha256(body).hexdigest(), limit_headers=lim)
        (out / "raw" / (name + ".gz")).write_bytes(gzip.compress(body, 6))
        res.append(rec)

    def guard(rec, e):
        nonlocal stop
        code = getattr(e, "code", None)
        rec.update(status=code, error=type(e).__name__)
        res.append(rec)
        stop = "HTTP %s" % code if code in (401, 403, 429) else "transport error %s" % type(e).__name__

    uw_headers = {"Authorization": "Bearer " + keys["UNUSUAL_WHALES"], "Accept": "application/json", "User-Agent": "US-Stock-Reader/1.0"}
    for r in [x for x in m["requests"] if x["provider"] == "UNUSUAL_WHALES"]:
        page = 1
        while True:
            if sent >= BUDGET or stop:
                break
            params = dict(r["params"], page=page)
            url = pp.UW + r["path"] + "?" + urllib.parse.urlencode(params)
            rec = {"id": r["id"].replace("-p1", "-p%d" % page), "provider": r["provider"], "path": r["path"], "params": params, "retrieved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            sent += 1
            try:
                st, body, lim = fetch(url, uw_headers)
                obj = json.loads(body.decode("utf-8", errors="replace"))
                rows = obj.get("data") if isinstance(obj, dict) else None
                rec["rows"] = len(rows or [])
                record(rec, st, body, lim, "%02d_%s" % (sent, rec["id"]))
                uw_rows += [x for x in (rows or []) if isinstance(x, dict)]
            except Exception as e:  # noqa: BLE001
                guard(rec, e)
                break
            print("%-34s status=%s rows=%s" % (rec["id"], rec.get("status"), rec.get("rows")))
            time.sleep(2.0)
            if rec["rows"] == r["page2_if_rows_equal"] and page == 1:
                page = 2
                continue
            break
        if stop:
            break
    if not stop and sent < BUDGET:
        tickers = pick_tickers(uw_rows)
        fz = next(x for x in m["requests"] if x["provider"] == "FINVIZ_ELITE")
        params = {"v": "152", "c": "1,68", "t": ",".join(tickers)}
        rec = {"id": fz["id"], "provider": fz["provider"], "path": fz["path"], "params": {"v": "152", "c": "1,68", "t": "<%d tickers>" % len(tickers), "tickers": tickers}, "retrieved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        sent += 1
        try:
            st, body, lim = fetch(FINVIZ + "?" + urllib.parse.urlencode(params) + "&auth=" + urllib.parse.quote(keys["FINVIZ_ELITE"]), {"User-Agent": "Mozilla/5.0"})
            rec["rows"] = max(0, len(list(csv.reader(io.StringIO(body.decode("utf-8", errors="replace"))))) - 1)
            record(rec, st, body, lim, "%02d_FZ-A" % sent)
        except Exception as e:  # noqa: BLE001
            guard(rec, e)
        print("%-34s status=%s rows=%s" % (rec["id"], rec.get("status"), rec.get("rows")))
        time.sleep(2.0)
    if not stop and sent < BUDGET:
        fh = next(x for x in m["requests"] if x["provider"] == "FINNHUB")
        rec = {"id": fh["id"], "provider": fh["provider"], "path": fh["path"], "params": fh["params"], "retrieved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        sent += 1
        try:
            st, body, lim = fetch(FINNHUB + "?" + urllib.parse.urlencode(fh["params"]), {"X-Finnhub-Token": keys["FINNHUB"], "Accept": "application/json"})
            obj = json.loads(body.decode("utf-8", errors="replace"))
            rec["rows"] = len((obj or {}).get("earningsCalendar") or []) if isinstance(obj, dict) else None
            record(rec, st, body, lim, "%02d_FH-A" % sent)
        except Exception as e:  # noqa: BLE001
            guard(rec, e)
        print("%-34s status=%s rows=%s" % (rec["id"], rec.get("status"), rec.get("rows")))
    lp.write_text(json.dumps({"manifest_sha256": a.manifest_sha256.lower(), "requests_sent": sent, "budget": BUDGET, "stopped": stop, "results": res}, indent=1), encoding="utf-8")
    print("requests sent:", sent, "| stopped:", stop, "| wrote", lp)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("--out-dir", required=True)
    f.add_argument("--today", default="")
    r = sub.add_parser("run")
    r.add_argument("--manifest", required=True)
    r.add_argument("--manifest-sha256", required=True)
    r.add_argument("--execute", action="store_true")
    r.add_argument("--out-dir", default="")
    a = ap.parse_args()
    freeze(a) if a.cmd == "freeze" else run(a)


if __name__ == "__main__":
    main()
