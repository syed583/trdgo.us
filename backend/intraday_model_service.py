"""
Intraday dual-score model (BUY score + SELL score), per the operator's
"Intraday Trading Model - Final" spec. Used ONLY by the Trdgo Stock board and
the Tradgo Call page; every other page keeps the single directional model.

The hard inputs (VWAP, opening range, 5-min EMA/RSI, relative volume, relative
strength, gap) are already computed by the short-term model as directional
signals with a bias in [-1, 1]. Here each parameter's bias is split into a BUY
contribution (its bullish strength) and a SELL contribution (its bearish
strength), weighted by the spec's separate BUY and SELL weight tables, with no
negative points. The two scores then drive the four-state signal
(Active / Weakening / Exit / Reverse) and the entry gates.

Phase 1: scores + stateless signal states from the parameters currently
available (intraday ones on the Today horizon, daily proxies otherwise).
Parameters the app does not yet compute separately -- absolute SPY/QQQ
direction, sector strength, today's analyst action -- are simply absent from
the denominator, so coverage scales the score rather than capping it. Phase 2
adds those inputs and the hysteresis buffer + candle-close gating.
"""

from __future__ import annotations

from typing import Optional

# Each signal name -> (BUY weight, SELL weight) from the spec's two weight
# tables. A 0 means the side does not score that parameter. Signals not listed
# here (volatility, disparity, slow OI, dividends, corporate events) are not
# part of either intraday score and are ignored.
PARAM_MAP: dict[str, tuple[int, int]] = {
    # Intraday Trend & Price
    "vwap": (8, 10),              # Price vs VWAP
    "intraday_trend": (7, 7),     # EMA 9 / 20 (5-min)
    "ema_trend": (7, 7),          # daily EMA proxy when no intraday trend
    "opening_range": (6, 7),      # Opening Range breakout / breakdown
    "price_action": (5, 6),       # Price Structure (HH/HL vs LH/LL)
    "rsi": (4, 5),                # RSI 14
    # Options Tape
    "options_flow": (9, 7),       # Call / Put flow
    "unusual_activity": (6, 4),   # Unusual activity
    "volume_pcr": (5, 3),         # Volume & Put/Call ratio
    "key_levels": (5, 4),         # Key option levels (gamma flip / walls)
    "flow_by_expiry": (3, 2),     # Flow by expiry
    "oi_positioning": (2, 0),     # OI positioning (BUY only)
    # Volume & Market
    "relative_volume": (8, 7),    # RVOL
    "market_direction": (7, 10),  # SPY / QQQ direction
    "relative_strength_day": (5, 7),  # Relative strength / weakness vs SPY
    "sector_strength": (5, 6),    # Sector ETF strength / weakness
    "analyst_action": (4, 4),     # Today's analyst upgrade / downgrade
    # Catalyst & Gap
    "gap_hold": (6, 6),           # Pre-market gap & news / gap down
    # Company Context
    "earnings_results": (3, 3),   # Recent earnings
    "insider_activity": (2, 2),   # Insider & fund
}

# Spec thresholds (section 5, 6, 8).
BUY_ON, BUY_OFF = 60.0, 50.0        # hysteresis: active >=60, exit <50
SELL_ON, SELL_OFF = 65.0, 55.0      # sell runs stricter: 65 on / 55 off
BUY_OPP_MAX = 40.0                  # BUY needs SELL <= 40
SELL_OPP_MAX = 35.0                 # SELL needs BUY <= 35
FULL_BUY, FULL_SELL = 75.0, 80.0    # full-size thresholds


def _bias_of(s: dict) -> Optional[float]:
    """A signal's bias, recovered from points/weight when bias is not stored
    (stored calls keep points, not the raw bias)."""
    b = s.get("bias")
    if b is not None:
        return b
    pts, wt = s.get("points"), s.get("weight")
    try:
        if pts is not None and wt:
            return max(-1.0, min(1.0, float(pts) / float(wt)))
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return None


def dual_scores(signals: list[dict]) -> tuple[Optional[float], Optional[float], dict]:
    """Return (buy_score, sell_score, coverage) from a list of model signals.

    Each score is 0-100: the share of its side's available weight that the
    parameters' bullish (resp. bearish) strength filled.
    """
    buy_num = sell_num = 0.0
    buy_den = sell_den = 0.0
    for s in signals or []:
        if not s.get("available"):
            continue
        bias = _bias_of(s)
        if bias is None:
            continue
        w = PARAM_MAP.get(s.get("name"))
        if not w:
            continue
        bw, sw = w
        if bw:
            buy_num += max(0.0, bias) * bw
            buy_den += bw
        if sw:
            sell_num += max(0.0, -bias) * sw
            sell_den += sw

    buy = round(100.0 * buy_num / buy_den, 1) if buy_den else None
    sell = round(100.0 * sell_num / sell_den, 1) if sell_den else None
    coverage = {
        "buy_weight": round(buy_den, 1),
        "sell_weight": round(sell_den, 1),
    }
    return buy, sell, coverage


def classify(buy: Optional[float], sell: Optional[float]) -> dict:
    """Map the two scores to the spec's signal state, colour and tradable side.

    Stateless (Phase 1): the hysteresis buffer and candle-close gate, which need
    the previous state and a closed 5-min candle, come in Phase 2. The on/off
    levels are still encoded so the Weakening band reads correctly.
    """
    b = buy if buy is not None else 0.0
    s = sell if sell is not None else 0.0

    # Active states first -- a strong side with the opposite side contained. A
    # reading past the full-size threshold (BUY 75+, SELL 80+) reads as STRONG.
    if b >= BUY_ON and s <= BUY_OPP_MAX:
        full = b >= FULL_BUY
        return _state("STRONG BUY" if full else "ACTIVE BUY", "buy", "green",
                      full=full, buy=buy, sell=sell)
    if s >= SELL_ON and b <= SELL_OPP_MAX:
        full = s >= FULL_SELL
        return _state("STRONG SELL" if full else "ACTIVE SELL", "sell", "green",
                      full=full, buy=buy, sell=sell)

    # Weakening bands: in the buffer, still leaning, opposite side not yet strong.
    if BUY_OFF <= b < BUY_ON and s < SELL_OFF:
        return _state("BUY WEAKENING", "buy", "yellow", buy=buy, sell=sell)
    if SELL_OFF <= s < SELL_ON and b < BUY_ON:
        return _state("SELL WEAKENING", "sell", "yellow", buy=buy, sell=sell)

    return _state("NO TRADE", "none", "none", buy=buy, sell=sell)


def _state(decision: str, side: str, color: str, full: bool = False,
           buy: Optional[float] = None, sell: Optional[float] = None) -> dict:
    return {
        "decision": decision,
        "side": side,                       # buy / sell / none
        "color": color,                     # green / yellow / none
        "actionable": side in ("buy", "sell") and color == "green",
        "full_size": full,
        "buy_score": buy,
        "sell_score": sell,
    }


def breakdown(signals: list[dict]) -> dict:
    """Per-parameter BUY and SELL point contributions, for the breakdown view.

    Each parameter's bias is split into the points it adds to the BUY score (its
    bullish strength x the BUY weight) and to the SELL score (bearish strength x
    the SELL weight), with the two running totals and scores.
    """
    rows = []
    buy_num = sell_num = buy_den = sell_den = 0.0
    for s in signals or []:
        w = PARAM_MAP.get(s.get("name"))
        if not w:
            continue
        bw, sw = w
        _b = _bias_of(s)
        avail = bool(s.get("available")) and _b is not None
        b = _b or 0.0
        bp = round(max(0.0, b) * bw, 1) if avail else None
        sp = round(max(0.0, -b) * sw, 1) if avail else None
        rows.append({
            "name": s.get("name"),
            "label": s.get("label") or s.get("name"),
            "buy_weight": bw, "sell_weight": sw,
            "buy_points": bp, "sell_points": sp,
            "available": avail,
        })
        if avail:
            if bw:
                buy_num += max(0.0, b) * bw
                buy_den += bw
            if sw:
                sell_num += max(0.0, -b) * sw
                sell_den += sw
    rows.sort(key=lambda r: (r["buy_weight"] + r["sell_weight"]), reverse=True)
    return {
        "buy_score": round(100.0 * buy_num / buy_den, 1) if buy_den else None,
        "sell_score": round(100.0 * sell_num / sell_den, 1) if sell_den else None,
        "buy_points_total": round(buy_num, 1),
        "buy_weight_total": round(buy_den, 1),
        "sell_points_total": round(sell_num, 1),
        "sell_weight_total": round(sell_den, 1),
        "rows": rows,
    }


def evaluate(signals: list[dict]) -> dict:
    """Full read for one name: both scores plus the signal state."""
    buy, sell, coverage = dual_scores(signals)
    out = classify(buy, sell)
    out["coverage"] = coverage
    return out
