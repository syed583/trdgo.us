"""The gate that decides whether the monitor may run at all, and ProviderAccess, the only door to a provider.

Default = OFF. Scheduling turns on only when ALL of these hold: the environment switch, a valid owner approval file (named owner, unexpired, explicit scope), a daily budget that covers the worst-case estimate
for the approved workload, and no kill switch. CACHE_ONLY approval allows scheduled scans that read caches and make no provider request; REFRESH approval additionally allows budgeted provider refreshes, one at a
time, behind a circuit breaker. Nothing here ever bypasses a kill switch."""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from . import config as C
from .store import Store, iso, parse

UTC = timezone.utc
ET = ZoneInfo("America/New_York")
ENV_ENABLED = "EARNINGS_MONITOR_ENABLED"
ENV_APPROVAL = "EARNINGS_MONITOR_APPROVAL"
ENV_KILL = "EARNINGS_MONITOR_KILL"
OFF, CACHE_ONLY, REFRESH = "OFF", "CACHE_ONLY", "REFRESH"


class ProviderBlocked(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class GateState:
    mode: str = OFF
    reasons: list = field(default_factory=list)
    approval: dict = field(default_factory=dict)
    daily_budget: int = 0
    purposes: set = field(default_factory=set)          # what a REFRESH approval allows: calendar, event (evidence), outcome (verification)

    @property
    def scheduling_on(self) -> bool:
        return self.mode in (CACHE_ONLY, REFRESH)


def day_key(now: datetime) -> str:
    return now.astimezone(ET).strftime("%Y-%m-%d")


def evaluate(cfg: C.Config, data_dir, now: datetime, env: Optional[dict] = None) -> GateState:
    env = os.environ if env is None else env
    d = Path(data_dir)
    st = GateState()
    kill = Path(env.get(ENV_KILL) or d / "KILL")
    if kill.exists():
        st.reasons.append("KILL_SWITCH_ENGAGED")
    if str(env.get(ENV_ENABLED, "")).strip() != "1":
        st.reasons.append("NOT_ENABLED_BY_ENVIRONMENT")
    ap = Path(env.get(ENV_APPROVAL) or d / "approval.json")
    if not ap.is_file():
        st.reasons.append("NO_OWNER_APPROVAL_FILE")
    else:
        try:
            a = json.loads(ap.read_text(encoding="utf-8"))
        except ValueError:
            a = None
            st.reasons.append("APPROVAL_FILE_UNREADABLE")
        if a is not None:
            st.approval = {k: a.get(k) for k in ("approved_by", "approved_at", "expires_at", "mode", "daily_request_budget", "expected_events_per_day", "refresh_calendar", "refresh_event_evidence", "verify_outcomes")}
            exp, ats = parse(a.get("expires_at")) if a.get("expires_at") else None, parse(a.get("approved_at")) if a.get("approved_at") else None
            if not str(a.get("approved_by") or "").strip() or ats is None:
                st.reasons.append("APPROVAL_NOT_SIGNED")
            if exp is None or exp <= now:
                st.reasons.append("APPROVAL_EXPIRED_OR_MISSING_EXPIRY")
            elif ats is not None and exp - ats > timedelta(days=30):
                st.reasons.append("APPROVAL_LONGER_THAN_30_DAYS")
            if a.get("scheduling_approved") is not True:
                st.reasons.append("SCHEDULING_NOT_APPROVED")
            mode = a.get("mode")
            if mode not in ("cache_only", "refresh"):
                st.reasons.append("APPROVAL_MODE_INVALID")
            if mode == "refresh":
                if a.get("provider_authorization") is not True:
                    st.reasons.append("PROVIDER_AUTHORIZATION_MISSING")
                if a.get("kill_switch_acknowledged") is not True:
                    st.reasons.append("KILL_SWITCH_NOT_ACKNOWLEDGED")
                purposes = {p for p, key in (("calendar", "refresh_calendar"), ("event", "refresh_event_evidence"), ("outcome", "verify_outcomes")) if a.get(key) is True}
                if not purposes:
                    st.reasons.append("NO_REFRESH_PURPOSE_APPROVED")
                st.purposes = purposes
                try:
                    budget = int(a.get("daily_request_budget"))
                except (TypeError, ValueError):
                    budget = 0
                if budget <= 0 or budget > cfg.hard_daily_cap:
                    st.reasons.append("DAILY_BUDGET_INVALID")
                else:
                    need = C.estimate(cfg, int(a.get("expected_events_per_day") or 20), purposes=purposes or ("calendar",))["total_requests_per_day"]
                    if need > budget:
                        st.reasons.append("BUDGET_BELOW_WORST_CASE_ESTIMATE (%d needed, %d approved)" % (need, budget))
                    st.daily_budget = budget
                if int(a.get("max_inflight") or 1) > cfg.max_inflight:
                    st.reasons.append("APPROVED_CONCURRENCY_ABOVE_CEILING")
            if not st.reasons:
                st.mode = REFRESH if mode == "refresh" else CACHE_ONLY
    return st


class ProviderAccess:
    """Every provider request the monitor starts goes through call(). Blocks (never bypasses) on: not REFRESH mode, kill switch, open circuit breaker, daily budget, in-flight ceiling."""

    def __init__(self, store: Store, cfg: C.Config, state: GateState, data_dir, clock: Callable[[], datetime], env: Optional[dict] = None):
        self.store, self.cfg, self.state, self.clock = store, cfg, state, clock
        self.kill = Path((env or os.environ).get(ENV_KILL) or Path(data_dir) / "KILL")
        self.collector_flag = Path(data_dir) / "collector.active"       # created while the separately authorised Smart Move collector runs: the monitor pauses its own provider calls
        self._sem = threading.BoundedSemaphore(max(1, cfg.max_inflight))

    def _breaker(self, provider: str) -> dict:
        return (self.store.kv_get("breaker", {}) or {}).get(provider, {"fails": 0, "open_until": None})

    def _set_breaker(self, provider: str, b: dict):
        allb = self.store.kv_get("breaker", {}) or {}
        allb[provider] = b
        self.store.kv_set("breaker", allb)

    def breaker_open(self, provider: str) -> bool:
        b = self._breaker(provider)
        return bool(b.get("open_until") and parse(b["open_until"]) > self.clock())

    def check(self, provider: str, n: int, purpose: Optional[str] = None):
        if self.state.mode != REFRESH:
            raise ProviderBlocked("PROVIDER_REFRESH_NOT_APPROVED (mode %s)" % self.state.mode)
        if self.kill.exists():
            raise ProviderBlocked("KILL_SWITCH_ENGAGED")
        if purpose is not None and purpose not in self.state.purposes:
            raise ProviderBlocked("PURPOSE_NOT_APPROVED (%s)" % purpose)
        if self.collector_flag.exists():
            raise ProviderBlocked("COLLECTOR_ACTIVE (the Smart Move collector is running; the monitor waits)")
        if self.breaker_open(provider):
            raise ProviderBlocked("CIRCUIT_BREAKER_OPEN")
        used = self.store.usage(day_key(self.clock()))
        if used + n > self.state.daily_budget:
            raise ProviderBlocked("DAILY_BUDGET_EXHAUSTED (%d used + %d > %d)" % (used, n, self.state.daily_budget))

    def call(self, provider: str, n: int, fn: Callable, purpose: Optional[str] = None):
        self.check(provider, n, purpose)
        if not self._sem.acquire(timeout=max(0, self.cfg.inflight_wait_s)):               # two lanes may want a provider at once: the second waits its turn instead of being refused
            raise ProviderBlocked("CONCURRENCY_CEILING")
        try:
            try:
                out = fn()
            except ProviderBlocked:
                raise                                                                  # the adapter itself declined (nothing was sent): not a failure, not a request
            except Exception:
                self.store.add_usage(day_key(self.clock()), provider, requests=n, failures=1)        # a failed attempt still counts
                b = self._breaker(provider)
                b["fails"] = int(b.get("fails", 0)) + 1
                if b["fails"] >= self.cfg.breaker_threshold:
                    b["open_until"] = iso(self.clock() + timedelta(minutes=self.cfg.breaker_cooldown_min))
                    b["fails"] = 0
                self._set_breaker(provider, b)
                raise
            self.store.add_usage(day_key(self.clock()), provider, requests=n)
            self._set_breaker(provider, {"fails": 0, "open_until": None})
            return out
        finally:
            self._sem.release()
