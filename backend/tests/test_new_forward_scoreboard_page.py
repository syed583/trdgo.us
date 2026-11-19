"""The redesigned Forward Scoreboard: no model names to a user, and no pretending the results are real.

A LEAK THIS PAGE WORKS AROUND. /api/earnings/forward-scoreboard returns `by_model`,
`attribution.by_model` and a `model` field on every trade row, keyed by the vendor's own name, and the
route applies NO model_privacy wrap (unlike the seventeen B.1 covered). The page therefore renders
nothing per-model to a non-administrator.

That is a mitigation, not a fix - the names are still in the payload - and the fix belongs server-side.
These tests pin the page's half so a later edit cannot quietly undo it, and `test_the_route_is_still_
unwrapped` is a REMINDER: when the server is fixed it fails, and whoever sees it can relax the page.

NO AGENT MAPPING IN THE FRONTEND. Translating "grok" to "Agent 1" here would put the vendor list back
into the bundle that B.2a removed. The server maps; the page renders.
"""
from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend" / "src" / "pages" / "NewForwardScoreboardPage.tsx"
ROUTES = ROOT / "backend" / "api_routes.py"


def _code() -> str:
    s = PAGE.read_text(encoding="utf-8")
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"//[^\n]*", "", s)
    return s


def test_the_page_exists_and_reads_the_scoreboard():
    assert PAGE.exists()
    assert "api2.forwardScoreboard(" in _code()


@pytest.mark.parametrize("vendor", ["grok", "meta", "kimi", "gemini", "claude", "chatgpt"])
def test_no_vendor_name_is_written_into_the_page(vendor):
    """B.2a took the vendor list out of the bundle; it must not come back through this page."""
    assert vendor not in _code().lower(), f"{vendor!r} appears in the page source"


def test_no_agent_mapping_is_built_here():
    code = _code()
    assert "Agent 1" not in code and "AGENT_ORDER" not in code, \
        "mapping models to agents is the server's job - doing it here restores the vendor list"


def test_the_model_field_is_only_read_on_a_gated_line():
    """`t.model` is rendered inline, so its own line must carry the gate."""
    for line in _code().splitlines():
        if "t.model" in line:
            assert "isAdmin" in line, f"model rendered without a gate on this line: {line.strip()!r}"


def test_the_by_model_attribution_sits_inside_the_admin_block():
    """`attribution.by_model` is rendered in a block, so it must come after that block opens."""
    code = _code()
    if "attribution?.by_model" not in code:
        return
    gate = code.find("{isAdmin && (")
    assert gate != -1, "the page must have an administrator-only region"
    assert code.find("attribution?.by_model") > gate,         "the per-model attribution is rendered before the admin block opens"


def test_the_model_column_is_gated():
    code = _code()
    assert "{isAdmin && <th>Model</th>}" in code, "the model column must be admin-only"
    assert "{isAdmin && <td" in code, "the model cell must be admin-only"


def test_the_results_are_called_simulated_prominently():
    """A scoreboard that reads like trading results and is not would be the worst thing here."""
    src = PAGE.read_text(encoding="utf-8")
    assert "simulated results, not trading results" in src
    assert "nfs-warn" in src, "the statement must be its own banner, not a footnote"


def test_the_after_cost_figure_is_the_headline():
    """Each aggregate carries cost and zero_cost; the honest headline is after costs."""
    code = _code()
    fn = code.split("const net =")[1].split(";")[0]
    assert "cost" in fn and "zero_cost" not in fn, "the headline must read the after-cost block"


def test_the_route_is_wrapped(self_check=None):
    """Kit L closed this. The page's workaround can now be relaxed whenever we choose to.

    This replaces a reminder that failed on purpose while the route was open; keeping it as a real
    assertion means the leak cannot quietly come back.
    """
    src = ROUTES.read_text(encoding="utf-8")
    route = src.split('@router.get("/earnings/forward-scoreboard")')[1].split("@router.")[0]
    assert "_privacy.public_payload" in route, "the route must not return the payload unwrapped"
    assert "_is_admin(request)" in route, "the decision must depend on the caller"
    assert "map_models=True" in route, "this payload is organised by model"
