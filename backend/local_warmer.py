"""Budgeted cache warmer for the top-1000 liquid US stocks.

Two modes, chosen by the environment (db_cache.namespace()):

  development (APP_ENV != production)   python local_warmer.py   (one-shot; --status)
      queue  : names reporting in the next 7 days first, then the rest of the 1,000
      budget : 15,000 calls/day, 2,000 kept in reserve
      refresh: a name warmed < 24h ago is skipped

  production (APP_ENV=production)       started by main.py, runs 24/7 as a thread
      queue  : universe names reporting within the next 10 TRADING days, soonest first
      budget : NONE on the unlimited plan (tab_settings.UW_PLAN["unlimited_daily"]); the provider's only limit
               is 3 simultaneous requests per token, enforced by the shared client. Warming runs in ONE
               background slot, yields to any waiting live request, stays small while the market is open
               and backs off (jittered, bounded) after a 429. The old 13,000/day cap applies only if the
               plan setting says the account has a daily quota.
      refresh: every name is re-warmed once a day (older than 20h); progress and per-symbol retry state
               are saved, a symbol with missing data ends as PARTIAL_DATA (never "warmed")

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
WORKERS = int(os.environ.get("WARM_WORKERS") or 1)   # unused by the sequential background warmer; kept for the doctor
DAILY_LIMIT = int(os.environ.get("WARM_DAILY_LIMIT") or (13000 if is_production() else 15000))   # plan: 15,000/day
MAX_AGE = (20 if is_production() else 24) * 3600.0          # refresh cadence
EQ = "earn:equity:{}"
TR = "earn:trade:default:{}"

_state: dict = {"status": "IDLE"}

# symbol -> report date (ISO) of the queue built by build_queue(); lets the warmer pick the refresh age by how close the report is
_event_dates: dict = {}


def max_age_for(sym: str) -> float:
    """Seconds a name's cache may age before it is re-warmed: shorter for names reporting within a couple of days
    (freshness_policy.DEFAULTS, overridable with EARN_FRESHNESS_JSON). Falls back to MAX_AGE if the policy cannot be read."""
    try:
        import earnings_clock as ec
        import freshness_policy as fp
        return fp.warm_max_age_seconds(ec.days_to(_event_dates.get(sym)))
    except Exception:  # noqa: BLE001 - the policy must never stop the warmer
        return MAX_AGE


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
    _event_dates.clear()
    for r in rows:
        s_ = (r.get("symbol") or "").upper()
        if s_ in inuni and s_ not in soon:
            soon.append(s_)
            _event_dates[s_] = r.get("date")
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


_seen: dict = {"day": None, "n": 0}


def sync_counter() -> None:
    """Fold EVERY Unusual Whales request made inside this process (the warmer, the other
    background loops, page views) into the persisted daily count. The provider sends no
    daily-usage headers here, so this is the only guard; counting just the warmer's own
    batches missed everything else and let the day run past the cap."""
    import unusualwhales_service as uw
    day, cur = uw._spent["day"], uw._spent["count"]
    if _seen["day"] is None:                       # first sample: nothing to add yet
        _seen.update(day=day, n=cur)
        return
    delta = (cur - _seen["n"]) if day == _seen["day"] else cur   # a new counting day began
    _seen.update(day=day, n=cur)
    if _state.get("day_id") != day_id():
        _state["day_id"], _state["calls_day"] = day_id(), 0
    _state["calls_day"] = _state.get("calls_day", 0) + max(0, delta)
    _state["calls_used_run"] = _state.get("calls_used_run", 0) + max(0, delta)


def _calls() -> int:
    import unusualwhales_service as uw
    return int(uw._metrics.get("net_calls", 0))


def day_id(now: datetime | None = None) -> str:
    """The provider's counting day: it resets at 8 pm US Eastern, not midnight."""
    from zoneinfo import ZoneInfo
    now = (now or datetime.now(ZoneInfo("America/New_York"))).astimezone(ZoneInfo("America/New_York"))
    return (now + timedelta(hours=4)).date().isoformat()


def in_live_hours(now: datetime | None = None) -> bool:
    """Weekdays 04:00-20:00 US Eastern: the live intraday engine owns the connections, so warming stays small."""
    from zoneinfo import ZoneInfo
    now = (now or datetime.now(ZoneInfo("America/New_York"))).astimezone(ZoneInfo("America/New_York"))
    return now.weekday() < 5 and 4 <= now.hour < 20


def _plan() -> dict:
    import tab_settings as ts
    return getattr(ts, "UW_PLAN", {})


def _daily_gate() -> tuple[bool, str]:
    """(may we spend more today?, why not). On the UNLIMITED plan there is no daily ceiling at all; the
    only limit is concurrency, which the client enforces. The old 13,000 cap is used ONLY when the plan
    setting says the account has a daily quota."""
    if _plan().get("unlimited_daily", True):
        return True, ""
    import unusualwhales_service as uw
    b = uw.budget()
    if _state.get("day_id") != day_id():
        _state["day_id"], _state["calls_day"] = day_id(), 0
    limit = int(_plan().get("daily_limit_if_capped") or DAILY_LIMIT)
    used = b["used_by_this_app"] if b.get("daily_limit_known") else _state.get("calls_day", 0)
    if limit - used - RESERVE < 25:
        return False, f"daily quota used ({used} of {limit}; {RESERVE} kept in reserve) - resumes after the 8 pm ET reset"
    return True, ""


def _hourly_gate() -> tuple[bool, str, float]:
    """A small clock-hour cap while the market is open (live trading owns the connections), a large one after."""
    p = _plan()
    cap = int(p.get("hourly_cap_live_hours", 400) if in_live_hours() else p.get("hourly_cap_off_hours", 6000))
    h = datetime.now().strftime("%Y%m%d%H")
    if _state.get("hour_id") != h:
        _state["hour_id"], _state["hour_calls"] = h, 0
    if _state["hour_calls"] >= cap:
        wait = 3600 - (time.time() % 3600)
        return False, f"hourly cap reached ({_state['hour_calls']} of {cap} warming requests this hour)" + \
            (" - the market is open, so warming stays small" if in_live_hours() else ""), wait
    return True, "", 0.0


def _provider_health() -> tuple[str, str]:
    """(OK|THROTTLED|API_ERROR, detail) from the shared client's own state."""
    import unusualwhales_service as uw
    cd = uw.background_cooldown_remaining()
    if cd > 0:
        return "THROTTLED", f"Unusual Whales answered 429; warming backs off for {int(cd)}s"
    if time.time() < uw._blocked_until:
        return "THROTTLED", f"Unusual Whales rate limit; waiting {int(uw._blocked_until - time.time())}s"
    return "OK", ""


def _apply(status: str, reason: str | None = None, **kw) -> None:
    _state.update(status=status, pause_reason=reason, **kw)


def _outcome(sym: str) -> str:
    """ok | partial: what the cache holds for `sym` after a build. Never 'ok' with required data missing."""
    import swr
    eq_v, tr_v = swr.peek(EQ.format(sym)), swr.peek(TR.format(sym))
    if isinstance(eq_v, dict) and eq_v.get("params") and tr_v is not None:
        return "ok"
    return "partial"


def _force(key: str, fn) -> None:
    """Recompute and store `key` NOW (swr.serve would return a stored value younger than the 48h window
    without recomputing). Only reached for names older than MAX_AGE or absent. A failed or empty build
    keeps the previous value."""
    import swr
    v = fn()
    if isinstance(v, dict) and v.get("status") not in ("LOADING", "RATE_LIMITED", "BUDGET_EXHAUSTED",
                                                       "PROVIDER_NOT_CONFIGURED", "ERROR", "BAD_REQUEST"):
        swr._run(key, lambda: v)


def _warm_one(sym: str) -> str:
    import earnings_equity_service as eq
    import earnings_trade_service as ets
    _force(EQ.format(sym), lambda: eq.get_analysis(sym))
    _force(TR.format(sym), lambda: ets.get_summary(sym, "default"))
    try:
        import gemini_service as gm
        gm.hit_rate_bias(sym, None, fetch=True)        # Finnhub (not UW); cached 6h
    except Exception:  # noqa: BLE001
        pass
    return _outcome(sym)


def _paused_manually() -> bool:
    return str(os.environ.get("WARM_PAUSED", "")).strip().lower() in ("1", "true", "yes")


def _sleep(seconds: float) -> None:
    time.sleep(max(0.0, min(seconds, 300.0)))


def summary(state: dict | None = None) -> dict:
    """Counts for the status endpoint: complete / pending / failed / retrying."""
    state = _state if state is None else state
    total = int(state.get("total") or 0)
    syms = state.get("symbols") or {}
    failed = sum(1 for v in syms.values() if v.get("s") == "failed")
    partial = sum(1 for v in syms.values() if v.get("s") == "partial")
    retrying = sum(1 for v in syms.values() if v.get("s") == "retry")
    complete = int(state.get("complete") or 0)
    pending = max(0, total - complete - failed - partial)
    return {"total": total, "complete": complete, "pending": pending, "failed": failed, "partial": partial,
            "retrying": retrying, "percent": round(100.0 * complete / total, 1) if total else 0.0}


# -------------------------------------------------------------------- run ---
def _run_once() -> int:
    import unusualwhales_service as uw
    was = uw.is_background()
    uw.set_background(True)                  # everything this run requests is lowest priority
    try:
        return _run_pass()
    finally:
        uw.set_background(was)


def _run_pass() -> int:
    import db_cache
    import unusualwhales_service as uw
    queue, n_priority = build_queue()
    ages = warm_ages(queue)
    total = len(queue)
    prev = load_state()                                   # resume today's progress after a restart
    resumed = prev.get("day_id") == day_id()
    _state["day_id"] = day_id()
    _state["calls_day"] = prev.get("calls_day", 0) if resumed else 0
    _state["symbols"] = dict(prev.get("symbols") or {}) if resumed else {}
    _state.update(total=total, priority_total=n_priority, done=0, priority_done=0, complete=0, skipped_fresh=0,
                  failed=0, calls_used_run=0, current=None, started_at=time.time(), last_success_at=None,
                  namespace=db_cache.namespace(), plan_unlimited=bool(_plan().get("unlimited_daily", True)))
    _apply("WARMING")
    save_state()
    print(f"warming {total} names ({n_priority} reporting soon first); plan_unlimited={_state['plan_unlimited']}",
          flush=True)

    max_tries = int(_plan().get("max_tries_per_symbol", 3))
    retry_s = float(_plan().get("retry_delay_s", 600))
    provider_errs = 0
    t_run = time.time()
    pass_no = 0
    counted: set = set()                                          # names counted complete in THIS run (each counted once)
    while True:
        pass_no += 1
        progressed = False
        waiting_retry: list[float] = []
        for i, sym in enumerate(queue):
            st = _state["symbols"].get(sym) or {}
            if st.get("s") in ("failed", "partial"):
                continue                                          # given up for today; counted in the report
            # A name is warm because its CACHE is young enough, not because a per-day flag says it was done earlier:
            # the flag let a name age past MAX_AGE all day, and left a resumed run counting 0 of N complete.
            if sym in ages and ages[sym] < max_age_for(sym):      # already warm (restart / resume / fresh)
                if sym not in counted:
                    counted.add(sym)
                    _state["complete"] += 1
                    _state["skipped_fresh"] += 1
                    _state["done"] += 1
                    _state["priority_done"] = min(n_priority, _state["priority_done"] + (1 if i < n_priority else 0))
                    _state["symbols"][sym] = {"s": "ok", "done": 1}
                continue
            if sym in counted:
                continue                                          # warmed earlier in this run
            if st.get("s") == "retry" and time.time() < st.get("next", 0):
                waiting_retry.append(st["next"])
                continue
            # ---- gates: each says WHY it is waiting; none of them is a quota on the unlimited plan
            while True:
                if _paused_manually():
                    _apply("PAUSED_MANUALLY", "WARM_PAUSED is set - warming is paused until it is cleared")
                    save_state()
                    _sleep(30)
                    continue
                ok, why = _daily_gate()
                if not ok:
                    _apply("THROTTLED", why, paused_until=next_reset_epoch())
                    save_state()
                    _sleep(300)
                    continue
                hs, hdetail = _provider_health()
                if hs != "OK":
                    _apply(hs, hdetail)
                    save_state()
                    _sleep(15)
                    continue
                ok, why, wait = _hourly_gate()
                if not ok:
                    _apply("THROTTLED", why, paused_until=time.time() + wait)
                    save_state()
                    _sleep(min(wait, 300))
                    continue
                break
            _apply("WARMING", None, current=sym, paused_until=None)
            before = _calls()
            errs0 = int(uw._metrics.get("net_errors", 0))
            try:
                res = _warm_one(sym)
            except Exception as e:  # noqa: BLE001 - one bad symbol never stops the job
                res = "error"
                print(f"  {sym}: {type(e).__name__}: {e}", flush=True)
            spent = max(0, _calls() - before)
            _state["hour_calls"] = _state.get("hour_calls", 0) + spent
            _state["calls_day"] = _state.get("calls_day", 0) + spent
            _state["calls_used_run"] = _state.get("calls_used_run", 0) + spent
            tries = int(st.get("tries", 0)) + (0 if res == "ok" else 1)
            hs, _ = _provider_health()
            if res == "ok":
                provider_errs = 0
                progressed = True
                if sym not in counted:
                    counted.add(sym)
                    _state["complete"] += 1
                    _state["done"] += 1
                ages[sym] = 0.0                                   # its cache is brand new now; later passes skip it
                _state["last_success_at"] = time.time()
                _state["symbols"][sym] = {"s": "ok", "done": 1}
            elif (hs != "OK" or int(uw._metrics.get("net_errors", 0)) > errs0) and int(st.get("faults", 0)) < 6:
                # the provider, not the symbol: do not burn the symbol's tries on it (bounded: 6 faults per symbol)
                provider_errs += 1
                _state["symbols"][sym] = {"s": "retry", "tries": int(st.get("tries", 0)), "faults": int(st.get("faults", 0)) + 1,
                                          "next": time.time() + float(_plan().get("provider_retry_s", 30))}
                if provider_errs >= 3 and hs == "OK":
                    _apply("API_ERROR", "Unusual Whales is returning errors; retrying with back-off")
                    save_state()
                    _sleep(min(300, 15 * 2 ** min(provider_errs - 3, 5)))
            elif tries >= max_tries:
                kind = "partial" if res == "partial" else "failed"
                _state["symbols"][sym] = {"s": kind, "tries": tries, "faults": int(st.get("faults", 0)), "reason": "required data missing" if kind == "partial" else res}
                _state["done"] += 1
            else:
                _state["symbols"][sym] = {"s": "retry", "tries": tries, "faults": int(st.get("faults", 0)),
                                          "next": time.time() + retry_s * tries}
            _state["priority_done"] = min(n_priority, _state["priority_done"] + (1 if i < n_priority and res == "ok" else 0))
            c = summary()
            _state.update(failed=c["failed"] + c["partial"], retrying=c["retrying"], pending=c["pending"],
                          percent=c["percent"], avg_calls_per_name=round(_state["calls_used_run"] / max(1, _state["complete"] - _state["skipped_fresh"]), 1))
            save_state()
            print(f"  {_state['complete']}/{total}  {sym} {res} ({spent} calls)  hour {_state['hour_calls']}", flush=True)
            _sleep(1.0 if in_live_hours() else 0.2)
        c = summary()
        if c["pending"] == 0 and not waiting_retry:
            break
        if not progressed and waiting_retry:
            _apply("RETRYING", f"{len(waiting_retry)} symbols wait for a retry", current=None)
            save_state()
            _sleep(min(60, max(5, min(waiting_retry) - time.time())))
        if (not progressed and not waiting_retry
                and not any(v.get("s") == "retry" for v in _state["symbols"].values())):
            break                                             # nothing can change in this run: stop instead of spinning
        if pass_no > 200:                                     # hard bound: no endless loop inside one run
            break
    c = summary()
    final = "COMPLETED" if (c["failed"] == 0 and c["partial"] == 0 and c["pending"] == 0) else \
        ("PARTIAL_DATA" if c["partial"] or c["failed"] else "WARMING")
    _apply(final, None if final == "COMPLETED" else
           f"{c['partial']} names have missing data, {c['failed']} failed", current=None,
           finished_at=time.time(), run_seconds=round(time.time() - t_run))
    save_state()
    print("done", {k: _state.get(k) for k in ("status", "complete", "total", "failed")}, flush=True)
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
