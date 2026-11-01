"""Smart Move automatic-collection decision logic. PURE: given the clock, the event universe and what is already done, it says what the next tick should do.
It sends nothing and registers nothing; a host (Windows Task Scheduler or a VPS timer) runs a cheap tick every 30 minutes and executes the returned actions only when the
live collector is separately authorized. Because the decision is made from the clock inside the tick, DST changes need no schedule edit.

Actions, in priority order: REFRESH_CALENDAR (stale), COLLECT (windows open, closing soonest first, within the budgets), FETCH_OUTCOMES (due, within budget), plus alerts.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docs" / "smart_move" / "shadow_impl"))
from smvshadow import cutoff as co  # noqa: E402

import smart_move_outcomes as outcomes  # noqa: E402

UTC = timezone.utc
REQUESTS_PER_STOCK = 4            # ohlc, earnings, vol_context, iv_rank (measured: 455 attempts for 112 stocks incl. shared ETFs and retries)
SHARED_REQUESTS = 15              # calendar + SPY/sector ETFs
CALENDAR_MAX_AGE_H = 5.0          # same bound the full-universe collector enforces
ALERT_BEFORE_CLOSE_H = 2.0


@dataclass
class Budget:
    daily_cap: int = 1700         # the governor's planning ceiling
    used_today: int = 0
    hourly_cap: int = 100         # the governor's shared hourly cap in live hours
    used_this_hour: int = 0
    stopped: bool = False         # a 401/429 earlier today

    def room(self) -> int:
        return 0 if self.stopped else max(0, min(self.daily_cap - self.used_today, self.hourly_cap - self.used_this_hour))


def estimate_daily_requests(stocks: int) -> int:
    return stocks * REQUESTS_PER_STOCK + SHARED_REQUESTS


def tick(now: datetime, events: Iterable[dict], captured: set, outcomes_done: set, budget: Budget, *, calendar_age_h: Optional[float],
         kill_switch_engaged: bool = True, outcome_fetch_enabled: bool = False, collection_enabled: bool = False, v2_tasks: Optional[list] = None, audit_due: bool = False) -> dict:
    """events: dicts with symbol, earnings_date (YYYY-MM-DD), kind (BMO/AMC/None). captured / outcomes_done: sets of (symbol, earnings_date)."""
    events = list(events)
    actions, alerts, open_ev, ahead, missed = [], [], [], 0, []
    for e in events:
        key = (e["symbol"], str(e["earnings_date"])[:10])
        if e.get("kind") not in ("BMO", "AMC"):
            alerts.append({"type": "UNKNOWN_TIMING", "symbol": e["symbol"], "why": "no BMO/AMC timing, so no window can be computed"})
            continue
        if key in captured:
            continue
        plan = co.plan_event(date.fromisoformat(key[1]), e["kind"])
        start, cut = plan["window_start_utc"], plan["cutoff_utc"]
        if now < start:
            ahead += 1
        elif now <= cut:
            open_ev.append((cut, e["symbol"]))
            if cut - now <= timedelta(hours=ALERT_BEFORE_CLOSE_H):
                alerts.append({"type": "WINDOW_CLOSING", "symbol": e["symbol"], "closes_utc": cut.isoformat()})
        else:
            missed.append(e["symbol"])
            alerts.append({"type": "MISSED_WINDOW", "symbol": e["symbol"], "closed_utc": cut.isoformat()})
    open_ev.sort()                                              # closing soonest first
    stale = calendar_age_h is None or calendar_age_h > CALENDAR_MAX_AGE_H
    if (open_ev or ahead) and stale:
        actions.append({"action": "REFRESH_CALENDAR", "why": "calendar older than %.0f h or unknown" % CALENDAR_MAX_AGE_H, "requests": 1})
    if open_ev and not stale:
        shared = SHARED_REQUESTS if not captured else 0
        n = max(0, min(len(open_ev), (budget.room() - shared) // REQUESTS_PER_STOCK))
        if n:
            actions.append({"action": "COLLECT", "symbols": [s for _, s in open_ev[:n]], "requests": n * REQUESTS_PER_STOCK + shared, "deferred": [s for _, s in open_ev[n:]]})
        else:
            alerts.append({"type": "BUDGET_EXHAUSTED", "deferred": len(open_ev), "room": budget.room()})
    if v2_tasks and open_ev and not stale and not budget.stopped:       # extra V2 evidence for stocks whose window is open, after the V1 collection, within what is left of the budget
        left = max(0, budget.room() - sum(a.get("requests", 0) for a in actions))
        take = v2_tasks[:left]
        if take:
            actions.append({"action": "COLLECT_V2", "symbols": sorted({t["symbol"] for t in take}), "tasks": take, "requests": len(take), "deferred": len(v2_tasks) - len(take)})
    if outcome_fetch_enabled:
        want = [e for e in events if (e["symbol"], str(e["earnings_date"])[:10]) in captured and (e["symbol"], str(e["earnings_date"])[:10]) not in outcomes_done
                and e.get("kind") in ("BMO", "AMC") and outcomes.due(date.fromisoformat(str(e["earnings_date"])[:10]), e["kind"], now)["due"]]
        take = want[:budget.room()]
        if take:
            actions.append({"action": "FETCH_OUTCOMES", "symbols": [e["symbol"] for e in take], "requests": len(take), "event_list": take})
    if audit_due:
        actions.append({"action": "GENERATE_AUDIT", "requests": 0, "why": "a collection or outcome run finished since the last audit; local, no provider request"})
    if budget.stopped:
        alerts.append({"type": "PROVIDER_STOPPED", "why": "a 401/429 stopped collection for the day"})
    live = [a for a in actions]
    if live and not collection_enabled:
        for a in live:
            a["blocked"] = "live collection is not authorized (collection_enabled=False); this is a plan only"
    return {"now": now.isoformat(), "actions": actions, "alerts": alerts, "open_windows": len(open_ev), "windows_ahead": ahead, "missed": missed,
            "kill_switch_engaged": kill_switch_engaged, "execute": bool(live) and collection_enabled and not budget.stopped,
            "note": "the kill switch is released only for the duration of an executed action and re-engaged afterwards"}
