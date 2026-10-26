"""Simulated execution prices for the paper trades: what price a CLOSE is booked at -- and when there is none.

This module is the ONLY place that decides a booked price. It never decides whether to close (that is the strategy's
stop / target / score / timed-exit conditions in demo_trade_service) and it never touches a signal or a score.

The old convention booked every stop and target exactly AT its level. That is right inside a continuous session and
wrong across an overnight gap: a stop at 99 with the stock opening at 94 is not filled at 99, and a target at 102
with the stock opening at 105 is not filled at 102 either (a resting limit order fills at the better opening price).

``gap_aware`` (default):

  * STOP / TARGET, and the observations on either side of the exit are separated by a CLOSED period (overnight,
    weekend, holiday -- ``market_calendar.closed_between``): booked at the FIRST AVAILABLE EXECUTABLE price after the
    gap, i.e. the first reliable price observed, whichever side of the level it is on;
  * STOP / TARGET inside a continuous session: booked at the level (unchanged: there is no tick data to do better);
  * SCORE_EXIT / TIMED_EXIT: booked at the observed reliable price (unchanged).

``level``: the old convention, kept so the two can be compared.

Nothing is invented. A close needs a RELIABLE price: a board price that passed the freshness gate (not older than
``max_price_age_s``), or a live quote served from the short cache. The last known price and the entry price are NOT
reliable -- if that is all there is, the close is DEFERRED (the position stays open and is flagged), and only after
``max_unpriced_trading_days`` trading days past its due date is it closed as NO_PRICE with no exit price and no P&L
(so it can never appear in a win rate or a total).
"""
from __future__ import annotations

from datetime import datetime
from typing import NamedTuple, Optional

import tab_settings as cfg


def _p() -> dict:
    return cfg.PAPER_EXECUTION


class Price(NamedTuple):
    value: Optional[float]
    reliable: bool
    source: str                 # board | quote | last_known | entry | none


def classify_price(board_price: Optional[float], board_age_s: Optional[float], board_wait: bool,
                   quote: Optional[float], last_known: Optional[float], entry: Optional[float]) -> Price:
    """The best price available and whether it may be used as a fill. Pure."""
    limit = float(_p()["max_price_age_s"])
    if board_price is not None and not board_wait and board_age_s is not None and board_age_s <= limit:
        return Price(float(board_price), True, "board")
    if quote is not None:
        return Price(float(quote), True, "quote")
    if last_known is not None:
        return Price(float(last_known), False, "last_known")
    if entry is not None:
        return Price(float(entry), False, "entry")
    return Price(None, False, "none")


def observation_gap(prev_obs: Optional[datetime], now: datetime) -> bool:
    """Is the previous observation of this position separated from now by a period in which it could not trade?"""
    if prev_obs is None:
        return False
    import market_calendar
    return market_calendar.closed_between(prev_obs, now)


def fill_price(side: str, reason: str, stop: Optional[float], target: Optional[float], observed: float,
               gap: bool, model: Optional[str] = None) -> float:
    """The price a close is booked at. ``observed`` is a RELIABLE price (the caller defers otherwise)."""
    model = model or _p()["fill_model"]
    if reason in ("STOP", "TARGET"):
        level = stop if reason == "STOP" else target
        if model == "gap_aware" and gap and level is not None:
            return float(observed)                      # the first executable price after the gap, either side of the level
        return float(level if level is not None else observed)
    return float(observed)


def gap_effect(side: str, reason: str, stop: Optional[float], target: Optional[float], exit_price: Optional[float],
               shares: int) -> Optional[float]:
    """Dollars a STOP / TARGET booking differs from the level (negative = worse than the level), else None."""
    if reason not in ("STOP", "TARGET") or exit_price is None:
        return None
    level = stop if reason == "STOP" else target
    if level is None or abs(exit_price - level) < 0.005:
        return None
    diff = (exit_price - level) if side == "LONG" else (level - exit_price)
    return round(diff * shares, 2)
