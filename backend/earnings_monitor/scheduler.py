"""Durable scheduling: recurring jobs are enqueued idempotently (a time-bucket dedupe key), a single worker claims them under a cross-process scan lock with a lease, and a restart recovers any job a dead
process left half-done. Failures back off exponentially and end in a dead-letter plus an alert; provider calls go through ProviderAccess (budget, breaker, kill switch, one at a time).
The gate is re-read on EVERY tick, so removing the approval or engaging the kill switch stops the next job immediately."""
from __future__ import annotations

import os
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from . import alerts as A
from . import calendar_us as CAL
from . import config as C
from .gate import GateState, OFF, ProviderAccess, ProviderBlocked, day_key
from .locks import ScanLock
from .service import Monitor, in_session_window, outcome_ready_at, today_et
from .store import Store, iso, parse

UTC = timezone.utc
KINDS = ("CALENDAR_REFRESH", "ELIGIBILITY_CHECK", "EVIDENCE_SCAN", "NEWS_FLOW_CHECK", "REFRESH_PLAN", "EVENT_REFRESH", "EARNINGS_DAY_CHECK", "OUTCOME_VERIFY", "SMART_MOVE_PLAN", "ALERT_DISPATCH")
# Two lanes with their own cross-process locks: a long scan can no longer keep alert delivery, outcome checks or planning waiting. Scans never overlap each other (one heavy lock), and alert delivery never overlaps itself.
LANE_OF = {"CALENDAR_REFRESH": "heavy", "ELIGIBILITY_CHECK": "heavy", "EVIDENCE_SCAN": "heavy", "NEWS_FLOW_CHECK": "heavy", "REFRESH_PLAN": "heavy", "EVENT_REFRESH": "heavy", "EARNINGS_DAY_CHECK": "heavy",
           "OUTCOME_VERIFY": "light", "SMART_MOVE_PLAN": "light", "ALERT_DISPATCH": "light"}
LANES = ("heavy", "light")
MAX_OUTCOME_TRIES = 8


def events_processed(res) -> Optional[int]:
    """How many events a job looked at, read from its own result (None when the job has no per-event work)."""
    if not isinstance(res, dict):
        return None
    for path in (("recompute", "events"), ("events",), ("observed", "new")):
        cur = res
        for k in path:
            cur = cur.get(k) if isinstance(cur, dict) else None
        if isinstance(cur, int):
            return cur
    return None


def run_meta(job: dict, t_start: datetime, t_end: datetime, res) -> dict:
    """Timing facts stored with every run: real start/finish, execution time and the time the job waited in the queue (start minus the moment it was due)."""
    return {"job_id": job["id"], "lane": job.get("lane"), "queue_wait_ms": max(0, int((t_start - parse(job["run_after"])).total_seconds() * 1000)),
            "duration_ms": max(0, int((t_end - t_start).total_seconds() * 1000)), "events_processed": events_processed(res)}


def _bucket(now: datetime, seconds: float) -> int:
    return int(now.timestamp() // seconds)


class Scheduler:
    def __init__(self, store: Store, cfg: C.Config, monitor: Monitor, access: ProviderAccess, lock: ScanLock, gate_fn: Callable[[datetime], GateState], worker_id: Optional[str] = None, light_lock: Optional[ScanLock] = None):
        self.store, self.cfg, self.mon, self.access, self.lock, self.gate_fn = store, cfg, monitor, access, lock, gate_fn
        self.locks = {"heavy": lock, "light": light_lock or ScanLock(Path(lock.path).with_name("light.lock"))}
        self.worker_id = worker_id or "w%d" % os.getpid()

    # ------------------------------------------------------------ enqueue
    def tick(self, now: datetime) -> dict:
        state = self.gate_fn(now)
        self.access.state = state
        self.store.kv_set("gate", {"mode": state.mode, "reasons": state.reasons, "checked_at": iso(now), "approval": state.approval, "daily_budget": state.daily_budget})
        if not state.scheduling_on:
            return {"scheduling": OFF, "reasons": state.reasons, "enqueued": 0}
        n = 0
        c = self.cfg

        def enq(kind, key, payload=None):
            nonlocal n
            if self.store.enqueue(kind, now, payload, dedupe_key=key, max_attempts=c.max_attempts, now=now, lane=LANE_OF[kind]):
                n += 1

        enq("CALENDAR_REFRESH", "CAL:%d" % _bucket(now, c.calendar_every_h * 3600))
        enq("ELIGIBILITY_CHECK", "ELIG:%d" % _bucket(now, c.eligibility_every_min * 60))
        enq("EVIDENCE_SCAN", "EVD:%d" % _bucket(now, c.evidence_scan_every_min * 60))                  # cached-evidence change detection: cache reads only
        enq("NEWS_FLOW_CHECK", "NEWS:%d" % _bucket(now, c.news_flow_every_min * 60))
        enq("REFRESH_PLAN", "PLAN:%d" % _bucket(now, c.refresh_plan_every_min * 60))                     # planning only: no request
        if c.plan_driven_refresh and state.mode == "REFRESH" and "event" in state.purposes:
            enq("EVENT_REFRESH", "EVR:%d" % _bucket(now, c.event_refresh_every_min * 60))
        exp = (state.approval or {}).get("expires_at")
        if exp and parse(exp) - now <= timedelta(days=c.approval_warn_days):                              # the owner's approval is about to end: say so, once a day
            A.enqueue(self.store, now, [A.approval_expiring(now, exp, c)], c)
        enq("ALERT_DISPATCH", "ALR:%d" % _bucket(now, 300))
        enq("SMART_MOVE_PLAN", "SMP:%d" % _bucket(now, 3600))                     # offline planning only: records what the separately authorised collector would need
        active_today = [e for e in self.store.q("SELECT * FROM events WHERE status='ACTIVE' AND earnings_date=?", (today_et(now).isoformat(),)) if in_session_window(c, now, e)]
        if active_today:
            enq("EARNINGS_DAY_CHECK", "DAY:%d" % _bucket(now, c.earnings_day_every_min * 60))
        for ev in self.store.q("SELECT * FROM events WHERE status IN ('ACTIVE','REPORTED') AND outcome_at IS NULL"):
            if ev["session"] in ("BMO", "AMC") and CAL.is_trading_day(date.fromisoformat(ev["earnings_date"])) and now >= outcome_ready_at(c, ev):      # an event dated on a market holiday is a calendar error: no provider request for it
                tries = int(self.store.kv_get("outcome_tries:%d" % ev["id"], 0))
                if tries < MAX_OUTCOME_TRIES:
                    key = "OUT:%d:%d" % (ev["id"], _bucket(now, 3600))
                    if self.store.enqueue("OUTCOME_VERIFY", now, {"event_id": ev["id"]}, dedupe_key=key, max_attempts=c.max_attempts, now=now, lane="light"):
                        self.store.kv_set("outcome_tries:%d" % ev["id"], tries + 1)
                        n += 1
        return {"scheduling": state.mode, "enqueued": n}

    # ------------------------------------------------------------ run
    def _handler(self, job: dict, now: datetime):
        k, p = job["kind"], job["payload"]
        if k == "CALENDAR_REFRESH":
            return self.mon.calendar_refresh(now)
        if k == "ELIGIBILITY_CHECK":
            return self.mon.eligibility_check(now)
        if k == "EVIDENCE_SCAN":
            return self.mon.evidence_scan(now)
        if k == "NEWS_FLOW_CHECK":
            return self.mon.news_flow_check(now)
        if k == "REFRESH_PLAN":
            return self.mon.refresh_plan(now)
        if k == "EVENT_REFRESH":
            return self.mon.event_refresh(now)
        if k == "EARNINGS_DAY_CHECK":
            return self.mon.earnings_day_check(now)
        if k == "OUTCOME_VERIFY":
            return self.mon.outcome_verify(now, p.get("event_id"))
        if k == "SMART_MOVE_PLAN":
            return self.mon.smart_move_plan(now)
        if k == "ALERT_DISPATCH":
            return self.mon.alert_dispatch(now)
        raise ValueError("unknown job kind %s" % k)

    def run_once(self, now: datetime, lane: str = "heavy") -> dict:
        state = self.gate_fn(now)
        self.access.state = state
        if not state.scheduling_on:
            return {"ran": False, "reason": "GATE_OFF", "gate": state.reasons}
        lock = self.locks[lane]
        if not lock.acquire():
            return {"ran": False, "reason": "SCAN_LOCK_HELD"}
        try:
            recovered = self.store.recover_expired(now)
            if recovered:
                self.store.audit(now, self.worker_id, "RECOVERED_JOBS", {"count": recovered})
            job = self.store.claim(now, self.worker_id, self.cfg.lease_s, lane)
            if job is None:
                return {"ran": False, "reason": "NO_DUE_JOB", "recovered": recovered}
            t_start = self.mon.clock()                                           # real start / end times of the job (the loop's `now` is one instant for the whole pass)
            run_id = self.store.start_run(job["kind"], t_start)
            try:
                res = self._handler(job, now)
            except ProviderBlocked as b:
                self.store.finish_job(job["id"], now, True, {"blocked": b.reason})
                self.store.finish_run(run_id, self.mon.clock(), False, {"blocked": b.reason})
                self.store.audit(now, self.worker_id, "JOB_BLOCKED", {"kind": job["kind"], "reason": b.reason})
                if b.reason.startswith(("CIRCUIT_BREAKER_OPEN", "DAILY_BUDGET_EXHAUSTED")):
                    A.enqueue(self.store, now, [A.scanner_failure(now, job["kind"], b.reason, self.cfg)], self.cfg)
                return {"ran": True, "kind": job["kind"], "blocked": b.reason}
            except Exception as e:  # noqa: BLE001
                err = "%s: %s" % (type(e).__name__, e)
                dead = job["attempts"] >= job["max_attempts"]
                delay = min(self.cfg.backoff_max_s, self.cfg.backoff_base_s * (2 ** (job["attempts"] - 1)))
                self.store.finish_job(job["id"], now, False, error=err, retry_at=now + timedelta(seconds=delay), dead=dead)
                self.store.finish_run(run_id, self.mon.clock(), False, {"error": err})
                self.store.audit(now, self.worker_id, "JOB_FAILED", {"kind": job["kind"], "attempt": job["attempts"], "error": err, "dead": dead, "retry_in_s": None if dead else delay})
                if dead:
                    A.enqueue(self.store, now, [A.scanner_failure(now, job["kind"], err, self.cfg)], self.cfg)
                return {"ran": True, "kind": job["kind"], "failed": err, "dead": dead, "retry_in_s": None if dead else delay}
            t_end = self.mon.clock()
            self.store.finish_job(job["id"], t_end, True, res)
            self.store.finish_run(run_id, t_end, True, dict(res if isinstance(res, dict) else {"result": res}, _run=run_meta(job, t_start, t_end, res)))
            return {"ran": True, "kind": job["kind"], "result": res}
        finally:
            lock.release()

    def run_pending(self, now: datetime, max_jobs: int = 25, lane: str = "heavy") -> list:
        out = []
        for _ in range(max_jobs):
            r = self.run_once(now, lane)
            if not r.get("ran"):
                if r.get("reason") == "SCAN_LOCK_HELD":
                    out.append(r)
                break
            out.append(r)
        return out


def status_report(store: Store, cfg: C.Config, now: datetime) -> dict:
    gate = store.kv_get("gate", {}) or {}
    last = {k: store.last_run(k) for k in KINDS}
    return {"scheduling": gate.get("mode", OFF), "gate_reasons": gate.get("reasons", ["NEVER_EVALUATED"]), "gate_checked_at": gate.get("checked_at"), "approval": gate.get("approval", {}),
            "last_success": {k: (v or {}).get("finished_at") for k, v in last.items()}, "last_any_success": max([v["finished_at"] for v in last.values() if v] or [None], default=None),
            "queue": store.job_counts(), "provider_requests_today": store.usage(day_key(now)), "daily_budget": gate.get("daily_budget", 0), "breakers": store.kv_get("breaker", {}) or {},
            "heartbeat": store.kv_get("heartbeat"), "intervals": {"calendar_h": cfg.calendar_every_h, "eligibility_min": cfg.eligibility_every_min, "evidence_scan_min": cfg.evidence_scan_every_min, "news_flow_min": cfg.news_flow_every_min, "refresh_plan_min": cfg.refresh_plan_every_min, "event_refresh_min": cfg.event_refresh_every_min, "earnings_day_min": cfg.earnings_day_every_min},
            "pending_alerts": (store.one("SELECT COUNT(*) n FROM alerts WHERE status='PENDING'") or {}).get("n", 0)}


class Runner:
    """Background thread for the live process: every few seconds re-reads the gate, enqueues, runs due jobs. Started only when EARNINGS_MONITOR_ENABLED=1 at boot; it can do nothing without an approval."""

    def __init__(self, sched: Scheduler, clock: Callable[[], datetime], every_s: float = 20.0):
        self.s, self.clock, self.every_s = sched, clock, every_s
        self._stop = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.light_thread: Optional[threading.Thread] = None

    def start(self):
        self.thread = threading.Thread(target=self._loop, args=("heavy",), name="earnings-monitor-scans", daemon=True)
        self.light_thread = threading.Thread(target=self._loop, args=("light",), name="earnings-monitor-light", daemon=True)
        self.thread.start()
        self.light_thread.start()
        return self

    def stop(self):
        self._stop.set()

    def _loop(self, lane: str = "heavy"):
        while not self._stop.is_set():
            try:
                now = self.clock()
                if lane == "heavy":
                    self.s.store.kv_set("heartbeat", iso(now))
                    self.s.tick(now)
                else:
                    self.s.store.kv_set("heartbeat_light", iso(now))
                self.s.run_pending(self.clock(), lane=lane)
            except Exception as e:  # noqa: BLE001
                try:
                    self.s.store.audit(self.clock(), "runner", "LOOP_ERROR", {"error": "%s: %s" % (type(e).__name__, e)})
                except Exception:  # noqa: BLE001
                    pass
            self._stop.wait(self.every_s)
