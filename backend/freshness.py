"""
How old is this, really.

Every panel in the app draws on a different feed and those feeds are not
remotely alike: a TWS print is now, an OPRA tape print is fifteen minutes ago,
a Form 4 is two days old and a 13F is a quarter old. All four look identical
once rendered as a number in a panel, and a reader cannot tell which is which
without knowing how the app is wired -- which is the wrong thing to require of
them.

So each payload carries a stamp saying what it is. The stamp is derived from
the source that actually answered, not hardcoded per panel: when the option
chain falls back from TWS to the provider outside market hours, the badge has
to change with it or it becomes a decoration that is wrong exactly when it
matters.

Deliberately blunt about the bad cases. A quarterly filing says QUARTERLY and
names the quarter; it does not say "as of" a date that suggests currency it
does not have.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

# The kinds, worst to best. Ordered so a payload assembled from several feeds
# can report the weakest one -- a panel is only as live as its slowest input.
QUARTERLY = "QUARTERLY"
FILED = "FILED"
SNAPSHOT = "SNAPSHOT"
DELAYED = "DELAYED"
LIVE = "LIVE"

_RANK = {QUARTERLY: 0, FILED: 1, SNAPSHOT: 2, DELAYED: 3, LIVE: 4}


def stamp(kind: str, *, source: str = "", detail: str = "",
          delay_minutes: Optional[float] = None,
          as_of: Optional[str] = None,
          label: Optional[str] = None) -> dict:
    """One freshness descriptor, ready to render."""
    return {
        "kind": kind,
        "label": label or _default_label(kind, delay_minutes),
        "source": source,
        "detail": detail,
        "delay_minutes": delay_minutes,
        "as_of": as_of or datetime.now(timezone.utc).isoformat(),
    }


def _default_label(kind: str, delay_minutes: Optional[float]) -> str:
    if kind == LIVE:
        return "LIVE"
    if kind == DELAYED:
        if delay_minutes:
            return f"{int(delay_minutes)}-MIN DELAYED"
        return "DELAYED"
    if kind == SNAPSHOT:
        return "LAST CLOSE"
    if kind == FILED:
        return "AS FILED"
    return "QUARTERLY"


def weakest(*stamps: Optional[dict]) -> Optional[dict]:
    """
    The least fresh of several. A panel built from three feeds is as current
    as its stalest one, and claiming otherwise is how a delayed figure ends up
    under a LIVE badge.
    """
    real = [s for s in stamps if s and s.get("kind") in _RANK]
    if not real:
        return None
    return min(real, key=lambda s: _RANK[s["kind"]])


# --------------------------------------------------------------------------
# the feeds this app actually uses
# --------------------------------------------------------------------------


def for_quote(price_source: str, session: str) -> dict:
    """
    A price is live only when a current print is what is being shown.

    Outside regular hours the headline is the last close by design, so the
    badge says LAST CLOSE rather than implying the market is moving. The
    broker print that used to earn a LIVE badge here is gone; during regular
    hours the feed's own last trade earns it instead, and it is a real trade
    rather than a snapshot.
    """
    src = (price_source or "").upper()
    if session == "OPEN" and src and not src.startswith("PROVIDER_SNAPSHOT"):
        return stamp(LIVE, source=price_source or UW,
                     detail="Last trade, during the regular session.")
    if session == "OPEN":
        return stamp(
            SNAPSHOT, source=price_source or "PROVIDER",
            detail="No live print was available, so this is the last "
                   "completed session's close rather than a live price.")
    return stamp(
        SNAPSHOT, source=price_source or "PROVIDER",
        detail="The market is closed; this is the last completed session's "
               "close. Any extended-hours print is shown separately.")


def for_chain(source: str, as_of: Optional[str] = None,
              session: Optional[str] = None) -> dict:
    """
    How fresh the option chain actually is.

    Read from the newest contract's last print (``as_of``) rather than a fixed
    "15 minutes" assumption. When the market is open and that print is within a
    couple of minutes of now, the chain is live and says so; when it is
    further behind, the badge names the real gap; when the market is closed,
    it is the last session's close, not a delay.
    """
    src = source or UW
    now = datetime.now(timezone.utc)

    age_min = None
    if as_of:
        try:
            ts = datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_min = max(0.0, (now - ts).total_seconds() / 60.0)
        except (TypeError, ValueError):
            age_min = None

    if session and session not in ("OPEN", "PRE_MARKET", "AFTER_HOURS"):
        return stamp(SNAPSHOT, source=src,
                     detail="The market is closed; this is the last session's "
                            "chain. It updates live when trading resumes.",
                     as_of=as_of)

    if age_min is None:
        # No timestamp to judge by: report the chain without claiming a delay
        # it may not have.
        return stamp(DELAYED, source=src,
                     detail="Provider chain: quotes, implied volatility and "
                            "greeks published per contract.", as_of=as_of)

    if age_min <= 2.0:
        return stamp(LIVE, source=src,
                     detail="Live chain: quotes, implied volatility and greeks "
                            "published per contract.", as_of=as_of)
    return stamp(DELAYED, source=src, delay_minutes=round(age_min),
                 detail=f"Chain last printed about {int(age_min)} minutes ago.",
                 as_of=as_of)


def for_tape(delay_minutes: float = 15.0, plan: Optional[dict] = None) -> dict:
    """
    The options tape. Delayed by licence, not by anything this app does.

    Said plainly because it is the one panel people most expect to be live:
    real-time OPRA is a paid entitlement, and no amount of code here changes
    the fifteen minutes.
    """
    detail = (f"OPRA prints are delivered on a {int(delay_minutes)}-minute "
              f"delay by the data plan. This is a licensing tier, not a "
              f"limit of the app.")
    if plan and plan.get("entitlement"):
        detail += f" Current plan: {plan['entitlement']}."
    return stamp(DELAYED, source="Unusual Whales",
                 delay_minutes=delay_minutes, detail=detail)


def for_filing(form: str, days: Optional[float] = None,
               filed: Optional[str] = None) -> dict:
    """Form 4, 13D, 13G -- filed within days of the event."""
    detail = f"{form} is filed within days of the transaction."
    if filed:
        detail += f" Most recent filing {filed}."
    return stamp(FILED, source="SEC", detail=detail, as_of=filed,
                 delay_minutes=(days * 24 * 60) if days else None,
                 label=f"{form} · AS FILED")


def for_quarterly(quarter: Optional[str]) -> dict:
    """13F -- a quarter old before anyone can read it."""
    label = f"{quarter} · 13F" if quarter else "QUARTERLY · 13F"
    return stamp(
        QUARTERLY, source="SEC 13F", label=label,
        detail="13F positions are disclosed quarterly, up to 45 days after "
               "the quarter ends. Nobody publishes institutional holdings in "
               "real time; this is the most current there is.")
