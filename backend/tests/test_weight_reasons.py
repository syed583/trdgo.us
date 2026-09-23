"""
Every weight has to say why it is that size.

The weights are judgements -- freshness against overlap -- and a judgement
nobody can see is indistinguishable from an arbitrary number. A parameter
that reaches the screen carrying "12 of 100" and no reason is asking to be
trusted rather than read, so this fails the build instead.
"""

import directional_model as dm
import horizon_model as hm


def test_every_scored_parameter_explains_its_weight():
    names = set(dm.WEIGHTS)
    for weights in hm.WEIGHTS.values():
        names |= set(weights)

    missing = sorted(n for n in names if not dm.WEIGHT_REASONS.get(n))
    assert not missing, f"no reason given for: {missing}"


def test_the_reason_travels_with_the_signal():
    signal = dm.Signal("options_flow", 0.5, detail="x")
    payload = signal.as_dict()
    assert payload["weight_reason"]
    assert "which side" in payload["weight_reason"]


def test_a_sizing_parameter_says_it_is_sizing():
    reason = dm.WEIGHT_REASONS["implied_volatility"]
    assert "sizing" in reason.lower()
    assert "never which way" in reason.lower()


def test_no_reason_is_left_pointing_at_a_parameter_that_no_longer_exists():
    names = set(dm.WEIGHTS)
    for weights in hm.WEIGHTS.values():
        names |= set(weights)
    stale = sorted(set(dm.WEIGHT_REASONS) - names)
    assert not stale, f"reasons for parameters the model dropped: {stale}"
