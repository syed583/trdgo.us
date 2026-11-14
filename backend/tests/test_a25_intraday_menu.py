"""A.25, intraday menu (owner decisions 2026-11-13 after the intraday audit):
  * Tradgo Call, Sector Comparison, Signal History and Trade Plan leave the MENU; their routes and modules stay;
  * the Trade Plan scanner no longer starts and its Claude read is off (the only unprompted AI spend in the app);
  * the Analysis page is renamed, and the scored parameters, their weights and the category breakdown go only to an
    administrator - everyone else gets the verdict, the score and the key insights.
No provider request and no AI request is made by these tests.
"""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import analysis_stream_service as stream  # noqa: E402
import trade_plan_service as tp  # noqa: E402

SIDEBAR = (ROOT.parent / "frontend" / "src" / "components" / "Sidebar.tsx").read_text(encoding="utf-8")
APP = (ROOT.parent / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
PAGE = (ROOT.parent / "frontend" / "src" / "pages" / "InsightsPage.tsx").read_text(encoding="utf-8")
MAIN = (ROOT / "main.py").read_text(encoding="utf-8")
ROUTES = (ROOT / "api_routes.py").read_text(encoding="utf-8")

HIDDEN = ("/tradgo-call", "/trade-plan", "/signal-history", "/peers")


# ----------------------------------------------------------------- the menu
def test_the_four_pages_leave_the_menu_and_keep_their_routes():
    for path in HIDDEN:
        assert ("to: '%s'" % path) not in SIDEBAR, "%s is still a menu item" % path
        assert ('path="%s"' % path) in APP, "%s lost its route" % path


def test_the_analysis_item_is_renamed_and_the_kept_items_are_untouched():
    assert "label: 'Analysis Intraday'" in SIDEBAR and "to: '/ai-insights'" in SIDEBAR
    for path in ("/strategy-trades", "/smart-move/engine20"):
        assert ("to: '%s'" % path) in SIDEBAR, "%s should still be in the menu" % path
    # A.26 (owner decision 2026-11-14) took the four boards and All Intraday out of the user menu; their routes stay,
    # which test_a26_agent_names.py covers.
    for path in ("/grok-intraday", "/meta-intraday", "/kimi-intraday", "/gemini-intraday", "/all-intraday-models"):
        assert ("to: '%s'" % path) not in SIDEBAR


# ----------------------------------------------------------------- Trade Plan
def test_the_trade_plan_scanner_no_longer_starts():
    body = MAIN[MAIN.index("def _start_signal_scanner"):]
    body = body[:body.index("\n@_bg")]
    assert "tp.start_scanner" in body                       # the function is kept ...
    before = MAIN[:MAIN.index("def _start_signal_scanner")]
    assert not before.rstrip().endswith("@_bg"), "the scanner is still registered to start"
    assert "A.25" in before[-400:]


def test_the_written_read_from_claude_is_off_by_default():
    assert inspect.signature(tp.get_plan).parameters["with_read"].default is False
    route = ROUTES[ROUTES.index("def trade_plan("):]
    route = route[:route.index("\n@router.")]
    assert "with_read" not in route, "the route must not ask for the read"


def test_the_read_helper_is_kept_so_it_can_be_turned_back_on():
    assert callable(getattr(tp, "_read", None))
    src = (ROOT / "trade_plan_service.py").read_text(encoding="utf-8")
    assert "claude_service.ask" in src and "with_read=True" in src or "with_read" in src


# ----------------------------------------------------------------- the model's working
def test_the_stream_takes_an_include_params_flag_that_defaults_to_private():
    assert inspect.signature(stream.stream_analysis).parameters["include_params"].default is False


def test_a_non_admin_result_carries_the_verdict_but_no_parameters():
    """Drive the generator's result branch with a stubbed worker: no provider, no network."""
    rows = _run(include_params=False)
    res = [r for r in rows if r["type"] == "result"][0]
    assert res["result"]["decision"] == "BUY" and res["result"]["direction_score"] == 71
    assert res["result"]["confidence"] == 60 and res["result"]["coverage_pct"] == 90
    assert res["result"].get("reasons") == ["flow is one-sided"]
    assert "signals" not in res["result"], "the parameter list reached a non-admin"
    assert res["categories"] == []


def test_an_admin_result_still_carries_everything():
    rows = _run(include_params=True)
    res = [r for r in rows if r["type"] == "result"][0]
    names = [x["name"] for x in res["result"]["signals"]]
    assert "options_flow" in names and len(names) >= 1      # the worker may append extra intraday signals
    assert res["categories"], "the admin lost the category breakdown"


def test_the_route_decides_by_role_and_defaults_to_private():
    route = ROUTES[ROUTES.index("def analyze_stream("):]
    route = route[:route.index("\n@router.")]
    assert "auth.current_user(request)" in route
    assert 'role' in route and '"admin"' in route
    assert "include_params=is_admin" in route


# ----------------------------------------------------------------- the page
def test_the_page_hides_the_working_when_no_parameters_arrive():
    assert "const showModel = signals.length > 0;" in PAGE
    assert "{showModel && (\n        <Panel title=\"Category Breakdown\"" in PAGE.replace("\r\n", "\n")
    assert "showModel ? TABS : TABS.filter" in PAGE
    assert "{showModel && (<>" in PAGE


def test_the_page_keeps_the_verdict_and_every_section_below():
    assert '<div className="an-decision"' in PAGE and "{result.decision}" in PAGE
    assert "Confidence {Math.round(result.confidence)}%" in PAGE
    assert "{result.coverage_pct}% of the model returned data" in PAGE
    for section in ("<Disparity symbol={symbol} />", "<CorporateEvents symbol={symbol} />",
                    "<SymbolNews", "<Dividends symbol={symbol} />", "<FundActivity",
                    "<InstitutionalPanel symbol={symbol} />"):
        assert section in PAGE, "%s disappeared from the page" % section


# ----------------------------------------------------------------- helper
def _run(include_params: bool) -> list:
    """Run the real generator with its collaborators stubbed: no provider, no AI, no database."""
    import json

    import calls_service
    import directional_score_service as ds

    result = {"decision": "BUY", "direction_score": 71, "confidence": 60, "coverage_pct": 90,
              "reasons": ["flow is one-sided"], "note": "n", "blocked_reasons": [], "status": "OK",
              "signals": [{"name": "options_flow", "label": "Options flow", "weight": 34, "points": 20,
                           "available": True, "directional": True, "points_label": "+20", "detail": "d",
                           "rule": "r", "category": "flow"}]}

    o_score, o_rec = ds.get_directional_score, calls_service.record_async
    ds.get_directional_score = lambda symbol, progress=None: dict(result, signals=[dict(x) for x in result["signals"]])
    calls_service.record_async = lambda *a, **k: None
    try:
        raw = "".join(stream.stream_analysis("AAA", horizon="SWING", include_params=include_params))
    finally:
        ds.get_directional_score, calls_service.record_async = o_score, o_rec

    out = []
    for block in raw.split(chr(10) + chr(10)):
        line = block.strip()
        if line.startswith('data:'):
            out.append(json.loads(line.split(':', 1)[1].strip()))
    return out
