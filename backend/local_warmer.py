"""Local cache warmer for the top-1000 liquid US stocks (development machines only).

    python local_warmer.py            # warm, pausing/resuming around the daily API limit
    python local_warmer.py --status   # print the saved progress and exit

* Universe: universe_service (top 1,000 by average daily dollar volume).
* Order: names reporting earnings in the next 7 days first (soonest first), then the
  rest of the 1,000 by liquidity rank.
* Budget: before every name it reads the provider's own daily counter. It never spends
  into the reserve (WARM_RESERVE, default 2,000 calls, left for live use and for
  production, which shares the same key). When the day's allowance is used it sleeps
  until the provider's reset (8 pm US Eastern) and resumes by itself.
* Persistence: results land in the environment-namespaced DB cache, so they survive
  restarts; a name already warm (< 24h old) is skipped, so a restart resumes where it
  left off. Progress is saved to the `local_warm_state` table and served at
  GET /api/warm/progress.
* Refuses to run when APP_ENV=production.
"""
from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

RESERVE = int(os.environ.get("WARM_RESERVE") or 2000)
WORKERS = int(os.environ.get("WARM_WORKERS") or 3)
DAILY_LIMIT = int(os.environ.get("WARM_DAILY_LIMIT") or 15000)   # Unusual Whales plan: 15,000 calls/day
MAX_AGE = 24 * 3600.0
EQ = "earn:equity:{}"
TR = "earn:trade:default:{}"

_state: dict = {"status": "IDLE"}


# ----------------------------------------------------------------- state ----
def _ensure_table(db) -> None:
    from sqlalchemy import text
    db.execute(text("CREATE TABLE IF NOT EXISTS local_warm_state ("
                    "id INTEGER PRIMARY KEY, state TEXT NOT NULL, updated_at DOUBLE PRECISION NOT NULL)"))


def save_state() -> None:
    from sqlalchemy import text
    from database import SessionLocal
    _state["updated_at"] = time.time()
    db = SessionLocal()
    try:
        _ensure_table(db)
        db.execute(text("INSERT INTO local_warm_state (id, state, updated_at) VALUES (1, :s, :t) "
                        "ON CONFLICT (id) DO UPDATE SET state = :s, updated_at = :t"),
                   {"s": json.dumps(_state, default=str), "t": _state["updated_at"]})
        db.commit()
    except Exception:  # noqa: BLE001 - progress is best effort
        db.rollback()
    finally:
        db.close()


def load_state() -> dict:
    from sqlalchemy import text
    from database import SessionLocal
    db = SessionLocal()
    try:
        _ensure_table(db)
        row = db.execute(text("SELECT state FROM local_warm_state WHERE id = 1")).first()
        db.commit()
        return json.loads(row[0]) if row else {"status": "NOT_STARTED"}
    except Exception:  # noqa: BLE001
        db.rollback()
        return {"status": "UNKNOWN"}
    finally:
        db.close()


# ----------------------------------------------------------------- helpers --
def next_reset_epoch(now: datetime | None = None) -> float:
    """Next 8:00 pm US Eastern (when the provider's daily counter resets)."""
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    now = (now or datetime.now(et)).astimezone(et)
    reset = now.replace(hour=20, minute=0, second=0, microsecond=0)
    if reset <= now:
        reset += timedelta(days=1)
    return reset.timestamp()


def build_queue() -> tuple[list[str], int]:
    """(ordered symbols, number of priority names reporting in the next 7 days)."""
    import universe_service as us
    import uw_earnings_calendar as cal
    u = us.get()
    if not u:
        raise RuntimeError("universe unavailable (Unusual Whales screener)")
    ranked = [r["ticker"] for r in u["rows"]]
    inuni = set(ranked)
    soon: list[str] = []
    rows = sorted((cal.calendar(days=7) or {}).get("rows") or [], key=lambda r: str(r.get("date") or ""))
    for r in rows:
        s = (r.get("symbol") or "").upper()
        if s in inuni and s not in soon:
            soon.append(s)
    rest = [s for s in ranked if s not in set(soon)]
    return soon + rest, len(soon)


def warm_ages(symbols: list[str]) -> dict[str, float]:
    """symbol -> age (s) of the OLDER of its two cache entries; missing = absent."""
    import db_cache
    keys = [k.format(s) for s in symbols for k in (EQ, TR)]
    held = db_cache.get_many(keys)
    now = time.time()
    out: dict[str, float] = {}
    for s in symbols:
        a, b = held.get(EQ.format(s)), held.get(TR.format(s))
        if a and b and isinstance(a[1], dict) and a[1].get("params"):
            out[s] = now - min(a[0], b[0])
    return out


def _calls() -> int:
    import unusualwhales_service as uw
    return int(uw._metrics.get("net_calls", 0))


def day_id(now: datetime | None = None) -> str:
    """The provider's counting day: it resets at 8 pm US Eastern, not midnight."""
    from zoneinfo import ZoneInfo
    now = (now or datetime.now(ZoneInfo("America/New_York"))).astimezone(ZoneInfo("America/New_York"))
    return (now + timedelta(hours=4)).date().isoformat()


def _budget() -> dict:
    """Calls used / left today. Uses the provider's own daily headers when it sends
    them; otherwise this warmer's own persisted per-day counter against the 15,000
    plan limit (the app's default ceiling of 10,000,000 is meaningless here)."""
    import unusualwhales_service as uw
    b = uw.budget()
    if _state.get("day_id") != day_id():                  # a new counting day began
        _state["day_id"], _state["calls_day"] = day_id(), 0
    if b.get("daily_limit_known"):
        limit, used = b["app_budget"], b["used_by_this_app"]
    else:
        limit, used = DAILY_LIMIT, _state.get("calls_day", 0)
    return {"used_by_this_app": used, "app_budget": limit, "app_left": max(0, limit - used),
            "blocked": b["blocked"], "blocked_for": b.get("blocked_for_seconds", 0)}


def _warm_one(sym: str) -> bool:
    import swr
    import signal_history_service as sh
    sh.warm_earnings([sym])
    sh.warm_trade([sym])
    try:
        import gemini_service as gm
        gm.hit_rate_bias(sym, None, fetch=True)        # Finnhub (not UW budget)
    except Exception:  # noqa: BLE001
        pass
    eq = swr.peek(EQ.format(sym))
    return isinstance(eq, dict) and bool(eq.get("params")) and swr.peek(TR.format(sym)) is not None


# -------------------------------------------------------------------- run ---
def run() -> int:
    import db_cache
    if db_cache.namespace() == "":
        print("refusing to run: APP_ENV=production (this warmer is for development machines)")
        return 2
    queue, n_priority = build_queue()
    ages = warm_ages(queue)
    total = len(queue)
    cost_hist: list[int] = []
    prev = load_state()                                   # resume today's call count after a restart
    if prev.get("day_id") == day_id():
        _state["day_id"], _state["calls_day"] = prev["day_id"], prev.get("calls_day", 0)
    else:
        # Fresh day: start from WARM_START_USED (calls already spent today by other
        # processes -- earlier warm-ups, health checks -- that this counter cannot see).
        _state["day_id"], _state["calls_day"] = day_id(), int(os.environ.get("WARM_START_USED") or 0)
    _state.update(status="RUNNING", total=total, priority_total=n_priority, done=0, priority_done=0,
                  skipped_fresh=0, failed=0, calls_used_run=0, current=None,
                  started_at=time.time(), paused_until=None, namespace=db_cache.namespace())
    save_state()
    print(f"warming {total} names ({n_priority} reporting within 7 days first); reserve={RESERVE}", flush=True)

    i = 0
    while i < total:
        sym = queue[i]
        if sym in ages and ages[sym] < MAX_AGE:               # already warm (restart / resume)
            _state["done"] += 1
            _state["skipped_fresh"] += 1
            _state["priority_done"] += 1 if i < n_priority else 0
            i += 1
            continue
        est = max(25, int(max(cost_hist[-20:] or [25]) * 1.3))
        b = _budget()
        if b["blocked"]:                                      # a short provider rate-limit block, NOT the daily cap
            _state.update(status="RATE_LIMITED", current=None)
            time.sleep(min(120, max(5, b["blocked_for"] + 2)))
            _state["status"] = "RUNNING"
            continue
        if b["app_left"] - RESERVE < est:
            until = next_reset_epoch()
            _state.update(status="PAUSED_BUDGET", paused_until=until, current=None,
                          calls_today=b["used_by_this_app"], calls_limit=b["app_budget"])
            save_state()
            print(f"budget: {b['used_by_this_app']}/{b['app_budget']} used (reserve {RESERVE}); "
                  f"pausing until {datetime.fromtimestamp(until):%Y-%m-%d %H:%M}", flush=True)
            while time.time() < until:                        # sleeps through the reset, then re-checks
                time.sleep(min(300, max(5, until - time.time())))
                if _budget()["app_left"] - RESERVE >= est:
                    break
            _state.update(status="RUNNING", paused_until=None)
            continue
        # Warm a small batch in parallel (the provider client paces and caps in-flight
        # requests itself, so this only overlaps the waiting). Budget is checked per batch.
        batch: list[str] = []
        for k in range(i, min(total, i + WORKERS)):
            if queue[k] in ages and ages[queue[k]] < MAX_AGE:
                break                                         # leave fresh names to the skip branch
            batch.append(queue[k])
        batch = batch or [sym]
        if len(batch) > 1 and _budget()["app_left"] - RESERVE < est * len(batch):
            batch = batch[:1]
        before = _calls()
        _state["current"] = ",".join(batch)
        results: dict[str, bool] = {}

        def _one(x: str) -> None:
            try:
                results[x] = _warm_one(x)
            except Exception as e:  # noqa: BLE001
                results[x] = False
                print(f"  {x}: {type(e).__name__}: {e}", flush=True)

        with ThreadPoolExecutor(max_workers=len(batch)) as pool:
            list(pool.map(_one, batch))
        spent = max(0, _calls() - before)
        per = max(1, spent // len(batch))
        cost_hist.extend([per] * len(batch))
        _state["calls_used_run"] += spent
        _state["calls_day"] = _state.get("calls_day", 0) + spent
        for x in batch:
            _state["done"] += 1
            _state["priority_done"] += 1 if i < n_priority else 0
            if not results.get(x):
                _state["failed"] += 1
            i += 1
        b = _budget()
        _state.update(calls_today=b["used_by_this_app"], calls_limit=b["app_budget"],
                      avg_calls_per_name=round(sum(cost_hist) / len(cost_hist), 1))
        save_state()
        print(f"  {_state['done']}/{total}  last {batch[-1]} ({spent} calls / {len(batch)})  "
              f"today {b['used_by_this_app']}/{b['app_budget']}", flush=True)
        continue
    _state.update(status="DONE", current=None)
    save_state()
    print("done", _state, flush=True)
    return 0


if __name__ == "__main__":
    if "--status" in sys.argv:
        print(json.dumps(load_state(), indent=2))
        sys.exit(0)
    sys.exit(run())
