"""Monitor configuration (schedule, thresholds, alert rules) and the request-cost model. Every default is the owner's proposed interval; a JSON file may override known keys only."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Optional

ALERT_RULES = ("NEW_BUY_SELL", "DIRECTION_CHANGE", "STRONG_AGREEMENT", "DATE_CHANGE", "STALE_OR_MISSING_EVIDENCE", "SCANNER_FAILURE")

# Provider requests the MONITOR itself may start (cache reads cost none). Measured/assumed per action; the budget check and the estimator use the same numbers.
COST_CALENDAR_REFRESH = 20       # uw_earnings_calendar.calendar(): premarket + afterhours for each weekday in the 14-day window (about 10 weekdays); cached dates cost 0
COST_EVENT_REFRESH = 15          # ASSUMPTION, to be measured on the first authorised run: earnings_equity_service.get_analysis + earnings_trade_service.get_summary fan out to estimates, peers, dark pool, flow, history
COST_OUTCOME_VERIFY = 2          # earnings_trade_service._historical_moves: earnings history + daily candles for ONE symbol
HARD_DAILY_CAP = 1000            # no approval can raise the monitor above this


@dataclass
class Config:
    calendar_every_h: float = 6.0
    eligibility_every_min: float = 30.0
    earnings_day_every_min: float = 15.0
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
    alert_cooldown_min: int = 360                # the same rule for the same event/model is not repeated inside this window
    alert_hourly_cap: int = 20                   # more than this many ordinary alerts in an hour are suppressed and summarised once
    flap_changes_24h: int = 3                    # a model changing its call this often in 24 h is reported as unstable, not alerted every time
    accuracy_min_sample: int = 30                # no accuracy percentage is shown below this many scored calls
    comparison_min_shared: int = 20              # no model-vs-model comparison below this many shared scored events
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
