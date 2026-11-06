"""Budgeted cache warmer for the top-1000 liquid US stocks (priority 4: it yields to everything).

Two modes, chosen by the environment (db_cache.namespace()):

  development (APP_ENV != production)   python local_warmer.py   (one-shot; --status)
      queue  : names reporting in the next 7 days first, then the rest of the 1,000
  production (APP_ENV=production)       started by main.py, runs 24/7 as a thread
      queue  : universe names reporting within the next 10 TRADING days, soonest first

Whether it may run is decided ONLY by the shared API budget controller (uw_budget):

  * the provider quota must be VERIFIED (provider headers, or a dashboard reading recorded with
    uw_budget_cli.py) -- a local counter that merely reset is not enough;
  * it never takes the budget live intraday trading is owed (priority 1 and 2 keep their share
    plus the emergency reserve);
  * its own daily budget and hourly cap (tab_settings.UW_BUDGET);
  * a 429 puts it into a cool-down.

Each of its requests is also admitted individually (priority 4 in the client's fair gate); a
refresh during which any request was refused is DISCARDED rather than stored half-built, and
retried later. Names already warm (< 20 h production / 24 h development) are skipped.
Progress is saved to `local_warm_state` (row 1 = development, row 2 = production) and served at
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

import uw_budget
import uw_priority as pri

def is_production() -> bool:
    import db_cache
    return db_cache.namespace() == ""


WORKERS = int(os.environ.get("WARM_WORKERS") or 3)
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
def day_id(now: "datetime | None" = None) -> str:
    """The provider's counting day: it resets at 8 pm US Eastern, not midnight."""
    from zoneinfo import ZoneInfo
    now = (now or datetime.now(ZoneInfo("America/New_York"))).astimezone(ZoneInfo("America/New_York"))
    return (now + timedelta(hours=4)).date().isoformat()


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


def _force(key: str, fn, tracker) -> bool:
    """Recompute and store `key` NOW. swr.serve would hand back any stored value younger than the
    cache window (48h) without recomputing, so a name that is 20-48h old would never actually
    refresh; the warmer only gets here for names older than MAX_AGE (or absent).

    A build during which the shared budget REFUSED any request (or the gate was busy) is partial: it
    is discarded, never stored as if it were good. Returns False in that case."""
    v = fn()
    if tracker.denied or tracker.busy:
        return False
    if isinstance(v, dict) and v.get("status") not in ("LOADING", "RATE_LIMITED", "BUDGET_EXHAUSTED",
                                                       "BUDGET_THROTTLED", "BUSY", "PROVIDER_NOT_CONFIGURED",
                                                       "ERROR", "BAD_REQUEST"):
        import swr
        swr._run(key, lambda: v)
    return True


def _warm_one(sym: str):
    """True = refreshed; False = failed; None = the budget/gate refused part of it (retry later)."""
    import swr
    import earnings_equity_service as eq
    import earnings_trade_service as ets
    tracker = pri.Tracker()
    with pri.use(pri.WARMER, "warmer", tracker):               # lowest priority: yields to everything
        a = _force(EQ.format(sym), lambda: eq.get_analysis(sym), tracker)
        b = _force(TR.format(sym), lambda: ets.get_summary(sym, "default"), tracker)
    if not (a and b):
        return None
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
    ctl = uw_budget.get()
    queue, n_priority = build_queue()
    ages = warm_ages(queue)
    total = len(queue)
    _state.update(status="RUNNING", total=total, priority_total=n_priority, done=0, priority_done=0,
                  skipped_fresh=0, failed=0, deferred=0, current=None, pause_reason=None,
                  started_at=time.time(), paused_until=None, namespace=db_cache.namespace())
    save_state()
    print(f"warming {total} names ({n_priority} reporting soon first)", flush=True)

    i = 0
    last_reason = None
    while i < total:
        sym = queue[i]
        if sym in ages and ages[sym] < MAX_AGE:               # already warm (restart / resume)
            _state["done"] += 1
            _state["skipped_fresh"] += 1
            _state["priority_done"] += 1 if i < n_priority else 0
            i += 1
            continue
        # The shared budget decides. It yields to live intraday trading, needs a VERIFIED provider
        # quota, honours the warmer's daily and hourly budgets, and cools down after a 429.
        ok, reason, wait = ctl.warmer_gate()
        if not ok:
            snap = ctl.snapshot()
            _state.update(status="PAUSED", pause_reason=reason, paused_until=time.time() + wait,
                          current=None, quota_state=snap["quota_state"],
                          calls_today=snap["budgets"]["warmer"]["used"],
                          calls_limit=snap["budgets"]["warmer"]["budget"], api_remaining=snap["remaining"])
            save_state()
            if reason != last_reason:
                print(f"warmer paused: {reason} (re-checking every {min(wait, 300):.0f}s)", flush=True)
                last_reason = reason
            time.sleep(min(wait, 300))
            continue
        last_reason = None
        _state.update(status="RUNNING", pause_reason=None, paused_until=None)
        # A small batch in parallel; the client's fair gate caps what the warmer may hold on the wire.
        batch: list[str] = []
        for k in range(i, min(total, i + WORKERS)):
            if queue[k] in ages and ages[queue[k]] < MAX_AGE:
                break                                         # leave fresh names to the skip branch
            batch.append(queue[k])
        batch = batch or [sym]
        _state["current"] = ",".join(batch)
        results: dict[str, object] = {}

        def _one(x: str) -> None:
            try:
                results[x] = _warm_one(x)
            except Exception as e:  # noqa: BLE001
                results[x] = False
                print(f"  {x}: {type(e).__name__}: {e}", flush=True)

        with ThreadPoolExecutor(max_workers=len(batch)) as pool:
            list(pool.map(_one, batch))
        advanced = 0
        for x in batch:                                       # a refused (None) name stays in the queue
            if results.get(x) is None:
                _state["deferred"] += 1
                break
            _state["done"] += 1
            _state["priority_done"] += 1 if i < n_priority else 0
            if not results.get(x):
                _state["failed"] += 1
            i += 1
            advanced += 1
        snap = ctl.snapshot()
        _state.update(calls_today=snap["budgets"]["warmer"]["used"], calls_limit=snap["budgets"]["warmer"]["budget"],
                      api_remaining=snap["remaining"], quota_state=snap["quota_state"])
        save_state()
        print(f"  {_state['done']}/{total}  last {batch[-1]}  warmer calls today "
              f"{snap['budgets']['warmer']['used']}/{snap['budgets']['warmer']['budget']}", flush=True)
        if advanced == 0:
            time.sleep(5)                                     # nothing moved: do not spin on a refusal
    _state.update(status="DONE", current=None)
    save_state()
    print("done", _state, flush=True)
    return 0


def run(forever: bool = False) -> int:
    """One pass (development) or an endless daily cycle (production)."""
    pri.set_thread_default(pri.WARMER, "warmer")              # everything this thread does is priority 4
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
