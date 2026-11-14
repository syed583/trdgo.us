"""A.29: the model's own working must never leave the server for a non-admin, on any route.

A.25 hid the parameters on the Analysis screen. The stored call behind that screen still carried them, so
GET /api/calls/latest/TSLA?horizon=TODAY&origin=analysis returned the full parameter list with weights, points
and sources, plus the buy/sell breakdown, to any signed-in user. These tests fail if that ever returns.
"""
import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

import calls_service as C  # noqa: E402

# A stored call shaped exactly like the one the reporter saw.
CALL = {
    "id": 11, "symbol": "TSLA", "horizon": "TODAY", "origin": "analysis",
    "decision": "DO NOT TRADE", "direction_score": 47.3, "confidence": "LOW",
    "agreement_pct": 55.0, "coverage_pct": 80.0, "price_at_call": 410.2,
    "model_version": "v2",
    "why": {
        "for": [{"label": "Options flow", "points": 6.0, "points_label": "+6.0",
                 "detail": "Unusual Whales: call premium 3.1x put premium"}],
        "against": [{"label": "Relative strength", "points": -4.0, "points_label": "-4.0",
                     "detail": "Intraday model: -1.2% vs SPY"}],
        "missing": ["Institutional ownership"],
        "blocked": ["spread too wide"],
        "explanation": "Flow leans bullish but the stock is lagging the market.",
        "buy_sell": {"rows": [{"name": "options_flow", "buy_weight": 6, "sell_weight": 0, "points": 6}],
                     "buy_total": 51, "sell_total": 49},
    },
    "parameters": [{"name": "options_flow", "label": "Options flow", "weight": 8,
                    "points": 6.0, "source": "Unusual Whales", "available": True}],
    "outcome": {"correct": None},
}

SECRET_WORDS = ("buy_weight", "sell_weight", "points", "weight",
                "Unusual Whales", "Intraday model", "options_flow")


def test_a_non_admin_view_drops_the_parameter_list():
    out = C.public_view(CALL)
    assert "parameters" not in out


def test_a_non_admin_view_drops_the_buy_sell_breakdown_and_the_parameter_names():
    out = C.public_view(CALL)
    assert "buy_sell" not in out["why"]
    # the key must survive: the screen does w.missing.length with no guard, so dropping it crashes the page
    assert out["why"]["missing"] == [], "the missing list names parameters, so it must arrive empty"


def test_a_non_admin_view_drops_points_and_source_detail_from_every_reason():
    out = C.public_view(CALL)
    for side in ("for", "against"):
        for row in out["why"][side]:
            assert "points" not in row
            assert "points_label" not in row
            assert "detail" not in row, "detail carries the arithmetic and the data-source name"


def test_no_weight_point_or_source_word_survives_anywhere_in_the_payload():
    """The strongest form: serialise the whole answer and look for the words themselves."""
    blob = json.dumps(C.public_view(CALL))
    for word in SECRET_WORDS:
        assert word not in blob, "%r still reaches a non-admin" % word


def test_what_a_reader_keeps():
    out = C.public_view(CALL)
    assert out["decision"] == "DO NOT TRADE"
    assert out["direction_score"] == 47.3
    assert out["confidence"] == "LOW"
    assert out["coverage_pct"] == 80.0
    assert [r["label"] for r in out["why"]["for"]] == ["Options flow"]
    assert out["why"]["explanation"].startswith("Flow leans")
    assert out["why"]["blocked"] == ["spread too wide"]


def test_an_admin_keeps_every_field():
    assert C.public_view(CALL) is not CALL
    assert "parameters" in CALL and "buy_sell" in CALL["why"], "the stored call itself must not be mutated"


def test_a_list_of_calls_is_sanitised_row_by_row():
    rows = C.public_views([CALL, CALL])
    assert len(rows) == 2
    assert all("parameters" not in r for r in rows)


def test_none_survives():
    assert C.public_view(None) is None
    assert C.public_views(None) == []


# --------------------------------------------------------------------------- the routes themselves
@pytest.mark.parametrize("route_fn,kwargs", [
    ("calls_latest", {"symbol": "TSLA", "horizon": "TODAY", "origin": "analysis"}),
    ("calls_get", {"call_id": 11}),
    ("calls_history", {"symbol": "TSLA"}),
])
def test_each_call_route_sanitises_for_a_non_admin(monkeypatch, route_fn, kwargs):
    import api_routes

    monkeypatch.setattr(api_routes, "_is_admin", lambda request: False)
    monkeypatch.setattr(C, "latest", lambda *a, **k: dict(CALL))
    monkeypatch.setattr(C, "get", lambda *a, **k: dict(CALL))
    monkeypatch.setattr(C, "history", lambda *a, **k: [dict(CALL)])
    out = getattr(api_routes, route_fn)(request=None, **kwargs)
    blob = json.dumps(out)
    for word in ("buy_weight", "sell_weight", "Unusual Whales"):
        assert word not in blob


@pytest.mark.parametrize("route_fn,kwargs", [
    ("calls_latest", {"symbol": "TSLA", "horizon": "TODAY", "origin": "analysis"}),
    ("calls_get", {"call_id": 11}),
])
def test_an_admin_still_receives_the_working(monkeypatch, route_fn, kwargs):
    import api_routes

    monkeypatch.setattr(api_routes, "_is_admin", lambda request: True)
    monkeypatch.setattr(C, "latest", lambda *a, **k: dict(CALL))
    monkeypatch.setattr(C, "get", lambda *a, **k: dict(CALL))
    out = getattr(api_routes, route_fn)(request=None, **kwargs)
    assert "parameters" in (out["call"] or {})
    assert "buy_sell" in (out["call"] or {})["why"]


def test_the_weight_table_is_admin_only(monkeypatch):
    import api_routes

    monkeypatch.setattr(api_routes, "_is_admin", lambda request: False)
    out = api_routes.directional_weights(request=None)
    assert out.get("status") == "ADMIN_ONLY"
    assert not out.get("weights")
    monkeypatch.setattr(api_routes, "_is_admin", lambda request: True)
    assert api_routes.directional_weights(request=None).get("weights")


def test_the_parameter_explainer_is_admin_only(monkeypatch):
    import api_routes

    monkeypatch.setattr(api_routes, "_is_admin", lambda request: False)
    out = api_routes.explain_parameter(request=None, symbol="TSLA", parameter="options_flow", horizon="TODAY")
    assert out.get("status") == "ADMIN_ONLY"
    assert not out.get("explanation")


def test_per_parameter_hit_rates_are_admin_only(monkeypatch):
    import api_routes

    monkeypatch.setattr(api_routes, "_is_admin", lambda request: False)
    out = api_routes.snapshot_evaluate(request=None, horizon=10)
    assert out.get("status") == "ADMIN_ONLY"
    assert out.get("parameters") == []


def test_is_admin_fails_closed(monkeypatch):
    """Any problem reading the session must mean 'not an admin'."""
    import api_routes
    import auth_service

    monkeypatch.setattr(auth_service, "current_user", lambda request: (_ for _ in ()).throw(RuntimeError("boom")))
    assert api_routes._is_admin(None) is False
    monkeypatch.setattr(auth_service, "current_user", lambda request: None)
    assert api_routes._is_admin(None) is False
    monkeypatch.setattr(auth_service, "current_user", lambda request: {"role": "user"})
    assert api_routes._is_admin(None) is False
    monkeypatch.setattr(auth_service, "current_user", lambda request: {"role": "Admin"})
    assert api_routes._is_admin(None) is True
