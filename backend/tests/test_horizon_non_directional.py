"""
A sizing parameter must not vote on direction, whichever outlook is asked.

The short outlooks reuse the swing model's readings, and that reuse forced
every parameter to directional -- so implied volatility, which the app calls
"sizing only" on every screen, quietly scored -1.1 of 2 on the today outlook.
A reader comparing the two panels would have found the same parameter voting
in one and abstaining in the other.
"""

import directional_model as dm
import horizon_model as hm


def _base(**biases):
    return {"status": "OK", "signals": [
        {"name": name, "bias": bias, "available": bias is not None,
         "label": name.replace("_", " ").title(), "detail": "", "evidence": {},
         "source": "test"}
        for name, bias in biases.items()]}


def test_implied_volatility_never_votes_on_a_short_outlook():
    signal = hm._reuse(_base(implied_volatility=-0.8), "implied_volatility", 2)
    assert signal.available is True
    assert signal.directional is False
    assert signal.points == 0.0, "a sizing parameter scored direction"


def test_a_directional_parameter_still_votes():
    signal = hm._reuse(_base(options_flow=0.5), "options_flow", 10)
    assert signal.directional is True
    assert signal.points == 5.0


def test_every_outlook_agrees_with_the_model_about_what_is_directional():
    for name, weights in hm.WEIGHTS.items():
        for parameter in weights:
            if parameter in dm.NON_DIRECTIONAL:
                signal = hm._reuse(_base(**{parameter: 1.0}), parameter,
                                   weights[parameter])
                assert signal.directional is False, (name, parameter)
