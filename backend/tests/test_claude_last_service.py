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


def test_short_interest_is_two_sided_with_price():
    # High short interest + rising price = squeeze (bullish); + falling = bearish.
    hi = {"pct_float": 0.30, "days_to_cover": 8}
    assert v3._short_interest_bias(hi, 0.8) > 0      # price up -> squeeze
    assert v3._short_interest_bias(hi, -0.8) < 0     # price down -> short pressure
    assert v3._short_interest_bias(hi, 0.0) == 0.0   # flat price -> wait
    assert v3._short_interest_bias({"pct_float": 0.02}, 0.8) == 0.0  # low short -> no signal
    assert v3._short_interest_bias(None, 0.8) is None


def test_v31_has_ten_parameters_including_separate_skew_and_short():
    res = v3.score_one("X", _eq({"disparity": 0.5}), None, {})
    names = [p["name"] for p in res["params"]]
    assert len(names) == 10
    for required in ("skew_disparity", "options_flow_unusual", "insider", "short_interest"):
        assert required in names


def test_insider_uses_pure_signal_when_present():
    # Pure insider_activity signal should feed the insider param, not short_insider.
    eq = _eq({"short_insider": -1.0})
    eq["signals"] = {"insider_activity": 1.0}
    res = v3.score_one("X", eq, None, {})
    ins = next(p for p in res["params"] if p["name"] == "insider")
    assert ins["bias"] == 1.0


def test_dark_pool_only_counts_when_it_agrees():
    # Dark pool bullish but price action bearish -> pinned to half weight.
    biases = {"price_action": -0.8, "dark_pool": 0.8}
    res = v3.score_one("DP", _eq(biases), None, {})
    dp = next(p for p in res["params"] if p["name"] == "dark_pool")
    assert dp["confirmed"] is False
    assert abs(dp["points"] - dp["max"] * 0.5) < 0.01
