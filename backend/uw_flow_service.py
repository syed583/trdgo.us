"""
The options tape, from Unusual Whales.

This replaces two providers at once. OptionData served a 15-minute-delayed
OPRA tape this app then had to classify itself -- inferring from a fill price
whether a trade was bought or sold, and calling that "bullish premium".
OptionsBell served an unusual-activity filter on a 2,000-request day that ran
out before lunch. Both are gone; what follows answers the same questions from
one subscription, and answers two of them better:

* **Which side crossed.** Theirs is counted, not inferred: every contract
  arrives with its bid-side and ask-side volume separated. Buying calls at
  the ask and selling puts at the bid is pressure one way; the app no longer
  has to guess which happened.
* **What is unusual.** Their filter carries the contract's own history --
  days of volume above open interest, the seven-day side split, the change
  in implied volatility -- rather than a premium threshold alone.

The shapes here are deliberately the ones the screens already read, so the
Options Flow page, the Disparity tab and the flow parameters did not have to
be rewritten around a new vocabulary.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import unusualwhales_service as uw

SOURCE = "Unusual Whales"

# A block is a single large print; a sweep is one order filled across several
# exchanges at once. The second is the more urgent of the two, which is why
# they are counted separately rather than added together.
BLOCK_PREMIUM = 250000.0


def configured() -> bool:
    return uw.configured()


def _epoch(stamp: Optional[str]) -> Optional[int]:
    """A trade's time in epoch milliseconds, for plotting it on an axis."""
    if not stamp:
        return None
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return int(when.timestamp() * 1000)


def _f(value) -> Optional[float]:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _trade(row: dict) -> dict:
    """One tape row in the shape the Options Flow screen renders."""
    return {
        "symbol": row.get("symbol"),
        "option_symbol": row.get("option_symbol"),
        "right": row.get("right"),
        "strike": row.get("strike"),
        "expiry": row.get("expiry"),
        "volume": row.get("volume"),
        "open_interest": row.get("open_interest"),
        "notional": row.get("premium"),
        "premium": row.get("premium"),
        "ratio": row.get("ratio"),
        "iv": row.get("iv"),
        "side": row.get("side"),
        "sweep": row.get("sweep_like"),
        "kind": ("SWEEP" if row.get("sweep_like")
                 else "BLOCK" if (row.get("premium") or 0) >= BLOCK_PREMIUM
                 else "PRINT"),
        "sentiment": _sentiment(row),
        "spot": row.get("stock_price"),
        "type": "CALL" if row.get("right") != "P" else "PUT",
        "contracts": row.get("volume"),
        "price": (round((row.get("premium") or 0)
                        / ((row.get("volume") or 1) * 100), 4)
                  if row.get("volume") else None),
        "expiry_label": row.get("expiry"),
        "time": row.get("last_fill"),
        "timestamp": row.get("last_fill"),
        "epoch": _epoch(row.get("last_fill")),
    }


def _sentiment(row: dict) -> str:
    """
    What this print means directionally, from the side it crossed on.

    Calls lifted at the ask and puts sold at the bid are both bullish; the
    mirror is bearish. A contract that traded mid is neither, and is said to
    be neither rather than rounded into one of the two.
    """
    side, right = row.get("side"), row.get("right")
    if side == "mid" or right not in ("C", "P"):
        return "NEUTRAL"
    bullish = (right == "C") == (side == "ask")
    return "BULLISH" if bullish else "BEARISH"


def _print_row(row: dict) -> dict:
    """
    One executed print, as they classified it.

    Their tags carry the classification this app used to make for itself --
    which side of the spread the order crossed, and whether that reads
    bullish or bearish on that contract. Taking theirs removes the step
    where a fill at the midpoint got rounded into a direction.
    """
    tags = [str(t).lower() for t in (row.get("tags") or [])]
    size = _f(row.get("size")) or 0.0
    price = _f(row.get("price")) or 0.0
    right = (str(row.get("option_type") or "")[:1]
             or uw._right_from_symbol(str(row.get("option_chain_id") or ""))).upper()
    side = ("ask" if "ask_side" in tags else "bid" if "bid_side" in tags
            else "mid")
    premium = _f(row.get("premium")) or (size * price * 100.0)
    return {
        "symbol": row.get("underlying_symbol"),
        "option_symbol": row.get("option_chain_id"),
        "right": "P" if right == "P" else "C",
        "strike": _f(row.get("strike")),
        "expiry": row.get("expiry"),
        "volume": _f(row.get("volume")),
        "open_interest": _f(row.get("open_interest")),
        "size": size,
        "price": price,
        "notional": round(premium, 2),
        "premium": round(premium, 2),
        "iv": _f(row.get("implied_volatility")),
        "side": side,
        "sweep": "sweep" in tags,
        "kind": ("SWEEP" if "sweep" in tags
                 else "BLOCK" if premium >= BLOCK_PREMIUM else "PRINT"),
        "sentiment": ("BULLISH" if "bullish" in tags
                      else "BEARISH" if "bearish" in tags else "NEUTRAL"),
        "tags": tags,
        "type": "CALL" if right != "P" else "PUT",
        "contracts": size,
        "expiry_label": row.get("expiry"),
        "time": row.get("executed_at"),
        "timestamp": row.get("executed_at"),
        "epoch": _epoch(row.get("executed_at")),
    }


def _tape_freshness(trades: list[dict]) -> dict:
    """
    How far behind live the tape actually is, from its newest print.

    Read from the data, not hardcoded: during the session a print within a
    couple of minutes of now is live (no delay badge); further behind, the
    badge names the real gap, which is the account's OPRA entitlement rather
    than anything the app does; and when the market is closed it is the last
    session's tape, not a delay. ``as_of`` is the newest print's time.
    """
    import time as _time

    epochs = [t.get("epoch") for t in trades if t.get("epoch")]
    newest = max(epochs) if epochs else None
    as_of = None
    age_min = None
    if newest:
        as_of = datetime.fromtimestamp(newest / 1000, timezone.utc).isoformat()
        age_min = max(0.0, (_time.time() * 1000 - newest) / 60000.0)

    try:
        from live_market_service import market_clock
        session = (market_clock() or {}).get("session")
    except Exception:  # noqa: BLE001
        session = None

    live_note = ("Live option flow. The side each contract crossed on is "
                 "counted by the provider, not inferred from the price.")

    if session and session not in ("OPEN", "PRE_MARKET", "AFTER_HOURS"):
        return {"delay_minutes": 0, "as_of": as_of,
                "note": ("The market is closed; this is the last session's "
                         "tape. It prints live when trading resumes.")}
    if age_min is None or age_min <= 2.0:
        return {"delay_minutes": 0, "as_of": as_of, "note": live_note}
    return {"delay_minutes": round(age_min), "as_of": as_of,
            "note": (f"The tape is about {int(age_min)} minutes behind live -- "
                     "the account's OPRA data entitlement, not a limit of the "
                     "app.")}


def get_flow(symbol: str) -> dict:
    """The whole per-print picture for one symbol, for the Options Flow page."""
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return {"symbol": symbol, "status": "INVALID_SYMBOL", "source": SOURCE}

    prints = uw.stock_flow(symbol, limit=200)
    tape = uw.tape_rows(symbol, limit=200)
    if prints["status"] != "OK" and tape["status"] != "OK":
        return {"symbol": symbol, "status": prints["status"],
                "detail": prints.get("detail"), "trades": [], "source": SOURCE}

    # The tape is every print; the unusual list is the contracts whose day is
    # abnormal for them. Two different questions, kept apart on the screen.
    trades = sorted((_print_row(r) for r in uw._rows(prints)),
                    key=lambda t: -(t["premium"] or 0))
    unusual = [_trade(r) for r in tape.get("rows") or []]
    sweeps = [t for t in trades if t["kind"] == "SWEEP"]
    blocks = [t for t in trades if t["kind"] == "BLOCK"]
    bullish = sum(t["premium"] or 0 for t in trades if t["sentiment"] == "BULLISH")
    total = sum(t["premium"] or 0 for t in trades) or 0.0

    return {
        "symbol": symbol,
        "session_date": (trades[0]["time"] or "")[:10] if trades else None,
        "trades": trades,
        "blocks": len(blocks),
        "sweeps": len(sweeps),
        "unusual": unusual,
        "unusual_listed": len(unusual),
        "unusual_capped": False,
        "all_count": len(trades),
        "classified": True,
        "pressure": {
            "bullish_share": round(bullish / total * 100, 1) if total else None,
            "bullish_premium": round(bullish, 0),
            "total_premium": round(total, 0),
            "status": "OK" if total else "NO_DATA",
            "source": SOURCE,
        },
        "bullish_premium_share": round(bullish / total * 100, 1) if total else None,
        "ranking_basis": "PREMIUM",
        **_tape_freshness(trades),
        "status": "OK" if trades else "NO_TRADES",
        "source": SOURCE,
    }


def daily_oi_change(symbol: str, days: int = 5) -> dict:
    """
    Open interest per session, and which side is being built.

    Rising open interest means positions are being opened rather than closed,
    which is what separates accumulation from a crowd unwinding. Their
    endpoint reports the change per contract, so the per-print duplication
    the old provider needed collapsing for does not arise here.
    """
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return {"symbol": symbol, "status": "INVALID_SYMBOL", "source": SOURCE}

    out = uw.oi_change(symbol, limit=500)
    if out["status"] != "OK":
        return {"symbol": symbol, "status": out["status"],
                "detail": out.get("detail"), "source": SOURCE}

    # Their rows are per contract, today against yesterday: no per-print
    # duplication to collapse, which is what made this figure swing between a
    # billion and eight billion on the old provider.
    now = before = call_now = call_before = put_now = put_before = 0.0
    contracts = 0
    for row in uw._rows(out):
        current, previous = _f(row.get("curr_oi")), _f(row.get("last_oi"))
        if current is None:
            continue
        side = (str(row.get("option_type") or "")[:1]
                or uw._right_from_symbol(
                    str(row.get("option_symbol") or ""))).upper()
        contracts += 1
        now += current
        before += previous or 0.0
        if side == "P":
            put_now += current
            put_before += previous or 0.0
        else:
            call_now += current
            call_before += previous or 0.0

    if not contracts:
        return {"symbol": symbol, "status": "NO_DATA", "source": SOURCE}

    change_pct = round((now - before) / before * 100, 2) if before else None
    call_change, put_change = call_now - call_before, put_now - put_before

    # The whole chain's open interest, for scale: the rows above are the
    # contracts that moved, which is a different number from what is open.
    chain_oi = sum(_f(r.get("oi")) or 0.0 for r in uw._rows(uw.expirations(symbol)))

    return {
        "symbol": symbol,
        "sessions": 2,
        "contracts_moved": contracts,
        "series": [{"date": "previous", "total_oi": round(before, 0),
                    "call_oi": round(call_before, 0),
                    "put_oi": round(put_before, 0)},
                   {"date": "latest", "total_oi": round(now, 0),
                    "call_oi": round(call_now, 0),
                    "put_oi": round(put_now, 0)}],
        "total_oi": round(now, 0),
        "previous_oi": round(before, 0),
        "chain_open_interest": round(chain_oi, 0),
        "change_pct": change_pct,
        "call_oi_change": round(call_change, 0),
        "put_oi_change": round(put_change, 0),
        # Which side is being built matters more than the total: open
        # interest rising on calls and on puts are opposite messages.
        "building": ("CALLS" if call_change > put_change
                     else "PUTS" if put_change > call_change else "BALANCED"),
        "trend": ("RISING" if change_pct and change_pct > 1
                  else "FALLING" if change_pct and change_pct < -1 else "FLAT"),
        "status": "OK",
        "source": SOURCE,
    }


def unusual_activity(symbol: str = "", limit: int = 50) -> dict:
    """Contracts trading far above their own normal volume."""
    return uw.tape_rows(symbol, limit=limit)


# ---------------------------------------------------------------------------
# the market, rather than one stock
# ---------------------------------------------------------------------------


def market_summary() -> dict:
    """
    The market's option premium through the session, and the day's totals.

    Two measurements rather than one sampled from a watchlist: the tide is
    net call and put premium for the entire market minute by minute, and the
    totals are every contract traded today.
    """
    tide = uw.market_tide()
    totals = uw.total_options_volume()
    rows = uw._rows(tide)
    latest = rows[-1] if rows else {}
    day = (uw._rows(totals) or [{}])[0]

    call_prem = _f(day.get("call_premium")) or 0.0
    put_prem = _f(day.get("put_premium")) or 0.0
    total_prem = call_prem + put_prem

    return {
        "status": tide["status"] if tide["status"] != "OK" else "OK",
        "as_of": latest.get("timestamp"),
        "net_call_premium": _f(latest.get("net_call_premium")),
        "net_put_premium": _f(latest.get("net_put_premium")),
        "net_volume": _f(latest.get("net_volume")),
        "call_volume": _f(day.get("call_volume")),
        "put_volume": _f(day.get("put_volume")),
        "call_premium": call_prem,
        "put_premium": put_prem,
        "call_share": round(call_prem / total_prem * 100, 1) if total_prem else None,
        "put_call_volume_ratio": (
            round((_f(day.get("put_volume")) or 0) /
                  (_f(day.get("call_volume")) or 1), 3)
            if day.get("call_volume") else None),
        "series": [{"time": r.get("timestamp"),
                    "net_call_premium": _f(r.get("net_call_premium")),
                    "net_put_premium": _f(r.get("net_put_premium")),
                    "net_volume": _f(r.get("net_volume"))} for r in rows],
        "detail": ("Net option premium across the whole market, minute by "
                   "minute, with the day's totals."),
        "source": SOURCE,
    }


def market_tape(limit: int = 40) -> dict:
    """The largest unusual prints in the market right now, any symbol."""
    tape = uw.tape_rows(limit=limit)
    return {"status": tape["status"],
            "trades": [_trade(r) for r in tape.get("rows") or []],
            "detail": tape.get("detail"), "source": SOURCE}


def busiest_names(limit: int = 25) -> dict:
    """Where the option market's attention is, by transaction count."""
    out = uw.options_pulse_top(limit)
    rows = []
    for r in uw._rows(out):
        calls, puts = _f(r.get("call_txn")) or 0.0, _f(r.get("put_txn")) or 0.0
        rows.append({
            "symbol": r.get("ticker"),
            "call_trades": calls, "put_trades": puts,
            "put_call_ratio": _f(r.get("put_call_ratio")),
            "sentiment_score": _f(r.get("sntm_score")),
            "lean": ("CALLS" if calls > puts else "PUTS" if puts > calls
                     else "BALANCED"),
        })
    return {"status": out["status"], "rows": rows,
            "detail": "The names the option market is busiest in today.",
            "source": SOURCE}


def flow_by_expiry(symbol: str) -> dict:
    """One stock's option flow split by expiry."""
    symbol = (symbol or "").upper().strip()
    out = uw.flow_per_expiry(symbol)
    rows = []
    for r in uw._rows(out):
        rows.append({
            "expiry": r.get("expiry"),
            "call_volume": _f(r.get("call_volume")),
            "put_volume": _f(r.get("put_volume")),
            "call_ask_side": _f(r.get("call_volume_ask_side")),
            "call_bid_side": _f(r.get("call_volume_bid_side")),
            "put_ask_side": _f(r.get("put_volume_ask_side")),
            "put_bid_side": _f(r.get("put_volume_bid_side")),
        })
    rows.sort(key=lambda r: r["expiry"] or "")
    return {"symbol": symbol, "status": out["status"], "rows": rows,
            "source": SOURCE}
