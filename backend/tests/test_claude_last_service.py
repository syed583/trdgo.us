"""Claude Last (Direction v3) scoring: all-positive, two-sided, SELL-capable."""
import claude_last_service as v3


def _eq(biases: dict) -> dict:
    return {"params": [{"name": k, "bias": v} for k, v in biases.items()]}


def test_all_neutral_lands_at_50():
    # No biases at all -> every parameter at half weight -> exactly 50.
    res = v3.score_one("TEST", _eq({}), None, {})
    assert abs(res["score"] - 50.0) < 0.6
    assert res["signal"] == "NEUTRAL"


def test_strong_bullish_is_buy():
    biases = {k: 1.0 for k in ["price_action", "disparity", "options_positioning",
                               "unusual_options", "eps_estimates", "short_insider",
                               "peer_readthrough", "guidance", "dark_pool"]}
    res = v3.score_one("BULL", _eq(biases), {"historical_moves": [{"move_pct": 5}, {"move_pct": 3}]}, {})
    assert res["score"] >= 55
    assert res["signal"] == "BUY"


def test_strong_bearish_is_sell():
    # The model MUST be able to say SELL -- the whole point of v3.
    biases = {k: -1.0 for k in ["price_action", "disparity", "options_positioning",
                                "unusual_options", "eps_estimates", "short_insider",
                                "peer_readthrough", "guidance", "dark_pool"]}
    res = v3.score_one("BEAR", _eq(biases), {"historical_moves": [{"move_pct": -5}, {"move_pct": -3}]}, {})
    assert res["score"] <= 45
    assert res["signal"] == "SELL"


def test_cutoffs_are_55_45():
    import tab_settings as cfg
    assert cfg.V3_CUTOFFS["buy"] == 55.0
    assert cfg.V3_CUTOFFS["sell"] == 45.0


def test_weights_sum_to_100():
    import tab_settings as cfg
    assert sum(cfg.V3_WEIGHTS.values()) == 100


def test_hist_reaction_bias_direction():
    # 3 up, 1 down -> positive bias; all down -> -1.
    assert v3._hist_reaction_bias([{"move_pct": 2}, {"move_pct": 1},
                                   {"move_pct": 3}, {"move_pct": -1}]) > 0
    assert v3._hist_reaction_bias([{"move_pct": -2}, {"move_pct": -1}]) == -1.0
    assert v3._hist_reaction_bias([]) is None


def test_low_confidence_flag_on_thin_data():
    # Only one small parameter has data -> coverage far below 50%.
    res = v3.score_one("THIN", _eq({"guidance": 0.5}), None, {})
    assert res["low_confidence"] is True


def test_dark_pool_only_counts_when_it_agrees():
    # Dark pool bullish but price action bearish -> pinned to half weight.
    biases = {"price_action": -0.8, "dark_pool": 0.8}
    res = v3.score_one("DP", _eq(biases), None, {})
    dp = next(p for p in res["params"] if p["name"] == "dark_pool")
    assert dp["confirmed"] is False
    assert abs(dp["points"] - dp["max"] * 0.5) < 0.01
