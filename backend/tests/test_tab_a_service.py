"""Tab A -- pre-earnings direction v2, all-positive 0-100 scale."""

import tab_a_service as ta

_NAMES = ["disparity", "options_positioning", "unusual_options", "price_action",
          "short_insider", "peer_readthrough", "eps_estimates",
          "historical_reaction", "revenue_estimates", "guidance", "dark_pool"]


def _eq(bias):
    return {"params": [{"name": n, "bias": bias} for n in _NAMES]}


def test_all_bullish_is_100_buy():
    r = ta.score_one("A", _eq(1.0), {"date": "2026-10-20"})
    assert r["score"] == 100.0 and r["signal"] == "BUY"


def test_no_signal_is_50_neutral():
    r = ta.score_one("B", {"params": []}, {})
    assert r["score"] == 50.0 and r["signal"] == "NEUTRAL"


def test_all_bearish_is_0_sell():
    r = ta.score_one("C", _eq(-1.0), {"date": "2026-10-20"})
    assert r["score"] == 0.0 and r["signal"] == "SELL"


def test_rows_sum_to_score():
    r = ta.score_one("D", _eq(0.3), {"date": "2026-10-20"})
    assert round(sum(p["points"] for p in r["params"]), 1) == r["score"]


def test_dark_pool_stays_neutral_without_confirmation():
    # Dark pool bullish but options/price absent -> stays at half its weight.
    r = ta.score_one("E", {"params": [{"name": "dark_pool", "bias": 1.0}]}, {})
    dp = next(p for p in r["params"] if p["name"] == "dark_pool")
    assert dp["confirmed"] is False
    # Half of the normalized 4-pt weight (4 * 100/90 / 2 ~= 2.22).
    assert abs(dp["points"] - 2.22) < 0.05
