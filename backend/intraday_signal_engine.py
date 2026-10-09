"""
Intraday signal LOGIC for the Trdgo Stock board.

The 100-point BUY score and 100-point SELL score are produced upstream
(``intraday_model_service.dual_scores``) and are NOT touched here -- the weights
are correct and stay as they are. This module decides, from those two scores
plus live market context, what signal (if any) to show, following the operator's
rules exactly:

  1. HARD GATES  -- a signal is only allowed when VWAP / SPY+QQQ / RVOL (and for
     SELL the opening-range low) all line up. A failed gate => NO TRADE with the
     reason shown, no matter how high the score.
  2. TIME RULES  -- no new signal in the first 15 min or the last 30 min, SQUARE
     OFF 10 min before the close, early-close days handled, times in ET + Dubai.
  3. CANDLE CLOSE -- the signal state only changes when a 5-min candle closes.
  4. ENTRY RULES -- BUY >=60 & SELL <=30 (+gates); SELL >=65 & BUY <=25 (+gates);
     FULL / HALF size by score.
  5. SIGNAL STATES -- REVERSE / EXIT / WEAKENING / ACTIVE, evaluated only for a
     name that is ALREADY ACTIVE; a never-active name is NO TRADE, never yellow.

Portfolio limits (max 4 / max 2-per-sector / no buy+sell in a sector) span many
names, so they are applied by the board layer, not here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# Time zones and the early-close calendar.
# ---------------------------------------------------------------------------

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # noqa: BLE001 - fall back to a fixed offset if tz data is absent
    _ET = timezone(timedelta(hours=-4))
_DUBAI = timezone(timedelta(hours=4))

REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
# Half-day closes (1:00 PM ET). Extend this set as the exchange publishes them.
EARLY_CLOSE_DAYS: dict[str, time] = {
    "2026-11-27": time(13, 0),   # day after Thanksgiving
    "2026-12-24": time(13, 0),   # Christmas Eve
}

# Rule windows.
NO_ENTRY_OPEN_MIN = 15     # no new signals in the first 15 minutes
NO_ENTRY_CLOSE_MIN = 30    # no new signals in the last 30 minutes
SQUARE_OFF_MIN = 10        # square off open signals 10 minutes before the close


def now_et() -> datetime:
    return datetime.now(_ET)


def close_time_for(d: date) -> time:
    """The regular close for a given date, honouring half-day closes."""
    return EARLY_CLOSE_DAYS.get(d.isoformat(), REGULAR_CLOSE)


@dataclass
class SessionWindow:
    """Where 'now' sits inside the trading day, per the time rules."""
    is_open: bool
    in_open_block: bool     # first 15 minutes -> no new signals
    in_close_block: bool    # last 30 minutes -> no new signals
    in_square_off: bool     # last 10 minutes -> square off open signals
    close_et: time
    now_et: datetime
    early_close: bool

    def dubai_str(self) -> str:
        return self.now_et.astimezone(_DUBAI).strftime("%Y-%m-%d %H:%M:%S")

    def et_str(self) -> str:
        return self.now_et.strftime("%Y-%m-%d %H:%M:%S")


def session_window(when: Optional[datetime] = None) -> SessionWindow:
    n = when or now_et()
    d = n.date()
    close_t = close_time_for(d)
    early = close_t != REGULAR_CLOSE
    weekday = n.weekday() < 5
    t = n.time()
    is_open = weekday and REGULAR_OPEN <= t < close_t

    open_cut = (datetime.combine(d, REGULAR_OPEN, _ET) + timedelta(minutes=NO_ENTRY_OPEN_MIN)).time()
    close_block_start = (datetime.combine(d, close_t, _ET) - timedelta(minutes=NO_ENTRY_CLOSE_MIN)).time()
    square_off_start = (datetime.combine(d, close_t, _ET) - timedelta(minutes=SQUARE_OFF_MIN)).time()

    return SessionWindow(
        is_open=is_open,
        in_open_block=is_open and t < open_cut,
        in_close_block=is_open and t >= close_block_start,
        in_square_off=is_open and t >= square_off_start,
        close_et=close_t,
        now_et=n,
        early_close=early,
    )


# ---------------------------------------------------------------------------
# Entry / state thresholds (per the spec; these are decision cut-offs, NOT the
# scoring weights, which live in intraday_model_service and are unchanged).
# ---------------------------------------------------------------------------

BUY_ENTRY, BUY_OPP_MAX = 60.0, 30.0
SELL_ENTRY, SELL_OPP_MAX = 65.0, 25.0
FULL_BUY, FULL_SELL = 75.0, 80.0

RVOL_MIN = 1.5

# States (only for an already-ACTIVE name).
BUY_REVERSE_SELL = 65.0
BUY_EXIT_BELOW = 50.0
BUY_EXIT_SELL_ABOVE = 40.0
BUY_WEAK_LO, BUY_WEAK_HI = 50.0, 59.0
BUY_ACTIVE = 60.0

SELL_REVERSE_BUY = 60.0
SELL_EXIT_BELOW = 55.0
SELL_EXIT_BUY_ABOVE = 35.0
SELL_WEAK_LO, SELL_WEAK_HI = 55.0, 64.0
SELL_ACTIVE = 65.0


@dataclass
class Gates:
    """Inputs for the hard gates. None means 'unknown' -> the gate cannot pass."""
    price: Optional[float] = None
    vwap: Optional[float] = None
    spy_above_vwap: Optional[bool] = None
    qqq_above_vwap: Optional[bool] = None
    rvol: Optional[float] = None
    or_low: Optional[float] = None          # opening-range (first 15 min) low

    # --- derived ---
    @property
    def price_above_vwap(self) -> Optional[bool]:
        if self.price is None or self.vwap is None:
            return None
        return self.price > self.vwap

    @property
    def price_below_vwap(self) -> Optional[bool]:
        if self.price is None or self.vwap is None:
            return None
        return self.price < self.vwap

    def buy_check(self) -> tuple[bool, list[str]]:
        """(all pass, list of failure reasons) for the BUY gates."""
        fails: list[str] = []
        if self.price_above_vwap is not True:
            fails.append("price not above VWAP" if self.price_above_vwap is False else "no VWAP yet")
        if self.spy_above_vwap is not True:
            fails.append("SPY not above VWAP")
        if self.qqq_above_vwap is not True:
            fails.append("QQQ not above VWAP")
        if self.rvol is None or self.rvol < RVOL_MIN:
            fails.append(f"RVOL < {RVOL_MIN}" if self.rvol is not None else "no RVOL yet")
        return (not fails, fails)

    def sell_check(self) -> tuple[bool, list[str]]:
        """(all pass, list of failure reasons) for the SELL gates."""
        fails: list[str] = []
        if self.price_below_vwap is not True:
            fails.append("price not below VWAP" if self.price_below_vwap is False else "no VWAP yet")
        if self.spy_above_vwap is not False:
            fails.append("SPY not below VWAP")
        if self.qqq_above_vwap is not False:
            fails.append("QQQ not below VWAP")
        if self.or_low is None or self.price is None or not (self.price < self.or_low):
            fails.append("price not below opening-range low" if self.or_low is not None else "no opening range yet")
        if self.rvol is None or self.rvol < RVOL_MIN:
            fails.append(f"RVOL < {RVOL_MIN}" if self.rvol is not None else "no RVOL yet")
        return (not fails, fails)


# Signal states.
NO_TRADE = "NO TRADE"
ACTIVE_BUY, ACTIVE_SELL = "ACTIVE BUY", "ACTIVE SELL"
BUY_WEAKENING, SELL_WEAKENING = "BUY WEAKENING", "SELL WEAKENING"
EXIT, REVERSE = "EXIT", "REVERSE"
SQUARE_OFF = "SQUARE OFF"
WAITING_LIMIT = "WAITING - LIMIT FULL"


@dataclass
class Decision:
    state: str                      # ACTIVE BUY / BUY WEAKENING / EXIT / ...
    side: str                       # buy / sell / none
    color: str                      # green / yellow / red / none
    size: Optional[str] = None      # FULL SIZE / HALF SIZE
    blocked_reason: str = ""        # e.g. "Blocked: SPY below VWAP"
    square_off: bool = False
    # which active side (if any) the name holds AFTER this decision, so the
    # caller can persist it for the next candle's state check.
    active_after: Optional[str] = None   # "buy" / "sell" / None
    gates: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "state": self.state, "side": self.side, "color": self.color,
            "size": self.size, "blocked_reason": self.blocked_reason,
            "square_off": self.square_off, "gates": self.gates,
        }


def _size(buy: float, sell: float, side: str) -> str:
    if side == "buy":
        return "FULL SIZE" if buy >= FULL_BUY else "HALF SIZE"
    return "FULL SIZE" if sell >= FULL_SELL else "HALF SIZE"


def decide(
    buy: Optional[float],
    sell: Optional[float],
    gates: Gates,
    *,
    prior_active: Optional[str],     # "buy" / "sell" / None  (from the store)
    candle_closed: bool,             # did a NEW 5-min candle just close?
    window: SessionWindow,
    prior_state: str = NO_TRADE,     # the last state shown, for mid-candle hold
) -> Decision:
    """
    Resolve the signal for ONE name from its two scores and live context.

    Portfolio limits are applied by the caller, not here.
    """
    b = buy if buy is not None else 0.0
    s = sell if sell is not None else 0.0
    buy_pass, buy_fails = gates.buy_check()
    sell_pass, sell_fails = gates.sell_check()
    gate_info = {
        "price_above_vwap": gates.price_above_vwap,
        "price_below_vwap": gates.price_below_vwap,
        "spy_above_vwap": gates.spy_above_vwap,
        "qqq_above_vwap": gates.qqq_above_vwap,
        "rvol": gates.rvol,
        "or_low": gates.or_low,
        "buy_gates_pass": buy_pass,
        "sell_gates_pass": sell_pass,
        "buy_gate_fails": buy_fails,
        "sell_gate_fails": sell_fails,
    }

    # --- market closed: nothing to show ---
    if not window.is_open:
        return Decision(NO_TRADE, "none", "none", active_after=None, gates=gate_info)

    # --- square off window: flatten anything open ---
    if window.in_square_off and prior_active in ("buy", "sell"):
        return Decision(SQUARE_OFF, "none", "red", square_off=True,
                        active_after=None, gates=gate_info)

    # --- candle-close gate: between candles, hold the last state unchanged ---
    if not candle_closed:
        return Decision(prior_state,
                        "buy" if prior_active == "buy" else "sell" if prior_active == "sell" else "none",
                        _hold_color(prior_state),
                        active_after=prior_active, gates=gate_info)

    # ===================================================================
    # From here on a 5-min candle has just closed, so (re)evaluate.
    # ===================================================================

    # ---- an already-ACTIVE name: run the state ladder (first match wins) ----
    if prior_active == "buy":
        if s > BUY_REVERSE_SELL:
            return Decision(REVERSE, "none", "red", active_after=None, gates=gate_info)
        if b < BUY_EXIT_BELOW or s > BUY_EXIT_SELL_ABOVE or gates.price_below_vwap is True:
            return Decision(EXIT, "none", "red", active_after=None, gates=gate_info)
        if BUY_WEAK_LO <= b <= BUY_WEAK_HI:
            return Decision(BUY_WEAKENING, "buy", "yellow",
                            size=_size(b, s, "buy"), active_after="buy", gates=gate_info)
        if b >= BUY_ACTIVE:
            return Decision(ACTIVE_BUY, "buy", "green",
                            size=_size(b, s, "buy"), active_after="buy", gates=gate_info)
        return Decision(EXIT, "none", "red", active_after=None, gates=gate_info)

    if prior_active == "sell":
        if b > SELL_REVERSE_BUY:
            return Decision(REVERSE, "none", "red", active_after=None, gates=gate_info)
        if s < SELL_EXIT_BELOW or b > SELL_EXIT_BUY_ABOVE or gates.price_above_vwap is True:
            return Decision(EXIT, "none", "red", active_after=None, gates=gate_info)
        if SELL_WEAK_LO <= s <= SELL_WEAK_HI:
            return Decision(SELL_WEAKENING, "sell", "yellow",
                            size=_size(b, s, "sell"), active_after="sell", gates=gate_info)
        if s >= SELL_ACTIVE:
            return Decision(ACTIVE_SELL, "sell", "green",
                            size=_size(b, s, "sell"), active_after="sell", gates=gate_info)
        return Decision(EXIT, "none", "red", active_after=None, gates=gate_info)

    # ---- a name that is NOT active: can it ENTER? ----
    # No NEW signals in the first 15 min or the last 30 min.
    if window.in_open_block or window.in_close_block:
        why = "first 15 min" if window.in_open_block else "last 30 min"
        return Decision(NO_TRADE, "none", "none",
                        blocked_reason=f"No new signals ({why})",
                        active_after=None, gates=gate_info)

    # BUY entry
    if b >= BUY_ENTRY and s <= BUY_OPP_MAX:
        if buy_pass:
            return Decision(ACTIVE_BUY, "buy", "green",
                            size=_size(b, s, "buy"), active_after="buy", gates=gate_info)
        return Decision(NO_TRADE, "none", "none",
                        blocked_reason="Blocked: " + "; ".join(buy_fails),
                        active_after=None, gates=gate_info)

    # SELL entry
    if s >= SELL_ENTRY and b <= SELL_OPP_MAX:
        if sell_pass:
            return Decision(ACTIVE_SELL, "sell", "green",
                            size=_size(b, s, "sell"), active_after="sell", gates=gate_info)
        return Decision(NO_TRADE, "none", "none",
                        blocked_reason="Blocked: " + "; ".join(sell_fails),
                        active_after=None, gates=gate_info)

    # Nothing qualifies -> NO TRADE (never yellow for a name that was not active).
    return Decision(NO_TRADE, "none", "none", active_after=None, gates=gate_info)


def _hold_color(state: str) -> str:
    if state in (ACTIVE_BUY, ACTIVE_SELL):
        return "green"
    if state in (BUY_WEAKENING, SELL_WEAKENING):
        return "yellow"
    if state in (EXIT, REVERSE, SQUARE_OFF):
        return "red"
    return "none"
