"""
The general US-stock directional model: weights, direction and confidence.

Deliberately separate from the earnings composite. That one answers "how does
this company look going into a report"; this one answers "which way is this
stock leaning right now", and mixing an earnings-specific signal into a
broad positioning read is how both end up meaning less.

Two outputs, not one
--------------------
Direction says which way the evidence points. Confidence says how much of the
evidence actually arrived and how strongly it agrees.

The separation is the point. Implied volatility, expected move, ATR and dealer
gamma describe how far and how violently price is likely to travel -- they
have no opinion about which way. Folding a volatility reading into a direction
score corrupts the number without anyone noticing, so those inputs shape
confidence and position sizing instead.

Missing data lowers confidence, never direction
-----------------------------------------------
A parameter that could not be computed contributes nothing to direction and
is subtracted from the confidence ceiling. A stock with three of thirteen
inputs available does not get a neutral score; it gets whatever those three
say, with confidence reported low enough to show the reading is thin.
"""

from __future__ import annotations

from typing import Any, Optional

# --------------------------------------------------------------------------
# weights
# --------------------------------------------------------------------------

# One table, so re-weighting the model is an edit here rather than a rewrite.
# Every value is a maximum contribution to the 100-point direction score.
WEIGHTS: dict[str, int] = {
    # Two lists, deliberately unequal.
    #
    # Eighty points read the market: the options tape, the trend, and how
    # much movement is being priced. Twenty read the company: who owns it,
    # what it earned, and what it filed. The split is a judgement about
    # freshness rather than importance -- an options print is minutes old and
    # a 13F is a quarter old, so the tape gets to move the score and the
    # filings get to colour it.
    #
    # Within each list, points measure how much *independent* evidence a
    # parameter adds. Disparity re-reads call-versus-put volume that options
    # flow and the put/call ratio already score, so it stays below both
    # however many readings it carries.

    # ---- LIST 1: market and price -- 80 -------------------------------
    # The options tape, 52 of them. Minutes old, and the only place the app
    # sees positioning being put on rather than inferred.
    "options_flow": 12,       # the only reading that knows which side crossed
    "unusual_activity": 11,   # contracts far above their own baseline
    "disparity": 9,           # thirteen imbalance readings, partly overlapping
    "volume_pcr": 7,
    "key_levels": 6,          # strikes where open interest slows price
    "daily_oi_change": 4,     # positions opened or closed overnight
    "flow_by_expiry": 2,
    "oi_positioning": 1,      # standing open interest, the slowest of these

    # Trend, 19. Three readings that overlap each other, which is why the two
    # behind the moving averages are small.
    "ema_trend": 10,
    "rsi": 5,
    "price_action": 4,

    # How far, never which way -- 9 points that shape confidence and sizing
    # and contribute nothing to direction.
    #
    # These two say the same thing in different units: implied volatility as
    # a percentage, the expected move as the dollar range that percentage
    # implies by expiry. Both are listed because both are read, and neither
    # votes on direction, so the overlap costs confidence arithmetic rather
    # than pointing the score anywhere.
    "implied_volatility": 6,
    "expected_move": 3,

    # ---- LIST 2: company and ownership -- 20 --------------------------
    # Who owns it, 9. Held down by age: a Form 4 is two days old, a 13F a
    # quarter.
    "insider_activity": 5,
    "fund_flows": 4,

    # What the company reported, 9. Rare events: mostly zero, and the reason
    # the stock moved on the day they land.
    "earnings_results": 4,
    "merger_activity": 3,
    "funding_activity": 2,

    # Slow signals, 2.
    "dividend_trend": 1,
    "event_radar": 1,

    # Management changes were dropped as a parameter. An 8-K says an officer
    # came or went, not who or why, so it never scored -- carrying a weight
    # it could not use made the model look like it read something it did not.
    # The filings still appear in the Company Events panel.
}

TOTAL = sum(WEIGHTS.values())

# Why each parameter carries the weight it does, in one sentence.
#
# The weights above are judgements, not measurements -- freshness against
# overlap -- and a judgement nobody can see is indistinguishable from an
# arbitrary number. Stated per parameter so the reason sits beside the score
# on screen rather than in a comment only a developer reads.
WEIGHT_REASONS: dict[str, str] = {
    "options_flow": (
        "The heaviest single parameter: minutes old, and the only reading "
        "that knows which side of the spread the money crossed on."),
    "unusual_activity": (
        "Same-day and market-wide -- contracts trading far above their own "
        "baseline, across 7,000 symbols rather than a watchlist."),
    "disparity": (
        "Thirteen readings of the options market at once, but held below "
        "flow and put/call because it partly re-reads what they already "
        "score. Breadth, not new evidence."),
    "volume_pcr": (
        "Same-day and independent of the flow tape: put volume against call "
        "volume says what today's traders actually did."),
    "key_levels": (
        "Where open interest is heavy enough to slow price. Raised when "
        "dealer gamma was dropped, since it reads the same chain for the "
        "same question and is available far more often."),
    "daily_oi_change": (
        "Positions opened or closed overnight. It mostly confirms what the "
        "flow tape already showed, so it earns less than the tape."),
    "flow_by_expiry": (
        "Whether the bet is days or months out. Real information, but a "
        "detail of the flow rather than a reading of its own."),
    "oi_positioning": (
        "Standing open interest: the slowest of the options readings, and "
        "largely yesterday's story."),
    "ema_trend": (
        "The strongest single trend reading, and the one most independent "
        "of the options tape."),
    "rsi": "Overbought or oversold, but it overlaps the moving averages.",
    "price_action": (
        "Swing structure and strength against SPY. Kept small because both "
        "the trend and the session readings already cover much of it."),
    "expected_move": (
        "Three points: the dollar range the options market prices by expiry. "
        "It sizes a trade and never points it -- the same reading implied "
        "volatility gives in percent, which is why it sits below it."),
    "implied_volatility": (
        "Sizing only. How big a move is priced, never which way -- folding "
        "it into direction would make expensive options read as bullish."),
    "insider_activity": (
        "The freshest company reading there is: a Form 4 lands two days "
        "after an executive actually traded their own stock."),
    "fund_flows": (
        "Real institutional money, but a 13F is a quarter old before anyone "
        "can read it -- which is what holds it below insider activity."),
    "earnings_results": (
        "Hard evidence of what the company delivered, but it moves four "
        "times a year and is backward-looking by nature."),
    "merger_activity": (
        "A takeover bid is the single biggest thing a filing can say. It "
        "scores three because it is zero on almost every stock, almost "
        "always -- and when it is not, the parameter dominates its group."),
    "funding_activity": (
        "Dilution and covenant trouble hit holders directly, but like "
        "mergers, most companies file nothing of the sort in a quarter."),
    "dividend_trend": (
        "A cut is a board admitting something. Small because it moves "
        "slowly and is usually already in the price."),
    "event_radar": (
        "Analyst upgrades, downgrades and target changes -- thin coverage "
        "on most symbols, and often a reaction to news already scored."),
    # Session parameters, scored by the today and tomorrow outlooks.
    "vwap": (
        "Where price sits against the average price actually paid today: "
        "the session's own reference line."),
    "opening_range": (
        "The first half hour sets the day's frame; holding or losing it is "
        "the clearest intraday structure there is."),
    "intraday_trend": "The five-minute trend, once enough of the session exists to read one.",
    "relative_strength_day": "How the stock is doing against SPY today, which separates the stock from the market.",
    "relative_volume": "Whether today's move has volume behind it, matched against the same time of day.",
    "gap_hold": "Whether an opening gap is holding or filling.",
    "close_location": "Where it closed inside the day's range -- near the high is strength carried into tomorrow.",
    "after_hours": "The only thing that has happened since the close, which is why it leads the pre-market outlook.",
}

# Parameters that describe magnitude rather than direction. They are scored
# for confidence and sizing, and contribute zero to the directional total --
# a high IV rank is not bullish or bearish, it is just loud.
NON_DIRECTIONAL = {"implied_volatility", "expected_move"}

# The weight that can actually vote. Coverage is measured against this, so a
# parameter the caller never supplied counts as missing rather than as absent
# from the question.
DIRECTIONAL_WEIGHT = sum(
    w for name, w in WEIGHTS.items() if name not in NON_DIRECTIONAL)

# How the 9-point insider bucket divides. 13F is barely represented on
# purpose: it is a quarter old before anyone can read it.
# 13F left this bucket when fund_flows became its own parameter: it was
# one point inside a signal about insiders, which is a different thing
# from what institutions own.
INSIDER_SPLIT = {"form4": 4, "ownership_13dg": 1}

DECISIONS = ("STRONG SELL", "SELL", "WAIT", "BUY", "STRONG BUY")

# What the reading must clear before it is allowed to suggest a trade.
#
# A direction score is always computable from whatever arrived; that does not
# make it worth acting on. Three separate ways a reading can be untrustworthy,
# and any one of them is enough to withhold the call:
#
#   coverage  - too much of the model is missing to judge anything
#   agreement - the parameters that did arrive contradict each other
#   confidence - the combined reading is too weak to distinguish from noise
#
# Showing "BUY" on a fifty-fifty split is the single most harmful thing this
# panel could do, because a label carries more authority than the number
# beside it.
MIN_CONFIDENCE = 55.0
MIN_AGREEMENT = 58.0
MIN_COVERAGE = 50.0

NO_TRADE = "DO NOT TRADE"


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class Signal:
    """
    One parameter's contribution.

    ``bias`` runs -1..+1, where -1 is maximally bearish and +1 maximally
    bullish. Multiplying by the weight is what turns a reading into points,
    and keeping the two apart means a parameter's strength and its importance
    stay independently adjustable.
    """

    __slots__ = ("name", "label", "bias", "weight", "available", "detail",
                 "directional", "rule", "evidence", "source", "unavailable_reason")

    def __init__(self, name: str, bias: Optional[float],
                 detail: str = "", directional: Optional[bool] = None,
                 label: str = "", rule: str = "",
                 evidence: Optional[dict] = None, source: str = "",
                 unavailable_reason: str = ""):
        self.name = name
        self.label = label or name.replace("_", " ").title()
        self.weight = WEIGHTS.get(name, 0)
        self.available = bias is not None
        self.bias = _clamp(bias, -1.0, 1.0) if bias is not None else None
        self.detail = detail
        self.directional = (name not in NON_DIRECTIONAL
                            if directional is None else directional)
        # The three fields that make a score explainable rather than merely
        # visible: the rule that was applied, the raw numbers it was applied
        # to, and where those numbers came from. A score nobody can audit is
        # a number to be trusted on faith, which is the opposite of the point.
        self.rule = rule
        self.evidence = evidence or {}
        self.source = source
        self.unavailable_reason = unavailable_reason

    @property
    def points(self) -> float:
        """Signed contribution to the direction score."""
        if not self.available or not self.directional:
            return 0.0
        return self.bias * self.weight

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "weight": self.weight,
            "available": self.available,
            "bias": round(self.bias, 3) if self.bias is not None else None,
            "points": round(self.points, 2),
            # Points expressed against this parameter's own maximum, so a
            # reader can see "7 of 11" rather than a bare signed number.
            "points_label": (
                f"{self.points:+.1f} of {self.weight}" if self.available
                and self.directional else
                ("not directional" if self.available else "no data")),
            "directional": self.directional,
            "detail": self.detail,
            "rule": self.rule,
            # Why this parameter is worth what it is worth. The rule says how
            # the score was reached; this says why the score is allowed to
            # matter as much as it does, which is the other half of the
            # question a reader asks at a row saying "12 of 100".
            "weight_reason": WEIGHT_REASONS.get(self.name),
            "evidence": self.evidence,
            "source": self.source,
            "unavailable_reason": self.unavailable_reason,
            "leaning": (None if not self.available or self.bias is None
                        else "Bullish" if self.bias > 0.15
                        else "Bearish" if self.bias < -0.15 else "Neutral"),
        }


def _decision(score: float) -> str:
    """
    Map 0-100 onto a decision.

    The neutral band is wide on purpose. Most days, for most stocks, the
    honest answer is that the evidence does not point anywhere, and a model
    that says BUY or SELL every day is not reading the market -- it is
    reading its own thresholds.
    """
    if score >= 70:
        return "STRONG BUY"
    if score >= 58:
        return "BUY"
    if score <= 30:
        return "STRONG SELL"
    if score <= 42:
        return "SELL"
    return "WAIT"


def score_direction(signals: list[Signal]) -> dict:
    """
    Combine signals into a 0-100 direction score and a confidence reading.

    Direction is computed over the weight that actually arrived, not the full
    hundred. Scoring an absent parameter as neutral would drag every reading
    toward fifty and make a stock with no data look like a stock with
    perfectly balanced data -- two very different situations.
    """
    directional = [s for s in signals if s.directional]
    available = [s for s in directional if s.available]

    # Coverage is measured against the whole model, not against whatever the
    # caller happened to pass. Otherwise handing in one signal reports full
    # coverage, and a reading built on a single parameter claims the same
    # confidence as one built on all thirteen.
    possible = DIRECTIONAL_WEIGHT
    present = sum(s.weight for s in available)

    if not present:
        return {
            "direction_score": None,
            "decision": NO_TRADE,
            "lean": "NO_DATA",
            "actionable": False,
            "blocked_reasons": ["No directional parameter could be computed."],
            "confidence": 0,
            "coverage_pct": 0.0,
            "signals": [s.as_dict() for s in signals],
            "reasons": ["No directional parameter could be computed"],
        }

    # Net bias across what arrived, mapped from -1..+1 onto 0..100.
    net = sum(s.points for s in available) / present
    score = _clamp(50 + net * 50, 0.0, 100.0)

    coverage = present / possible * 100 if possible else 0.0

    # Agreement: how much of the available weight leans the same way as the
    # net. Ten parameters split five against five produce the same score as
    # one lone signal, and the difference belongs in confidence.
    if net > 0:
        agreeing = sum(s.weight for s in available if (s.bias or 0) > 0)
    elif net < 0:
        agreeing = sum(s.weight for s in available if (s.bias or 0) < 0)
    else:
        agreeing = 0
    agreement = agreeing / present * 100 if present else 0.0

    # Conviction: how far from neutral the reading actually is. A score of 51
    # is not a weak buy, it is a coin toss.
    conviction = abs(score - 50) * 2

    confidence = _clamp(
        coverage * 0.40 + agreement * 0.35 + conviction * 0.25, 0.0, 100.0)

    # The lean is still reported -- it is real information -- but whether it
    # is worth acting on is a separate question, answered here.
    lean = _decision(score)
    blocks: list[str] = []
    if coverage < MIN_COVERAGE:
        blocks.append(
            f"Only {coverage:.0f}% of the model returned data "
            f"(needs {MIN_COVERAGE:.0f}%); too much is missing to judge.")
    if agreement < MIN_AGREEMENT:
        blocks.append(
            f"The parameters disagree -- {agreement:.0f}% of the available "
            f"weight leans the same way (needs {MIN_AGREEMENT:.0f}%).")
    if confidence < MIN_CONFIDENCE:
        blocks.append(
            f"Confidence {confidence:.0f} is below the {MIN_CONFIDENCE:.0f} "
            f"threshold; the reading is not separable from noise.")
    if lean == "WAIT":
        blocks.append("The evidence does not point anywhere decisively.")

    actionable = not blocks
    decision = lean if actionable else NO_TRADE

    return {
        "direction_score": round(score, 1),
        "decision": decision,
        "lean": lean,
        "actionable": actionable,
        "blocked_reasons": blocks,
        "thresholds": {
            "min_confidence": MIN_CONFIDENCE,
            "min_agreement": MIN_AGREEMENT,
            "min_coverage": MIN_COVERAGE,
        },
        "confidence": round(confidence, 1),
        "coverage_pct": round(coverage, 1),
        "agreement_pct": round(agreement, 1),
        "conviction_pct": round(conviction, 1),
        "weight_available": present,
        "weight_possible": possible,
        "signals": [s.as_dict() for s in signals],
        "reasons": [s.detail for s in sorted(
            available, key=lambda s: abs(s.points), reverse=True)
            if s.detail][:6],
    }


def volatility_context(signals: list[Signal]) -> dict:
    """
    The non-directional parameters, reported rather than scored.

    These carry real weight in the framework but no directional vote: they
    tell you how big a position should be and whether to wait for a better
    entry, which is a different question from which way to lean.
    """
    context = [s for s in signals if not s.directional]
    return {
        "parameters": [s.as_dict() for s in context],
        "weight": sum(s.weight for s in context),
        "available": sum(1 for s in context if s.available),
        "notes": [s.detail for s in context if s.detail and s.available],
    }
