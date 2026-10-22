"""Budgeted cache warmer for the top-1000 liquid US stocks.

Two modes, chosen by the environment (db_cache.namespace()):

  development (APP_ENV != production)   python local_warmer.py   (one-shot; --status)
      queue  : names reporting in the next 7 days first, then the rest of the 1,000
      budget : 15,000 calls/day, 2,000 kept in reserve
      refresh: a name warmed < 24h ago is skipped

  production (APP_ENV=production)       started by main.py, runs 24/7 as a thread
      queue  : universe names reporting within the next 10 TRADING days, soonest first
      budget : 13,000 calls/day TOTAL (provider's own counter when it sends one),
               1,500 kept in reserve for live requests and the capture job
      refresh: every name is re-warmed once a day (older than 20h); when the day's
               allowance is spent it sleeps until the 8 pm US Eastern reset

Common: universe_service ranks the top 1,000 by average daily dollar volume; results land
in the environment-namespaced DB cache (so they survive restarts); before each batch the
daily budget is checked, never spending into the reserve; progress is saved to the
`local_warm_state` table (row 1 = development, row 2 = production) and served at
GET /api/warm/progress.
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

def is_production() -> bool:
    import db_cache
    return db_cache.namespace() == ""


RESERVE = int(os.environ.get("WARM_RESERVE") or (1500 if is_production() else 2000))
WORKERS = int(os.environ.get("WARM_WORKERS") or 3)
DAILY_LIMIT = int(os.environ.get("WARM_DAILY_LIMIT") or (13000 if is_production() else 15000))   # plan: 15,000/day
MAX_AGE = (20 if is_production() else 24) * 3600.0          # refresh cadence
EQ = "earn:equity:{}"
TR = "earn:trade:default:{}"

_state: dict = {"status": "IDLE"}


# ----------------------------------------------------------------- state ----
def _ensure_table(db) -> None:
    from sqlalchemy import text
    db.execute(text("CREATE TABLE IF NOT EXISTS local_warm_state ("
                    "id INTEGER PRIMARY KEY, state TEXT NOT NULL, updated_at DOUBLE PRECISION NOT NULL)"))


def _row_id() -> int:
    return 2 if is_production() else 1


def save_state() -> None:
    from sqlalchemy import text
    from database import SessionLocal
    _state["updated_at"] = time.time()
    db = SessionLocal()
    try:
        _ensure_table(db)
        db.execute(text("INSERT INTO local_warm_state (id, state, updated_at) VALUES (:i, :s, :t) "
                        "ON CONFLICT (id) DO UPDATE SET state = :s, updated_at = :t"),
                   {"i": _row_id(), "s": json.dumps(_state, default=str), "t": _state["updated_at"]})
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
        row = db.execute(text("SELECT state FROM local_warm_state WHERE id = :i"), {"i": _row_id()}).first()
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


def trading_days_window(n: int = 10, start: "datetime | None" = None) -> int:
    """Calendar days from today that cover the next `n` trading days (weekends skipped)."""
    d = (start or datetime.now()).date()
    seen = cal_days = 0
    while seen < n:
        d += timedelta(days=1)
        cal_days += 1
        if d.weekday() < 5:
            seen += 1
    return cal_days


def build_queue() -> tuple[list[str], int]:
    """(ordered symbols, number of priority names).
    Production: universe names reporting within the next 10 trading days, soonest first
    (all of them are 'priority'). Development: next 7 days first, then the rest of the 1,000."""
    import universe_service as us
    import uw_earnings_calendar as cal
    u = us.get(wait=240)                          # the first build takes ~25s
    if not u:
        raise RuntimeError("universe unavailable (Unusual Whales screener)")
    ranked = [r["ticker"] for r in u["rows"]]
    inuni = set(ranked)
    soon: list[str] = []
    window = trading_days_window(10) if is_production() else 7
    rows = sorted((cal.calendar(days=window) or {}).get("rows") or [], key=lambda r: str(r.get("date") or ""))
    for r in rows:
        s_ = (r.get("symbol") or "").upper()
        if s_ in inuni and s_ not in soon:
            soon.append(s_)
    if is_production():
        return soon, len(soon)
    rest = [x for x in ranked if x not in set(soon)]
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
        limit, used = min(b["app_budget"], DAILY_LIMIT), b["used_by_this_app"]
    else:
        limit, used = DAILY_LIMIT, _state.get("calls_day", 0)
    return {"used_by_this_app": used, "app_budget": limit, "app_left": max(0, limit - used),
            "blocked": b["blocked"], "blocked_for": b.get("blocked_for_seconds", 0)}


def _force(key: str, fn) -> None:
    """Recompute and store `key` NOW. swr.serve would hand back any stored value younger
    than the cache window (48h) without recomputing, so a name that is 20-48h old would
    never actually refresh; the warmer only gets here for names older than MAX_AGE (or
    absent), so a real rebuild is exactly what is wanted. A failed/empty build keeps the
    previous value."""
    import swr
    v = fn()
    if isinstance(v, dict) and v.get("status") not in ("LOADING", "RATE_LIMITED", "BUDGET_EXHAUSTED",
                                                       "PROVIDER_NOT_CONFIGURED", "ERROR", "BAD_REQUEST"):
        swr._run(key, lambda: v)


def _warm_one(sym: str) -> bool:
    import swr
    import earnings_equity_service as eq
    import earnings_trade_service as ets
    _force(EQ.format(sym), lambda: eq.get_analysis(sym))
    _force(TR.format(sym), lambda: ets.get_summary(sym, "default"))
    try:
        import gemini_service as gm
        gm.hit_rate_bias(sym, None, fetch=True)        # Finnhub (not UW budget); cached 6h
    except Exception:  # noqa: BLE001
        pass
    eq_v = swr.peek(EQ.format(sym))
    return isinstance(eq_v, dict) and bool(eq_v.get("params")) and swr.peek(TR.format(sym)) is not None


# -------------------------------------------------------------------- run ---
def _run_once() -> int:
    import db_cache
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


def run(forever: bool = False) -> int:
    """One pass (development) or an endless daily cycle (production)."""
    if not forever:
        return _run_once()
    while True:                                   # production: never let the warmer die
        try:
            _run_once()
            time.sleep(900)                       # queue empty -> look again in 15 min (new day, new calendar)
        except Exception as e:  # noqa: BLE001
            print(f"warmer error: {type(e).__name__}: {e}", flush=True)
            _state.update(status="ERROR", error=f"{type(e).__name__}: {e}")
            time.sleep(300)


def start_background() -> None:
    """Start the endless warmer on a daemon thread (production server only)."""
    import threading
    threading.Thread(target=lambda: run(forever=True), name="budgeted-cache-warmer", daemon=True).start()


if __name__ == "__main__":
    if "--status" in sys.argv:
        print(json.dumps(load_state(), indent=2))
        sys.exit(0)
    sys.exit(run())
