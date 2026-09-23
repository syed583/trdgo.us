"""
Price-action structure computed from daily bars.

Everything here is a pure function of the bars handed to it. That is the whole
design constraint: the same call has to work for today's window and for any
historical window during a backtest, so nothing reads a clock, a cache or a
provider. Give it bars up to a date and it describes the market as of that
date, with no way to see past it.

What this adds that the indicator set did not have
--------------------------------------------------
The existing technicals answer "where is price relative to its averages".
They never answer whether a move had participation behind it, whether the
structure of highs and lows actually supports the trend, or where price itself
says the levels are. Those are the questions below.

Volume matters most of the four. A breakout on half the usual volume and one
on triple volume scored identically before this module existed, because
``avg_volume_20`` was computed and then never read by anything.
"""

from __future__ import annotations

from typing import Any, Optional

# A swing point needs clear air either side; two bars is the usual minimum
# before a high or low is worth calling a pivot rather than noise.
SWING_SPAN = 2

# How close price must sit to a level before it counts as testing it.
LEVEL_PROXIMITY_PCT = 1.5

# Volume multiples. Below the first, a move happened on nobody's participation.
VOLUME_WEAK = 0.7
VOLUME_STRONG = 1.5
VOLUME_SURGE = 2.5

# Structure needs a few confirmed swings before it says anything.
MIN_SWINGS = 2


def _closes(bars: list[dict]) -> list[float]:
    return [float(b["close"]) for b in bars if b.get("close") is not None]


def _num(value) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# bollinger bands
# ---------------------------------------------------------------------------


def bollinger(closes: list[float], period: int = 20,
              mult: float = 2.0) -> Optional[dict]:
    """
    Bands, plus the two readings that actually get used.

    ``percent_b`` places price inside the band (0 = lower, 1 = upper) and
    ``bandwidth`` measures how wide the band is relative to price. Width is
    the more useful of the two: a band squeezing toward its own recent lows
    tends to precede expansion, which belongs in a confidence measure rather
    than a directional one.
    """
    if len(closes) < period:
        return None

    window = closes[-period:]
    middle = sum(window) / period
    variance = sum((c - middle) ** 2 for c in window) / period
    sd = variance ** 0.5
    upper, lower = middle + mult * sd, middle - mult * sd
    close = closes[-1]

    return {
        "upper": round(upper, 4),
        "middle": round(middle, 4),
        "lower": round(lower, 4),
        "bandwidth": round((upper - lower) / middle * 100, 3) if middle else None,
        "percent_b": (round((close - lower) / (upper - lower), 4)
                      if upper > lower else None),
    }


# ---------------------------------------------------------------------------
# swing structure
# ---------------------------------------------------------------------------


def swing_points(bars: list[dict], span: int = SWING_SPAN) -> dict:
    """
    Confirmed pivot highs and lows.

    A pivot is only confirmed once ``span`` bars have printed on both sides,
    so the most recent bars can never be pivots. That lag is deliberate --
    calling a high before the right-hand bars exist is looking into the
    future, which would quietly inflate any backtest built on this.
    """
    highs: list[dict] = []
    lows: list[dict] = []
    if len(bars) < span * 2 + 1:
        return {"highs": highs, "lows": lows}

    for i in range(span, len(bars) - span):
        window = bars[i - span:i + span + 1]
        high = _num(bars[i].get("high"))
        low = _num(bars[i].get("low"))
        if high is not None and all(
                high >= (_num(b.get("high")) or 0) for b in window):
            highs.append({"index": i, "date": bars[i].get("date"), "price": high})
        if low is not None and all(
                low <= (_num(b.get("low")) or float("inf")) for b in window):
            lows.append({"index": i, "date": bars[i].get("date"), "price": low})

    return {"highs": highs, "lows": lows}


def market_structure(bars: list[dict], span: int = SWING_SPAN) -> dict:
    """
    Higher highs and higher lows, or the reverse.

    This is the question the moving averages cannot answer. An EMA stack can
    look healthy while price is carving lower highs beneath it, and that
    divergence is usually the more informative of the two.
    """
    swings = swing_points(bars, span)
    highs = [s["price"] for s in swings["highs"]][-3:]
    lows = [s["price"] for s in swings["lows"]][-3:]

    if len(highs) < MIN_SWINGS or len(lows) < MIN_SWINGS:
        return {"structure": "UNDEFINED", "higher_highs": None,
                "higher_lows": None, "swing_highs": len(swings["highs"]),
                "swing_lows": len(swings["lows"])}

    higher_highs = highs[-1] > highs[-2]
    higher_lows = lows[-1] > lows[-2]

    if higher_highs and higher_lows:
        structure = "UPTREND"
    elif not higher_highs and not higher_lows:
        structure = "DOWNTREND"
    else:
        # One rising and one falling is a range or a transition, and calling
        # it a trend in either direction would be inventing a signal.
        structure = "RANGE"

    return {
        "structure": structure,
        "higher_highs": higher_highs,
        "higher_lows": higher_lows,
        "last_swing_high": highs[-1],
        "last_swing_low": lows[-1],
        "swing_highs": len(swings["highs"]),
        "swing_lows": len(swings["lows"]),
    }


def support_resistance(bars: list[dict], span: int = SWING_SPAN) -> dict:
    """
    Nearest price-derived levels above and below.

    Distinct from the option walls already in the product: those come from
    open interest and vanish when positioning rolls. These are where price
    itself turned, which is the only reference left when options data is thin
    or absent.
    """
    swings = swing_points(bars, span)
    closes = _closes(bars)
    if not closes:
        return {"support": None, "resistance": None}

    close = closes[-1]
    above = sorted(s["price"] for s in swings["highs"] if s["price"] > close)
    below = sorted((s["price"] for s in swings["lows"] if s["price"] < close),
                   reverse=True)

    resistance = above[0] if above else None
    support = below[0] if below else None

    def distance(level: Optional[float]) -> Optional[float]:
        return round((level - close) / close * 100, 2) if level else None

    return {
        "close": close,
        "support": round(support, 4) if support else None,
        "resistance": round(resistance, 4) if resistance else None,
        "support_distance_pct": distance(support),
        "resistance_distance_pct": distance(resistance),
        "testing_resistance": bool(
            resistance and abs(resistance - close) / close * 100
            <= LEVEL_PROXIMITY_PCT),
        "testing_support": bool(
            support and abs(support - close) / close * 100
            <= LEVEL_PROXIMITY_PCT),
    }


# ---------------------------------------------------------------------------
# volume
# ---------------------------------------------------------------------------


def volume_confirmation(bars: list[dict], period: int = 20) -> dict:
    """
    Whether the latest move had participation behind it.

    The missing piece in the previous indicator set: a breakout on half the
    usual volume and one on triple volume were scored identically, because the
    twenty-day average was computed and then read by nothing.

    Direction is carried alongside, because heavy volume is not bullish by
    itself -- heavy volume on a down day is distribution.
    """
    volumes = [_num(b.get("volume")) or 0.0 for b in bars]
    closes = _closes(bars)
    if len(volumes) < period + 1 or len(closes) < 2:
        return {"status": "INSUFFICIENT_DATA"}

    average = sum(volumes[-period - 1:-1]) / period
    latest = volumes[-1]
    if not average:
        return {"status": "INSUFFICIENT_DATA"}

    ratio = latest / average
    change = closes[-1] - closes[-2]

    if ratio >= VOLUME_SURGE:
        label = "SURGE"
    elif ratio >= VOLUME_STRONG:
        label = "STRONG"
    elif ratio <= VOLUME_WEAK:
        label = "WEAK"
    else:
        label = "NORMAL"

    return {
        "volume": latest,
        "average_volume": round(average, 2),
        "ratio": round(ratio, 2),
        "label": label,
        "direction": "UP" if change > 0 else "DOWN" if change < 0 else "FLAT",
        # Confirmed means the move and the participation agree. Unconfirmed
        # moves are the ones that tend not to hold.
        "confirms_move": bool(ratio >= VOLUME_STRONG and change != 0),
        "status": "OK",
    }


# ---------------------------------------------------------------------------
# breakouts
# ---------------------------------------------------------------------------


def breakout(bars: list[dict], lookback: int = 20,
             span: int = SWING_SPAN) -> dict:
    """
    Price clearing its recent range, and whether volume came with it.

    The volume condition is the point. A close above a twenty-day high on
    below-average volume is the classic false breakout, and treating it the
    same as a heavy-volume break is how a signal ends up with no edge.
    """
    if len(bars) < lookback + 2:
        return {"state": "NONE", "status": "INSUFFICIENT_DATA"}

    closes = _closes(bars)
    highs = [_num(b.get("high")) or 0.0 for b in bars]
    lows = [_num(b.get("low")) or 0.0 for b in bars]

    close = closes[-1]
    prior_high = max(highs[-lookback - 1:-1])
    prior_low = min(lows[-lookback - 1:-1])

    volume = volume_confirmation(bars)
    confirmed = bool(volume.get("confirms_move"))

    if close > prior_high:
        state = "BREAKOUT_UP"
    elif close < prior_low:
        state = "BREAKDOWN"
    else:
        state = "NONE"

    return {
        "state": state,
        "lookback": lookback,
        "prior_high": round(prior_high, 4),
        "prior_low": round(prior_low, 4),
        "close": close,
        "volume_ratio": volume.get("ratio"),
        "volume_confirmed": confirmed if state != "NONE" else None,
        # Named rather than scored here: an unconfirmed break is a real event,
        # it just should not carry the weight of a confirmed one.
        "quality": ("CONFIRMED" if state != "NONE" and confirmed
                    else "UNCONFIRMED" if state != "NONE" else None),
        "status": "OK",
    }


# ---------------------------------------------------------------------------
# relative strength
# ---------------------------------------------------------------------------


def relative_strength(bars: list[dict], benchmark: list[dict],
                      period: int = 63) -> dict:
    """
    Performance against a benchmark over the same window.

    Without this the model cannot separate a strong stock from a strong
    market. A name up eight percent in a quarter looks healthy until the index
    is up twelve, at which point it is lagging and the trend reading is
    flattering it.
    """
    a, b = _closes(bars), _closes(benchmark)
    if len(a) < period + 1 or len(b) < period + 1:
        return {"status": "INSUFFICIENT_DATA"}

    def change(series: list[float]) -> Optional[float]:
        start = series[-period - 1]
        return (series[-1] - start) / start * 100 if start else None

    symbol_change = change(a)
    bench_change = change(b)
    if symbol_change is None or bench_change is None:
        return {"status": "INSUFFICIENT_DATA"}

    spread = symbol_change - bench_change
    return {
        "period_days": period,
        "symbol_change_pct": round(symbol_change, 2),
        "benchmark_change_pct": round(bench_change, 2),
        "spread_pct": round(spread, 2),
        "outperforming": spread > 0,
        "status": "OK",
    }


# ---------------------------------------------------------------------------
# assembly and scoring
# ---------------------------------------------------------------------------


def analyse(bars: list[dict], benchmark: Optional[list[dict]] = None,
            span: int = SWING_SPAN) -> dict:
    """Every price-action reading for one bar window."""
    if not bars:
        return {"status": "NO_DATA"}

    closes = _closes(bars)
    return {
        "bars": len(bars),
        "close": closes[-1] if closes else None,
        "structure": market_structure(bars, span),
        "levels": support_resistance(bars, span),
        "volume": volume_confirmation(bars),
        "breakout": breakout(bars, span=span),
        "bollinger": bollinger(closes),
        "relative_strength": (relative_strength(bars, benchmark)
                              if benchmark else {"status": "NO_BENCHMARK"}),
        "status": "OK",
    }


def score_price_action(analysis: dict) -> dict:
    """
    Directional score from price action, -10 to +10.

    Only things that point somewhere vote. Bollinger bandwidth and ATR
    describe how volatile the market is, not which way it is going, so they
    are reported for confidence and sizing instead of being folded in here --
    a wide band is not bullish or bearish, and pretending otherwise is how a
    volatility reading ends up masquerading as a direction.
    """
    if not analysis or analysis.get("status") != "OK":
        return {"score": 0, "max_score": 10, "bias": "NO_DATA",
                "reasons": ["No price data available"], "components": {}}

    score = 0.0
    reasons: list[str] = []
    components: dict[str, float] = {}

    # -- structure of highs and lows: 4 -------------------------------------
    structure = (analysis.get("structure") or {}).get("structure")
    if structure == "UPTREND":
        score += 4
        components["structure"] = 4
        reasons.append("Higher highs and higher lows")
    elif structure == "DOWNTREND":
        score -= 4
        components["structure"] = -4
        reasons.append("Lower highs and lower lows")
    else:
        components["structure"] = 0
        if structure == "RANGE":
            reasons.append("Swings are mixed: range rather than trend")

    # -- breakout, weighted by whether volume came with it: 3 ---------------
    brk = analysis.get("breakout") or {}
    if brk.get("state") == "BREAKOUT_UP":
        points = 3 if brk.get("volume_confirmed") else 1
        score += points
        components["breakout"] = points
        reasons.append("Breakout above the recent range"
                       + (" on strong volume" if points == 3
                          else " but volume did not confirm"))
    elif brk.get("state") == "BREAKDOWN":
        points = -3 if brk.get("volume_confirmed") else -1
        score += points
        components["breakout"] = points
        reasons.append("Breakdown below the recent range"
                       + (" on strong volume" if points == -3
                          else " but volume did not confirm"))
    else:
        components["breakout"] = 0

    # -- volume behind the latest move: 2 -----------------------------------
    volume = analysis.get("volume") or {}
    if volume.get("status") == "OK":
        ratio = volume.get("ratio") or 1.0
        direction = volume.get("direction")
        if ratio >= VOLUME_STRONG and direction == "UP":
            score += 2
            components["volume"] = 2
            reasons.append(f"Up move on {ratio:.1f}x average volume")
        elif ratio >= VOLUME_STRONG and direction == "DOWN":
            score -= 2
            components["volume"] = -2
            reasons.append(f"Down move on {ratio:.1f}x average volume")
        elif ratio <= VOLUME_WEAK:
            components["volume"] = 0
            reasons.append("Move lacked volume participation")
        else:
            components["volume"] = 0

    # -- strength against the benchmark: 3 ----------------------------------
    rs = analysis.get("relative_strength") or {}
    if rs.get("status") == "OK":
        spread = rs.get("spread_pct") or 0.0
        if spread >= 5:
            score += 3
            components["relative_strength"] = 3
            reasons.append(f"Outperforming the benchmark by {spread:.1f}%")
        elif spread > 0:
            score += 1
            components["relative_strength"] = 1
            reasons.append(f"Modestly ahead of the benchmark ({spread:.1f}%)")
        elif spread <= -5:
            score -= 3
            components["relative_strength"] = -3
            reasons.append(f"Lagging the benchmark by {abs(spread):.1f}%")
        else:
            score -= 1
            components["relative_strength"] = -1
            reasons.append(f"Slightly behind the benchmark ({spread:.1f}%)")

    score = max(-10.0, min(10.0, score))

    bias = ("STRONG_BULLISH" if score >= 6 else "BULLISH" if score >= 2
            else "STRONG_BEARISH" if score <= -6 else "BEARISH" if score <= -2
            else "NEUTRAL")

    return {
        "score": round(score, 1),
        "max_score": 10,
        "bias": bias,
        "reasons": reasons or ["No decisive price-action signal"],
        "components": components,
    }


def confidence_inputs(analysis: dict, atr: Optional[float] = None) -> dict:
    """
    Readings that shape conviction and sizing rather than direction.

    Kept separate on purpose. A volatility measure has no opinion about which
    way price goes, and folding one into a direction score corrupts the
    number without anyone noticing.
    """
    if not analysis or analysis.get("status") != "OK":
        return {"status": "NO_DATA"}

    bands = analysis.get("bollinger") or {}
    close = analysis.get("close")
    levels = analysis.get("levels") or {}

    return {
        "bandwidth": bands.get("bandwidth"),
        "percent_b": bands.get("percent_b"),
        "atr": atr,
        # ATR as a share of price is the comparable form: two dollars of range
        # means something different on a $20 stock than on a $400 one.
        "atr_pct": (round(atr / close * 100, 2)
                    if atr and close else None),
        "near_resistance": levels.get("testing_resistance"),
        "near_support": levels.get("testing_support"),
        "status": "OK",
    }
