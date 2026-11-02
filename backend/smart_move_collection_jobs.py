"""Operational collection workflow for news (INITIAL / FINAL passes) and analyst-estimate snapshots (A about 10 days before, B inside the prediction window).

Safe by construction:
  * DRY_RUN is the default and never calls the provider (not even the calendar); it only plans, counts and persists a PENDING job record;
  * LIVE needs `armed=True` (set only by the runner after its explicit flags), a released kill switch and a positive allowance - otherwise the job is BLOCKED and nothing is sent;
  * every request goes through the existing governed path (RefreshService._call: authorization cap, 2 s spacing, hourly share, 401/403/429 stop, kill switch);
  * run state is persisted after every batch (done task ids, requests per day, job statuses) so an interrupted run resumes without repeating a request; a lock file prevents two jobs from running at once.
Job statuses: PENDING (planned, nothing sent), RUNNING, COMPLETED, PARTIAL (some tasks done, then stopped / ceiling / failures), FAILED (nothing succeeded), BLOCKED (not allowed to start).
It changes no score, threshold or stored prediction.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import smart_move_calendar_guard as G
import smart_move_collection_plan as CP
import smart_move_v2_collect as C

UTC = timezone.utc
STATUSES = ("PENDING", "RUNNING", "COMPLETED", "PARTIAL", "FAILED", "BLOCKED")
DEFAULT_CEILING = 700
DEFAULT_BATCH = 10
LOCK_STALE_H = 2.0
STATE_FILE, LOCK_FILE = "collection_state.json", "collection.lock"


# ---------------------------------------------------------------- persistent state
def _read_state(d: Path) -> dict:
    f = d / STATE_FILE
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"version": 1, "jobs": {}, "done_tasks": [], "requests_by_day": {}}


def _write_state(d: Path, st: dict):
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (STATE_FILE + ".tmp")
    tmp.write_text(json.dumps(st, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, d / STATE_FILE)


def acquire_lock(d: Path, now: datetime) -> bool:
    d.mkdir(parents=True, exist_ok=True)
    f = d / LOCK_FILE
    try:
        fd = os.open(str(f), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            t = datetime.fromisoformat(json.loads(f.read_text(encoding="utf-8"))["at"])
        except (ValueError, KeyError, OSError):
            return False
        if now - t < timedelta(hours=LOCK_STALE_H):
            return False
        f.unlink()                                                  # a lock older than LOCK_STALE_H belongs to a dead run
        return acquire_lock(d, now)
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps({"at": now.isoformat(), "pid": os.getpid()}))
    return True


def release_lock(d: Path):
    try:
        (d / LOCK_FILE).unlink()
    except FileNotFoundError:
        pass


def useful_calendar_dates(now, lookahead_days: int = 9) -> list:
    """Event dates for which an event can have an OPEN 48-to-24-hour window at `now` (BMO or AMC), by the same plan_event rule the scorer uses. Dates whose windows are already closed, or have not opened yet,
    cannot produce a collectable event, so a small-budget refresh (the pilot) requests only these."""
    from datetime import date as _date
    from smvshadow import cutoff as _co
    mc = _co._mc()
    today = now.astimezone(_co.ET).date()
    out = []
    for k in range(-2, lookahead_days + 1):
        d = today + timedelta(days=k)
        if not mc.is_trading_day(d):
            continue
        for kind in ("BMO", "AMC"):
            p = _co.plan_event(d, kind)
            if p["window_start_utc"] <= now <= p["cutoff_utc"]:
                out.append(d)
                break
    return out


def useful_calendar_slots(now) -> list:
    """Like useful_calendar_dates but per SIDE: (date, "premarket") for a BMO window that is open now, (date, "afterhours") for an AMC window that is open now. A refresh for these slots skips the closed or
    not-yet-open side of every day, which roughly halves its cost."""
    from smvshadow import cutoff as _co
    mc = _co._mc()
    today = now.astimezone(_co.ET).date()
    out = []
    for k in range(-2, 10):
        d = today + timedelta(days=k)
        if not mc.is_trading_day(d):
            continue
        for kind, side in (("BMO", "premarket"), ("AMC", "afterhours")):
            p = _co.plan_event(d, kind)
            if p["window_start_utc"] <= now <= p["cutoff_utc"]:
                out.append((d, side))
    return out


def attempts(svc) -> int:
    """Every governor-granted attempt (a retry counts as an attempt). The ceiling is enforced on THIS number, not on the number of tasks."""
    return sum(svc.gov.used_day.values()) if svc.gov.used_day else 0


# ---------------------------------------------------------------- planning
def to_news_events(events) -> list:
    return [{"symbol": e["symbol"], "window_start_utc": e["plan"]["window_start_utc"], "cutoff_utc": e["plan"]["cutoff_utc"]} for e in events]


def to_snapshot_events(events) -> list:
    return [{"symbol": e["symbol"], "event_date": e["event_date"], "release_utc": e["plan"]["assumed_release_utc"], "window_start_utc": e["plan"]["window_start_utc"], "cutoff_utc": e["plan"]["cutoff_utc"]}
            for e in events]


class AttemptRecorder:
    """Wraps a fetcher and appends ONE line per provider attempt (time, kind, symbol, params without credentials, outcome) to attempts.jsonl - failures and 429s included.
    Failed attempts are otherwise visible only in the governor ledger; this is the pilot's complete, append-only attempt log."""

    def __init__(self, inner, path, clock):
        self.inner, self.path, self.clock = inner, Path(path), clock
        self.mode = getattr(inner, "mode", "LIVE")

    def fetch(self, kind, symbol, params):
        outcome = {"result": "OK"}
        try:
            body = self.inner.fetch(kind, symbol, params)
            outcome["bytes"] = len(body) if body is not None else 0
            return body
        except Exception as e:  # noqa: BLE001
            import smart_move_secret_check as _sc
            outcome = {"result": "ERROR", "error_kind": getattr(e, "kind", type(e).__name__), "status": _sc.redact(str(getattr(e, "status", "")))}
            raise
        finally:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"at": self.clock.now().isoformat(), "kind": kind, "symbol": symbol, "params": {k: v for k, v in (params or {}).items() if k.lower() not in ("key", "token", "authorization")}, **outcome}, sort_keys=True) + chr(10))

    def __getattr__(self, name):
        return getattr(self.inner, name)


def build_tasks(window_events, tracking_events, store, now, stage_caps=None) -> dict:
    """window_events: events with an open 48-to-24 h window (news INITIAL/FINAL, snapshot B). tracking_events: events within the ~14-day horizon (snapshot A)."""
    recs = C._records(store)
    news = CP.news_tasks_due(to_news_events(window_events), [r for r in recs if r["kind"] == "uw_news"], now)
    est = [r for r in recs if r["kind"] == "uw_estimates"]
    snaps_b = [t for t in CP.snapshot_stage_tasks_due(to_snapshot_events(window_events), est, now) if t["stage"] == "B"]
    snaps_a = [t for t in CP.snapshot_stage_tasks_due(to_snapshot_events(tracking_events), est, now) if t["stage"] == "A"]
    caps = stage_caps or {}
    if "snapshot_b" in caps:
        snaps_b = snaps_b[:caps["snapshot_b"]]                                                      # earliest cutoff first (the lists are cutoff-ordered)
    if "snapshot_a" in caps:
        snaps_a = snaps_a[:caps["snapshot_a"]]
    seen, tasks = set(), []
    rank = lambda t: 0 if t.get("stage") == "A" else 1 if t.get("stage") == "B" else 2          # priority: Snapshot A, Snapshot B, then news (INITIAL / FINAL)  # noqa: E731
    for t in sorted(news + snaps_b + snaps_a, key=lambda t: (rank(t), t["cutoff_utc"], t["symbol"])):
        if t["id"] not in seen:
            seen.add(t["id"])
            tasks.append(t)
    return {"news": news, "snapshot_b": snaps_b, "snapshot_a": snaps_a, "tasks": tasks}


def request_range(tasks: list) -> dict:
    news = sum(1 for t in tasks if t["kind"] == "uw_news")
    other = len(tasks) - news
    return {"min": len(tasks), "max": news * CP.NEWS_MAX_PAGES + other}


def preflight(svc, tasks_by_kind: dict, *, now: datetime, ceiling: int, state: dict, window_events, tracking_events, stopped: Callable[[], bool] = lambda: False, scope_dates=None) -> dict:
    """Everything that must be known before a run. Reads local state only."""
    age = G.calendar_age_h(svc.store, now)
    day = now.date().isoformat()
    used = state["requests_by_day"].get(day, 0)
    rng = request_range(tasks_by_kind["tasks"])
    try:
        hourly = int(svc.gov.hourly_allowance())
    except Exception:  # noqa: BLE001
        hourly = None
    daily_left = max(0, ceiling - used)
    cal_days = len(svc._trading_days(now, svc.cfg.tracking_days_ahead)) if not scope_dates else len({s[0] for s in scope_dates})
    cal_cost = (2 * cal_days if not scope_dates else len(scope_dates)) + 2                                                   # two sides per trading day plus one Finviz and one Finnhub request; EXTRA pages are added for every side/day that fills a page of 100 (cannot be known before the call)
    blockers = []
    if svc.kill_engaged:
        blockers.append("KILL_SWITCH_ENGAGED_OR_REFRESH_DISABLED")
    if stopped():
        blockers.append("PROVIDER_STOPPED_TODAY")
    if daily_left <= 0:
        blockers.append("RUN_CEILING_REACHED")
    if hourly is not None and hourly <= 0:
        blockers.append("NO_HOURLY_ALLOWANCE")
    stale = age is None or age > G.MAX_AGE_H
    return {"calendar_age_h": age, "calendar_state": "UNKNOWN" if age is None else "STALE" if stale else "FRESH", "calendar_refresh_needed": stale, "calendar_refresh_estimate_requests": cal_cost if stale else 0, "calendar_trading_days": cal_days, "calendar_dates": ["%s %s" % (s[0], s[1]) for s in scope_dates] if scope_dates else None,
            "eligible_window_events": len(window_events), "tracking_events": len(tracking_events), "tasks": {k: len(v) for k, v in tasks_by_kind.items()}, "request_estimate": rng,
            "ceiling": ceiling, "used_today": used, "daily_left": daily_left, "hourly_allowance": hourly, "spacing_s": svc.cfg.spacing_s, "estimated_minutes_at_spacing": round(rng["max"] * svc.cfg.spacing_s / 60, 1),
            "blockers": blockers, "allowed": not blockers}


# ---------------------------------------------------------------- the cycle
def run_cycle(svc, *, now: datetime, state_dir, mode: str = "DRY_RUN", armed: bool = False, ceiling: int = DEFAULT_CEILING, batch_size: int = DEFAULT_BATCH,
              events_fn: Optional[Callable] = None, stopped: Optional[Callable[[], bool]] = None, news_pages: Optional[int] = None, calendar_requests: Optional[int] = None, stage_caps: Optional[dict] = None, calendar_scope: str = "horizon") -> dict:
    """events_fn(now, horizon_days) -> events (the existing eligible_events). DRY_RUN never calls the provider. LIVE needs armed=True."""
    d = Path(state_dir)
    st = _read_state(d)
    stopped = stopped or (lambda: bool(svc.gov.stopped_until and svc.clock.now() < svc.gov.stopped_until))      # the governor's own 401/403/429 stop for the day
    job_id = "collect-" + now.strftime("%Y%m%dT%H%M%S")
    events_fn = events_fn or (lambda n, h=None: svc.eligible_events(n, h) if h else svc.eligible_events(n))
    window_events = events_fn(now, None)
    tracking_events = events_fn(now, svc.cfg.tracking_days_ahead)
    tb = build_tasks(window_events, tracking_events, svc.store, now, stage_caps)
    pf = preflight(svc, tb, now=now, ceiling=ceiling, state=st, window_events=window_events, tracking_events=tracking_events, stopped=stopped,
                   scope_dates=useful_calendar_slots(now) if calendar_scope == "useful" else None)
    job = {"id": job_id, "mode": mode, "started_utc": now.isoformat(), "preflight": pf, "requests": 0, "tasks_planned": len(tb["tasks"]), "tasks_done": 0, "failures": [], "status": "PENDING"}
    if mode != "LIVE":
        job.update(dry_run=True, provider_requests=0, note="dry run: nothing was sent, not even the calendar refresh")
        st["jobs"][job_id] = job
        _write_state(d, st)
        return job
    # ---- LIVE
    if not armed:
        job.update(status="BLOCKED", reason="NOT_ARMED (the runner arms a live run only with its explicit authorization flags)")
    elif not pf["allowed"]:
        job.update(status="BLOCKED", reason=",".join(pf["blockers"]))
    elif not acquire_lock(d, now):
        job.update(status="BLOCKED", reason="OVERLAPPING_JOB_HOLDS_THE_LOCK")
    else:
        saved_pages = C.NEWS_MAX_PAGES
        if news_pages:
            C.NEWS_MAX_PAGES = news_pages                                   # e.g. 1 for a pilot: no pagination can consume the ceiling
        try:
            job["status"] = "RUNNING"
            st["jobs"][job_id] = job
            _write_state(d, st)
            used_start = st["requests_by_day"].get(now.date().isoformat(), 0)
            gov_base = attempts(svc)
            if pf["calendar_refresh_needed"]:
                g = G.ensure_fresh(svc, budget_left=min(ceiling - used_start, pf["hourly_allowance"] if pf["hourly_allowance"] is not None else ceiling), stopped=stopped, days_ahead=svc.cfg.tracking_days_ahead,
                                   max_requests=calendar_requests, dates=useful_calendar_slots(svc.clock.now()) if calendar_scope == "useful" else None)
                job["calendar_refresh"] = g
                cal_attempts = attempts(svc) - gov_base                          # calendar requests counted as governor attempts
                gov_base = attempts(svc)
                used_start += cal_attempts
                st["requests_by_day"][now.date().isoformat()] = used_start
                job["requests"] += cal_attempts
                job["calendar_attempts"] = cal_attempts
                if not g["ok"]:
                    job.update(status="BLOCKED", reason="CALENDAR_NOT_FRESH:" + str(g.get("reason")))
                else:
                    window_events, tracking_events = events_fn(svc.clock.now(), None), events_fn(svc.clock.now(), svc.cfg.tracking_days_ahead)
                    tb = build_tasks(window_events, tracking_events, svc.store, svc.clock.now(), stage_caps)
                    job["tasks_planned"] = len(tb["tasks"])
            if job["status"] == "RUNNING":
                done = set(st["done_tasks"])
                todo = [t for t in tb["tasks"] if t["id"] not in done]
                ckpt = d / ("checkpoint_%s.json" % job_id)
                halted = None
                for i in range(0, len(todo), batch_size):
                    batch = todo[i:i + batch_size]
                    day = svc.clock.now().date().isoformat()
                    left = ceiling - st["requests_by_day"].get(day, 0)
                    if left <= 0:
                        halted = "RUN_CEILING_REACHED"
                        break
                    if svc.kill_engaged:
                        halted = "KILL_SWITCH"
                        break
                    if stopped():
                        halted = "PROVIDER_STOPPED"
                        break
                    pages = news_pages or CP.NEWS_MAX_PAGES
                    acc = C.run(svc, batch, max_requests=min(left, len(batch) * pages), checkpoint=ckpt, stopped=stopped)
                    delta = attempts(svc) - gov_base                                  # governor attempts incl. retries: the number the ceiling is enforced on
                    gov_base += delta
                    st["requests_by_day"][day] = st["requests_by_day"].get(day, 0) + delta
                    job["requests"] += delta
                    finished = set(json.loads(ckpt.read_text())["done"]) if ckpt.exists() else set()                # C.run lists a task id here only after an OK, validated response
                    ok_batch = [t for t in batch if t["id"] in finished]
                    done |= {t["id"] for t in ok_batch}
                    job["tasks_done"] += len(ok_batch)
                    job["failures"] += acc["failures"] + ([{"kind": "invalid", "reason": "INVALID_RESPONSE"}] * acc["invalid"])
                    st["done_tasks"] = sorted(done)
                    st["jobs"][job_id] = job
                    _write_state(d, st)                                                # persisted after every batch: a crash resumes from here
                    if acc["stopped"] or acc.get("budget_exhausted"):
                        halted = "PROVIDER_STOP" if acc["stopped"] else "BUDGET_EXHAUSTED"
                        break
                if halted:
                    job["halted"] = halted
                job["status"] = ("COMPLETED" if job["tasks_done"] == len(todo) and not halted and not job["failures"] else
                                 "FAILED" if job["tasks_done"] == 0 and (job["failures"] or halted) else "PARTIAL")
        finally:
            C.NEWS_MAX_PAGES = saved_pages
            release_lock(d)
    st["jobs"][job_id] = job
    _write_state(d, st)
    return job
