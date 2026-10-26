"""Tiered live scanning: spend the live engine's API budget where an actionable BUY / SELL is closest.

Rescoring every name every few minutes costs far more than the live budget allows for 38 names
(measured: ~66 requests per name per hour at a 3-minute rescan). So the names are ranked and given a
rescore PERIOD by tier:

    hot   open positions, pending confirmations, then the names leaning hardest from neutral
    warm  the next strongest leaners
    cold  near-neutral names -- they cannot become actionable without first moving, so a slow
          look is enough; a name that starts to lean is promoted at its next look

Every name stays on the boards. A name whose last live score is older than the engine-score limit is
shown as WAIT by the data-safety gate -- tiering lowers coverage, never data quality.

The split is computed, not guessed: ``plan()`` takes the hourly live budget and the measured cost of one
rescore and returns the largest hot, then warm, tiers whose total cost fits. It is pure and tested.
"""
from __future__ import annotations

from typing import Optional

import tab_settings as cfg


def _p() -> dict:
    return cfg.INTRADAY_TIERS


def plan(ranked: list, forced_hot: set, budget_per_hour: float, cost_per_pass: float,
         hot_s: Optional[float] = None, warm_s: Optional[float] = None, cold_s: Optional[float] = None) -> dict:
    """Assign tiers. ``ranked`` = symbols strongest first. ``forced_hot`` always hot (and paid for first).

    Returns {"hot": [...], "warm": [...], "cold": [...], "cost_per_hour": float, "fits": bool}.
    ``fits`` is False when even an all-cold plan exceeds the budget (the hard cap then protects)."""
    p = _p()
    hot_s = float(hot_s or p["hot_period_s"])
    warm_s = float(warm_s or p["warm_period_s"])
    cold_s = float(cold_s or p["cold_period_s"])
    cost = max(0.0, float(cost_per_pass))
    budget = max(0.0, float(budget_per_hour)) * float(p["budget_safety"])
    forced = [s for s in ranked if s in forced_hot] + [s for s in forced_hot if s not in ranked]
    rest = [s for s in ranked if s not in forced_hot]

    def per_hour(n_hot: int, n_warm: int, n_cold: int, cold_period: float) -> float:
        return cost * (n_hot * 3600.0 / hot_s + n_warm * 3600.0 / warm_s + n_cold * 3600.0 / cold_period)

    # Reliable = rescored inside the engine-score limit (the warm period is). So the budget buys as many
    # WARM names as it can, strongest first; only open positions / pending confirmations (``forced``)
    # are rescored faster. Whatever is left is cold, as slow as allowed.
    n_rest = len(rest)
    best = None
    for cold_period in dict.fromkeys((cold_s, max(cold_s, float(p["cold_max_period_s"])))):
        for n_warm in range(n_rest, -1, -1):
            n_cold = n_rest - n_warm
            c = per_hour(len(forced), n_warm, n_cold, cold_period)
            if c <= budget:
                if best is None or n_warm > best[0]:          # slower cold names may buy more warm ones
                    best = (n_warm, n_cold, c, cold_period)
                break
    if best is None:                                                     # nothing fits: all cold, as slow as allowed
        cold_period = max(cold_s, float(p["cold_max_period_s"]))
        best = (0, n_rest, per_hour(len(forced), 0, n_rest, cold_period), cold_period)
    n_warm, n_cold, c, cold_period = best
    return {"hot": forced, "warm": rest[:n_warm], "cold": rest[n_warm:], "cost_per_hour": round(c, 1),
            "fits": c <= budget, "reliable": len(forced) + n_warm,
            "periods_s": {"hot": hot_s, "warm": warm_s, "cold": cold_period}}


def period_for(symbol: str, tiers: dict) -> float:
    for name in ("hot", "warm", "cold"):
        if symbol in tiers.get(name, []):
            return float(tiers["periods_s"][name])
    return float(tiers["periods_s"]["cold"])


def tier_of(symbol: str, tiers: dict) -> str:
    for name in ("hot", "warm", "cold"):
        if symbol in tiers.get(name, []):
            return name
    return "cold"


def hourly_budget(snapshot: dict, hours_left: float) -> float:
    """Live requests per hour the engine may spend: what is left of its hard cap spread over the rest of
    the session, never above the hourly cap."""
    live = snapshot["budgets"]["live_engine"]
    left = max(0, int(live["hard_cap"]) - int(live["used"]))
    hours = max(float(_p()["min_hours_left"]), float(hours_left))
    return float(min(live["hour_cap"], left / hours))


def rank(symbols: list, scores: dict) -> list:
    """Strongest leaners first: |score - 50|; names with no score yet go first (they need a first look)."""
    def key(s):
        v = scores.get(s)
        return (0, 0.0) if v is None else (1, -abs(float(v) - 50.0))
    return sorted(symbols, key=key)
