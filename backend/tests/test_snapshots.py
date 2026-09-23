"""
Snapshot measurement, on synthetic rows with known outcomes.

These tests carry unusual weight. The measurement they cover cannot be
checked against real data for about a month -- nothing has a forward return
until twenty sessions have passed -- and its output is what the weight table
will eventually be rebuilt from. A quiet error here would not surface as a
bug; it would surface as a confident, wrong set of weights.

The property that matters most is that a bearish call is scored on price
falling. Measuring every parameter against "did price rise" would mark every
correct bearish call as a miss, and the parameters most likely to be right in
a downturn would be the ones the measurement penalised.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

import snapshot_service as snap
from models_snapshots import ScoreSnapshot


@pytest.fixture
def store(monkeypatch):
    """An in-memory stand-in for the snapshot table."""
    rows: list[ScoreSnapshot] = []

    class Query:
        def __init__(self, data): self._data = data
        def filter(self, *a, **k): return self
        def filter_by(self, **k): return self
        def all(self): return self._data

    class Session:
        def query(self, *a, **k): return Query(rows)
        def close(self): pass

    monkeypatch.setattr(snap, "SessionLocal", lambda: Session())
    monkeypatch.setattr(snap, "create_all", lambda engine: [])
    return rows


def _snapshot(signals: dict, forward_10d: float,
              day: date = date(2026, 1, 5)) -> ScoreSnapshot:
    row = ScoreSnapshot(symbol="TEST", snapshot_date=day)
    row.signals = json.dumps({
        name: {"bias": bias, "points": bias * 10,
               "available": True, "directional": True}
        for name, bias in signals.items()
    })
    row.forward_10d_pct = forward_10d
    return row


# --------------------------------------------------------------------------
# a bearish call is right when price falls
# --------------------------------------------------------------------------


def test_a_bearish_call_followed_by_a_fall_is_a_hit(store):
    """
    The error that would invalidate everything: scoring every parameter on
    "did price rise" marks correct bearish calls as misses.
    """
    for _ in range(40):
        store.append(_snapshot({"options_flow": -0.9}, forward_10d=-3.0))

    result = snap.evaluate_parameters(horizon=10)
    flow = next(p for p in result["parameters"] if p["parameter"] == "options_flow")

    assert flow["hit_rate_pct"] == 100.0
    assert flow["bearish_calls"] == 40
    # Return "when followed" is sign-flipped for a short call, so a correct
    # bearish reading shows as a positive outcome.
    assert flow["avg_return_when_followed_pct"] == 3.0


def test_a_bearish_call_followed_by_a_rise_is_a_miss(store):
    for _ in range(40):
        store.append(_snapshot({"options_flow": -0.9}, forward_10d=+3.0))

    result = snap.evaluate_parameters(horizon=10)
    flow = next(p for p in result["parameters"] if p["parameter"] == "options_flow")

    assert flow["hit_rate_pct"] == 0.0
    assert flow["avg_return_when_followed_pct"] == -3.0


def test_a_bullish_call_is_scored_on_the_rise(store):
    for _ in range(40):
        store.append(_snapshot({"ema_trend": 0.8}, forward_10d=+2.0))

    result = snap.evaluate_parameters(horizon=10)
    ema = next(p for p in result["parameters"] if p["parameter"] == "ema_trend")

    assert ema["hit_rate_pct"] == 100.0
    assert ema["bullish_calls"] == 40


# --------------------------------------------------------------------------
# edge against the base rate
# --------------------------------------------------------------------------


def test_a_parameter_matching_the_base_rate_has_no_edge(store):
    """
    In a rising market most readings precede a rise. A 70% hit rate against a
    70% base rate is not skill, and the weight table must not reward it.
    """
    # Seven rises, three falls -- and a parameter that is always bullish.
    for i in range(40):
        move = 2.0 if i % 10 < 7 else -2.0
        store.append(_snapshot({"rsi": 0.9}, forward_10d=move))

    result = snap.evaluate_parameters(horizon=10)
    rsi = next(p for p in result["parameters"] if p["parameter"] == "rsi")

    assert result["base_rate_pct"] == 70.0
    assert rsi["hit_rate_pct"] == 70.0
    assert rsi["edge_vs_base_pct"] == 0.0


def test_a_parameter_beating_the_base_rate_shows_positive_edge(store):
    # Base rate 50%, but this parameter only turns bullish before the rises.
    for i in range(40):
        rising = i % 2 == 0
        store.append(_snapshot(
            {"unusual_activity": 0.9 if rising else -0.9},
            forward_10d=2.0 if rising else -2.0))

    result = snap.evaluate_parameters(horizon=10)
    unusual = next(p for p in result["parameters"]
                   if p["parameter"] == "unusual_activity")

    assert result["base_rate_pct"] == 50.0
    assert unusual["hit_rate_pct"] == 100.0
    assert unusual["edge_vs_base_pct"] == 50.0


# --------------------------------------------------------------------------
# what does not count as a call
# --------------------------------------------------------------------------


def test_a_near_neutral_reading_is_not_counted_as_a_call(store):
    """A parameter reading 0.02 has not predicted anything."""
    for _ in range(40):
        store.append(_snapshot({"price_action": 0.02}, forward_10d=5.0))

    result = snap.evaluate_parameters(horizon=10)

    assert all(p["parameter"] != "price_action" for p in result["parameters"])


def test_an_unavailable_parameter_is_not_counted(store):
    row = _snapshot({"ema_trend": 0.9}, forward_10d=2.0)
    row.signals = json.dumps({
        "event_radar": {"bias": None, "points": 0.0,
                        "available": False, "directional": True}})
    for _ in range(40):
        store.append(row)

    result = snap.evaluate_parameters(horizon=10)

    assert all(p["parameter"] != "event_radar" for p in result["parameters"])


# --------------------------------------------------------------------------
# sample size
# --------------------------------------------------------------------------


def test_a_thin_sample_is_flagged_as_not_ready_to_judge(store):
    for _ in range(5):
        store.append(_snapshot({"options_flow": 0.9}, forward_10d=2.0))

    result = snap.evaluate_parameters(horizon=10, min_observations=30)
    flow = next(p for p in result["parameters"] if p["parameter"] == "options_flow")

    assert flow["calls"] == 5
    assert flow["sufficient_sample"] is False
    assert result["ready_to_judge"] == 0


def test_no_completed_snapshots_reports_rather_than_computing(store):
    result = snap.evaluate_parameters(horizon=10)

    assert result["status"] == "NO_DATA"
    assert "forward return" in result["detail"]


# --------------------------------------------------------------------------
# ranking
# --------------------------------------------------------------------------


def test_parameters_are_ranked_by_edge(store):
    for i in range(40):
        rising = i % 2 == 0
        store.append(_snapshot(
            {
                "options_flow": 0.9 if rising else -0.9,   # always right
                "rsi": 0.9,                                 # always bullish
            },
            forward_10d=2.0 if rising else -2.0))

    result = snap.evaluate_parameters(horizon=10)
    names = [p["parameter"] for p in result["parameters"]]

    assert names[0] == "options_flow"
    assert result["parameters"][0]["edge_vs_base_pct"] > \
        result["parameters"][-1]["edge_vs_base_pct"]
