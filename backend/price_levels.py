"""
Support and resistance, read off the same bars the chart draws.

A level here is not a line somebody felt like drawing. It is a price the
market has turned at more than once: swing highs and lows are found first,
then the ones that sit at effectively the same price are grouped, and a group
is only called a level if price reversed there at least twice. A single pivot
is an event, not a level, and drawing it as one is how a chart starts telling
its reader what they hoped to see.

Two decisions worth stating, because both are places this could quietly lie:

  * The grouping tolerance scales with the instrument. A 1.5-point gap is the
    same level on a $600 stock and two different levels on a $20 one, so the
    band is a percentage of price rather than a fixed number of points.

  * Nothing is returned when nothing qualifies. An empty list draws no lines,
    which is the honest answer for a stock that has gone straight up for six
    months and has never turned anywhere.

The computation is here rather than in the browser so that it is tested, and
so every screen that draws a chart draws the same levels.
"""

from __future__ import annotations

from typing import Any, Optional

# How far either side a bar must be the extreme to count as a swing.
#
# Three is the usual choice and it matters: at one, every second bar is a
# pivot and the chart fills with lines; at ten, a six-month daily chart has
# almost none.
PIVOT_WINDOW = 3

# Two pivots within this much of each other are the same level. Percentage of
# the level's own price, not points.
CLUSTER_PCT = 0.9

# A level has to have been tested at least this many times.
MIN_TOUCHES = 2

# How many levels to return each side. More than a couple is clutter: the
# reader cannot act on the fifth-strongest resistance.
PER_SIDE = 2


def _pivots(rows: list[dict], field: str, kind: str) -> list[dict]:
    """Swing highs (kind='high') or swing lows (kind='low')."""
    out: list[dict] = []
    n = len(rows)
    for i in range(PIVOT_WINDOW, n - PIVOT_WINDOW):
        value = rows[i].get(field)
        if value is None:
            continue
        window = rows[i - PIVOT_WINDOW:i + PIVOT_WINDOW + 1]
        values = [r.get(field) for r in window if r.get(field) is not None]
        if len(values) < 3:
            continue
        extreme = max(values) if kind == "high" else min(values)
        if value != extreme:
            continue
        # A flat stretch would otherwise register every bar in it. Keep the
        # first bar of the stretch and drop the rest.
        if out and out[-1]["index"] >= i - PIVOT_WINDOW:
            continue
        out.append({"index": i, "price": float(value), "date": rows[i].get("t")})
    return out


def _cluster(pivots: list[dict], kind: str) -> list[dict]:
    """Group pivots that sit at effectively the same price."""
    if not pivots:
        return []

    ordered = sorted(pivots, key=lambda p: p["price"])
    groups: list[list[dict]] = [[ordered[0]]]
    for p in ordered[1:]:
        anchor = groups[-1][0]["price"]
        if abs(p["price"] - anchor) <= anchor * CLUSTER_PCT / 100.0:
            groups[-1].append(p)
        else:
            groups.append([p])

    levels = []
    for g in groups:
        if len(g) < MIN_TOUCHES:
            continue
        prices = [p["price"] for p in g]
        last = max(g, key=lambda p: p["index"])
        levels.append({
            "price": round(sum(prices) / len(prices), 2),
            "kind": kind,
            "touches": len(g),
            "last_touch": last["date"],
            "last_index": last["index"],
            "low": round(min(prices), 2),
            "high": round(max(prices), 2),
        })
    return levels


def _rank(levels: list[dict], bars: int) -> list[dict]:
    """
    Strongest first: how often price turned there, then how recently.

    Recency is a tiebreak rather than a term of its own. A level tested four
    times last quarter is a better level than one tested twice last week, and
    weighting recency heavily would reverse that.
    """
    def score(level: dict) -> tuple:
        recency = (level["last_index"] + 1) / max(bars, 1)
        return (level["touches"], recency)

    return sorted(levels, key=score, reverse=True)


def find_levels(rows: list[dict], last_price: Optional[float] = None) -> dict:
    """
    Support below the current price, resistance above it.

    ``rows`` are the chart's own bars: dicts with ``high``, ``low``, ``close``
    and ``t``. Returns empty lists rather than inventing levels when the bars
    do not contain any.
    """
    usable = [r for r in (rows or []) if r.get("high") is not None
              and r.get("low") is not None]
    if len(usable) < PIVOT_WINDOW * 2 + MIN_TOUCHES:
        return {
            "support": [], "resistance": [],
            "status": "NO_DATA",
            "note": "Not enough bars to find a level price has turned at.",
        }

    price = last_price
    if price is None:
        price = usable[-1].get("close")
    if price is None:
        return {"support": [], "resistance": [], "status": "NO_DATA",
                "note": "No current price to place the levels against."}
    price = float(price)

    highs = _cluster(_pivots(usable, "high", "high"), "resistance")
    lows = _cluster(_pivots(usable, "low", "low"), "support")

    # Which side a level falls on is decided by where price is now, not by
    # whether it came from a high or a low: broken resistance becomes support,
    # and a chart that still labels it resistance is describing the past.
    candidates = highs + lows
    below = [level for level in candidates if level["price"] < price]
    above = [level for level in candidates if level["price"] > price]

    support = _rank(below, len(usable))[:PER_SIDE]
    resistance = _rank(above, len(usable))[:PER_SIDE]

    for level in support:
        level["kind"] = "support"
        level["distance_pct"] = round((level["price"] - price) / price * 100, 2)
    for level in resistance:
        level["kind"] = "resistance"
        level["distance_pct"] = round((level["price"] - price) / price * 100, 2)

    # Nearest first, which is the order they matter in from here.
    support.sort(key=lambda level: -level["price"])
    resistance.sort(key=lambda level: level["price"])

    return {
        "support": support,
        "resistance": resistance,
        "status": "OK" if (support or resistance) else "NONE",
        "basis": {
            "bars": len(usable),
            "pivot_window": PIVOT_WINDOW,
            "cluster_pct": CLUSTER_PCT,
            "min_touches": MIN_TOUCHES,
        },
        "note": (
            f"Swing highs and lows over {len(usable)} bars, grouped within "
            f"{CLUSTER_PCT}% of each other. A level is only drawn where price "
            f"turned at least {MIN_TOUCHES} times."
        ),
    }


def attach(payload: dict[str, Any]) -> dict[str, Any]:
    """Add levels to a chart payload, in place."""
    try:
        rows = payload.get("bars") or []
        last = None
        if rows:
            last = rows[-1].get("close")
        payload["levels"] = find_levels(rows, last)
    except Exception:  # noqa: BLE001 - a chart must still draw without levels
        payload["levels"] = {
            "support": [], "resistance": [], "status": "ERROR",
            "note": "Levels could not be computed for these bars.",
        }
    return payload
