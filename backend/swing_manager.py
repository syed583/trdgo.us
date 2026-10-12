"""Swing/positional trade management (Oct 2026 spec, items 1D-1G / 2D-2G).

Pure functions, no I/O, so the stop/trailing/exit rules are unit-testable on
their own and reused by the Trade Plan engine that persists the positions.

The rules, for a LONG (SHORT mirrors every comparison):
  - Stop starts at entry - STOP_ATR_MULT x ATR (1.0x within NEAR_52W_PCT of a
    52-week high; 2.5% fallback when ATR is unknown) and only ever moves UP.
  - At +1 ATR of profit the stop moves to the entry price (risk-free).
  - After that the stop trails TRAIL_ATR_MULT x ATR below the best price reached
    (1.0x for 52-week names).
  - Exits (first that triggers wins): score back through 50 -> STRONG SQUARE-OFF
    at market; price hits the trailing stop -> EXIT (trail hit); 3 sessions with
    no +1 ATR profit -> EXIT (time).
Weights are never touched here; every number comes from trade_settings.
"""
from __future__ import annotations

from typing import Optional

import trade_settings as cfg


def initial_stop(bias: str, entry: float, atr: Optional[float],
                 near_52w: bool = False) -> float:
    """The hard stop set the moment a position is opened."""
    mult = cfg.STOP_ATR_MULT_NEAR_52W if near_52w else cfg.STOP_ATR_MULT
    if atr and atr > 0:
        dist = mult * atr
    else:
        dist = entry * (cfg.STOP_FALLBACK_PCT / 100.0)
    return round(entry - dist if bias == "LONG" else entry + dist, 2)


def manage(*, bias: str, entry: float, price: float,
           best_price: Optional[float], stop: Optional[float],
           score: Optional[float], atr: Optional[float],
           days_held: int = 0, near_52w: bool = False) -> dict:
    """Advance a live swing position by one tick.

    Returns {stop, moved_to_breakeven, trail_updated, exit, alert, reason}.
    `stop` in the result is the (possibly tightened) stop; `exit` is a code
    (SCORE / TRAIL / TIME) or None; `alert` is the app-facing label.
    """
    long_ = bias == "LONG"
    trail_mult = cfg.TRAIL_ATR_MULT_NEAR_52W if near_52w else cfg.TRAIL_ATR_MULT
    be_dist = cfg.BREAKEVEN_TRIGGER_ATR * atr if (atr and atr > 0) else None
    profit = (price - entry) if long_ else (entry - price)

    if stop is None:
        stop = initial_stop(bias, entry, atr, near_52w)
    new_stop = stop
    moved_be = False
    trailed = False

    # 1) Move the stop to breakeven once +1 ATR in profit, then trail from the
    #    best price. The stop only moves in the trade's favour.
    if be_dist is not None and profit >= be_dist:
        be = entry
        cand = max(new_stop, be) if long_ else min(new_stop, be)
        if cand != new_stop:
            new_stop, moved_be = cand, True
        if best_price is not None and atr:
            trail = (best_price - trail_mult * atr) if long_ else (best_price + trail_mult * atr)
            cand = max(new_stop, round(trail, 2)) if long_ else min(new_stop, round(trail, 2))
            if (cand > new_stop) if long_ else (cand < new_stop):
                new_stop, trailed = cand, True

    def out(exit_code=None, alert=None, reason=None):
        return {"stop": round(new_stop, 2),
                "moved_to_breakeven": moved_be,
                "trail_updated": trailed and exit_code is None,
                "exit": exit_code, "alert": alert, "reason": reason}

    # 2) Exits -- first that triggers wins.
    # Score crossed back through the middle: strong square-off, any price.
    if score is not None:
        if long_ and score < cfg.SCORE_EXIT_BUY_BELOW:
            return out("SCORE", "STRONG SQUARE-OFF", f"Score {score:.0f} < 50")
        if (not long_) and score > cfg.SCORE_EXIT_SELL_ABOVE:
            return out("SCORE", "STRONG SQUARE-OFF", f"Score {score:.0f} > 50")
    # Trailing stop hit.
    if (price <= new_stop) if long_ else (price >= new_stop):
        return out("TRAIL", "EXIT - TRAIL HIT", f"Price {price} hit stop {new_stop}")
    # Time stop: held the max sessions without reaching +1 ATR of profit.
    if days_held >= cfg.TIME_EXIT_TRADING_DAYS and (be_dist is None or profit < be_dist):
        return out("TIME", "EXIT - TIME", f"{days_held} sessions, no +1 ATR")

    if moved_be:
        return out(None, "MOVE STOP TO BREAKEVEN", f"+1 ATR -> stop to entry {entry}")
    if trailed:
        return out(None, "TRAIL UPDATED", f"Stop trailed to {new_stop}")
    return out()
