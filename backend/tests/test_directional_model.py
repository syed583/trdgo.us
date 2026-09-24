"""
The directional model's arithmetic.

Three properties decide whether this model is honest. Volatility must never
vote on direction. Missing data must lower confidence rather than drag the
score toward neutral. And a thin reading must be distinguishable from a
balanced one, because "nothing is known" and "everything cancels out" look
identical in a single number and mean opposite things.
"""

from __future__ import annotations

import directional_model as dm


def _signal(name, bias, detail=""):
    return dm.Signal(name, bias, detail)


# --------------------------------------------------------------------------
# the weight table
# --------------------------------------------------------------------------


def test_weights_total_one_hundred():
    assert dm.TOTAL == 100


def test_volatility_parameters_carry_weight_but_never_vote():
    """
    Implied volatility and the expected move describe magnitude, not
    direction. Both hold real weight in the framework and both must still
    contribute nothing to the directional total -- they size a trade and
    never point it.

    Dealer gamma used to sit beside them and was dropped: it needed the live
    chain and went missing whenever TWS was down.
    """
    for gone in ("gamma_exposure",):
        assert gone not in dm.WEIGHTS, gone
    for name in ("implied_volatility", "expected_move"):
        assert dm.WEIGHTS[name] > 0
        assert dm.Signal(name, 1.0).points == 0.0
        assert dm.Signal(name, -1.0).points == 0.0


def test_insider_split_fits_its_bucket():
    assert sum(dm.INSIDER_SPLIT.values()) == dm.WEIGHTS["insider_activity"]


def test_the_stalest_input_is_the_smallest():
    """A 13D/13G lands five days after the trade; a Form 4 two."""
    assert dm.INSIDER_SPLIT["ownership_13dg"] < dm.INSIDER_SPLIT["form4"]
    # Institutional holdings left this bucket entirely: they are their own
    # parameter now, and at five points rather than one.
    assert "institutional_13f" not in dm.INSIDER_SPLIT
    assert dm.WEIGHTS["fund_flows"] > dm.INSIDER_SPLIT["ownership_13dg"]


# --------------------------------------------------------------------------
# direction
# --------------------------------------------------------------------------


def test_unanimous_bullish_signals_score_high():
    signals = [_signal(n, 1.0) for n in
               ("unusual_activity", "options_flow", "ema_trend", "rsi")]

    result = dm.score_direction(signals)

    assert result["direction_score"] == 100.0
    # "lean" is the direction; "decision" additionally asks whether the
    # reading is solid enough to act on, which four signals is not.
    assert result["lean"] == "STRONG BUY"


def test_unanimous_bearish_signals_score_low():
    signals = [_signal(n, -1.0) for n in
               ("unusual_activity", "options_flow", "ema_trend", "rsi")]

    result = dm.score_direction(signals)

    assert result["direction_score"] == 0.0
    assert result["lean"] == "STRONG SELL"


def test_opposing_signals_land_neutral():
    signals = [_signal("options_flow", 1.0), _signal("unusual_activity", -1.0)]

    result = dm.score_direction(signals)

    assert 40 <= result["direction_score"] <= 60
    assert result["lean"] == "WAIT"


def test_a_heavier_parameter_outweighs_a_lighter_one():
    """Unusual activity is 11 points; OI positioning is 2."""
    signals = [_signal("unusual_activity", 1.0), _signal("oi_positioning", -1.0)]

    assert dm.score_direction(signals)["direction_score"] > 50


def test_the_neutral_band_is_wide_enough_to_say_wait():
    """
    A model that says BUY or SELL every day is reading its thresholds, not
    the market. A marginal score has to resolve to WAIT.
    """
    assert dm._decision(52) == "WAIT"
    assert dm._decision(45) == "WAIT"
    assert dm._decision(58) == "BUY"
    assert dm._decision(42) == "SELL"


# --------------------------------------------------------------------------
# missing data
# --------------------------------------------------------------------------


def test_absent_parameters_do_not_drag_the_score_toward_neutral():
    """
    Scoring what is missing as neutral would make a stock with one bullish
    signal and twelve gaps look balanced. It is not balanced; it is thin.
    """
    thin = dm.score_direction([
        _signal("unusual_activity", 1.0),
        _signal("options_flow", None),
        _signal("ema_trend", None),
    ])

    assert thin["direction_score"] == 100.0     # what arrived was unanimous
    assert thin["confidence"] < 70              # but almost nothing arrived


def test_missing_data_shows_up_as_low_coverage():
    thin = dm.score_direction([_signal("oi_positioning", 1.0)])
    full = dm.score_direction(
        [_signal(n, 1.0) for n in dm.WEIGHTS if n not in dm.NON_DIRECTIONAL])

    assert thin["coverage_pct"] < 5
    assert full["coverage_pct"] == 100.0
    assert full["confidence"] > thin["confidence"]


def test_nothing_available_is_reported_not_guessed():
    result = dm.score_direction([_signal("options_flow", None)])

    assert result["direction_score"] is None
    assert result["lean"] == "NO_DATA"
    assert result["decision"] == dm.NO_TRADE
    assert result["confidence"] == 0


# --------------------------------------------------------------------------
# confidence
# --------------------------------------------------------------------------


def test_agreement_raises_confidence_more_than_a_split_does():
    """
    Ten parameters split five against five can produce the same score as one
    lone signal. The difference is conviction, and it belongs in confidence.
    """
    agreed = dm.score_direction([
        _signal("unusual_activity", 0.8), _signal("options_flow", 0.8),
        _signal("ema_trend", 0.8), _signal("rsi", 0.8)])
    split = dm.score_direction([
        _signal("unusual_activity", 1.0), _signal("options_flow", -1.0),
        _signal("ema_trend", 1.0), _signal("rsi", -1.0)])

    assert agreed["agreement_pct"] == 100.0
    assert split["agreement_pct"] < 100.0
    assert agreed["confidence"] > split["confidence"]


def test_a_coin_toss_reading_reports_low_conviction():
    result = dm.score_direction([
        _signal("options_flow", 0.02), _signal("ema_trend", -0.02)])

    assert result["conviction_pct"] < 10
    assert result["lean"] == "WAIT"


def test_volatility_inputs_are_reported_separately():
    signals = [
        _signal("options_flow", 1.0),
        _signal("implied_volatility", 0.9, "IV rank 82: options are expensive"),
    ]

    context = dm.volatility_context(signals)

    assert context["weight"] == dm.WEIGHTS["implied_volatility"]
    assert len(context["notes"]) == 1
    # And none of it moved the direction.
    assert dm.score_direction(signals)["direction_score"] == 100.0


# --------------------------------------------------------------------------
# bounds
# --------------------------------------------------------------------------


def test_bias_beyond_the_range_is_clamped():
    assert dm.Signal("options_flow", 5.0).bias == 1.0
    assert dm.Signal("options_flow", -5.0).bias == -1.0


def test_score_stays_within_zero_and_one_hundred():
    for bias in (-1.0, -0.5, 0.0, 0.5, 1.0):
        result = dm.score_direction(
            [_signal(n, bias) for n in ("unusual_activity", "options_flow")])
        assert 0.0 <= result["direction_score"] <= 100.0
        assert 0.0 <= result["confidence"] <= 100.0


# --------------------------------------------------------------------------
# the do-not-trade gate
# --------------------------------------------------------------------------


def test_a_split_reading_is_not_tradeable():
    """
    Signals contradicting each other must not surface as BUY. A label carries
    more authority than the number beside it, so a coin flip shown as a
    decision is worse than showing nothing.
    """
    result = dm.score_direction([
        _signal("unusual_activity", 1.0), _signal("options_flow", -1.0),
        _signal("ema_trend", 1.0), _signal("rsi", -1.0)])

    assert result["decision"] == dm.NO_TRADE
    assert result["actionable"] is False
    assert any("disagree" in r for r in result["blocked_reasons"])


def test_a_thin_reading_is_not_tradeable_even_when_unanimous():
    """Three parameters agreeing is not the model agreeing."""
    result = dm.score_direction([_signal("oi_positioning", 1.0)])

    assert result["decision"] == dm.NO_TRADE
    assert result["actionable"] is False
    assert any("returned data" in r for r in result["blocked_reasons"])


def test_a_strong_broad_reading_is_tradeable():
    signals = [_signal(n, 0.9) for n in dm.WEIGHTS
               if n not in dm.NON_DIRECTIONAL]

    result = dm.score_direction(signals)

    assert result["actionable"] is True
    assert result["decision"] == "STRONG BUY"
    assert result["blocked_reasons"] == []


def test_the_lean_is_still_reported_when_a_trade_is_withheld():
    """
    Blocking the call must not hide the reading. The direction is real
    information; only the instruction to act on it is withheld.
    """
    result = dm.score_direction([_signal("oi_positioning", 1.0)])

    assert result["decision"] == dm.NO_TRADE
    assert result["direction_score"] is not None
    assert result["lean"] in dm.DECISIONS


def test_no_data_is_never_tradeable():
    result = dm.score_direction([_signal("options_flow", None)])

    assert result["decision"] == dm.NO_TRADE
    assert result["actionable"] is False


def test_a_marginal_score_is_withheld_even_with_good_coverage():
    """
    Full coverage and agreement still cannot rescue a reading that sits on
    the neutral line -- 51 is not a weak buy, it is a coin toss.
    """
    signals = [_signal(n, 0.02) for n in dm.WEIGHTS
               if n not in dm.NON_DIRECTIONAL]

    result = dm.score_direction(signals)

    assert result["lean"] == "WAIT"
    assert result["decision"] == dm.NO_TRADE
