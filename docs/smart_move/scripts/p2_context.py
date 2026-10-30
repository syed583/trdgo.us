"""P2: at most 40 read-only Unusual Whales volatility-context requests for the previously selected development events (hash-ordered, outcome-blind selection recorded in the
frozen manifest). Sequential, 2 s apart, no retries; stops on 401/403/429 or after 3 consecutive failures; a 404/422 on one request is recorded as the answer for that event.

    python p2_context.py run --manifest <live_verification_manifest_PROPOSED_v2.json> --manifest-sha256 <hex> [--execute] --out-dir <new dir>
"""
import argparse
import gzip
import hashlib
import json
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import p1_calendar as p1  # noqa: E402
import provider_probe as pp  # noqa: E402

BUDGET = 40


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run"])
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--manifest-sha256", required=True)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    raw = Path(a.manifest).read_bytes()
    if hashlib.sha256(raw).hexdigest() != a.manifest_sha256.strip().lower():
        sys.exit("STOPPED: manifest SHA-256 does not match the frozen value")
    reqs = json.loads(raw.decode("ascii"))["P2_recommended_40_event_context_sample"]["requests"]
    if len(reqs) > BUDGET:
        sys.exit("STOPPED: more than %d requests" % BUDGET)
    key = pp.api_key()
    print("P2 plan: %d requests; manifest %s verified; credential present: %s" % (len(reqs), a.manifest_sha256[:12], bool(key)))
    for r in reqs[:3] + reqs[-2:]:
        print("  %s %s %s" % (r["id"], r["path"], r["params"]))
    if not a.execute:
        print("dry run: nothing sent")
        return
    out = Path(a.out_dir)
    (out / "raw").mkdir(parents=True, exist_ok=True)
    lp = out / "p2_results.json"
    if lp.exists() or not key:
        sys.exit("STOPPED: results exist or no credential")
    res, sent, fails, stop = [], 0, 0, None
    hdr = {"Authorization": "Bearer " + key, "Accept": "application/json", "User-Agent": "US-Stock-Reader/1.0"}
    for r in reqs:
        if sent >= BUDGET or stop:
            break
        rec = {"id": r["id"], "path": r["path"], "params": r["params"], "event": r["event"], "retrieved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        sent += 1
        try:
            st, body, lim = p1.fetch(pp.UW + r["path"] + "?" + urllib.parse.urlencode(r["params"]), hdr, timeout=90)
            rec.update(status=st, bytes=len(body), raw_sha256=hashlib.sha256(body).hexdigest(), limit_headers=lim)
            (out / "raw" / ("%s.gz" % r["id"])).write_bytes(gzip.compress(body, 6))
            fails = 0
        except Exception as e:  # noqa: BLE001
            code = getattr(e, "code", None)
            rec.update(status=code, error=type(e).__name__)
            fails += 1
            if code in (401, 403, 429):
                stop = "HTTP %s" % code
            elif fails >= 3:
                stop = "3 consecutive failures"
        res.append(rec)
        print("%s status=%s bytes=%s %s" % (r["id"], rec.get("status"), rec.get("bytes"), ("STOP: " + stop) if stop else ""))
        time.sleep(2.0)
    lp.write_text(json.dumps({"manifest_sha256": a.manifest_sha256.lower(), "requests_sent": sent, "budget": BUDGET, "stopped": stop, "results": res}, indent=1), encoding="utf-8")
    print("requests sent:", sent, "| stopped:", stop)


if __name__ == "__main__":
    main()
