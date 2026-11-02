"""Calendar-freshness guard for any long-running Smart Move collection (root cause of the 42 stale-calendar captures of the one-time V2 run).

The frozen rule: inside D-4 an earnings calendar older than 6 h cannot confirm an event, so evidence captured against it is not eligible. The guard keeps a collector inside that rule with a margin:
  * `age_h` is always computed from the wall clock at the moment of the check, so a sleep, a pause for our own hourly limit or a resume cannot hide an ageing calendar;
  * `ensure_fresh` refreshes the calendar (through the governed RefreshService, so the request cap, spacing, hourly share, 401/429 stop and kill switch all apply) when it is older than MAX_AGE_H (5 h);
  * if the refresh cannot run (budget, provider stop, failure) it returns ok=False and the caller MUST stop collecting: a stale-calendar capture is never made;
  * `GapWatcher` records wall-clock jumps (a suspended PC) so the report shows them.
Nothing here changes scoring or any threshold.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

MAX_AGE_H = 5.0                  # refresh before the frozen 6 h limit, with a margin
REFRESH_MAX_REQUESTS = 30        # upper bound of one calendar refresh (observed: 12-13 requests)
MIN_BUDGET_FOR_REFRESH = 15      # do not start a refresh that the remaining cap cannot finish


def calendar_age_h(store, now: datetime) -> Optional[float]:
    """Hours since the newest calendar observation in the store (None when there is none)."""
    obs = store.read("cal_obs")
    if not obs:
        return None
    newest = max(datetime.fromisoformat(o["observed_at"]) for o in obs)
    return round((now - newest).total_seconds() / 3600, 3)


def needs_refresh(store, now: datetime, max_age_h: float = MAX_AGE_H) -> bool:
    age = calendar_age_h(store, now)
    return age is None or age > max_age_h


def ensure_fresh(svc, *, budget_left: int, max_age_h: float = MAX_AGE_H, stopped=lambda: False) -> dict:
    now = svc.clock.now()
    before = calendar_age_h(svc.store, now)
    out = {"checked_at": now.isoformat(), "age_before_h": before, "refreshed": False, "requests": 0, "ok": True, "reason": None}
    if before is not None and before <= max_age_h:
        out["age_after_h"] = before
        return out
    if stopped():
        return dict(out, ok=False, reason="PROVIDER_STOP")
    if budget_left < MIN_BUDGET_FOR_REFRESH:
        return dict(out, ok=False, reason="BUDGET_TOO_LOW_FOR_CALENDAR_REFRESH (%d left, %d needed)" % (budget_left, MIN_BUDGET_FOR_REFRESH))
    budget = [min(REFRESH_MAX_REQUESTS, budget_left)]
    done = svc.refresh_calendar(budget)
    out.update(refreshed=True, requests=done)
    after = calendar_age_h(svc.store, svc.clock.now())
    out["age_after_h"] = after
    if stopped():
        out.update(ok=False, reason="PROVIDER_STOP_DURING_REFRESH")
    elif done == 0 or after is None or after > max_age_h:
        out.update(ok=False, reason="REFRESH_DID_NOT_FRESHEN_THE_CALENDAR")
    return out


class GapWatcher:
    """Wall-clock gaps between two checks, e.g. a suspended PC. threshold_s should exceed the longest normal pause step."""

    def __init__(self, threshold_s: float = 120.0):
        self.threshold, self.last, self.gaps = timedelta(seconds=threshold_s), None, []

    def tick(self, now: datetime) -> Optional[float]:
        gap = None
        if self.last is not None and now - self.last > self.threshold:
            gap = (now - self.last).total_seconds()
            self.gaps.append({"from": self.last.isoformat(), "to": now.isoformat(), "seconds": round(gap)})
        self.last = now
        return gap
