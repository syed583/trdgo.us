"""
Freshness policy for the earnings analysis: how old each KIND of data may be.

The earnings caches (``earn:equity:SYM`` and ``earn:trade:default:SYM``) are composite: one entry holds estimates that change
daily, price and options data that change by the hour, and history that changes once a quarter. One age limit for the whole
entry is therefore wrong in both directions, so the limits live per kind of data (``DEFAULTS`` below) and are
evaluated against the parameters that actually carry weight in a given score.

What this module can and cannot say: the cache stores ONE build time per entry, not a timestamp per provider field. The age
of the entry is therefore an UPPER bound on the age of every value in it, and a verdict of STALE is reliable while FRESH
means only "built recently enough". Per-field source timestamps do not exist in the stored payloads (the ``evidence`` object
of every parameter is empty); the evidence record written at capture time says so explicitly.

Nothing here changes a score, a weight or a threshold. It only labels data age and decides what the warmer and the capture
gate do with an old entry.
"""

from __future__ import annotations

import json
import os
from typing import Optional

FRESH = "FRESH"
STALE = "STALE"
UNKNOWN = "UNKNOWN"

# Which kind of data each of the 12 Equity parameters is made of (earnings_equity_service.get_analysis).
PARAM_DATA_TYPE: dict[str, str] = {
    "eps_estimates": "ESTIMATES",
    "revenue_estimates": "ESTIMATES",
    "guidance": "ESTIMATES",
    "historical_reaction": "HISTORICAL",
    "price_action": "PRICE",
    "sector_market": "MARKET",
    "options_positioning": "OPTIONS_FLOW",
    "unusual_options": "OPTIONS_FLOW",
    "disparity": "OPTIONS_FLOW",
    "dark_pool": "DARK_POOL",
    "peer_readthrough": "PEERS",
    "short_insider": "OWNERSHIP",
}

# Where each parameter's input comes from (from the code audit of earnings_equity_service / directional_signals).
PARAM_SOURCE: dict[str, str] = {
    "eps_estimates": "Unusual Whales /api/companies/{t}/earnings-estimates (+ Finnhub /stock/earnings fallback)",
    "revenue_estimates": "Unusual Whales /api/companies/{t}/earnings-estimates",
    "guidance": "Unusual Whales /api/companies/{t}/earnings-estimates (analyst forward quarter, not company guidance)",
    "historical_reaction": "Unusual Whales /api/stock/{t}/earnings (EPS surprise history, not price reaction)",
    "price_action": "Unusual Whales daily candles + option-chain key levels (internal calculation)",
    "sector_market": "Live quote of SPY, one-day change (market-wide, identical for every symbol)",
    "options_positioning": "Unusual Whales option flow + open interest + SEC 13F institutional change",
    "unusual_options": "Unusual Whales unusual-activity prints + option-chain volume",
    "disparity": "Unusual Whales option chain (put/call skew, volume put/call ratio)",
    "dark_pool": "Unusual Whales dark-pool price levels versus spot (not trade side)",
    "peer_readthrough": "Finnhub /stock/peers + /stock/earnings + /stock/recommendation",
    "short_insider": "Unusual Whales short interest + Form 4 / 13D-G insider activity",
}


# Defaults. They live here, not in tab_settings.py, because tab_settings is a pinned scoring-configuration module and these values
# change no score, weight or threshold. Every value can be overridden with ONE JSON environment variable, EARN_FRESHNESS_JSON.
#   limits_h : kind -> (imminent <= imminent_days, near <= near_days, far) maximum age in HOURS before that kind is STALE
#   warm_*   : the warmer re-warms a name older than warm_imminent_h (reporting within imminent_days) or warm_default_h
#   capture_days : prediction-capture window in calendar days (enforce mode); None = the warmer's own window (10 trading days)
DEFAULTS: dict = {
    "imminent_days": 2,
    "near_days": 10,
    "warm_imminent_h": 12.0,
    "warm_default_h": 20.0,
    "capture_days": None,
    "limits_h": {
        "ESTIMATES": (36.0, 48.0, 72.0),        # analyst EPS / revenue / forward quarter (changes daily at most)
        "HISTORICAL": (168.0, 168.0, 168.0),    # past surprises: changes only when a company reports
        "PRICE": (12.0, 24.0, 36.0),            # price action, trend, RSI, key levels
        "MARKET": (12.0, 24.0, 36.0),           # market-wide backdrop used as the sector parameter
        "OPTIONS_FLOW": (12.0, 24.0, 48.0),     # options flow, open interest, unusual activity, disparity
        "DARK_POOL": (12.0, 24.0, 48.0),
        "PEERS": (48.0, 72.0, 96.0),
        "OWNERSHIP": (336.0, 336.0, 336.0),     # short interest / insider: twice-monthly data; needs its own as_of
        "NEWS": (1.0, 1.0, 1.0),                # not consumed by the earnings score today
    },
}


def config() -> dict:
    """DEFAULTS with the optional EARN_FRESHNESS_JSON override applied."""
    cfg = json.loads(json.dumps(DEFAULTS, default=list))
    raw = (os.environ.get("EARN_FRESHNESS_JSON") or "").strip()
    if raw:
        try:
            over = json.loads(raw)
            for k, v in over.items():
                if k == "limits_h" and isinstance(v, dict):
                    cfg.setdefault("limits_h", {}).update(v)
                else:
                    cfg[k] = v
        except (ValueError, TypeError):
            pass                                   # a malformed override is ignored, never fatal
    return cfg


def band(days_to: Optional[int], cfg: Optional[dict] = None) -> str:
    """'imminent' (0..imminent_days), 'near' (..near_days), 'far' (beyond), 'unknown' when there is no usable date."""
    cfg = cfg or config()
    if days_to is None:
        return "unknown"
    if days_to <= int(cfg.get("imminent_days", 2)):
        return "imminent"
    if days_to <= int(cfg.get("near_days", 10)):
        return "near"
    return "far"


def limit_hours(data_type: str, days_to: Optional[int], cfg: Optional[dict] = None) -> float:
    """Maximum age in hours for ``data_type``. An unknown date uses the strictest (imminent) limit; an unknown kind is strict."""
    cfg = cfg or config()
    trio = (cfg.get("limits_h") or {}).get(data_type)
    if not trio:
        return 12.0
    b = band(days_to, cfg)
    return float(trio[{"imminent": 0, "near": 1, "far": 2, "unknown": 0}[b]])


def warm_max_age_seconds(days_to: Optional[int], cfg: Optional[dict] = None) -> float:
    """How old a name's cache may be before the warmer re-warms it. Shorter for names reporting within imminent_days."""
    cfg = cfg or config()
    # An unknown date uses the default age (it is rare, and re-warming it more often would only spend requests).
    hours = cfg.get("warm_imminent_h", 12.0) if band(days_to, cfg) == "imminent" else cfg.get("warm_default_h", 20.0)
    return float(hours) * 3600.0


def assess(cache_age_h: Optional[float], params: Optional[list], days_to: Optional[int], cfg: Optional[dict] = None) -> dict:
    """Judge a cache entry against the kinds of data that carry weight in it.

    Returns {status, limit_h, cache_age_h, stale, per_param, notes}. ``stale`` lists the parameters whose kind is older than
    its limit. Only parameters with ``available`` and a positive weight count: an unavailable parameter contributes nothing to
    the score, so its age does not matter. Parameters with no as-of data of their own (OWNERSHIP) are noted, not failed."""
    cfg = cfg or config()
    if cache_age_h is None:
        return {"status": UNKNOWN, "limit_h": None, "cache_age_h": None, "stale": [], "per_param": {}, "notes": ["NO_CACHE_TIMESTAMP"]}
    per: dict[str, dict] = {}
    stale: list[str] = []
    strictest: Optional[float] = None
    for p in params or []:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name"))
        if not p.get("available") or not (p.get("weight") or 0) > 0:
            continue
        kind = PARAM_DATA_TYPE.get(name, "UNMAPPED")
        lim = limit_hours(kind, days_to, cfg)
        ok = cache_age_h <= lim
        per[name] = {"data_type": kind, "limit_h": lim, "status": FRESH if ok else STALE, "source": PARAM_SOURCE.get(name)}
        strictest = lim if strictest is None else min(strictest, lim)
        if not ok:
            stale.append(name)
    notes = ["AGE_IS_CACHE_BUILD_TIME_UPPER_BOUND"]
    return {"status": STALE if stale else FRESH, "limit_h": strictest, "cache_age_h": round(cache_age_h, 2),
            "stale": stale, "per_param": per, "notes": notes}


def stale_weight_share(params: Optional[list], stale: list[str]) -> float:
    """Share (0..1) of the available weight sitting in stale parameters."""
    avail = [p for p in params or [] if isinstance(p, dict) and p.get("available")]
    total = sum(p.get("weight", 0) or 0 for p in avail)
    bad = sum(p.get("weight", 0) or 0 for p in avail if str(p.get("name")) in set(stale))
    return round(bad / total, 3) if total else 0.0
