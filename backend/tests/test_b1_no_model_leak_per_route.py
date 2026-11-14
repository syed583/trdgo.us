"""B.1: one leak test per route. Each serialises the whole non-admin response and asserts no forbidden string survives.

The routes were split by reading the frontend, not by guessing which pages exist:
  ADMIN-ONLY  every caller is a page A.25/A.26 removed from the menu
  SHAPED      a page a normal user still sees reads it, so it must keep working with the working removed
"""
import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

import model_privacy as mp  # noqa: E402

# A payload shaped like the real ones: every forbidden class of field in one object.
RICH = {
    "status": "OK", "symbol": "TSLA", "score": 61.0, "decision": "BUY", "confidence": "HIGH",
    "source": "Unusual Whales options tape", "model": "grok", "model_version": "directional-16/v1",
    "models": ["grok", "meta", "kimi", "gemini"],
    "signals": [{"name": "options_flow", "label": "Options flow", "weight": 8, "points": 6.0,
                 "source": "Unusual Whales", "detail": "Finnhub: 3.1x"}],
    "categories": [{"key": "market", "score": 61, "weight": 40}],
    "parameters": [{"name": "gex", "weight": 5}],
    "buy_sell": {"rows": [{"name": "iv_greeks", "buy_weight": 4, "sell_weight": 1}]},
    "rows": [{"symbol": "AAPL", "model": "kimi", "points": 3, "detail": "Finviz 8:30 AM"}],
    "note": "scored by the Intraday model using Benzinga headlines",
}


def blob(x):
    return json.dumps(x, default=str)


def assert_clean(payload):
    text = blob(payload)
    bad = [w for w in mp.FORBIDDEN_STRINGS if w in text]
    assert not bad, "forbidden strings reached a non-admin: %s" % bad


# --------------------------------------------------------------------------- the helper itself
def test_the_helper_removes_every_forbidden_string():
    assert_clean(mp.public_payload(RICH, admin=False, map_models=True))


def test_the_helper_keeps_what_a_reader_needs():
    out = mp.public_payload(RICH, admin=False, map_models=True)
    assert out["decision"] == "BUY" and out["score"] == 61.0 and out["confidence"] == "HIGH"
    assert out["symbol"] == "TSLA" and out["status"] == "OK"


def test_collections_are_emptied_not_removed():
    """Removing a key crashes a page that reads it without a guard: DirectionalScorePanel does d.signals.filter()."""
    out = mp.public_payload(RICH, admin=False)
    for k in ("signals", "categories", "parameters"):
        assert k in out, "%s must survive as a key" % k
        assert out[k] == [], "%s must arrive empty" % k


def test_an_admin_gets_the_object_unchanged():
    assert mp.public_payload(RICH, admin=True) is RICH


def test_model_names_become_agent_labels():
    out = mp.public_payload(RICH, admin=False, map_models=True)
    assert out["model"] == "Agent 1"
    assert out["models"] == ["Agent 1", "Agent 2", "Agent 3", "Agent 4"]
    assert out["rows"][0]["model"] == "Agent 3"


def test_vendor_names_are_replaced_inside_free_text():
    out = mp.public_payload(RICH, admin=False)
    assert "the data provider" in out["note"]
    assert "Benzinga" not in out["note"] and "Intraday model" not in out["note"]


@pytest.mark.parametrize("model,label", [("grok", "Agent 1"), ("meta", "Agent 2"), ("kimi", "Agent 3"),
                                         ("gemini", "Agent 4"), ("GROK", "Agent 1"), ("", "")])
def test_agent_labels_are_stable(model, label):
    assert mp.agent_label(model) == label


def test_nested_rows_are_cleaned():
    out = mp.public_payload(RICH, admin=False, map_models=True)
    row = out["rows"][0]
    assert "points" not in row and "detail" not in row
    assert row["symbol"] == "AAPL"


# --------------------------------------------------------------------------- the routes
SHAPED = [
    ("directional", lambda m: m.directional(request=None, symbol="TSLA")),
    ("all_models_scan", lambda m: m.all_models_scan(request=None, days=15)),
    ("model_accuracy_scoreboard", lambda m: m.model_accuracy_scoreboard(request=None)),
    ("pre_earnings_direction_scan", lambda m: m.pre_earnings_direction_scan(request=None, days=15)),
    ("intraday_score", lambda m: m.intraday_score(request=None, symbol="TSLA")),
]

ADMIN_ONLY = [
    ("grok_intraday_board", lambda m: m.grok_intraday_board(request=None)),
    ("meta_intraday_board", lambda m: m.meta_intraday_board(request=None)),
    ("kimi_intraday_board", lambda m: m.kimi_intraday_board(request=None)),
    ("gemini_intraday_board", lambda m: m.gemini_intraday_board(request=None)),
    ("grok_intraday_detail", lambda m: m.grok_intraday_detail(request=None, symbol="TSLA")),
    ("meta_intraday_detail", lambda m: m.meta_intraday_detail(request=None, symbol="TSLA")),
    ("kimi_intraday_detail", lambda m: m.kimi_intraday_detail(request=None, symbol="TSLA")),
    ("gemini_intraday_detail", lambda m: m.gemini_intraday_detail(request=None, symbol="TSLA")),
    ("all_intraday_models_scan", lambda m: m.all_intraday_models_scan(request=None)),
    ("intraday_board", lambda m: m.intraday_board(request=None)),
    ("tradgo_call", lambda m: m.tradgo_call(request=None, symbol="TSLA")),
]


@pytest.mark.parametrize("name,call", ADMIN_ONLY, ids=[n for n, _ in ADMIN_ONLY])
def test_admin_only_routes_refuse_a_non_admin(monkeypatch, name, call):
    import api_routes

    monkeypatch.setattr(api_routes, "_is_admin", lambda request: False)
    out = call(api_routes)
    assert out.get("status") == "ADMIN_ONLY", "%s answered a non-admin with real data" % name
    assert_clean(out)


@pytest.mark.parametrize("name,call", SHAPED, ids=[n for n, _ in SHAPED])
def test_shaped_routes_answer_a_non_admin_without_the_working(monkeypatch, name, call):
    """These must still ANSWER - a normal user's page reads them - but carry none of the model."""
    import api_routes

    monkeypatch.setattr(api_routes, "_is_admin", lambda request: False)
    monkeypatch.setattr(api_routes.swr, "peek", lambda *a, **k: dict(RICH))
    monkeypatch.setattr(api_routes.swr, "serve", lambda key, fn, ttl=0, *a, **k: dict(RICH))
    try:
        out = call(api_routes)
    except Exception as exc:
        # NOT a skip. A skip hid a real bug here once: `import model_prediction_service as mp` shadowed
        # `import model_privacy as mp`, so the shaping call raised AttributeError and the test quietly passed.
        raise AssertionError("%s raised %s: %s" % (name, type(exc).__name__, exc))
    assert isinstance(out, dict)
    assert out.get("status") != "ADMIN_ONLY", "%s must keep answering a normal user" % name
    assert_clean(out)
