"""One vocabulary for how current a figure or a signal is, used the same way on the Earnings, Options Flow and Smart Money Move screens.

  LIVE         produced just now from a live feed or by the frozen engine on in-window evidence, and not older than its freshness limit
  CACHED       real data that was not fetched for this request: served from the application's own caches within their limit, or a feed that is permanently delayed by the account's entitlement
  STALE        older than its freshness limit: do not treat it as current
  HISTORICAL   a stored earlier run, a closed market's last session, or a call about an event that already reported
  UNAVAILABLE  there is no usable data (provider offline, rate-limited, empty answer, no call made)

The Options Flow payloads already carry a freshness stamp (LIVE / DELAYED / SNAPSHOT / FILED / QUARTERLY); this module maps them, together with the cache age and the market session, onto the five words above.
Nothing here fetches anything.
"""
from __future__ import annotations

from typing import Any, Optional

CLASSES = ("LIVE", "CACHED", "STALE", "HISTORICAL", "UNAVAILABLE")
OPEN_SESSIONS = ("OPEN", "PRE_MARKET", "AFTER_HOURS")


def from_stamp(kind: Optional[str], stale: bool = False) -> str:
    """A freshness stamp kind (freshness.py) -> class. LIVE stays LIVE unless stale; DELAYED is a real but delayed copy (CACHED); SNAPSHOT / FILED / QUARTERLY are stored states of the world (HISTORICAL)."""
    k = (kind or "").upper()
    if k in ("SNAPSHOT", "FILED", "QUARTERLY"):
        return "HISTORICAL"
    if stale:
        return "STALE"
    if k == "LIVE":
        return "LIVE"
    if k == "DELAYED":
        return "CACHED"
    return "UNAVAILABLE"


def options_flow_class(payload: Any, session: Optional[str], cache_age_s: Optional[float], ttl_s: Optional[float]) -> str:
    """Class of one Options Flow payload.
    UNAVAILABLE when the provider did not answer or the tape is empty; HISTORICAL when the market is closed (it is the last session's tape); STALE when the cached copy is older than three times its freshness limit or the newest print
    in an open session is more than an hour old; LIVE when the newest print is within two minutes and the copy is within its limit; CACHED otherwise (served from cache within its limit, or a delayed tape)."""
    if not isinstance(payload, dict) or payload.get("status") not in (None, "OK"):
        return "UNAVAILABLE"
    trades = payload.get("trades")
    if trades is not None and not trades and not payload.get("unusual"):
        return "UNAVAILABLE"
    if session and session not in OPEN_SESSIONS:
        return "HISTORICAL"
    ttl = float(ttl_s) if ttl_s else None
    if cache_age_s is not None and ttl and cache_age_s > 3 * ttl:
        return "STALE"
    delay = payload.get("delay_minutes")
    if isinstance(delay, (int, float)) and delay > 60:
        return "STALE"
    if isinstance(delay, (int, float)) and delay <= 2 and (cache_age_s is None or not ttl or cache_age_s <= ttl):
        return "LIVE"
    return "CACHED"
