"""
Options disparity: thirteen readings, and the line between loud and bullish.

The distinction these tests exist to protect: heavy volume against open
interest, an extreme IV rank, piled-up strikes and dealer gamma all say the
options market is stretched -- none of them says which way. They raise the
stretch and must never move the tilt. A provider that answers nothing is
missing, not balanced.
"""

import directional_signals as sig
import disparity_service as d


def _row(right, volume=1000, oi=100, premium=100000, iv=40.0, delta=0.5,
         strike=200.0, expiry="01/16/26", ratio=10.0, sweep=False):
    return {"right": right, "volume": volume, "open_interest": oi,
            "premium": premium, "iv": iv, "delta": delta, "strike": strike,
            "expiry": expiry, "ratio": ratio, "sweep_like": sweep}


def _named(readings):
    return {r["name"]: r for r in readings}


def test_calls_dominating_reads_as_a_call_lean():
    readings = _named(d._from_rows([_row("C", premium=900000),
                                    _row("P", premium=100000)]))
    assert readings["call_put_premium"]["value"] > 0.5
    assert readings["call_put_volume"]["value"] == 0.0  # equal volume


def test_puts_dominating_reads_the_other_way():
    readings = _named(d._from_rows([_row("P", volume=9000), _row("C", volume=1000)]))
    assert readings["call_put_volume"]["value"] < -0.5


def test_loud_readings_never_carry_a_direction():
    readings = _named(d._from_rows([_row("C", ratio=50.0), _row("P", ratio=50.0)]))
    for name in ("volume_vs_oi", "strike_concentration", "expiry_concentration"):
        assert readings[name]["directional"] is False, name


def test_skew_is_read_from_what_each_side_pays():
    readings = _named(d._from_rows([_row("C", iv=60.0), _row("P", iv=30.0)]))
    assert readings["iv_skew"]["value"] > 0.5
    readings = _named(d._from_rows([_row("C", iv=30.0), _row("P", iv=60.0)]))
    assert readings["iv_skew"]["value"] < -0.4


def test_delta_imbalance_weighs_contracts_by_the_stock_behind_them():
    readings = _named(d._from_rows([_row("C", delta=0.6, volume=5000),
                                    _row("P", delta=-0.6, volume=500)]))
    assert readings["delta_imbalance"]["value"] > 0.5


def test_a_missing_reading_is_missing_not_balanced():
    readings = _named(d._from_rows([]))
    assert readings["call_put_volume"]["available"] is False
    assert readings["call_put_volume"]["value"] is None
    assert readings["call_put_volume"]["missing_reason"]


def test_flow_against_price_only_scores_when_they_disagree():
    rows = [_row("C", premium=900000), _row("P", premium=100000)]
    agreeing = d._flow_against_price(rows, 2.0)   # calls bought, stock up
    diverging = d._flow_against_price(rows, -2.0)  # calls bought, stock down
    assert agreeing["value"] == 0.0
    assert diverging["value"] > 0.5
    assert "disagrees" in diverging["detail"]


def test_the_signal_scales_the_lean_by_how_stretched_the_market_is():
    strong = sig.disparity({"status": "OK", "tilt": 0.6, "stretch": 80,
                            "leaning": "calls", "readings": [],
                            "readings_stretched": 8, "readings_available": 11})
    weak = sig.disparity({"status": "OK", "tilt": 0.6, "stretch": 8,
                          "leaning": "calls", "readings": [],
                          "readings_stretched": 1, "readings_available": 11})
    assert strong.bias > weak.bias
    # A lean nobody is trading on is not a signal.
    assert weak.bias < 0.2


def test_no_readings_means_no_parameter():
    out = sig.disparity({"status": "NO_DATA", "detail": "nothing today"})
    assert out.available is False
    assert "nothing today" in out.unavailable_reason
