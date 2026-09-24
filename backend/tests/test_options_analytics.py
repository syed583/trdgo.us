"""
Aggregation tests over a synthetic option chain. No IBKR connection.

The chain below is deliberately asymmetric so each aggregation has a single
unambiguous right answer.
"""

import pytest

import live_options_analytics as agg


def row(strike, right, volume=0.0, oi=0.0, mid=1.0, delta=None, iv=0.30):
    return {
        "con_id": int(strike * 10 + (1 if right == "C" else 2)),
        "symbol": "TEST",
        "expiry": "20260918",
        "expiry_label": "09/18/26",
        "dte": 8.0,
        "strike": float(strike),
        "right": right,
        "bid": mid - 0.05,
        "ask": mid + 0.05,
        "last": mid,
        "close": mid,
        "mid": mid,
        "volume": volume,
        "open_interest": oi,
        "iv": iv,
        "delta": delta,
        "gamma": 0.01,
        "vega": 0.1,
        "theta": -0.05,
        "rho": 0.01,
        "notional": volume * mid * 100,
        "oi_notional": oi * mid * 100,
    }


@pytest.fixture
def chain():
    return {
        "symbol": "TEST",
        "spot": 100.0,
        "expiry": "20260918",
        "expiry_label": "09/18/26",
        "dte": 8.0,
        "multiplier": 100,
        "rows": [
            row(95, "C", volume=100, oi=1000, mid=6.0, delta=0.80, iv=0.34),
            row(95, "P", volume=50, oi=400, mid=1.0, delta=-0.20, iv=0.32),
            row(100, "C", volume=300, oi=2000, mid=3.0, delta=0.52, iv=0.30),
            row(100, "P", volume=100, oi=1500, mid=2.5, delta=-0.48, iv=0.31),
            row(105, "C", volume=200, oi=5000, mid=1.0, delta=0.25, iv=0.28),
            row(105, "P", volume=25, oi=300, mid=5.5, delta=-0.75, iv=0.36),
            row(90, "P", volume=75, oi=3000, mid=0.5, delta=-0.10, iv=0.38),
        ],
        "other_expiry_rows": [],
        "status": "OK",
    }


# -------------------------------------------------------------------- tiles


def test_tiles_sum_volume_and_open_interest_by_right(chain):
    t = agg.get_tiles(chain)
    assert t["call_volume"] == 600      # 100 + 300 + 200
    assert t["put_volume"] == 250       # 50 + 100 + 25 + 75
    assert t["total_volume"] == 850
    assert t["call_oi"] == 8000
    assert t["put_oi"] == 5200
    assert t["volume_basis"] == "SESSION"


def test_put_call_ratio_and_bias(chain):
    t = agg.get_tiles(chain)
    assert t["put_call_volume"] == pytest.approx(250 / 600, abs=0.01)
    assert t["put_call_bias"] == "Bullish"      # ratio well under 0.9


def test_tiles_fall_back_to_the_tape_when_session_volume_is_zero(chain):
    for r in chain["rows"]:
        r["volume"] = 0.0
    flow = {
        "tape_volume": [
            {"expiry": "20260918", "strike": 100.0, "right": "C", "contracts": 400},
            {"expiry": "20260918", "strike": 100.0, "right": "P", "contracts": 100},
        ]
    }
    t = agg.get_tiles(chain, None, flow)
    assert t["volume_basis"] == "LAST_SESSION_TAPE"
    assert t["call_volume"] == 400
    assert t["put_volume"] == 100
    # Notional is rebuilt from the tape against the quoted mid.
    assert t["notional"] == pytest.approx(400 * 3.0 * 100 + 100 * 2.5 * 100)


# ---------------------------------------------------------------- by strike


def test_volume_by_strike_splits_calls_and_puts(chain):
    out = agg.get_volume_by_strike(chain, "volume")
    by = {s["strike"]: s for s in out["strikes"]}
    assert by[100.0]["calls"] == 300
    assert by[100.0]["puts"] == 100
    assert out["peak"] == 300


def test_open_interest_mode_uses_open_interest(chain):
    out = agg.get_volume_by_strike(chain, "open_interest")
    by = {s["strike"]: s for s in out["strikes"]}
    assert by[105.0]["calls"] == 5000
    assert out["peak"] == 5000


def test_delta_exposure_uses_absolute_delta(chain):
    out = agg.get_volume_by_strike(chain, "delta_exposure")
    by = {s["strike"]: s for s in out["strikes"]}
    # |0.52| * 2000 OI * 100 multiplier * 100 spot
    assert by[100.0]["calls"] == pytest.approx(0.52 * 2000 * 100 * 100)


# --------------------------------------------------------------- risk zones


def test_call_and_put_walls_are_the_largest_open_interest_strikes(chain):
    rz = agg.get_risk_zones(chain)
    assert rz["call_wall"] == 105.0     # 5000 contracts
    assert rz["call_wall_oi"] == 5000
    assert rz["put_wall"] == 90.0       # 3000 contracts
    assert rz["put_wall_oi"] == 3000


def test_max_pain_minimises_aggregate_intrinsic_payout(chain):
    rz = agg.get_risk_zones(chain)
    strikes = sorted({r["strike"] for r in chain["rows"]})
    assert rz["max_pain"] in strikes

    def pain(k):
        total = 0.0
        for r in chain["rows"]:
            if r["right"] == "C" and k > r["strike"]:
                total += (k - r["strike"]) * r["open_interest"]
            if r["right"] == "P" and k < r["strike"]:
                total += (r["strike"] - k) * r["open_interest"]
        return total

    assert pain(rz["max_pain"]) == min(pain(k) for k in strikes)


# ------------------------------------------------------------------ metrics


def test_metrics_pick_the_atm_legs_and_build_the_straddle(chain):
    m = agg.get_metrics(chain)
    assert m["atm_call_strike"] == 100.0
    assert m["atm_put_strike"] == 100.0
    assert m["atm_straddle"] == pytest.approx(5.5)          # 3.0 + 2.5
    assert m["expected_move_percent"] == pytest.approx(5.5)  # 5.5 / 100 spot
    assert m["expected_range"]["lower"] == pytest.approx(94.5)
    assert m["expected_range"]["upper"] == pytest.approx(105.5)


def test_skew_compares_the_25_delta_wings(chain):
    m = agg.get_metrics(chain)
    # 105C sits at delta 0.25; 95P at -0.20 is the closest put to 25 delta.
    assert m["skew_25d"] == pytest.approx((0.28 - 0.32) * 100, abs=1e-6)


def test_metrics_report_status_when_the_chain_is_empty():
    empty = {"rows": [], "spot": None, "status": "NO_DATA"}
    assert agg.get_metrics(empty)["status"] == "NO_DATA"


# ---------------------------------------------------------------- breakdown


def test_breakdown_buckets_the_tape_by_side_and_right(chain):
    flow = {
        "trades": [
            {"contracts": 100, "right": "C", "side": "Buy"},
            {"contracts": 40, "right": "C", "side": "Sell"},
            {"contracts": 30, "right": "P", "side": "Buy"},
            {"contracts": 30, "right": "P", "side": "Mid"},
        ]
    }
    b = agg.get_breakdown(flow, chain)
    pcts = {s["label"]: s["percent"] for s in b["segments"]}
    assert b["basis"] == "TAPE"
    assert b["total"] == 200
    assert pcts["Call Buys"] == 50.0
    assert pcts["Call Sells"] == 20.0
    assert pcts["Put Buys"] == 15.0
    assert pcts["Mid / Unclassified"] == 15.0


def test_breakdown_falls_back_to_open_interest_without_a_tape(chain):
    b = agg.get_breakdown({"trades": []}, chain)
    assert b["basis"] == "OPEN_INTEREST"
    assert b["total"] == 13200          # 8000 calls + 5200 puts
    assert b["note"]


# ---------------------------------------------------------------- sentiment


def test_sentiment_weights_sum_to_one_hundred(chain):
    m = agg.get_metrics(chain)
    s = agg.get_sentiment(chain, {"trades": []}, m)
    assert s["status"] == "OK"
    assert 0 <= s["score"] <= 100
    assert s["label"] in ("BULLISH", "NEUTRAL", "BEARISH")
    assert sum(c["weight"] for c in s["components"]) == pytest.approx(100, abs=2)


def test_sentiment_turns_bearish_when_puts_dominate(chain):
    for r in chain["rows"]:
        r["volume"] = 500 if r["right"] == "P" else 5
        r["open_interest"] = 9000 if r["right"] == "P" else 100
    s = agg.get_sentiment(chain, {"trades": []}, agg.get_metrics(chain))
    assert s["label"] == "BEARISH"


def test_sentiment_reports_no_data_on_an_empty_chain():
    s = agg.get_sentiment({"rows": [], "other_expiry_rows": []}, {}, {})
    assert s["status"] == "NO_DATA"
    assert s["score"] is None


# ------------------------------------------------------- expiration flow


def test_expiration_flow_groups_by_expiry(chain):
    chain["other_expiry_rows"] = [
        row(100, "C", volume=50, oi=10, mid=4.0),
        row(100, "P", volume=20, oi=10, mid=4.0),
    ]
    for r in chain["other_expiry_rows"]:
        r["expiry"] = "20260925"
        r["expiry_label"] = "09/25/26"

    out = agg.get_expiration_flow(chain)
    by = {e["expiry"]: e for e in out["expirations"]}
    assert by["20260918"]["calls"] == 600
    assert by["20260925"]["calls"] == 50
    assert by["20260925"]["puts"] == 20


def test_expiration_flow_falls_back_to_open_interest_on_one_expiry(chain):
    """
    The tape only samples front-expiry contracts, so a volume-only chart would
    have a single bar. Open interest is quoted across every expiry.
    """
    chain["other_expiry_rows"] = [
        row(100, "C", volume=0, oi=800, mid=4.0),
        row(100, "P", volume=0, oi=600, mid=4.0),
    ]
    for r in chain["other_expiry_rows"]:
        r["expiry"] = "20260925"
        r["expiry_label"] = "09/25/26"

    for r in chain["rows"]:
        r["volume"] = 0.0

    flow = {
        "tape_volume": [
            {"expiry": "20260918", "strike": 100.0, "right": "C", "contracts": 400},
        ]
    }

    out = agg.get_expiration_flow(chain, flow)
    assert out["basis"] == "OPEN_INTEREST"
    by = {e["expiry"]: e for e in out["expirations"]}
    assert by["20260925"]["calls"] == 800
    assert by["20260918"]["calls"] == 8000


def test_expiration_flow_keeps_volume_when_several_expiries_traded(chain):
    chain["other_expiry_rows"] = [row(100, "C", volume=90, oi=10, mid=4.0)]
    chain["other_expiry_rows"][0]["expiry"] = "20260925"
    chain["other_expiry_rows"][0]["expiry_label"] = "09/25/26"

    out = agg.get_expiration_flow(chain)
    assert out["basis"] == "SESSION"
    by = {e["expiry"]: e for e in out["expirations"]}
    assert by["20260925"]["calls"] == 90


# ------------------------------------------------- option trading-class choice


class _Params:
    """Minimal stand-in for an OptionChain row from reqSecDefOptParams."""

    def __init__(self, exchange, trading_class, expirations, strikes, multiplier="100"):
        self.exchange = exchange
        self.tradingClass = trading_class
        self.expirations = expirations
        self.strikes = strikes
        self.multiplier = multiplier


def _choose(params, symbol):
    """The selection rule from live_options_service._chain_meta."""
    pool = [p for p in params if p.exchange == "SMART"] or list(params)
    chosen = next((p for p in pool if p.tradingClass == symbol), None)
    if chosen is None:
        chosen = max(pool, key=lambda p: len(p.expirations or ()) * len(p.strikes or ()))
    return chosen


def test_standard_trading_class_wins_over_an_adjusted_one():
    """
    reqSecDefOptParams lists adjusted classes alongside the standard one. For
    SPY the first SMART row is '2SPY', which lists a single strike; taking it
    returned a one-strike chain for the most liquid underlying there is.
    """
    params = [
        _Params("SMART", "2SPY", {"20260911"}, {759.0}),
        _Params("SMART", "SPY", {"20260911", "20260914"}, {750.0, 755.0, 760.0}),
        _Params("CBOE", "SPY", {"20260911"}, {750.0}),
    ]
    assert _choose(params, "SPY").tradingClass == "SPY"


def test_falls_back_to_the_widest_chain_when_no_class_matches():
    """Some underlyings list under a class that is not the ticker."""
    params = [
        _Params("SMART", "1XYZ", {"20260911"}, {10.0}),
        _Params("SMART", "XYZW", {"20260911", "20260918"}, {10.0, 11.0, 12.0}),
    ]
    assert _choose(params, "XYZ").tradingClass == "XYZW"


def test_non_smart_rows_are_used_only_when_smart_is_absent():
    params = [_Params("CBOE", "ABC", {"20260911"}, {10.0, 11.0})]
    assert _choose(params, "ABC").exchange == "CBOE"


# ------------------------------------------- a failed chain load is held briefly


def test_failed_chain_load_is_cached_so_one_outage_is_paid_once():
    """
    Building an earnings overview calls load_chain more than once (the score
    does its own pass). Without holding the failure, a single TWS outage cost
    the full 180s timeout on every one of those calls - 365s for one page.
    """
    import time

    import live_market_service as market
    import live_options_service as opts

    key = "chain:TEST:front:0"
    market.cache.clear()
    try:
        offline = opts._offline_chain("TEST", "PROVIDER_OFFLINE", "timeout")
        assert offline["status"] == "PROVIDER_OFFLINE"
        market.cache.put(key, offline)

        # A failure is readable inside the short window ...
        held = market.cache.get(key, opts.CHAIN_FAIL_TTL)
        assert held is not None and held["status"] != "OK"

        # ... and expires quickly, so recovery is picked up.
        with market.cache._lock:
            market.cache._data[key] = (
                time.time() - (opts.CHAIN_FAIL_TTL + 30), offline)
        assert market.cache.get(key, opts.CHAIN_FAIL_TTL) is None
    finally:
        market.cache.clear()


def test_a_good_chain_is_not_mistaken_for_a_held_failure():
    import live_market_service as market
    import live_options_service as opts

    key = "chain:TEST:front:0"
    market.cache.clear()
    try:
        market.cache.put(key, {"symbol": "TEST", "status": "OK", "rows": [1]})
        hit = market.cache.get(key, opts.chain_ttl())
        assert hit and hit["status"] == "OK"
    finally:
        market.cache.clear()
