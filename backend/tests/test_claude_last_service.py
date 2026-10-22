"""Claude — Pre-Earnings Direction v2 scoring: 12 params, BUY>70 / SELL<50."""
import claude_last_service as v3


def _eq(biases: dict, signals: dict | None = None) -> dict:
    d = {"params": [{"name": k, "bias": v} for k, v in biases.items()]}
    if signals:
        d["signals"] = signals
    return d


# Equity-bias keys that drive the v2 parameters.
_BULL = {"price_action": 1.0, "options_positioning": 1.0, "unusual_options": 1.0,
         "disparity": 1.0, "eps_estimates": 1.0, "revenue_estimates": 1.0,
         "sector_market": 1.0, "peer_readthrough": 1.0, "short_insider": 1.0,
         "dark_pool": 1.0}
_BEAR = {k: -1.0 for k in _BULL}


def test_all_neutral_lands_at_50():
    # Real (zero) readings -> exactly neutral. Parameters with no data are excluded,
    # not defaulted to neutral, so an empty analysis cannot be scored at all.
    res = v3.score_one("TEST", _eq({k: 0.0 for k in _BULL}, {"funding_activity": 0.0}), None, {})
    assert abs(res["score"] - 50.0) < 0.6
    assert res["signal"] == "NEUTRAL"
    assert res["coverage"] < 100                      # hist_reaction etc. had no data


def test_no_data_parameters_are_excluded_and_rescaled():
    res = v3.score_one("X", _eq({"eps_estimates": 1.0}), None, {})
    eps = next(p for p in res["params"] if p["name"] == "eps_estimates")
    assert res["score"] == 100.0 and eps["max"] == 100.0       # only EPS present -> rescaled to 100
    assert next(p for p in res["params"] if p["name"] == "revenue_estimates")["points"] is None
    assert abs(res["coverage"] - 15.3) < 0.1                   # 15 of 98 weight (timing is label-only)
    assert res["signal"] == "LOW_DATA" and res["tier"] == "Low data"   # coverage < 70%


def test_empty_analysis_raises_instead_of_scoring_neutral():
    import pytest
    with pytest.raises(ValueError):
        v3.score_one("X", _eq({}), None, {})


def test_calendar_timing_is_label_only():
    row = {"report_time": "premarket", "date": "2026-10-21"}
    res = v3.score_one("T", _eq(_BULL, {"funding_activity": 1.0}), None, row)
    t = next(p for p in res["params"] if p["name"] == "calendar_timing")
    assert t["label_only"] is True and t["points"] is None and t["timing_label"] == "BMO Wed"
    base = v3.score_one("T", _eq(_BULL, {"funding_activity": 1.0}), None, {})
    assert res["score"] == base["score"] and res["coverage"] == base["coverage"]   # no effect on score


def test_low_data_below_minimum_coverage():
    import claude_last_service as c
    assert c.low_data(69.9) and not c.low_data(70.0)


def test_strong_bullish_is_buy():
    res = v3.score_one("BULL", _eq(_BULL, {"funding_activity": 1.0}),
                       {"historical_moves": [{"move_pct": 5}, {"move_pct": 3}]}, {})
    assert res["score"] > 70
    assert res["signal"] in ("BUY", "NEUTRAL")   # NEUTRAL only if the IM gate blocks
    assert res["raw_signal"] == "BUY"


def test_strong_bearish_is_sell():
    res = v3.score_one("BEAR", _eq(_BEAR, {"funding_activity": -1.0}),
                       {"historical_moves": [{"move_pct": -5}, {"move_pct": -3}]}, {})
    assert res["score"] < 50
    assert res["raw_signal"] == "SELL"


def test_claude_cutoffs_are_70_50():
    import tab_settings as cfg
    assert cfg.CLAUDE_CUTOFFS["buy"] == 70.0
    assert cfg.CLAUDE_CUTOFFS["sell"] == 50.0


def test_weights_sum_to_100_and_twelve_params():
    import tab_settings as cfg
    assert sum(cfg.V3_WEIGHTS.values()) == 100
    assert len(cfg.V3_WEIGHTS) == 12


def test_twelve_parameters_v2():
    res = v3.score_one("X", _eq({"disparity": 0.5}), None, {})
    names = [p["name"] for p in res["params"]]
    assert len(names) == 12
    for required in ("options_combined", "hist_reaction", "big_fund",
                     "short_insider", "calendar_timing"):
        assert required in names


def test_hist_reaction_bias_direction():
    assert v3._hist_reaction_bias([{"move_pct": 2}, {"move_pct": 1},
                                   {"move_pct": 3}, {"move_pct": -1}]) > 0
    assert v3._hist_reaction_bias([{"move_pct": -2}, {"move_pct": -1}]) == -1.0
    assert v3._hist_reaction_bias([]) is None


def test_low_confidence_flag_on_thin_data():
    res = v3.score_one("THIN", _eq({"eps_estimates": 0.5}), None, {})
    assert res["low_confidence"] is True


def test_short_interest_is_two_sided_with_price():
    hi = {"pct_float": 0.30, "days_to_cover": 8}
    assert v3._short_interest_bias(hi, 0.8) > 0
    assert v3._short_interest_bias(hi, -0.8) < 0
    assert v3._short_interest_bias(hi, 0.0) == 0.0
    assert v3._short_interest_bias({"pct_float": 0.02}, 0.8) == 0.0
    assert v3._short_interest_bias(None, 0.8) is None


def test_dark_pool_only_counts_when_it_agrees():
    biases = {"price_action": -0.8, "dark_pool": 0.8}
    res = v3.score_one("DP", _eq(biases), None, {})
    dp = next(p for p in res["params"] if p["name"] == "dark_pool")
    assert dp["confirmed"] is False
    assert abs(dp["points"] - dp["max"] * 0.5) < 0.01
