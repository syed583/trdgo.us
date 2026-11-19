"""Kit L: three routes that handed model internals to any signed-in user, and the helper gap behind two.

B.1 wrapped seventeen routes. These three were missed:

  /api/earnings/forward-scoreboard   by_model keyed BY the vendor's name, attribution.by_model rows
                                     carrying it in `key`, and a `model` on every trade row
  /api/earnings/historical-backtest  the same, plus present_factors/missing_factors - the model's
                                     complete seventeen-factor input list, which A.29 keeps to admins
  /api/sentiment/{symbol}            source: "Claude · headline read"

THE PART THAT WAS NOT A ONE-LINE FIX. public_payload maps a model name held as a VALUE under
model/model_name/engine. It never touched a dict keyed BY a model, nor a row whose model sits in `key`.
Both shapes passed straight through, so wrapping those two routes alone would have changed nothing
visible. That is what these first tests pin.

The factor lists are not vendor names at all, so no scrub could reach them. They are replaced by counts,
because "4 of 17 inputs could be rebuilt" is the honest limitation on a partial backtest - deleting it
outright would make the result look complete.
"""
from __future__ import annotations

import pathlib
import re

import pytest

import model_privacy as mp

ROUTES = (pathlib.Path(__file__).resolve().parents[1] / "api_routes.py").read_text(encoding="utf-8")


def _route(path: str) -> str:
    return ROUTES.split(f'@router.get("{path}")')[1].split("@router.")[0]


# ---------------------------------------------------------------- the helper gap
def test_a_dict_keyed_by_the_model_is_relabelled():
    out = mp.public_payload({"by_model": {"grok": {"n": 3}, "meta": {"n": 4}}},
                            admin=False, map_models=True)
    assert set(out["by_model"]) == {"Agent 1", "Agent 2"}, \
        "a dict keyed BY a model used to pass through untouched"
    assert out["by_model"]["Agent 1"]["n"] == 3, "the figures must survive the relabelling"


def test_attribution_rows_carrying_the_model_in_key_are_relabelled():
    out = mp.public_payload({"attribution": {"by_model": [{"key": "grok", "n": 3}]}},
                            admin=False, map_models=True)
    assert out["attribution"]["by_model"][0]["key"] == "Agent 1"
    assert out["attribution"]["by_model"][0]["n"] == 3


def test_a_model_field_on_a_row_is_still_mapped():
    out = mp.public_payload({"trades": [{"model": "kimi", "symbol": "NVDA"}]},
                            admin=False, map_models=True)
    assert out["trades"][0]["model"] == "Agent 3"
    assert out["trades"][0]["symbol"] == "NVDA", "only the model name may change"


def test_an_administrator_still_sees_everything():
    payload = {"by_model": {"grok": {"n": 3}}, "attribution": {"by_model": [{"key": "meta"}]}}
    assert mp.public_payload(payload, admin=True, map_models=True) == payload


def test_the_attribution_source_is_removed():
    out = mp.public_payload({"source": "Claude · headline read", "score": 81}, admin=False)
    assert "source" not in out, "the engine that scored the headlines must not be named"
    assert out["score"] == 81, "the reading itself stays"


# ---------------------------------------------------------------- the three routes
@pytest.mark.parametrize("path", ["/earnings/forward-scoreboard",
                                  "/earnings/historical-backtest",
                                  "/sentiment/{symbol}"])
def test_the_route_now_applies_model_privacy(path):
    body = _route(path)
    assert "_privacy.public_payload" in body, f"{path} still returns the payload unwrapped"
    assert "_is_admin(request)" in body, f"{path} must decide by the caller, not unconditionally"


@pytest.mark.parametrize("path", ["/earnings/forward-scoreboard", "/earnings/historical-backtest"])
def test_the_model_keyed_routes_ask_for_model_mapping(path):
    assert "map_models=True" in _route(path), \
        f"{path} carries by_model, which only relabels when map_models is on"


def test_the_factor_names_are_replaced_by_counts_for_a_user():
    body = _route("/earnings/historical-backtest")
    assert 'out["present_factors"] = []' in body and 'out["missing_factors"] = []' in body, \
        "the model's input names must not reach a user"
    for k in ("present_factor_count", "missing_factor_count", "factor_total"):
        assert k in body, f"{k} must be provided so the limitation can still be stated"
    assert "if not admin" in body, "an administrator must keep the names"


def test_each_route_takes_the_request_it_needs():
    """_is_admin(request) is useless if the handler never receives one."""
    for path, fn in (("/earnings/forward-scoreboard", "def earnings_forward_scoreboard("),
                     ("/earnings/historical-backtest", "def earnings_historical_backtest("),
                     ("/sentiment/{symbol}", "def sentiment(")):
        sig = _route(path).split(fn)[1].split(")")[0]
        assert "request: Request" in sig, f"{path} must accept the request"


# ---------------------------------------------------------------- the pages can drop their workaround
def test_the_backtest_page_reads_the_counts():
    page = (pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages"
            / "NewHistoricalBacktestPage.tsx").read_text(encoding="utf-8")
    assert "present_factor_count" in page and "factor_total" in page, \
        "the page must use the counts the server now sends"
