"""Shared provider request governor (component 3). It is the only place that may allow a provider request.

* DEFAULT AUTHORIZATION IS ZERO: `authorized_daily` must be set explicitly by an approved configuration; the 1,700 planning ceiling is only an upper bound, never a grant.
* Shared 400-requests-per-hour constraint of the existing warmer in live hours (04:00-20:00 ET): this governor may use only what the existing operations
  are not using; `reserve_existing_hourly` of the 400 is always kept for them (a provisional parameter, set by the owner).
* Core requests are never starved by lower-priority ones (full-capture extras, calendar refresh): those are refused when the remaining daily budget would fall below
  the requests still reserved for core capture.
* One request at a time, at least `spacing_s` apart; at most `max_retries` retry for transient failures; 429 / 401 / 403 stop everything for the rest of the day.
* Everything that is skipped, refused, retried, failed or missed is written to an append-only ledger.
"""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = timezone.utc
LIVE_START, LIVE_END = time(4, 0), time(20, 0)
PRIORITY = {"core": 0, "full_extra": 1, "calendar": 2}


class ProviderError(Exception):
    def __init__(self, kind, status=None, retry_after=None):
        super().__init__("%s %s" % (kind, status))
        self.kind, self.status, self.retry_after = kind, status, retry_after           # transient | rate_limited | auth; retry_after = seconds the provider asked us to wait (HTTP Retry-After), when it said


class FakeClock:
    def __init__(self, start: datetime):
        self.t = start

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += timedelta(seconds=seconds)


class Governor:
    def __init__(self, clock, *, authorized_daily=0, planning_ceiling=1700, hourly_shared_cap=400, reserve_existing_hourly=300,
                 offhours_hourly_cap=6000, reserve_existing_offhours=1000, existing_usage_fn=lambda ts: 0, spacing_s=2.0, max_retries=1, enabled=True,
                 rate_limit_policy="stop_day", backoff_base_s=30.0, backoff_max_s=900.0, breaker_threshold=4, breaker_cooldown_s=1800.0, breaker_trips_per_day=3, rng=None):
        self.clock, self.authorized_daily, self.planning_ceiling = clock, authorized_daily, planning_ceiling
        self.hourly_shared_cap, self.reserve_existing_hourly = hourly_shared_cap, reserve_existing_hourly
        self.offhours_hourly_cap, self.reserve_existing_offhours = offhours_hourly_cap, reserve_existing_offhours
        self.existing_usage_fn, self.spacing_s, self.max_retries, self.enabled = existing_usage_fn, spacing_s, max_retries, enabled
        self.ledger, self.used_day, self.used_hour = [], {}, {}
        self.core_reserved = 0
        self.stopped_until = None
        # HTTP 429 policy. "stop_day" (the DEFAULT, unchanged): the first 429 stops everything until midnight New York time. "backoff" (opt-in): pause for max(Retry-After, bounded exponential backoff with jitter),
        # open a circuit breaker after `breaker_threshold` 429s in a row (cool-down), stop for the day after `breaker_trips_per_day` breaker trips. Daily/hourly/per-run limits are untouched either way; a refused request is still counted.
        self.rate_limit_policy, self.backoff_base_s, self.backoff_max_s = rate_limit_policy, backoff_base_s, backoff_max_s
        self.breaker_threshold, self.breaker_cooldown_s, self.breaker_trips_per_day = breaker_threshold, breaker_cooldown_s, breaker_trips_per_day
        self.rng = rng or __import__("random").random
        self.consecutive_429, self.breaker_trips = 0, {}
        self.last_request = None

    # ------------------------------------------------------------------ helpers
    def log(self, event, **kw):
        self.ledger.append(dict(kw, type=event, at=self.clock.now().isoformat()))

    def _keys(self):
        n = self.clock.now().astimezone(ET)
        return n.strftime("%Y-%m-%d"), n.strftime("%Y-%m-%d %H"), LIVE_START <= n.time() < LIVE_END

    def reserve_core(self, n):
        self.core_reserved = max(0, int(n))

    def daily_limit(self):
        return min(self.authorized_daily, self.planning_ceiling)

    def hourly_allowance(self):
        day, hour, live = self._keys()
        existing = self.existing_usage_fn(self.clock.now())
        if live:
            allowance = self.hourly_shared_cap - max(existing, self.reserve_existing_hourly)
        else:
            allowance = self.offhours_hourly_cap - max(existing, self.reserve_existing_offhours)
        return max(0, allowance - self.used_hour.get(hour, 0))

    # ------------------------------------------------------------------ decision
    def request(self, purpose, priority="core", units=1, symbol=None):
        if not self.enabled:
            self.log("DENIED_KILL_SWITCH", purpose=purpose, symbol=symbol)
            return False, "KILL_SWITCH"
        now = self.clock.now()
        if self.stopped_until and now < self.stopped_until:
            self.log("DENIED_STOPPED", purpose=purpose, symbol=symbol)
            return False, "STOPPED"
        day, hour, live = self._keys()
        if self.authorized_daily <= 0:
            self.log("DENIED_NOT_AUTHORIZED", purpose=purpose, symbol=symbol)
            return False, "NOT_AUTHORIZED"
        used = self.used_day.get(day, 0)
        if used + units > self.daily_limit():
            self.log("DENIED_DAILY_CAP", purpose=purpose, symbol=symbol, used=used, limit=self.daily_limit())
            return False, "DAILY_CAP"
        if PRIORITY[priority] > 0 and used + units > self.daily_limit() - self.core_reserved:
            self.log("DENIED_CORE_RESERVATION", purpose=purpose, symbol=symbol, priority=priority, used=used, core_reserved=self.core_reserved)
            return False, "CORE_RESERVATION"
        if units > self.hourly_allowance():
            self.log("DENIED_HOURLY_SHARED_CAP", purpose=purpose, symbol=symbol, live_hours=live, allowance=self.hourly_allowance())
            return False, "HOURLY_SHARED_CAP"
        if self.last_request is not None:
            wait = self.spacing_s - (now - self.last_request).total_seconds()
            if wait > 0:
                self.clock.sleep(wait)
        self.last_request = self.clock.now()
        day, hour, _ = self._keys()
        self.used_day[day] = self.used_day.get(day, 0) + units
        self.used_hour[hour] = self.used_hour.get(hour, 0) + units
        return True, "OK"

    def can_afford(self, units, priority="core"):
        """Side-effect-free pre-check used by the runner before it starts an event: (True, 'OK') or (False, reason). Nothing is logged or counted."""
        if not self.enabled:
            return False, "KILL_SWITCH"
        now = self.clock.now()
        if self.stopped_until and now < self.stopped_until:
            return False, "STOPPED"
        if self.authorized_daily <= 0:
            return False, "NOT_AUTHORIZED"
        day, hour, live = self._keys()
        used = self.used_day.get(day, 0)
        if used + units > self.daily_limit():
            return False, "DAILY_CAP"
        if PRIORITY[priority] > 0 and used + units > self.daily_limit() - self.core_reserved:
            return False, "CORE_RESERVATION"
        if units > self.hourly_allowance():
            return False, "HOURLY_SHARED_CAP"
        return True, "OK"

    def seconds_to_next_hour(self):
        n = self.clock.now().astimezone(ET)
        nxt = n.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        return max(1.0, (nxt - n).total_seconds())

    def _stop_for_the_day(self, why):
        n = self.clock.now().astimezone(ET)
        self.stopped_until = datetime.combine(n.date() + timedelta(days=1), time(0, 0), ET).astimezone(UTC)
        self.log("STOP_" + why, until=self.stopped_until.isoformat())

    def _on_429(self, retry_after=None):
        if self.rate_limit_policy != "backoff":
            self._stop_for_the_day("429")
            return
        self.consecutive_429 += 1
        n = self.consecutive_429
        backoff = min(self.backoff_max_s, self.backoff_base_s * (2 ** (n - 1))) * (0.5 + 0.5 * self.rng())          # bounded exponential, jittered 50-100%
        pause = max(backoff, min(float(retry_after or 0), self.backoff_max_s))                                         # the provider's Retry-After is a floor (capped, so a bad header cannot park us for a day)
        day = self.clock.now().astimezone(ET).date().isoformat()
        if n >= self.breaker_threshold:                                                                                # circuit breaker: repeated 429s
            self.breaker_trips[day] = self.breaker_trips.get(day, 0) + 1
            self.consecutive_429 = 0
            if self.breaker_trips[day] >= self.breaker_trips_per_day:
                self._stop_for_the_day("429_BREAKER_DAY")
                return
            pause = max(pause, self.breaker_cooldown_s)
            self.log("BREAKER_OPEN_429", trips_today=self.breaker_trips[day], cooldown_s=round(pause))
        self.stopped_until = self.clock.now() + timedelta(seconds=pause)                                              # nothing (including the runner) sends until then; kill switch and counters unaffected
        self.log("PAUSE_429", seconds=round(pause), consecutive=n, retry_after=retry_after)

    def execute(self, fn, *, purpose, priority="core", units=1, symbol=None):
        """Run fn() under the governor. Returns dict(status=OK|FAILED|DENIED, value, attempts, reason)."""
        attempts = 0
        while True:
            ok, why = self.request(purpose + (":retry" if attempts else ""), priority, units, symbol)
            if not ok:
                return {"status": "DENIED", "reason": why, "attempts": attempts, "value": None}
            attempts += 1
            try:
                val = fn()
                self.consecutive_429 = 0
                return {"status": "OK", "value": val, "attempts": attempts, "reason": None}
            except ProviderError as e:
                self.log("PROVIDER_FAILURE", purpose=purpose, symbol=symbol, kind=e.kind, status=e.status, attempt=attempts)
                if e.kind == "rate_limited":
                    self._on_429(getattr(e, "retry_after", None))
                    return {"status": "FAILED", "reason": "RATE_LIMITED", "attempts": attempts, "value": None}
                if e.kind == "auth":
                    self._stop_for_the_day("AUTH")
                    return {"status": "FAILED", "reason": "AUTH", "attempts": attempts, "value": None}
                if attempts > self.max_retries:
                    return {"status": "FAILED", "reason": "TRANSIENT_RETRIES_EXHAUSTED", "attempts": attempts, "value": None}

    def missed_deadline(self, symbol, event_date, deadline, reason):
        self.log("MISSED_DEADLINE", symbol=symbol, event_date=str(event_date), deadline=deadline.isoformat(), reason=reason)
