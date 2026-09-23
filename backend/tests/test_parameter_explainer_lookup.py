"""
A parameter on the screen must be a parameter the explainer can find.

The swing score holds most of them; the session readings -- VWAP, the opening
range, relative volume -- live only inside a short outlook. Looking in one
place and answering "the model has no reading for that parameter yet" for
every row from the other half of the model is the failure this catches, and
it shows up exactly when the market is open and the reader is asking.
"""

import directional_model as dm
import horizon_model as hm
import parameter_explainer as pe


def test_every_parameter_the_model_scores_is_explainable():
    names = set(dm.WEIGHTS)
    for weights in hm.WEIGHTS.values():
        names |= set(weights)
    names |= set(dm.INSIDER_SPLIT)

    missing = sorted(n for n in names if n not in pe.MEANING)
    assert not missing, f"no plain-words meaning for: {missing}"


def test_a_session_reading_is_looked_up_in_the_outlook_that_scores_it(monkeypatch):
    swing = {"signals": [{"name": "ema_trend", "label": "EMA Trend"}]}
    today = {"signals": [{"name": "vwap", "label": "Price vs VWAP",
                          "points_label": "-6.9 of 9"}]}
    asked = []

    import directional_score_service as ds
    monkeypatch.setattr(ds, "get_directional_score", lambda s: swing)

    def score_horizon(symbol, horizon, base=None):
        asked.append(horizon)
        return today if horizon == "TODAY" else {"signals": []}

    monkeypatch.setattr(hm, "score_horizon", score_horizon)

    found = pe._signal("NVDA", "vwap", "TODAY")
    assert found and found["points_label"] == "-6.9 of 9"
    assert asked == ["TODAY"], "should not score outlooks it does not need"


def test_a_swing_parameter_never_costs_an_outlook_run(monkeypatch):
    import directional_score_service as ds
    monkeypatch.setattr(ds, "get_directional_score",
                        lambda s: {"signals": [{"name": "rsi", "label": "RSI"}]})

    def forbidden(*a, **k):
        raise AssertionError("the swing score already had this parameter")

    monkeypatch.setattr(hm, "score_horizon", forbidden)
    assert pe._signal("NVDA", "rsi")["label"] == "RSI"


def test_an_unknown_parameter_still_reports_no_reading(monkeypatch):
    import directional_score_service as ds
    monkeypatch.setattr(ds, "get_directional_score", lambda s: {"signals": []})
    assert pe._signal("NVDA", "not_a_parameter") is None
