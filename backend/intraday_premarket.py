"""Pre-market board for Trdgo Stock -- what to BUY / SELL before the open.

The intraday RTH model (intraday_model / intraday_signal_v2) cannot run before
09:30 ET: there are no 5-minute bars yet, so no VWAP, opening range or RVOL, and
the new-signal window is 10:00-15:30. This gives a usable call at ~07:00 ET
(3 PM Dubai) from the data that DOES exist before the open:

  * Gap / pre-market move   (weight 30) -- pre-market price vs prior close
  * Overnight options flow   (weight 25) -- bullish vs bearish premium (UW)
  * Daily trend / EMA stack  (weight 25) -- price vs EMA 20 / 50 / 200
  * Prior-day level break    (weight 20) -- pre-market price vs prior day hi/lo

All-positive scale (points = weight * (0.5 + 0.5*bias), neutral/missing = half),
sum = 100. BUY >= 55 / NEUTRAL / SELL <= 45 (same cut-offs as Claude New). Entry
= pre-market price; SL = +/- 0.5 x daily ATR; TP = +/- 1.0 x ATR (2:1). This is
a ranking, not advice -- it reads public pre-market data only.
"""
from __future__ import annotations

from typing import Optional

import intraday_indicators as ind

SOURCE = "Trdgo Stock — Pre-Market"
MODEL = "premarket-v1"

W = {"gap": 30, "flow": 25, "trend": 25, "level": 20}   # sum = 100
BUY_CUT, SELL_CUT = 55.0, 45.0


def _f(v) -> Optional[float]:
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _clamp(b: float) -> float:
    return max(-1.0, min(1.0, b))


def _pts(weight: float, bias: Optional[float]) -> float:
    if bias is None:
        return weight * 0.5
    return weight * (0.5 + 0.5 * _clamp(bias))


def _flow_bias(symbol: str) -> Optional[float]:
    """Latest daily bullish vs bearish premium skew from UW options-volume."""
    import unusualwhales_service as uw
    try:
        rows = uw._rows(uw.get(f"/api/stock/{symbol}/options-volume", {"limit": 2}))
    except Exception:  # noqa: BLE001
        return None
    if not rows:
        return None
    r = rows[0]
    bull, bear = _f(r.get("bullish_premium")), _f(r.get("bearish_premium"))
    if bull is None or bear is None or (bull + bear) <= 0:
        return None
    return _clamp((bull - bear) / (bull + bear))


def _trend_bias(price: Optional[float], closes: list[float]) -> Optional[float]:
    if price is None or len(closes) < 60:
        return None
    e20 = ind._ema(closes[-80:], 20)
    e50 = ind._ema(closes[-150:], 50)
    e200 = ind._ema(closes, 200) if len(closes) >= 200 else ind._ema(closes, min(len(closes), 150))
    if None in (e20, e50, e200):
        return None
    b = 0.0
    b += 0.4 if price > e20 else -0.4
    b += 0.3 if e20 > e50 else -0.3
    b += 0.3 if e50 > e200 else -0.3
    return _clamp(b)


def _level_bias(price, prior_high, prior_low, prior_close) -> Optional[float]:
    if None in (price, prior_high, prior_low, prior_close) or prior_high <= prior_low:
        return None
    if price >= prior_high:
        return 1.0                       # gapping over yesterday's high -> breakout
    if price <= prior_low:
        return -1.0                      # under yesterday's low -> breakdown
    span = (prior_high - prior_low) / 2.0
    return _clamp((price - prior_close) / span) if span else 0.0


def score_one(symbol: str) -> Optional[dict]:
    import unusualwhales_service as uw
    q = uw.get_quote(symbol) or {}
    price = _f(q.get("price"))
    prev = _f(q.get("previous_close"))
    if price is None or prev is None or prev <= 0:
        return None

    gap = (price - prev) / prev                      # overnight / pre-market move
    gap_bias = _clamp(gap / 0.02)                    # +-2% move = full lean
    flow_bias = _flow_bias(symbol)

    daily = uw.get_daily_bars(symbol, "1 Y") or []
    closes = [c for c in (_f(b.get("close")) for b in daily) if c is not None]
    prior = daily[-1] if daily else {}
    # The last daily bar may be today's forming bar; use the prior SESSION's hi/lo.
    pbar = daily[-2] if len(daily) >= 2 else prior
    trend_bias = _trend_bias(price, closes)
    level_bias = _level_bias(price, _f(pbar.get("high")), _f(pbar.get("low")), prev)
    atr = ind.atr14_daily(daily)

    comp = [
        {"name": "gap", "label": "Gap / Pre-market move", "max": W["gap"], "bias": gap_bias,
         "detail": f"{gap*100:+.2f}% vs prior close"},
        {"name": "flow", "label": "Overnight options flow", "max": W["flow"], "bias": flow_bias},
        {"name": "trend", "label": "Daily trend (EMA 20/50/200)", "max": W["trend"], "bias": trend_bias},
        {"name": "level", "label": "Prior-day level break", "max": W["level"], "bias": level_bias},
    ]
    for c in comp:
        c["points"] = round(_pts(c["max"], c["bias"]), 2)
        c["bias"] = (round(c["bias"], 3) if c["bias"] is not None else None)

    score = round(sum(c["points"] for c in comp), 1)
    signal = "BUY" if score >= BUY_CUT else "SELL" if score <= SELL_CUT else "NEUTRAL"

    entry = price
    sl = tp = None
    if atr and signal != "NEUTRAL":
        if signal == "BUY":
            sl, tp = entry - 0.5 * atr, entry + 1.0 * atr
        else:
            sl, tp = entry + 0.5 * atr, entry - 1.0 * atr

    covered = sum(c["max"] for c in comp if c["bias"] is not None)
    return {
        "symbol": symbol, "price": round(price, 2), "prev_close": round(prev, 2),
        "gap_pct": round(gap * 100, 2), "score": score, "signal": signal,
        "components": comp, "atr": round(atr, 2) if atr else None,
        "entry": round(entry, 2), "stop": round(sl, 2) if sl else None,
        "target": round(tp, 2) if tp else None,
        "coverage": round(covered / 100.0 * 100, 0),
        "session": q.get("session"),
    }


def _score_cached(symbol: str) -> Optional[dict]:
    import swr
    return swr.serve(f"pm:score:{symbol}", lambda: score_one(symbol), 300.0)


def board() -> dict:
    """Score the whole liquid universe and split into buyers / sellers / neutral.
    Scored in parallel (each name is 3 UW calls) so a cold board is seconds, not
    minutes; per-symbol results are cached 5 min so later scans are cheap."""
    import ai_trade_service as ats
    from concurrent.futures import ThreadPoolExecutor
    syms = [s.upper() for s in getattr(ats, "UNIVERSE", [])]
    rows: list[dict] = []

    def _one(s: str):
        try:
            return _score_cached(s)
        except Exception:  # noqa: BLE001
            return None

    with ThreadPoolExecutor(max_workers=6) as ex:
        for r in ex.map(_one, syms):
            if r:
                rows.append(r)
    buyers = sorted([r for r in rows if r["signal"] == "BUY"],
                    key=lambda r: r["score"], reverse=True)
    sellers = sorted([r for r in rows if r["signal"] == "SELL"], key=lambda r: r["score"])
    neutral = [r for r in rows if r["signal"] == "NEUTRAL"]
    phase = ind.market_phase()
    return {"status": "OK", "source": SOURCE, "model": MODEL, "phase": phase,
            "buyers": buyers, "sellers": sellers, "neutral": neutral,
            "counts": {"buy": len(buyers), "sell": len(sellers), "neutral": len(neutral)},
            "universe": len(syms), "scored": len(rows)}
