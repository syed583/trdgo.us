"""Pre-Earnings Direction (v2): all-positive mapping, 65/35 signal, dark-pool rule."""

import pre_earnings_direction_service as ped


def _eq(biases: dict, signals: dict | None = None):
    return {"params": [{"name": k, "bias": v} for k, v in biases.items()],
            "signals": signals or {}}


def test_neutral_everywhere_scores_50(monkeypatch):
    monkeypatch.setattr(ped, "_rel_strength_bias", lambda spark: None)
    r = ped.score_one("AAA", _eq({}), trade={}, row={})
    assert r["score"] == 50.0 and r["signal"] == "NEUTRAL"
    # Every parameter sits at exactly half its weight.
    assert all(abs(p["points"] - p["max"] * 0.5) < 1e-9 for p in r["params"])


def test_all_bullish_scores_100_buy(monkeypatch):
    monkeypatch.setattr(ped, "_rel_strength_bias", lambda spark: 1.0)
    biases = {"disparity": 1, "options_positioning": 1, "unusual_options": 1,
              "price_action": 1, "peer_readthrough": 1, "eps_estimates": 1,
              "historical_reaction": 1, "revenue_estimates": 1, "guidance": 1,
              "dark_pool": 1}
    r = ped.score_one("BULL", _eq(biases, {"insider_activity": 1}), trade={}, row={})
    assert r["score"] == 100.0 and r["signal"] == "BUY"


def test_all_bearish_scores_0_sell(monkeypatch):
    monkeypatch.setattr(ped, "_rel_strength_bias", lambda spark: -1.0)
    biases = {"disparity": -1, "options_positioning": -1, "unusual_options": -1,
              "price_action": -1, "peer_readthrough": -1, "eps_estimates": -1,
              "historical_reaction": -1, "revenue_estimates": -1, "guidance": -1,
              "dark_pool": -1}
    r = ped.score_one("BEAR", _eq(biases, {"insider_activity": -1}), trade={}, row={})
    assert r["score"] == 0.0 and r["signal"] == "SELL"


def test_missing_data_is_half_not_zero(monkeypatch):
    monkeypatch.setattr(ped, "_rel_strength_bias", lambda spark: None)
    # Only EPS is bullish; everything else missing -> above 50, never dragged down.
    r = ped.score_one("AAA", _eq({"eps_estimates": 1}), trade={}, row={})
    assert r["score"] > 50.0
    eps = next(p for p in r["params"] if p["name"] == "eps_rev")
    assert eps["points"] == eps["max"]            # full weight when bias +1


def test_options_subsplit_sums_to_16(monkeypatch):
    monkeypatch.setattr(ped, "_rel_strength_bias", lambda spark: None)
    r = ped.score_one("AAA", _eq({"disparity": 1, "options_positioning": 0, "unusual_options": -1}),
                       trade={}, row={})
    opt = next(p for p in r["params"] if p["name"] == "options")
    assert opt["max"] == 16
    assert abs(sum(s["points"] for s in opt["sub"]) - opt["points"]) < 1e-9


def test_dark_pool_neutral_unless_confirmed(monkeypatch):
    monkeypatch.setattr(ped, "_rel_strength_bias", lambda spark: None)
    # Dark pool bullish but options & price action neutral -> stays at half (2.0).
    r = ped.score_one("AAA", _eq({"dark_pool": 1}), trade={}, row={})
    dp = next(p for p in r["params"] if p["name"] == "dark_pool")
    assert dp["points"] == 2.0 and dp["confirmed"] is False
    # Dark pool bullish AND options bullish -> confirmed, moves to full weight.
    r2 = ped.score_one("BBB", _eq({"dark_pool": 1, "options_positioning": 1}), trade={}, row={})
    dp2 = next(p for p in r2["params"] if p["name"] == "dark_pool")
    assert dp2["confirmed"] is True and dp2["points"] == 4.0


def test_score_is_rescaled_row_total(monkeypatch):
    monkeypatch.setattr(ped, "_rel_strength_bias", lambda spark: 0.3)
    r = ped.score_one("AAA", _eq({"disparity": 0.5, "price_action": -0.4, "guidance": 0.2}),
                      trade={}, row={})
    raw = round(sum(p["points"] for p in r["params"]), 2)
    assert r["raw_total"] == raw and r["max_total"] == 90
    assert abs(r["score"] - round(raw / 90 * 100, 1)) < 1e-9
