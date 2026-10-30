"""Smart Move volatility-history DEPTH probe: a FIXED plan of 6 Unusual Whales requests (iv-rank and term-structure for AAPL at
2025-10-01, 2025-04-01 and 2024-10-01). Dry run by default; --execute sends each request once, 2 s apart, no retries, no extra request.
Reuses fetch()/summarise() of provider_probe.py (key read from the dev machine's backend/.env, never printed, no raw bodies stored).
"""
import argparse
import json
import pathlib
import sys
import time
from datetime import datetime

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import provider_probe as pp  # noqa: E402

PLAN = []
for d in ("2025-10-01", "2025-04-01", "2024-10-01"):
    PLAN.append(("UW", "/api/stock/AAPL/iv-rank", {"date": d}, "iv-rank depth at " + d))
    PLAN.append(("UW", "/api/stock/AAPL/volatility/term-structure", {"date": d}, "term-structure depth at " + d))
assert len(PLAN) == 6 and all(p[0] == "UW" for p in PLAN)

ap = argparse.ArgumentParser()
ap.add_argument("--execute", action="store_true")
a = ap.parse_args()
print("PLAN (6 UW requests):")
for i, (k, p, q, why) in enumerate(PLAN, 1):
    print("  %d. %s %s -- %s" % (i, p, q, why))
if not a.execute:
    print("dry run: nothing sent. key present: %s" % bool(pp.api_key()))
    sys.exit(0)
out = HERE.parent / ("depth_probe_results_%s.json" % datetime.now().strftime("%Y%m%d-%H%M%S"))
if out.exists():
    sys.exit("STOPPED: exists")
res = []
for i, (k, p, q, why) in enumerate(PLAN, 1):
    r = pp.fetch(k, p, q)
    r["purpose"] = why
    res.append(r)
    print("%d. status=%s %s" % (i, r.get("status"), r.get("error", "")))
    time.sleep(2.0)
out.write_text(json.dumps({"requests_sent": len(res), "results": res}, indent=1), encoding="utf-8")
print("wrote", out)
