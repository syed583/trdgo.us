"""H1a event-list discovery: ONE Unusual Whales request per symbol of a FROZEN manifest, hard total budget 50 UW + 0 SEC, no retries.

    python h1a_runner.py --manifest <h1a_manifest.json> --manifest-sha256 <hex>            # dry run: prints the plan, sends nothing
    python h1a_runner.py --manifest <...> --manifest-sha256 <hex> --execute --out-dir <dir>

Refuses to run unless the manifest's SHA-256 equals the value given, the manifest holds at most 50 symbols and the output file does not
exist. Requests are `GET /api/earnings/{SYMBOL}?limit=24` (the call the application itself makes), sequential, 2 s apart; a 429 or a
concurrency refusal stops the run (the remaining symbols stay unrequested and are listed). The key is read from the dev machine's
backend/.env and never printed. Only rows with report_date >= 2024-10-01 are kept in the output (plus the count of older rows); no SEC request.
`--base-url` exists only for the offline test against a local fake server (127.0.0.1 only).
"""
import argparse
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

MAX_UW = 50
MAX_SEC = 0
FROM_DATE = "2024-10-01"
DEFAULT_BASE = pp.UW


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--manifest-sha256", required=True)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--out-dir", default="")
    ap.add_argument("--base-url", default=DEFAULT_BASE)
    a = ap.parse_args()
    raw = Path(a.manifest).read_bytes()
    if hashlib.sha256(raw).hexdigest() != a.manifest_sha256.strip().lower():
        sys.exit("STOPPED: manifest SHA-256 does not match the frozen value")
    man = json.loads(raw.decode("ascii"))
    syms = [p["symbol"] for p in man["selected_symbols"]]
    if len(syms) > MAX_UW or len(set(syms)) != len(syms):
        sys.exit("STOPPED: manifest has more than %d symbols or duplicates" % MAX_UW)
    local = a.base_url != DEFAULT_BASE
    if local and not re.match(r"^http://127\.0\.0\.1:\d+$", a.base_url):
        sys.exit("STOPPED: --base-url is for local tests on 127.0.0.1 only")
    print("H1a plan: %d symbols, %d UW requests, %d SEC requests; manifest %s verified" % (len(syms), len(syms), MAX_SEC, a.manifest_sha256[:12]))
    print("symbols:", " ".join(syms))
    if not a.execute:
        print("dry run: nothing sent. key present: %s" % (True if local else bool(pp.api_key())))
        return
    key = "TEST" if local else pp.api_key()
    if not key:
        sys.exit("STOPPED: no key")
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "h1a_events.json"
    if out.exists():
        sys.exit("STOPPED: %s exists (never overwritten)" % out)
    sent = 0
    results, unrequested = [], []
    stop = False
    for sym in syms:
        if stop:
            unrequested.append(sym)
            continue
        if sent >= MAX_UW:
            unrequested.append(sym)
            continue
        url = "%s/api/earnings/%s?limit=24" % (a.base_url, sym)
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + key, "Accept": "application/json", "User-Agent": "US-Stock-Reader/1.0"})
        rec = {"symbol": sym, "requested_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        sent += 1
        try:
            with pp.OPENER.open(req, timeout=60) as r:
                rec["status"] = r.status
                obj = json.loads(r.read().decode("utf-8", errors="replace"))
                rows = pp.find_rows(obj)
                rec["rows_total"] = len(rows)
                keep = [x for x in rows if isinstance(x, dict) and str(x.get("report_date", ""))[:10] >= FROM_DATE]
                rec["rows_older_dropped"] = len(rows) - len(keep)
                rec["events"] = keep
                rec["row_keys"] = sorted({k for x in rows if isinstance(x, dict) for k in x})
        except Exception as e:  # recorded, never retried
            code = getattr(e, "code", None)
            rec["status"] = code
            rec["error"] = type(e).__name__
            if code == 429:
                stop = True
        results.append(rec)
        print("%2d. %-6s status=%s events>=%s: %s" % (sent, sym, rec.get("status"), FROM_DATE, len(rec.get("events", []))))
        time.sleep(0.05 if local else 2.0)
    out.write_text(json.dumps({"manifest_sha256": a.manifest_sha256.lower(), "requests_sent": sent, "uw_budget": MAX_UW, "sec_requests": 0,
                               "unrequested": unrequested, "results": results}, indent=1), encoding="utf-8")
    print("requests sent:", sent, "| unrequested:", len(unrequested), "| wrote", out)


if __name__ == "__main__":
    main()