"""The two pages the frame mapping was corrected on, and the rules that correction has to keep.

The Figma frame named "Market intelligence dashboard" is NOT the Dashboard. Its panels - Top Movers,
the index list, the earnings and news list - are the ones on the ANALYSIS INTRADAY landing. It was
mapped to /dashboard by its name; the owner corrected it. These tests pin the correction so the name
cannot pull it back:

  * /dashboard renders OverviewPage for everyone, with no new-UI branch at all;
  * the Analysis landing branches on the flag, and ONLY in its idle state - the scanning view and the
    analysed result after a ticker search are reached by the same components either way;
  * neither redesigned page re-implements the data it shows. The landing is handed the live controls
    and the live panels; the Smart Move page imports the live views. A redesign that re-implemented
    them would be a second copy of how a prediction is displayed, and the copy is the one that goes
    stale;
  * the Smart Move page touches no engine, route or scanner file. Those are frozen (owner, 2026-11-12),
    and a redesign has no business in them;
  * what the frames show and the live site does not - Options Flow / Sentiment / Sector panels on the
    landing, Export / Columns / a table search on Smart Move - is ABSENT, not invented.
"""
from __future__ import annotations

import pathlib
import re

SRC = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"
APP = SRC / "App.tsx"
LANDING = SRC / "pages" / "NewAnalysisLanding.tsx"
INSIGHTS = SRC / "pages" / "InsightsPage.tsx"
SMART = SRC / "pages" / "NewSmartMovePage.tsx"


def _code(p: pathlib.Path) -> str:
    """Source with comments stripped - a comment that names a thing is documentation, not a use of it."""
    s = p.read_text(encoding="utf-8")
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    return re.sub(r"//[^\n]*", "", s)


# --------------------------------------------------------------- the routing correction
def test_dashboard_has_no_new_ui_branch_at_all():
    """The frame moved off /dashboard. Not 'points elsewhere' - there is no branch left to point."""
    app = _code(APP)
    route = [l for l in app.splitlines() if 'path="/dashboard"' in l]
    assert len(route) == 1, route
    assert "OverviewPage" in route[0]
    assert "newUi" not in route[0], "the Dashboard route still switches on the new-UI flag"
    assert "NewDashboardPage" not in app, "the page built from the mis-mapped frame is still wired"


def test_the_frame_landed_on_analysis_intraday():
    app = _code(APP)
    route = [l for l in app.splitlines() if 'path="/ai-insights"' in l]
    assert route, "the Analysis Intraday route is missing"
    # The branch is inside InsightsPage, not on the route, because only the landing changes.
    assert "NewAnalysisLanding" in _code(INSIGHTS)


def test_only_the_idle_landing_changes():
    """A search started on the new landing must reach the same scan and the same result screen."""
    code = _code(INSIGHTS)
    branch = re.search(r"if \(ctx\.newUi\) return <NewAnalysisLanding[^\n]*", code)
    assert branch, "the landing does not branch on the flag"
    # The branch sits in the idle block, above the results and scanning returns.
    idle = code.index("if (phase === 'idle')")
    results = code.index("if (phase === 'results'")
    assert idle < branch.start() < results, "the new landing is reached outside the idle state"
    for guarded in ("<ResultScreen", "<ScanScreen"):
        line = [l for l in code.splitlines() if guarded in l and "newUi" in l]
        assert not line, f"{guarded} was made conditional on the new UI: {line}"


# --------------------------------------------------------------- no second copy of the data
def test_the_landing_is_handed_the_live_pieces_and_reads_only_page_level_sources():
    """The landing now reads two sources of its own, and may read only those two.

    Options Flow, Market Sentiment and Sector Performance were added (owner, 2026-11-19) because we
    measure all three - the earlier omission applied "leave out what we cannot measure" as "show
    only what this page already showed". What must not change: the controls and the three live
    panels are still HANDED IN rather than rebuilt, and nothing here fetches per symbol or per row.
    This is the busiest page in the app, so a per-row call multiplies across every visit.
    """
    code = _code(LANDING)
    assert "controls: React.ReactNode" in code and "panels: React.ReactNode" in code
    calls = set(re.findall(r"api2\.([a-zA-Z0-9_]+)", code))
    allowed = {"marketPulse", "flowMarketSummary"}
    assert calls <= allowed, f"the landing reads a source it should not: {sorted(calls - allowed)}"
    for per_symbol in ("optionChain", "optionIvHistory", "api2.quote", "api2.news", "api2.sentiment"):
        assert per_symbol not in code, f"a per-symbol request on the landing: {per_symbol}"


def test_the_controls_that_start_an_analysis_exist_once():
    """Both landings render the SAME search box, horizon switch and suggestion chips."""
    code = _code(INSIGHTS)
    assert code.count("function LandingControls(") == 1
    assert code.count("<form className=\"anh-search\"") == 1, "the search form was duplicated"
    assert code.count("SUGGESTED.map(") == 1, "the suggestion chips were duplicated"
    # And the panels are the live component, not a rebuild.
    assert "<LandingPanels ctx={ctx}" in code
    assert code.count("function LandingPanels(") == 1


def test_the_smart_move_page_reuses_the_live_views():
    code = _code(SMART)
    assert "from './SmartMoveEngine20Page'" in code
    for view in ("LiveView", "UniverseView", "MondayBmoView", "SnapshotView"):
        assert f"<{view}" in code, f"{view} is not rendered - the live page offers it today"
    for banned in ("useApi", "earningsMonitor", "smartMoveEngine20", "fetch("):
        assert banned not in code, f"the redesigned Smart Move page reads data itself ({banned})"


def test_the_snapshot_tab_stays_gated_as_it_is_today():
    code = _code(SMART)
    assert "isAdmin && (" in code and 'data-testid="tab-snapshot"' in code
    gate = code.index("isAdmin && (")
    tab = code.index('data-testid="tab-snapshot"')
    assert gate < tab, "the historical snapshot tab is no longer behind the administrator check"


def test_smart_move_engine_and_route_files_are_not_in_this_change():
    """Frozen by the owner. The redesign is a display file and a stylesheet, nothing more."""
    code = _code(SMART)
    for frozen in ("earnings_monitor", "smart_live", "routes.py", "collection.lock"):
        assert frozen not in code


# --------------------------------------------------------------- the design's extras stay out
def test_the_landing_claims_no_figure_nothing_measures():
    """The frame's stat row advertises "150+ Analytical Signals" and "85% Backtest Coverage".

    Nothing measures either. The data-source count is real and stays; a claim about the product that
    a reader cannot check is what this redesign set out to remove - the same rule that took
    "12 Data Sources" off the page when there are four.
    """
    code = _code(LANDING)
    for claim in ("150+", "Analytical Signals", "Backtest Coverage", "85%"):
        assert claim not in code, f"the landing states a figure nothing measures: {claim}"


def test_the_landing_marks_a_flat_reading_as_flat():
    """+0.00% is not a gain. `>= 0` painted flat readings green on the strip and the sector list."""
    code = _code(LANDING)
    assert "'flat'" in code, "a flat percentage has no neutral tone"
    assert "change_percent) >= 0 ? 'up'" not in code, "a flat reading is still painted as a rise"


def test_smart_move_leaves_out_export_columns_and_table_search():
    code = _code(SMART)
    for extra in ("Export", "Columns", "Search symbol", "placeholder="):
        assert extra not in code, f"a Figma-only control was built: {extra}"
