"""
Analyst consensus from the provider's ratings feed.

The feed publishes one row per *action* -- a firm initiating, maintaining,
upgrading or downgrading -- not a consensus table. A consensus is therefore
built here, and the construction matters:

* One vote per firm. A firm that reiterates four times in a quarter is still
  one opinion; counting every row would let the loudest desk outvote the rest.
* Only the firm's most recent row counts, so an upgrade supersedes the stale
  rating it replaced.
* Ratings are mapped to buy / hold / sell by label. Labels outside the mapped
  set are counted as unclassified and reported, never silently folded into
  "hold" -- a consensus that quietly absorbs what it does not understand is
  worse than one that admits the gap.

Everything is dated and the window is reported alongside the numbers, because
"78% buy" means nothing without knowing across how many firms and how recently.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import date, timedelta
from typing import Any, Optional

import provider_config as cfg
import live_market_service as market

WINDOW_DAYS = 180
CONSENSUS_TTL = 6 * 3600.0

# Label -> bucket. Deliberately explicit rather than keyword matching:
# "Market Perform" and "Outperform" both contain "perform" and mean opposite
# things.
_BUY = {
    "buy", "strong buy", "overweight", "outperform", "sector outperform",
    "market outperform", "positive", "accumulate", "add", "conviction buy",
    "top pick",
}
_HOLD = {
    "hold", "neutral", "equal-weight", "equal weight", "market perform",
    "sector perform", "in-line", "in line", "peer perform", "sector weight",
    "perform",
}
_SELL = {
    "sell", "strong sell", "underweight", "underperform",
    "sector underperform", "market underperform", "negative", "reduce",
}


def _bucket(label: Any) -> Optional[str]:
    text = str(label or "").strip().lower()
    if not text:
        return None
    if text in _BUY:
        return "buy"
    if text in _HOLD:
        return "hold"
    if text in _SELL:
        return "sell"
    return None


def _num(value: Any) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def _fetch(symbol: str, days: int) -> Optional[list[dict]]:
    """
    Recent analyst actions, in the shape the rest of this file reads.

    The keys below are Benzinga's -- ``rating_current``, ``pt_current`` --
    and are kept because the bucketing, the per-firm de-duplication and the
    target average downstream are all written against them. Only the source
    changed.
    """
    import uw_company_service as uwc

    out = uwc.analyst_actions(symbol, limit=200)
    if out.get("status") == "OK":
        pass
    elif out.get("status") == "NO_DATA":
        return []
    else:
        return None

    since = (date.today() - timedelta(days=days)).isoformat()
    return [
        {
            "analyst": r.get("firm") or r.get("analyst"),
            "firm_id": r.get("firm"),
            "date": r.get("date"),
            "rating_current": r.get("recommendation"),
            "pt_current": r.get("target"),
            "adjusted_pt_current": None,
        }
        for r in out.get("rows") or []
        if str(r.get("date") or "") >= since
    ]


def get_consensus(symbol: str, days: int = WINDOW_DAYS) -> dict:
    """Buy / hold / sell shares and the consensus price target."""
    symbol = symbol.upper()
    key = f"consensus:{symbol}:{days}"
    cached = market.cache.get(key, CONSENSUS_TTL)
    if cached:
        return cached

    import uw_company_service as uwc

    if not uwc.configured():
        return {"status": "PROVIDER_NOT_CONFIGURED",
                "detail": "No ratings provider is configured."}

    rows = _fetch(symbol, days)
    if rows is None:
        return {"status": "PROVIDER_OFFLINE",
                "detail": "The ratings provider did not answer."}
    if not rows:
        result = {
            "status": "NO_COVERAGE",
            "detail": (f"No analyst actions published for {symbol} in the last "
                       f"{days} days. The provider's ratings feed covers a "
                       "restricted universe."),
            "firms": 0,
        }
        market.cache.put(key, result)
        return result

    # Latest row per firm. Rows are newest first, but that is the provider's
    # choice rather than a guarantee, so sort rather than trust it.
    rows = sorted(rows, key=lambda r: str(r.get("date") or ""), reverse=True)
    latest: dict[str, dict] = {}
    for row in rows:
        firm = str(row.get("analyst") or row.get("firm_id") or "").strip()
        if firm and firm not in latest:
            latest[firm] = row

    buckets = {"buy": 0, "hold": 0, "sell": 0}
    unclassified: list[str] = []
    targets: list[float] = []
    for row in latest.values():
        bucket = _bucket(row.get("rating_current"))
        if bucket:
            buckets[bucket] += 1
        else:
            label = str(row.get("rating_current") or "").strip()
            if label:
                unclassified.append(label)
        target = _num(row.get("adjusted_pt_current") or row.get("pt_current"))
        if target:
            targets.append(target)

    rated = sum(buckets.values())

    def share(n: int) -> Optional[float]:
        return round(n / rated * 100.0, 1) if rated else None

    consensus_target = (round(sum(targets) / len(targets), 2)
                        if targets else None)

    # The headline label follows the plurality, but only when it is a real
    # majority; a 45/40/15 split is not "Bullish".
    lean = "Mixed"
    if rated:
        top = max(buckets, key=lambda k: buckets[k])
        if buckets[top] / rated >= 0.5:
            lean = {"buy": "Bullish", "hold": "Neutral",
                    "sell": "Bearish"}[top]

    result = {
        "status": "OK",
        "symbol": symbol,
        "window_days": days,
        "firms": len(latest),
        "rated": rated,
        "buy": buckets["buy"], "hold": buckets["hold"], "sell": buckets["sell"],
        "buy_percent": share(buckets["buy"]),
        "hold_percent": share(buckets["hold"]),
        "sell_percent": share(buckets["sell"]),
        "lean": lean,
        "price_target": consensus_target,
        "price_target_firms": len(targets),
        "unclassified": sorted(set(unclassified)),
        "detail": (
            f"{rated} firms rated {symbol} in the last {days} days"
            + (f"; {len(unclassified)} rating label(s) not mapped to a bucket"
               if unclassified else "")
            + "."
        ),
    }
    market.cache.put(key, result)
    return result
