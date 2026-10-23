"""Shared Unusual Whales API budget: who may spend, how much, and when to stop.

One controller for the whole process. Every real request is admitted (or refused) here by its
priority class (uw_priority) and recorded against its module, so the live intraday engine
can never be starved by dashboards or by the historical warmer.

What counts as "used today", in order of authority:

  1. the provider's own daily-count headers, when it sends them (VERIFIED_PROVIDER);
  2. a reading you typed from the provider dashboard (uw_budget_cli.py usage N) plus the calls
     this process has made since that reading (VERIFIED_DASHBOARD);
  3. this process's own count only (UNVERIFIED). A local counter is NOT the provider's counter:
     other machines, scripts and sessions share the key. While UNVERIFIED the warmer stays
     paused; live trading is never blocked by uncertainty.

Admission, per request (remaining = allowance - used):

  P1 live intraday   allowed while remaining > 0
  P2 essential/boards allowed while remaining > emergency_reserve        (reserve belongs to P1)
  P3 dashboard       needs remaining > reserve + the intraday budget not yet spent, and its own
                     budget
  P4 warmer          as P3, plus: quota VERIFIED, its daily budget, its hourly cap, no 429
                     cooldown, and not manually paused

The day resets at the provider's reset hour (default 20:00 US Eastern); counters and any
dashboard reading are discarded at that moment, so a new day starts UNVERIFIED unless the
provider headers say otherwise.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from typing import Callable, NamedTuple, Optional

import tab_settings as cfg
import uw_priority as pri


class Decision(NamedTuple):
    ok: bool
    reason: str = ""
    code: str = "OK"


def _group(priority: int) -> str:
    return {pri.LIVE: "live", pri.ESSENTIAL: "essential", pri.DASHBOARD: "other"}.get(priority, "warmer")


class Controller:
    def __init__(self, settings: Optional[dict] = None, now: Callable[[], float] = time.time,
                 persist: bool = True) -> None:
        self.s = dict(settings or cfg.UW_BUDGET)
        self._now = now
        self._lock = threading.RLock()
        self._persist = persist
        self._since_save = 0
        self.paused_warmer = False
        self.state = self._fresh_state(self._day())
        if persist:
            self._load()

    # -- time ---------------------------------------------------------------------------
    def _et(self) -> datetime:
        from zoneinfo import ZoneInfo
        return datetime.fromtimestamp(self._now(), ZoneInfo("America/New_York"))

    def _day(self) -> str:
        """Provider counting day: it rolls over at reset_hour_et, not at midnight."""
        shift = 24 - int(self.s["reset_hour_et"])
        return (self._et() + timedelta(hours=shift)).date().isoformat()

    def _hour(self) -> int:
        return int(self._now() // 3600)

    def next_reset_epoch(self) -> float:
        et = self._et()
        reset = et.replace(hour=int(self.s["reset_hour_et"]), minute=0, second=0, microsecond=0)
        if reset <= et:
            reset += timedelta(days=1)
        return reset.timestamp()

    # -- state --------------------------------------------------------------------------
    def _fresh_state(self, day: str) -> dict:
        return {"day": day, "modules": {}, "groups": {"live": 0, "essential": 0, "other": 0, "warmer": 0},
                "warmer_hour": {"hour": self._hour(), "calls": 0}, "module_hour": {},
                "provider": None, "dashboard": None, "cooldown": {}, "denied": {}, "total_calls": 0}

    def _roll(self) -> None:
        d = self._day()
        if d != self.state["day"]:
            self.state = self._fresh_state(d)

    def _mod(self, module: str) -> dict:
        return self.state["modules"].setdefault(module, {
            "calls": 0, "failed": 0, "r429": 0, "timeouts": 0, "busy": 0, "denied": 0})

    # -- usage / quota ------------------------------------------------------------------
    def note_provider(self, used: Optional[int], limit: Optional[int]) -> None:
        """The provider's own daily headers: authoritative while fresh."""
        if used is None:
            return
        with self._lock:
            self._roll()
            self.state["provider"] = {"used": int(used), "limit": int(limit) if limit else None,
                                      "at": self._now()}

    def record_dashboard_usage(self, used: int, at: Optional[float] = None) -> dict:
        """Record the request count read from the provider dashboard (the authoritative source)."""
        with self._lock:
            self._roll()
            at = float(at if at is not None else self._now())
            self.state["dashboard"] = {"used": int(used), "at": at,
                                       "calls_at": self.state["total_calls"]}
            self._save()
            return dict(self.state["dashboard"])

    def quota_state(self) -> str:
        with self._lock:
            self._roll()
            p = self.state["provider"]
            if p and self._now() - p["at"] <= self.s["provider_headers_max_age_s"]:
                return "VERIFIED_PROVIDER"
            d = self.state["dashboard"]
            if d and self._now() - d["at"] <= self.s["dashboard_reading_max_age_s"]:
                return "VERIFIED_DASHBOARD"
            return "UNVERIFIED"

    def allowance(self) -> int:
        p = self.state["provider"]
        if p and p.get("limit") and self.quota_state() == "VERIFIED_PROVIDER":
            return int(p["limit"])
        return int(self.s["daily_limit"])

    def used_total(self) -> int:
        with self._lock:
            self._roll()
            q = self.quota_state()
            if q == "VERIFIED_PROVIDER":
                return int(self.state["provider"]["used"])
            if q == "VERIFIED_DASHBOARD":
                d = self.state["dashboard"]
                return int(d["used"] + max(0, self.state["total_calls"] - d["calls_at"]))
            return int(self.state["total_calls"])

    def remaining(self) -> int:
        return max(0, self.allowance() - self.used_total())

    # -- admission ----------------------------------------------------------------------
    def _poll_external(self) -> None:
        """Pick up what an operator recorded from OUTSIDE this process (uw_budget_cli.py): the
        dashboard usage reading and the warmer pause switch. Read at most every 30 s."""
        if not self._persist or self._now() - getattr(self, "_polled_at", 0.0) < 30.0:
            return
        self._polled_at = self._now()
        try:
            import db_cache
            held = db_cache.get("uwbudget:dashboard")
            if held and isinstance(held[1], dict):
                r = held[1]
                cur = self.state["dashboard"]
                if r.get("day") == self.state["day"] and (not cur or r["at"] > cur["at"]):
                    self.state["dashboard"] = {"used": int(r["used"]), "at": float(r["at"]),
                                               "calls_at": self.state["total_calls"]}
            ctl = db_cache.get("uwbudget:control")
            if ctl and isinstance(ctl[1], dict):
                self.paused_warmer = bool(ctl[1].get("paused"))
        except Exception:  # noqa: BLE001
            pass

    def _decide(self, priority: int) -> Decision:
        self._roll()
        self._poll_external()
        s = self.s
        rem = self.allowance() - self.used_total()
        if rem <= 0:
            return Decision(False, "daily allowance used up", "EXHAUSTED")
        if priority == pri.LIVE:
            return Decision(True)
        reserve = s["emergency_reserve"]
        if rem <= reserve:
            return Decision(False, f"only {rem} calls left: the emergency reserve ({reserve}) is kept "
                                   f"for the live intraday engine", "RESERVE")
        g = self.state["groups"]
        intraday_used = g["live"] + g["essential"]
        if priority == pri.ESSENTIAL:
            # Essential data may not eat the share the live engine is owed.
            if g["essential"] >= s["essential_budget"]:
                return Decision(False, "essential-data budget for today is used (the rest is kept for the "
                                       "live engine)", "ESSENTIAL_BUDGET")
            return Decision(True)
        headroom = reserve + max(0, s["intraday_budget"] - intraday_used)   # intraday keeps what it is owed
        if rem - headroom <= 0:
            return Decision(False, "the remaining calls are protected for live intraday trading",
                            "YIELD_TO_INTRADAY")
        cd = self.state["cooldown"].get(str(priority))
        if cd and self._now() < cd:
            return Decision(False, f"cooling down after a 429 for {int(cd - self._now())}s", "COOLDOWN_429")
        if priority == pri.DASHBOARD:
            if g["other"] >= s["other_budget"]:
                return Decision(False, "dashboard budget for today is used", "OTHER_BUDGET")
            return Decision(True)
        # warmer
        if self.paused_warmer:
            return Decision(False, "warmer paused manually", "PAUSED")
        if self.quota_state() == "UNVERIFIED":
            return Decision(False, "provider quota is not verified (record the dashboard usage with "
                                   "uw_budget_cli.py)", "UNVERIFIED_QUOTA")
        if g["warmer"] >= s["warmer_budget"]:
            return Decision(False, "warmer budget for today is used", "WARMER_BUDGET")
        wh = self.state["warmer_hour"]
        if wh["hour"] == self._hour() and wh["calls"] >= s["warmer_hourly_cap"]:
            return Decision(False, "warmer hourly cap reached", "HOURLY_CAP")
        return Decision(True)

    def admit(self, priority: int, module: str) -> Decision:
        with self._lock:
            d = self._decide(priority)
            if not d.ok:
                m = self._mod(module)
                m["denied"] += 1
                self.state["denied"][d.code] = self.state["denied"].get(d.code, 0) + 1
            return d

    def peek(self, priority: int) -> Decision:
        """Would a request of this priority be admitted now? (no side effects)"""
        with self._lock:
            return self._decide(priority)

    def warmer_gate(self) -> tuple[bool, str, float]:
        """(may the warmer run a batch now?, why not, suggested seconds to wait)."""
        with self._lock:
            d = self._decide(pri.WARMER)
            if d.ok:
                return True, "", 0.0
            wait = {"HOURLY_CAP": 3600 - (self._now() % 3600), "COOLDOWN_429": 120.0,
                    "UNVERIFIED_QUOTA": 300.0, "YIELD_TO_INTRADAY": 300.0, "PAUSED": 300.0,
                    "RESERVE": 300.0}.get(d.code, max(60.0, self.next_reset_epoch() - self._now()))
            return False, f"{d.code}: {d.reason}", float(min(wait, 3600.0))

    # -- recording ----------------------------------------------------------------------
    def record_call(self, priority: int, module: str, outcome: str) -> None:
        """A request actually went to the network. outcome: ok | error | 429 | timeout."""
        with self._lock:
            self._roll()
            m = self._mod(module)
            m["calls"] += 1
            self.state["total_calls"] += 1
            self.state["groups"][_group(priority)] += 1
            mh = self.state["module_hour"].setdefault(module, {"hour": self._hour(), "calls": 0})
            if mh["hour"] != self._hour():
                mh["hour"], mh["calls"] = self._hour(), 0
            mh["calls"] += 1
            if priority == pri.WARMER:
                wh = self.state["warmer_hour"]
                if wh["hour"] != self._hour():
                    wh["hour"], wh["calls"] = self._hour(), 0
                wh["calls"] += 1
            if outcome != "ok":
                m["failed"] += 1
            if outcome == "429":
                m["r429"] += 1
                for p, secs in self.s["cooldown_429_s"].items():
                    self.state["cooldown"][str(p)] = self._now() + float(secs)
            elif outcome == "timeout":
                m["timeouts"] += 1
            self._since_save += 1
            if self._since_save >= 25:
                self._save()

    def module_hour_calls(self, module: str) -> int:
        """Requests this module has made in the current clock hour."""
        with self._lock:
            self._roll()
            mh = self.state["module_hour"].get(module)
            return mh["calls"] if mh and mh["hour"] == self._hour() else 0

    def record_busy(self, priority: int, module: str) -> None:
        with self._lock:
            self._mod(module)["busy"] += 1

    # -- health / reporting -------------------------------------------------------------
    def api_health(self) -> str:
        """OK / DEGRADED / RATE_LIMITED / BUDGET_EXHAUSTED -- from recent outcomes, not from HTTP 200."""
        with self._lock:
            self._roll()
            if self.remaining() <= 0:
                return "BUDGET_EXHAUSTED"
            now = self._now()
            if any(now < t for t in self.state["cooldown"].values()):
                return "RATE_LIMITED"
            tot = self.state["total_calls"]
            failed = sum(m["failed"] for m in self.state["modules"].values())
            return "DEGRADED" if tot >= 20 and failed / tot > 0.2 else "OK"

    def snapshot(self) -> dict:
        with self._lock:
            self._roll()
            used = self.used_total()
            allow = self.allowance()
            g = self.state["groups"]
            return {
                "day": self.state["day"], "quota_state": self.quota_state(), "allowance": allow,
                "used_total": used, "remaining": max(0, allow - used),
                "local_calls": self.state["total_calls"],
                "emergency_reserve": self.s["emergency_reserve"],
                "budgets": {"intraday": {"budget": self.s["intraday_budget"], "used": g["live"] + g["essential"]},
                            "live_engine": {"used": g["live"]},
                            "essential": {"budget": self.s["essential_budget"], "used": g["essential"],
                                          "feed_hour_cap": self.s["feed_hourly_cap"],
                                          "feed_hour_used": self.module_hour_calls("ai_boards")},
                            "other": {"budget": self.s["other_budget"], "used": g["other"]},
                            "warmer": {"budget": self.s["warmer_budget"], "used": g["warmer"],
                                       "hour_cap": self.s["warmer_hourly_cap"],
                                       "hour_used": self.state["warmer_hour"]["calls"]
                                       if self.state["warmer_hour"]["hour"] == self._hour() else 0}},
                "modules": {k: dict(v) for k, v in sorted(self.state["modules"].items(),
                                                           key=lambda kv: -kv[1]["calls"])},
                "denied": dict(self.state["denied"]),
                "dashboard_reading": self.state["dashboard"], "provider_headers": self.state["provider"],
                "api_health": self.api_health(), "warmer_paused": self.paused_warmer,
                "warmer_gate": self.warmer_gate()[1] or "open",
                "next_reset_epoch": self.next_reset_epoch(),
            }

    # -- persistence (best effort; environment-namespaced by db_cache) ----------------------
    def _key(self) -> str:
        return "uwbudget:state"

    def _save(self) -> None:
        self._since_save = 0
        if not self._persist:
            return
        try:
            import db_cache
            db_cache.put(self._key(), {"state": self.state, "paused": self.paused_warmer})
        except Exception:  # noqa: BLE001
            pass

    def _load(self) -> None:
        try:
            import db_cache
            held = db_cache.get(self._key())
            if held and isinstance(held[1], dict) and held[1].get("state", {}).get("day") == self.state["day"]:
                self.state = held[1]["state"]
                self.paused_warmer = bool(held[1].get("paused"))
        except Exception:  # noqa: BLE001
            pass

    def set_warmer_paused(self, paused: bool) -> None:
        with self._lock:
            self.paused_warmer = bool(paused)
            self._save()

    def flush(self) -> None:
        with self._lock:
            self._save()


_controller: Optional[Controller] = None
_clock = threading.Lock()


def get() -> Controller:
    global _controller
    with _clock:
        if _controller is None:
            _controller = Controller()
        return _controller


def reset_for_tests(controller: Optional[Controller] = None) -> Controller:
    global _controller
    with _clock:
        _controller = controller or Controller(persist=False)
        return _controller
