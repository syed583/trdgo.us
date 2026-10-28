import os, sys, time
os.environ["APP_ENV"] = "development"
MAIN = r"C:\Users\initi\Downloads\US-STOCKS\backend"
sys.path.insert(0, MAIN)
import database as d                       # main checkout's dev-resolved database module
assert d.PROD_REF not in (d.DATABASE_URL or ""), "would be PRODUCTION - abort"
sys.path.insert(0, r"wt_sig\backend\tools")
import verify_earnings_readonly as v
# the SQL runs against the DEV database through the read-only runner; calendar names come from a fake reader
rows = v._run_ro("SELECT COUNT(*) n FROM model_predictions")
print("dev model_predictions rows:", rows)
fake_reader = lambda ks: {k: (time.time(), {"rows": [{"symbol": "AMZN"}, {"symbol": "EAT"}, {"symbol": "GRMN"}]}) for k in ks}
out = v.snapshots_stage(reader=fake_reader, require_prod=False)
for k, t in out["tables"].items():
    print(k, "->", t if isinstance(t, dict) else f"{len(t)} rows", (t[:2] if isinstance(t, list) else ""))
print(out["moves_distribution_calendar_names"]); print(out["unverifiable"]["conclusion"][:60])
# the write guard: a write inside the read-only runner must be refused by the server
try:
    v._run_ro("CREATE TEMP TABLE zz_probe(x int)"); print("WRITE ALLOWED?!")
except Exception as e: print("write refused:", type(e).__name__, str(e).splitlines()[0][:90])
