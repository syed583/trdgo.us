"""
Market-wide option flow, from Unusual Whales.

What changed, and why it is not just a change of supplier
---------------------------------------------------------
This screen used to say "the market" and mean thirty-eight symbols. The old
provider had no market-wide measurement, so the summary was assembled by
querying a watchlist and adding it up: five SQL scans per refresh, a row cap
that truncated busy sessions, and a "market sentiment" that was really the
sentiment of whatever happened to be on the board. When a symbol's query
failed it was quietly missing from the total.

Unusual Whales measures the market itself. The tide is net call and put
premium across every optionable name, minute by minute; the totals are every
contract traded today. So the numbers on this screen are now what they claim
to be, and one refresh costs a handful of requests instead of dozens.

The payload keys are unchanged -- the screen reads the same fields it always
did -- except where a field was describing the old method rather than the
market: ``symbols_covered`` now says how many names are in the reading, and
a truncation flag that no longer applies is gone.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional

import unusualwhales_service as uw
import uw_flow_service as flow

SOURCE = "Unusual Whales"

# How many sessions back the comparison looks for a previous trading day.
# Friday against Thursday is a comparison; Monday against Sunday is not.
BACK_DAYS = 6

BASELINE_SESSIONS = 20


def _f(value) -> Optional[float]:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def universe(extra: Optional[list[str]] = None) -> list[str]:
    """
    The names this screen reports on.

    Kept so callers that ask do not break, but the market readings no longer
    depend on it: the tide and the totals cover every optionable symbol.
    """
    try:
        import ai_trade_service as board

        names = list(board.UNIVERSE)
    except Exception:  # noqa: BLE001
        names = []
    for symbol in extra or []:
        if symbol and symbol.upper() not in names:
            names.append(symbol.upper())
    return names


def _sentiment(call_share: Optional[float]) -> str:
    if call_share is None:
        return "UNKNOWN"
    if call_share >= 60:
        return "BULLISH"
    if call_share <= 40:
        return "BEARISH"
    return "BALANCED"


def _sessions(count: int = 2) -> list[dict]:
    """The last few market sessions, newest first."""
    return uw._rows(uw.total_options_volume(count))


def _totals() -> dict:
    rows = _sessions(1)
    return rows[0] if rows else {}


def get_summary(extra: Optional[list[str]] = None) -> dict:
    """The day's option premium and volume across the whole market."""
    totals = _totals()
    tide = uw.market_tide()
    series = uw._rows(tide)
    pulse = uw._rows(uw.options_pulse_top(100))

    call_prem = _f(totals.get("call_premium")) or 0.0
    put_prem = _f(totals.get("put_premium")) or 0.0
    call_vol = _f(totals.get("call_volume")) or 0.0
    put_vol = _f(totals.get("put_volume")) or 0.0
    total_prem = call_prem + put_prem
    share = round(call_prem / total_prem * 100, 1) if total_prem else None

    if not total_prem and not call_vol:
        return {"status": tide.get("status", "NO_DATA"), "session": None,
                "detail": (tide.get("detail")
                           or "No market-wide option data for this session."),
                "source": SOURCE}

    leaders = [{
        "symbol": r.get("ticker"),
        "call_trades": _f(r.get("call_txn")),
        "put_trades": _f(r.get("put_txn")),
        "put_call_ratio": _f(r.get("put_call_ratio")),
        "lean": ("CALLS" if (_f(r.get("call_txn")) or 0) > (_f(r.get("put_txn")) or 0)
                 else "PUTS"),
    } for r in pulse[:15]]

    return {
        "status": "OK",
        "session": totals.get("date") or (series[-1].get("date") if series else None),
        # The reading is the market, not a watchlist. Both are reported so the
        # screen can say which, rather than implying a coverage it never had.
        "symbols_requested": None,
        "symbols_covered": len(pulse) or None,
        "market_wide": True,
        "call_premium": call_prem,
        "put_premium": put_prem,
        "net_premium": round(call_prem - put_prem, 2),
        "total_premium": round(total_prem, 2),
        "call_volume": call_vol,
        "put_volume": put_vol,
        "call_put_ratio": round(call_vol / put_vol, 3) if put_vol else None,
        "put_call_ratio": round(put_vol / call_vol, 3) if call_vol else None,
        "call_premium_share": share,
        "sentiment": _sentiment(share),
        "prints": int((_f(totals.get("call_volume")) or 0)
                      + (_f(totals.get("put_volume")) or 0)),
        "leaders": leaders,
        "per_symbol": leaders,
        "symbols_unavailable": [],
        "complete": True,
        "truncated": False,
        "detail": ("Every optionable name, measured by the provider rather "
                   "than summed from a watchlist."),
        "source": SOURCE,
    }


def get_tape(limit: int = 40, extra: Optional[list[str]] = None) -> dict:
    """The largest unusual option prints in the market right now."""
    tape = uw.tape_rows(limit=max(limit, 10))
    if tape["status"] != "OK":
        return {"status": tape["status"], "rows": [], "count": 0,
                "detail": tape.get("detail"), "source": SOURCE}

    rows = []
    for r in tape["rows"][:limit]:
        rows.append({
            "symbol": r["symbol"],
            "right": r["right"],
            "type": "CALL" if r["right"] == "C" else "PUT",
            "strike": r["strike"],
            "expiry": r["expiry"],
            "expiry_label": r["expiry"],
            "dte": _dte(r["expiry"]),
            "spot": r.get("stock_price"),
            "contracts": r["volume"],
            "volume": r["volume"],
            "open_interest": r["open_interest"],
            "volume_oi": r["ratio"],
            "premium": r["premium"],
            "iv": r["iv"],
            "delta": r.get("delta"),
            "gamma": r.get("gamma"),
            "side": r["side"],
            "sentiment": flow._sentiment(r),
            "time": r.get("last_fill"),
        })

    return {
        "status": "OK",
        "session": (rows[0]["time"] or "")[:10] if rows and rows[0].get("time") else None,
        "rows": rows, "count": len(rows),
        "min_contracts": None,
        "universe": "Every optionable symbol",
        "symbols_unavailable": [],
        "source": SOURCE,
        "detail": ("Contracts trading far above their own normal volume, "
                   "across the whole market."),
    }


def _dte(expiry: Optional[str]) -> Optional[int]:
    try:
        return (datetime.strptime(str(expiry)[:10], "%Y-%m-%d").date()
                - date.today()).days
    except (TypeError, ValueError):
        return None


def get_sector_flow(extra: Optional[list[str]] = None) -> dict:
    """Which sectors the option market is leaning on today."""
    out = uw.get("/api/options-pulse/sectors")
    if out["status"] != "OK":
        return {"status": out["status"], "rows": [],
                "detail": out.get("detail"), "source": SOURCE}

    rows = []
    for r in uw._rows(out):
        if str(r.get("category") or "") != "sector":
            continue
        calls, puts = _f(r.get("call_txn")) or 0.0, _f(r.get("put_txn")) or 0.0
        total = calls + puts
        rows.append({
            "sector": r.get("classification"),
            "call_trades": calls,
            "put_trades": puts,
            "total_trades": total,
            "share": round(calls / total * 100, 1) if total else None,
            "sentiment_score": _f(r.get("sntm_score")),
            "lean": ("CALLS" if calls > puts else "PUTS" if puts > calls
                     else "BALANCED"),
        })

    # Their sector rows are transaction counts rather than premium: the
    # column is named for what it is rather than relabelled as money.
    rows.sort(key=lambda r: -(r["total_trades"] or 0))
    return {"status": "OK" if rows else "NO_DATA",
            "session": (uw._rows(out) or [{}])[0].get("trd_dt"),
            "rows": rows,
            "total_trades": sum(r["total_trades"] or 0 for r in rows),
            "detail": ("Option transactions per sector today, and which side "
                       "of the market each is leaning on."),
            "source": SOURCE}


def get_unusual(limit: int = 15, extra: Optional[list[str]] = None) -> dict:
    """Contracts whose day is abnormal for them, market-wide."""
    tape = get_tape(limit=max(limit * 3, 30))
    if tape["status"] != "OK":
        return {**tape, "rows": [], "count": 0}

    rows = [r for r in tape["rows"] if (r["volume_oi"] or 0) >= 1.0][:limit]
    return {
        "status": "OK" if rows else "NO_DATA",
        "session": tape.get("session"),
        "rows": rows, "count": len(rows),
        "threshold": "volume at or above open interest",
        "min_volume": None,
        "source": SOURCE,
        "detail": ("Contracts that traded more today than were open before "
                   "the bell -- positions being opened, not passed around."),
    }


def get_comparison(extra: Optional[list[str]] = None) -> dict:
    """Today's market flow against the previous session."""
    # Their own last-two sessions, rather than a calendar guess at which day
    # the market was last open.
    rows = _sessions(2)
    today = rows[0] if rows else {}
    previous = rows[1] if len(rows) > 1 else {}
    previous_day = previous.get("date")

    if not today or not previous:
        return {"status": "NO_DATA", "session": today.get("date"),
                "detail": "No previous session to compare with yet.",
                "source": SOURCE}

    def delta(field: str) -> Optional[float]:
        now, before = _f(today.get(field)), _f(previous.get(field))
        if now is None or not before:
            return None
        return round((now - before) / abs(before) * 100, 1)

    call_now = _f(today.get("call_volume")) or 0.0
    put_now = _f(today.get("put_volume")) or 0.0
    call_before = _f(previous.get("call_volume")) or 0.0
    put_before = _f(previous.get("put_volume")) or 0.0
    ratio_now = round(call_now / put_now, 3) if put_now else None
    ratio_before = round(call_before / put_before, 3) if put_before else None

    return {
        "status": "OK",
        "session": today.get("date"),
        "previous_session": previous_day,
        "call_premium_change": delta("call_premium"),
        "put_premium_change": delta("put_premium"),
        "net_premium_change": _net_change(today, previous),
        "call_put_ratio_change": (round(ratio_now - ratio_before, 3)
                                  if ratio_now and ratio_before else None),
        "previous": {
            "call_premium": _f(previous.get("call_premium")),
            "put_premium": _f(previous.get("put_premium")),
            "call_volume": call_before,
            "put_volume": put_before,
            "call_put_ratio": ratio_before,
        },
        "detail": "Today's market-wide option flow against the last session.",
        "source": SOURCE,
    }


def _net_change(today: dict, previous: dict) -> Optional[float]:
    """Change in net premium -- calls minus puts -- between two sessions."""
    def net(row: dict) -> Optional[float]:
        calls, puts = _f(row.get("call_premium")), _f(row.get("put_premium"))
        return None if calls is None or puts is None else calls - puts

    now, before = net(today), net(previous)
    if now is None or not before:
        return None
    return round((now - before) / abs(before) * 100, 1)


def get_expiry_flow(extra: Optional[list[str]] = None, limit: int = 8) -> dict:
    """
    Where the premium is going along the expiry ladder.

    Built from the market's own unusual tape grouped by expiry. Their
    net-flow endpoint is a time series for the whole market rather than a
    split by expiry, and reading it as one produced a single bucket labelled
    with today's date -- a chart of nothing, drawn confidently.
    """
    tape = uw.tape_rows(limit=500)
    if tape["status"] != "OK":
        return {"status": tape["status"], "rows": [],
                "detail": tape.get("detail"), "source": SOURCE}

    buckets: dict[str, dict] = {}
    for row in tape["rows"]:
        expiry = str(row.get("expiry") or "")[:10]
        if not expiry:
            continue
        bucket = buckets.setdefault(
            expiry, {"expiry": expiry, "dte": _dte(expiry),
                     "calls": 0.0, "puts": 0.0, "contracts": 0})
        bucket["puts" if row["right"] == "P" else "calls"] += row["premium"] or 0.0
        bucket["contracts"] += 1

    rows = sorted(buckets.values(), key=lambda r: r["expiry"])[:limit]
    peak = max(rows, key=lambda r: (r["calls"] + r["puts"]), default=None) \
        if rows else None
    return {"status": "OK" if rows else "NO_DATA",
            "session": date.today().isoformat(),
            "rows": rows, "peak": peak,
            "detail": ("Premium on unusual contracts by expiry, across the "
                       "market."),
            "source": SOURCE}


def get_intraday(extra: Optional[list[str]] = None) -> dict:
    """The market's net option premium through the session, minute by minute."""
    tide = uw.market_tide()
    series = uw._rows(tide)
    if not series:
        return {"status": tide.get("status", "NO_DATA"), "points": [],
                "detail": tide.get("detail"), "source": SOURCE}

    points, labels = [], []
    for row in series:
        stamp = str(row.get("timestamp") or "")
        calls = _f(row.get("net_call_premium")) or 0.0
        puts = _f(row.get("net_put_premium")) or 0.0
        labels.append(stamp[11:16])
        points.append({
            "time": stamp,
            "label": stamp[11:16],
            "call_premium": calls,
            "put_premium": puts,
            "net_premium": round(calls - puts, 2),
            "call_put_ratio": round(calls / puts, 3) if puts else None,
            "net_volume": _f(row.get("net_volume")),
        })

    return {
        "status": "OK",
        "session": (series[-1].get("date") if series else None),
        "bucket_minutes": 5,
        "truncated": False,
        "points": points,
        "labels": labels,
        "series": {
            "call": [p["call_premium"] for p in points],
            "put": [p["put_premium"] for p in points],
            "net": [p["net_premium"] for p in points],
            "call_volume": [p.get("net_volume") for p in points],
            "put_volume": [],
        },
        "symbols_unavailable": [],
        "detail": ("Net call and put premium across the market, through the "
                   "session."),
        "source": SOURCE,
    }


def get_baseline(symbol: str, sessions: int = BASELINE_SESSIONS) -> dict:
    """Today's option volume for one symbol against its own recent average."""
    symbol = (symbol or "").upper().strip()
    out = uw.get(f"/api/stock/{symbol}/options-volume", {"limit": sessions})
    rows = uw._rows(out)
    if out["status"] != "OK" or not rows:
        return {"status": out["status"] if out["status"] != "OK" else "NO_DATA",
                "symbol": symbol, "detail": out.get("detail"), "source": SOURCE}

    def total(row: dict, field: str) -> float:
        return ((_f(row.get(f"call_{field}")) or 0.0)
                + (_f(row.get(f"put_{field}")) or 0.0))

    today, history = rows[0], rows[1:]
    today_volume, today_premium = total(today, "volume"), total(today, "premium")
    avg_volume = (sum(total(r, "volume") for r in history) / len(history)
                  if history else None)
    avg_premium = (sum(total(r, "premium") for r in history) / len(history)
                   if history else None)

    return {
        "status": "OK",
        "symbol": symbol,
        "session": today.get("date"),
        "sessions": len(rows),
        "today_volume": today_volume,
        "today_premium": today_premium,
        "average_volume": round(avg_volume, 0) if avg_volume else None,
        "average_premium": round(avg_premium, 0) if avg_premium else None,
        "volume_ratio": (round(today_volume / avg_volume, 2)
                         if avg_volume else None),
        "premium_ratio": (round(today_premium / avg_premium, 2)
                          if avg_premium else None),
        "window_days": len(history),
        "detail": ("Today's option volume for this symbol against its own "
                   "recent sessions."),
        "source": SOURCE,
    }
