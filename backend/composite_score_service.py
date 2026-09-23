"""
One technical scoring formula, shared by everything that scores price.

Why this module exists
----------------------
There were three separate implementations. The composite score used
``score_technicals`` (trend, RSI, MACD, confirmation, +/-20). The ticker cards
used a different weighted blend on a 0-100 scale. The backtest used a third,
with its own weights again.

That meant a backtest reporting "scores above 70 performed well" was a
statement about a formula that existed nowhere else in the product. The number
being validated was not the number anyone saw. Unifying them is what makes a
backtest result mean something about the live system.

Everything here is a pure function of the bars passed in, so the identical
call works for today's window and for any historical window during a
walk-forward run. No clock, no cache, no provider.
"""

from __future__ import annotations

from typing import Any, Optional

import price_action_service as pa
from technical_score_service import score_technicals
from technical_service import calculate_technicals

# Bars needed before any of this is meaningful. A 200-day EMA on sixty bars is
# arithmetic, not information, but sixty is enough for the rest of the set.
MIN_BARS = 60

# How the two halves combine into the single 0-100 reading.
#
# Indicators lead because they describe the trend that is in force; price
# action refines it with structure, participation and relative strength --
# the questions the indicators cannot answer. Neither alone was sufficient,
# which is how the product ended up with two disagreeing formulas.
TECHNICAL_WEIGHT = 0.60
PRICE_ACTION_WEIGHT = 0.40


def _rating(display: float) -> str:
    """Label for a -100..100 reading."""
    if display >= 60:
        return "Strong Buy"
    if display >= 25:
        return "Buy"
    if display <= -60:
        return "Strong Sell"
    if display <= -25:
        return "Sell"
    return "Watch"


def _normalise(score: Optional[float], maximum: float) -> Optional[float]:
    """A signed score on +/-maximum onto a 0-100 scale."""
    if score is None:
        return None
    return max(0.0, min(100.0, (score + maximum) / (2 * maximum) * 100))


def evaluate(bars: list[dict],
             benchmark: Optional[list[dict]] = None) -> Optional[dict]:
    """
    Score one bar window.

    ``bars`` must end at the decision date and contain nothing after it. The
    caller owns that slice; nothing here looks beyond the last element.
    """
    if not bars or len(bars) < MIN_BARS:
        return None

    technicals = calculate_technicals(bars)
    if not technicals:
        return None

    technical = score_technicals(technicals)
    analysis = pa.analyse(bars, benchmark)
    action = pa.score_price_action(analysis)

    technical_pct = _normalise(technical.get("score"),
                               technical.get("max_score") or 20)
    action_pct = _normalise(action.get("score"), action.get("max_score") or 10)

    parts: list[tuple[str, float, float]] = []
    if technical_pct is not None:
        parts.append(("Indicators", technical_pct, TECHNICAL_WEIGHT))
    # Price action only contributes when it has something to say. Scoring an
    # undefined structure as neutral would drag every reading toward the
    # middle on symbols with too little history.
    if action_pct is not None and action.get("bias") != "NO_DATA":
        parts.append(("Price action", action_pct, PRICE_ACTION_WEIGHT))

    if not parts:
        return None

    weight = sum(w for _, _, w in parts)
    score = sum(v * w for _, v, w in parts) / weight
    display = round((score - 50) * 2)

    return {
        "score": round(score, 1),
        "display_score": display,
        "rating": _rating(display),

        # The two halves, kept separate so a caller can show which one is
        # driving the reading rather than only the blend.
        "technical_score": technical.get("score"),
        "technical_bias": technical.get("bias"),
        "price_action_score": action.get("score"),
        "price_action_bias": action.get("bias"),

        "rsi": (round(technicals["rsi_14"], 1)
                if technicals.get("rsi_14") is not None else None),
        "ema20": (round(technicals["ema_20"], 2)
                  if technicals.get("ema_20") else None),
        "ema50": (round(technicals["ema_50"], 2)
                  if technicals.get("ema_50") else None),
        "ema200": (round(technicals["ema_200"], 2)
                   if technicals.get("ema_200") else None),

        "structure": (analysis.get("structure") or {}).get("structure"),
        "volume_label": (analysis.get("volume") or {}).get("label"),
        "breakout": (analysis.get("breakout") or {}).get("state"),
        "relative_strength_pct": (
            (analysis.get("relative_strength") or {}).get("spread_pct")),

        "reasons": (technical.get("reasons") or [])[:3]
                   + (action.get("reasons") or [])[:3],
        "components": [
            {"name": n, "score": round(v, 1), "weight": round(w / weight * 100)}
            for n, v, w in parts
        ],
        "confidence_inputs": pa.confidence_inputs(
            analysis, technicals.get("atr_14")),
        "basis": "TECHNICAL_COMPOSITE",
    }


def score_only(bars: list[dict],
               benchmark: Optional[list[dict]] = None) -> Optional[float]:
    """The 0-100 reading alone, for callers that need nothing else."""
    result = evaluate(bars, benchmark)
    return result["score"] if result else None
