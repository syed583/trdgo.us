"""A.26 (owner decisions 2026-11-14):
  PART 1  the four intraday boards and All Intraday leave the sidebar but keep running; their routes still answer, so an
          administrator reaches them by URL.
  PART 2  a non-admin is told the strategies are Agent 1-4. The mapping happens on the SERVER, so no model name reaches an
          ordinary user's browser - not in a label and not in a key - while the database rows, the internal keys, the
          scoring and the MT5 magic numbers keep the real names.
No provider request and no database write is made by these tests.
"""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import demo_trade_service as dts  # noqa: E402
import mt5_autotrade as mt5  # noqa: E402

SIDEBAR = (ROOT.parent / "frontend" / "src" / "components" / "Sidebar.tsx").read_text(encoding="utf-8")
APP = (ROOT.parent / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
PAGE = (ROOT.parent / "frontend" / "src" / "pages" / "StrategyTradesPage.tsx").read_text(encoding="utf-8")
ROUTES = (ROOT / "api_routes.py").read_text(encoding="utf-8")

BOARDS = ("/grok-intraday", "/meta-intraday", "/kimi-intraday", "/gemini-intraday", "/all-intraday-models")


# ----------------------------------------------------------------- PART 1
def test_the_boards_leave_the_user_menu_but_keep_their_routes():
    nav = SIDEBAR[SIDEBAR.index("const NAV_GROUPS"):]
    for path in BOARDS:
        assert ("to: '%s'" % path) not in nav, "%s is still in the user menu" % path
        assert ('path="%s"' % path) in APP, "%s lost its route: an admin must still reach it" % path


def test_the_analysis_group_is_exactly_the_three_pages_users_keep():
    nav = SIDEBAR[SIDEBAR.index("{ heading: 'Analysis'"):]
    nav = nav[:nav.index("{ heading: 'Earnings & news'")]
    kept = [p for p in ("/ai-insights", "/strategy-trades", "/smart-move/engine20") if ("to: '%s'" % p) in nav]
    assert kept == ["/ai-insights", "/strategy-trades", "/smart-move/engine20"]
    for gone in BOARDS + ("/tradgo-call", "/trade-plan", "/signal-history", "/peers"):
        assert ("to: '%s'" % gone) not in nav


def test_the_boards_keep_running_nothing_was_unwired():
    """The paper tracker still binds all four boards, so the feed, the blotter and the MT5 mirror are unchanged."""
    assert set(dts.MODELS) == {"grok", "meta", "kimi", "gemini"}
    assert dts.MODELS["grok"][1] == "intraday_grok_service:board"
    assert dts.MODELS["gemini"][1] == "gemini_intraday_service:board"


# ----------------------------------------------------------------- PART 2
def test_the_public_mapping_is_the_one_the_owner_asked_for():
    assert [dts.public_label(k) for k in ("grok", "meta", "kimi", "gemini")] == ["Agent 1", "Agent 2", "Agent 3", "Agent 4"]
    assert [dts.public_key(k) for k in ("grok", "meta", "kimi", "gemini")] == ["agent1", "agent2", "agent3", "agent4"]
    # the retired ones are mapped too, so ChatGPT and Claude Pro do not leak out of the dropdown
    assert "ChatGPT" not in dts.public_label("chatgpt") and "Claude" not in dts.public_label("claude_pro")


def test_a_public_key_maps_back_so_a_non_admin_filter_still_works():
    for pub, real in (("agent1", "grok"), ("agent2", "meta"), ("agent3", "kimi"), ("agent4", "gemini")):
        assert dts.internal_key(pub) == real
        assert dts.internal_key(pub.upper()) == real
    assert dts.internal_key("grok") == "grok"          # an admin's real key passes through
    assert dts.internal_key("all") == "all"
    assert dts.internal_key("") == ""


def test_the_real_names_are_untouched_internally():
    assert dts.strategy_label("grok") == "Grok Intraday"
    assert dts.strategy_label("gemini") == "Gemini Intraday"
    assert dts.MODELS["kimi"][0] == "Kimi Intraday"


def test_the_mt5_magic_numbers_are_untouched():
    assert mt5.magic_for("grok") == 240911 and mt5.magic_for("meta") == 240912
    assert mt5.magic_for("kimi") == 240913 and mt5.magic_for("gemini") == 240914


def test_the_blotter_takes_a_reveal_flag_and_the_route_sets_it_by_role():
    assert inspect.signature(dts.blotter).parameters["reveal"].default is True      # internal callers unchanged
    route = ROUTES[ROUTES.index("def strategy_trades("):]
    route = route[:route.index("\n@router.")]
    assert "auth.current_user(request)" in route and '"admin"' in route
    assert "reveal=reveal" in route


def test_every_row_and_the_dropdown_are_mapped_for_a_non_admin():
    """Both row builders and the strategies list go through the mapping; proven on the source, since the blotter needs a
    database session this test must not open."""
    src = (ROOT / "demo_trade_service.py").read_text(encoding="utf-8")
    body = src[src.index("def blotter("):]
    body = body[:body.index('\n        return {"status": "OK"') + 2000]
    assert body.count('"strategy": r.strategy if reveal else public_key(r.strategy)') == 2, \
        "a row builder still sends the real key"
    assert body.count("strategy_label(r.strategy) if reveal else public_label(r.strategy)") == 2
    assert "internal_key(strategy)" in body, "the incoming filter is not translated"
    assert "public_label(k)" in body and "public_key(k)" in body, "the dropdown list is not mapped"


def test_the_page_shows_whatever_label_the_server_sent():
    """The browser must not hold a mapping of its own: it renders strategy_label as given."""
    assert "r.strategy_label" in PAGE
    for name in ("Grok", "Meta Intraday", "Kimi", "Gemini"):
        assert name not in PAGE, "%s is hard-coded in the page" % name
    assert "{strategies.map((s) => <option value={s.key} key={s.key}>{s.label}</option>)}" in PAGE
