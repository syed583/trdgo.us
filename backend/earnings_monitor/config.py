"""Monitor configuration (schedule, thresholds, alert rules) and the request-cost model. Every default is the owner's proposed interval; a JSON file may override known keys only."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Optional

ALERT_RULES = ("NEW_BUY_SELL", "DIRECTION_CHANGE", "STRONG_AGREEMENT", "MODEL_DISAGREEMENT", "NEW_EARNINGS", "DATE_CHANGE", "STALE_OR_MISSING_EVIDENCE", "NEWS_OR_GUIDANCE", "UNUSUAL_OPTIONS_FLOW",
               "OUTCOME_VERIFIED", "APPROVAL_EXPIRING", "SMART_MOVE_WINDOW", "SCANNER_FAILURE", "PREDICTION_READY", "COLLECTOR_FAILED", "API_CAPACITY")

# Provider requests the MONITOR itself may start (cache reads cost none). Measured/assumed per action; the budget check and the estimator use the same numbers.
COST_CALENDAR_REFRESH = 46       # MEASURED (offline fan-out audit, cold cache): uw_earnings_calendar.calendar(days=30) = 23 weekdays x (premarket + afterhours); dates already cached cost 0
COST_EVENT_REFRESH = 61          # MEASURED LOWER BOUND (offline fan-out audit, cold cache, empty payloads): get_analysis 31 + get_summary 131 less the 80 calendar-date fetches shared with the calendar cache = 61. With real data
                                 # (per-expiry fan-out) the true figure can be higher; the first authorised run records the real count in the usage table
COST_OUTCOME_VERIFY = 2          # earnings_trade_service._historical_moves: measured 1 request (earnings history) cold; 2 allows for the daily-candle read
HARD_DAILY_CAP = 1000            # DEFAULT of Config.hard_daily_cap: the monitor's OWN operational safety guard against a runaway loop. It is not a provider limit (the provider has no daily or per-minute limit; its one limit is 3 concurrent REST requests across all machines).
                                 # Changing it is an owner decision made in em_data\config.json; an approval file alone cannot raise it.


@dataclass
class Config:
    horizon_days: int = 30                       # the rolling calendar window
    model_window_days: int = 15                  # the eight model tabs score the next 15 days (their scan caches are keyed :15); later events show OUT_OF_WINDOW, never PENDING-as-if-neutral
    calendar_every_h: float = 6.0
    eligibility_every_min: float = 30.0
    evidence_scan_every_min: float = 15.0        # cached-evidence change detection (cache reads only)
    news_flow_every_min: float = 60.0            # news / options-flow checks; provider refresh of them needs its own approval
    earnings_day_every_min: float = 15.0
    finnhub_verify_max_per_refresh: int = 0      # third-vendor date checks (1 Finnhub request each) for NEW or CHANGED events only; 0 = off
    inflight_wait_s: int = 60                    # how long a provider call waits for the single in-flight slot before it is refused
    bmo_window_et: tuple = ("05:30", "10:30")
    amc_window_et: tuple = ("15:00", "20:30")
    outcome_delay_min: int = 45                  # after the reaction session closes
    day_refresh_min_age_min: int = 360           # an event's evidence is refreshed at most this often (once per session window), even though checks run every 15 min
    stale_after_h: float = 36.0
    cancel_after_missing_scans: int = 2          # consecutive SUCCESSFUL calendar scans without the event
    coverage_delta: float = 0.10
    max_attempts: int = 4
    backoff_base_s: int = 60
    backoff_max_s: int = 3600
    breaker_threshold: int = 3
    breaker_cooldown_min: int = 30
    lease_s: int = 1800
    max_inflight: int = 1
    strong_agreement_min_models: int = 3
    strong_agreement_share: float = 0.75
    comparable_max_spread_h: float = 24.0
    new_event_alert_burst: int = 25              # more new events than this in one scan is a widened window, not 'newly announced' news
    hard_daily_cap: int = HARD_DAILY_CAP         # configurable operational guard (see HARD_DAILY_CAP); 1..100000
    sm_window_alert_h: float = 6.0               # alert when a Smart Money Move prediction window closes within this many hours and events inside it still have no result
    approval_warn_days: int = 3                  # an approval that ends within this many days raises one alert a day
    external_reserve: int = 250                  # LEAN planning baseline: requests/day kept free for the app's existing warmers, Options Flow and Smart Move collection (replace with the measured figure)
    plan_driven_refresh: bool = False            # OFF by default. True (set in em_data\config.json) lets a REFRESH approval that allows the 'event' purpose also refresh the funded events ahead of their day; False = only on the earnings day itself
    refresh_batch: int = 4                       # event refreshes per EVENT_REFRESH job (they run one at a time through the shared gate)
    refresh_plan_every_min: float = 60.0
    event_refresh_every_min: float = 15.0
    flow_alert_premium: float = 1_000_000.0      # an options-flow alert needs at least this much premium on one side ...
    flow_alert_share: float = 0.70               # ... with this share of it pointing the same way ...
    flow_alert_min_prints: int = 5               # ... across at least this many prints
    alert_cooldown_min: int = 360                # the same rule for the same event/model is not repeated inside this window
    alert_hourly_cap: int = 20                   # more than this many ordinary alerts in an hour are suppressed and summarised once
    flap_changes_24h: int = 3                    # a model changing its call this often in 24 h is reported as unstable, not alerted every time
    accuracy_min_sample: int = 30                # no accuracy percentage is shown below this many scored calls
    comparison_min_shared: int = 20              # no model-vs-model comparison below this many shared scored events
    collector_stale_h: float = 3.0               # a scheduled collector with no finished run for this long while prediction windows are open raises COLLECTOR_FAILED
    capacity_warn_share: float = 0.8             # API_CAPACITY at this share of the monitor guard / the collector daily governor (internal limits, not provider limits)
    alert_rules: dict = field(default_factory=lambda: {r: {"enabled": True} for r in ALERT_RULES})

    @classmethod
    def load(cls, path: Optional[str] = None) -> "Config":
        cfg = cls()
        if path and Path(path).is_file():
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            known = {f.name for f in fields(cls)}
            for k, v in raw.items():
                if k in known:
                    setattr(cfg, k, tuple(v) if isinstance(getattr(cfg, k), tuple) else v)
        return cfg


def _window_minutes(w: tuple) -> int:
    (h1, m1), (h2, m2) = [tuple(int(x) for x in s.split(":")) for s in w]
    return (h2 * 60 + m2) - (h1 * 60 + m1)


def estimate(cfg: Config, events_reporting_per_day: int, outcomes_per_day: Optional[int] = None, purposes=("calendar", "event", "outcome")) -> dict:
    """Worst-case provider requests per day started by the MONITOR itself (the existing warmers keep their own, separate traffic). Cache-only operation costs 0, and a purpose that is not approved costs 0.
    An upper bound: an event's evidence is refreshed at most once per day_refresh_min_age_min inside its session window."""
    n = max(0, int(events_reporting_per_day))
    per_event_refreshes = max(math.ceil(_window_minutes(cfg.bmo_window_et) / cfg.day_refresh_min_age_min), math.ceil(_window_minutes(cfg.amc_window_et) / cfg.day_refresh_min_age_min))
    cal = math.ceil(24 / cfg.calendar_every_h) * COST_CALENDAR_REFRESH if "calendar" in purposes else 0
    day = n * per_event_refreshes * COST_EVENT_REFRESH if "event" in purposes else 0
    out = (n if outcomes_per_day is None else int(outcomes_per_day)) * COST_OUTCOME_VERIFY if "outcome" in purposes else 0
    return {"events_reporting_per_day": n, "purposes": sorted(purposes), "calendar_refreshes_per_day": math.ceil(24 / cfg.calendar_every_h), "calendar_requests": cal,
            "earnings_day_refreshes_per_event": per_event_refreshes, "earnings_day_requests": day, "outcome_requests": out, "total_requests_per_day": cal + day + out, "cache_only_requests_per_day": 0,
            "assumptions": {"calendar_refresh": COST_CALENDAR_REFRESH, "event_refresh": COST_EVENT_REFRESH, "outcome_verify": COST_OUTCOME_VERIFY,
                            "note": "event_refresh is an unmeasured upper-bound assumption; the monitor records the real count in the usage table"}}
