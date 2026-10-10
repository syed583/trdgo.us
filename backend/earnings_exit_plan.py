"""Earnings-trade EXIT PLAN.

Given a live directional earnings trade (BUY or SELL on the equity), decide
when to get out -- HOLD / TRIM / EXIT -- from four conditions on the company
*now* (not from the previous day's call):

    1. Grade ............ letter grade off the equity earnings score
    2. Exponential ...... EMA 9 vs EMA 20 compare on the recent closes
    3. Price action ..... the spot against the options expected-move band
    4. Liquidity ........ the ATM straddle spread width / greeks

Conditions are checked in that priority order; the first one that says EXIT
sets the verdict and the headline reason. A TRIM downgrades a HOLD but never
overrides an EXIT. Pure functions, no I/O, so this is unit-testable and cheap
to call from inside get_summary() (it reuses data already fetched there).
"""
from __future__ import annotations

from typing import Optional


def _f(v) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _ema(values: list[float], period: int) -> Optional[float]:
    vals = [v for v in values if v is not None]
    if len(vals) < period:
        return None
    k = 2.0 / (period + 1)
    ema = sum(vals[:period]) / period
    for v in vals[period:]:
        ema = v * k + ema * (1 - k)
    return ema


def _grade(score: Optional[float]) -> Optional[str]:
    if score is None:
        return None
    if score >= 70:
        return "A"
    if score >= 55:
        return "B"
    if score >= 40:
        return "C"
    return "D"


# severity ranking so we can take the worst of the four
_RANK = {"ok": 0, "warn": 1, "trim": 2, "exit": 3}


def compute_exit_plan(
    *,
    direction: Optional[str],
    score: Optional[float],
    spark: Optional[list],
    spot: Optional[float],
    em_lower: Optional[float],
    em_upper: Optional[float],
    construction: Optional[dict] = None,
) -> Optional[dict]:
    """Return an exit-plan block, or None when there is no directional trade."""
    side = (direction or "").upper()
    if side not in ("BUY", "SELL"):
        return None  # only a directional equity trade has an exit to manage

    spot = _f(spot)
    closes = [c for c in (spark or []) if _f(c) is not None]
    con = construction or {}

    checklist: list[dict] = []

    def add(key: str, label: str, state: str, note: str):
        checklist.append({"key": key, "label": label, "state": state, "note": note})

    # 1) GRADE -----------------------------------------------------------
    g = _grade(_f(score))
    if g is None:
        add("grade", "Grade", "warn", "No equity score available.")
    elif g == "D":
        add("grade", "Grade", "exit", f"Grade {g}: the setup no longer supports the trade.")
    elif g == "C":
        add("grade", "Grade", "trim", f"Grade {g}: edge has weakened -- trim.")
    else:
        add("grade", "Grade", "ok", f"Grade {g}: setup still intact.")

    # 2) EXPONENTIAL (EMA 9 vs EMA 20) -----------------------------------
    ema9, ema20 = _ema(closes, 9), _ema(closes, 20)
    if ema9 is None or ema20 is None or spot is None:
        add("ema", "Exponential (EMA 9/20)", "warn", "Not enough price history for EMA.")
    else:
        if side == "BUY":
            if spot < ema9:
                add("ema", "Exponential (EMA 9/20)", "exit",
                    f"Price {spot:.2f} closed below EMA9 {ema9:.2f}.")
            elif ema9 < ema20:
                add("ema", "Exponential (EMA 9/20)", "trim",
                    f"EMA9 {ema9:.2f} crossed below EMA20 {ema20:.2f}.")
            else:
                add("ema", "Exponential (EMA 9/20)", "ok",
                    f"Trend up: price {spot:.2f} ≥ EMA9 {ema9:.2f} ≥ EMA20 {ema20:.2f}.")
        else:  # SELL
            if spot > ema9:
                add("ema", "Exponential (EMA 9/20)", "exit",
                    f"Price {spot:.2f} closed above EMA9 {ema9:.2f}.")
            elif ema9 > ema20:
                add("ema", "Exponential (EMA 9/20)", "trim",
                    f"EMA9 {ema9:.2f} crossed above EMA20 {ema20:.2f}.")
            else:
                add("ema", "Exponential (EMA 9/20)", "ok",
                    f"Trend down: price {spot:.2f} ≤ EMA9 {ema9:.2f} ≤ EMA20 {ema20:.2f}.")

    # 3) PRICE ACTION (vs expected-move band) ----------------------------
    lo, hi = _f(em_lower), _f(em_upper)
    if spot is None or lo is None or hi is None:
        add("price", "Price action", "warn", "No expected-move band to test against.")
    else:
        band = hi - lo
        near = band * 0.1 if band else 0.0
        if side == "BUY":
            if spot <= lo:
                add("price", "Price action", "exit",
                    f"Broke the lower band {lo:.2f}.")
            elif spot <= lo + near:
                add("price", "Price action", "trim",
                    f"Pressing the lower band {lo:.2f}.")
            else:
                add("price", "Price action", "ok", f"Holding above {lo:.2f}.")
        else:  # SELL
            if spot >= hi:
                add("price", "Price action", "exit",
                    f"Broke the upper band {hi:.2f}.")
            elif spot >= hi - near:
                add("price", "Price action", "trim",
                    f"Pressing the upper band {hi:.2f}.")
            else:
                add("price", "Price action", "ok", f"Holding below {hi:.2f}.")

    # 4) LIQUIDITY (straddle spread / decay) -----------------------------
    spread_pct = _f(con.get("spread_pct"))
    if spread_pct is None:
        add("liquidity", "Liquidity", "warn", "No quote spread available.")
    elif spread_pct > 0.20:
        add("liquidity", "Liquidity", "trim",
            f"Wide spread {spread_pct * 100:.0f}% of premium -- exits are costly.")
    elif spread_pct > 0.10:
        add("liquidity", "Liquidity", "warn",
            f"Spread {spread_pct * 100:.0f}% of premium -- watch fills.")
    else:
        add("liquidity", "Liquidity", "ok",
            f"Tight spread {spread_pct * 100:.0f}% of premium.")

    # Verdict = worst state; headline reason = first condition at that state,
    # in the priority order grade -> ema -> price -> liquidity.
    worst = max((c["state"] for c in checklist), key=lambda s: _RANK[s])
    verdict = {"exit": "EXIT", "trim": "TRIM", "warn": "HOLD", "ok": "HOLD"}[worst]
    reason = next((c["note"] for c in checklist if c["state"] == worst), "")

    # Exit level: the EMA9 or the against-you band edge, whichever is closer to
    # the spot on the losing side.
    exit_level = None
    if side == "BUY":
        cands = [v for v in (ema9, lo) if v is not None]
        exit_level = max(cands) if cands else None
    else:
        cands = [v for v in (ema9, hi) if v is not None]
        exit_level = min(cands) if cands else None

    return {
        "side": side,
        "verdict": verdict,
        "reason": reason,
        "exit_level": round(exit_level, 2) if exit_level is not None else None,
        "grade": g,
        "checklist": checklist,
    }
