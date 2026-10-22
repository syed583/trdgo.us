"""Diagnose (and repair) why the cache warmer is not starting.   python warmer_doctor.py

Prints each step: code version, environment, the cached universe entry, a live universe
build, the repair (overwrites a bad cache entry), the warmer queue, and the saved warmer
state. Makes ~10 Unusual Whales calls. Never prints keys.
"""
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def step(name):
    print(f"\n== {name}", flush=True)


step("code version")
try:
    print(subprocess.run(["git", "log", "--oneline", "-1"], cwd=str(HERE), capture_output=True,
                         text=True).stdout.strip())
except Exception as e:  # noqa: BLE001
    print("git unavailable:", e)

step("environment")
import database  # noqa: E402  (loads .env)
import db_cache  # noqa: E402
print("APP_ENV production:", database.is_production(), "| cache namespace:", repr(db_cache.namespace()))

step("cached universe entry (database)")
import universe_service as us  # noqa: E402
held = db_cache.get(us._KEY)
if held is None:
    print("no entry")
else:
    age_h = (time.time() - held[0]) / 3600
    v = held[1]
    print(f"entry age {age_h:.1f}h, value type {type(v).__name__}",
          "-> BAD (null)" if v is None else f"rows={len((v or {}).get('rows') or [])}")

step("live universe build")
good = None
t = time.time()
try:
    good = us.build()
    print(f"OK {good['count']} names in {time.time()-t:.0f}s; rank 1000 = ${good['min_dollar_volume']:,}/day")
except Exception as e:  # noqa: BLE001
    print(f"FAILED after {time.time()-t:.0f}s: {type(e).__name__}: {e}")
    import unusualwhales_service as uw
    print("provider budget view:", {k: v for k, v in uw.budget().items() if k in
                                    ("used_by_this_app", "app_budget", "blocked", "blocked_for_seconds")})

if good:
    step("repair (store the good universe, overwriting any bad entry)")
    import swr  # noqa: E402
    swr._run(us._KEY, lambda: good)
    time.sleep(3)                                   # the DB write runs on a thread
    again = db_cache.get(us._KEY)
    print("stored:", again is not None and isinstance(again[1], dict) and bool(again[1].get("rows")))

step("warmer queue")
try:
    import local_warmer as lw
    q, n = lw.build_queue()
    print(f"{len(q)} names queued ({n} priority); first: {q[:6]}")
    print("daily limit", lw.DAILY_LIMIT, "reserve", lw.RESERVE, "refresh age h", lw.MAX_AGE / 3600)
except Exception as e:  # noqa: BLE001
    print(f"FAILED: {type(e).__name__}: {e}")

step("saved warmer state")
try:
    import local_warmer as lw
    st = lw.load_state()
    print({k: st.get(k) for k in ("status", "done", "total", "calls_day", "error", "updated_at")})
except Exception as e:  # noqa: BLE001
    print(f"FAILED: {type(e).__name__}: {e}")

print("\nNext: restart the service (Restart-Service USStockReader), wait ~3 minutes, run: "
      "python local_warmer.py --status")
