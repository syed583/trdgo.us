"""A.25 (owner decisions 2026-11-13 after the Earnings & news audit):
  * the seven per-model boards leave the MENU while their routes and modules stay;
  * Unusual Activity stops competing with the live intraday boards: labelled calls, its own named hourly cap,
    and a 900 s per-symbol series cache instead of 300 s;
  * the Claude board no longer warms 12 symbols on every page load, while the identical warm in the
    Pre-Earnings Direction route is deliberately kept.
No provider request is made by these tests.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tab_settings as cfg  # noqa: E402
import unusual_activity_service as ua  # noqa: E402
import uw_budget  # noqa: E402
import uw_priority as pri  # noqa: E402

SIDEBAR = ROOT.parent / "frontend" / "src" / "components" / "Sidebar.tsx"
APP = ROOT.parent / "frontend" / "src" / "App.tsx"
ROUTES = (ROOT / "api_routes.py").read_text(encoding="utf-8")
HIDDEN = ("/claude-last", "/grok", "/meta", "/chatgpt", "/kimi", "/gemini", "/claude-pro")


# ----------------------------------------------------------------- the menu
def test_the_seven_boards_are_out_of_the_menu_but_their_routes_still_exist():
    side = SIDEBAR.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")
    for path in HIDDEN:
        assert ("to: '%s'" % path) not in side, "%s is still a menu item" % path
        assert ('path="%s"' % path) in app, "%s lost its route: the board must stay reachable" % path
    # the ones the owner kept are untouched
    for path in ("/earnings-calendar", "/all-models", "/model-accuracy", "/option-scanner", "/unusual-activity",
                 "/pre-earnings-direction", "/earnings-signals", "/forward-scoreboard", "/historical-backtest",
                 "/post-earnings-forward"):
        assert ("to: '%s'" % path) in side, "%s should still be in the menu" % path


def test_the_intraday_boards_keep_their_menu_entries():
    side = SIDEBAR.read_text(encoding="utf-8")
    for path in ("/grok-intraday", "/meta-intraday", "/kimi-intraday", "/gemini-intraday"):
        assert ("to: '%s'" % path) in side


# ----------------------------------------------------------------- Unusual Activity priority and budget
def test_unusual_activity_calls_are_labelled_below_the_live_boards():
    assert ua.MODULE == "unusual_activity"
    assert ua._priority() == pri.DASHBOARD                      # owner decision: demoted from the essential class
    with ua._labelled():
        prio, module, _ = pri.current()
    assert (prio, module) == (pri.DASHBOARD, "unusual_activity")
    assert pri.is_explicit() is False                            # the label is gone again outside the block


def test_the_label_stops_the_promotion_that_made_this_tab_essential():
    """An anonymous page call on these paths is promoted to ESSENTIAL; a labelled one is not."""
    import uw_freshness as fresh
    import unusualwhales_service as uwsvc
    for path in ("/api/option-activity/unusual", "/api/stock/AAPL/options-volume"):
        assert fresh.classify(path) in uwsvc._ESSENTIAL_TYPES    # why it used to be promoted
    with ua._labelled():
        assert pri.is_explicit() is True                         # ... and why it is not any more


def test_the_tab_has_its_own_named_hourly_cap_and_it_binds_at_every_priority():
    caps = cfg.UW_BUDGET.get("module_hourly_caps") or {}
    assert caps.get("unusual_activity") == 600
    b = uw_budget.Controller(dict(cfg.UW_BUDGET))
    for _ in range(600):
        b.record_call(pri.DASHBOARD, "unusual_activity", "ok")
    d = b.admit(pri.DASHBOARD, "unusual_activity")
    assert d.ok is False and d.code == "MODULE_HOUR_CAP"
    assert b.admit(pri.DASHBOARD, "other_page").ok is True       # the cap is this module's alone
    assert b.admit(pri.ESSENTIAL, "ai_boards").ok is True        # the live boards are untouched by it


def test_the_cap_also_binds_if_the_tab_is_ever_put_back_in_the_essential_class():
    b = uw_budget.Controller(dict(cfg.UW_BUDGET))
    for _ in range(600):
        b.record_call(pri.ESSENTIAL, "unusual_activity", "ok")
    assert b.admit(pri.ESSENTIAL, "unusual_activity").code == "MODULE_HOUR_CAP"


# ----------------------------------------------------------------- the cache that drives the request rate
def test_the_per_symbol_series_cache_is_900_seconds():
    assert cfg.UA_OV_TTL_SEC == 900.0
    assert cfg.UA_REFRESH_SEC == 300.0                           # the scan cadence itself is unchanged
    src = (ROOT / "unusual_activity_service.py").read_text(encoding="utf-8")
    m = re.search(r"def _ov_series_warm.*?swr\.serve\((.*?)\)\n", src, re.S)
    assert m and "UA_OV_TTL_SEC" in m.group(1), "the series warm must use the new TTL"


def test_the_steady_request_rate_is_a_third_of_what_it_was():
    """Part B's sustained rate is (universe / series TTL). The TTL decides it; nothing else changed."""
    before, after = 300.0, cfg.UA_OV_TTL_SEC
    for universe in (40, 80, 120):
        assert round(universe / after * 60, 4) == round(universe / before * 60 / 3, 4)


def test_refresh_state_tells_the_page_what_is_happening(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr(uw_budget.get(), "in_session", lambda: True, raising=False)
    st = ua.refresh_state()
    assert st["paused"] is True and st["priority"] == "dashboard" and st["hourly_cap"] == 600
    assert "last completed scan" in st["note"] or "showing its last" in st["note"]
    monkeypatch.setattr(uw_budget.get(), "in_session", lambda: False, raising=False)
    assert ua.refresh_state()["paused"] is False


# ----------------------------------------------------------------- the warm that went with the menu entry
def test_the_claude_board_no_longer_warms_on_every_page_load():
    scan = ROUTES[ROUTES.index("def claude_last_scan"):]
    scan = scan[:scan.index("\n@router.")]
    assert "warm_earnings" not in scan
    assert "A.25" in scan


def test_the_pre_earnings_direction_warm_is_deliberately_untouched():
    ped = ROUTES[ROUTES.index("def pre_earnings_direction_scan"):]
    ped = ped[:ped.index("\n@router.")]
    assert "warm_earnings" in ped and "missing" in ped


def test_nothing_in_this_change_touches_smart_move_mt5_or_the_intraday_boards():
    import subprocess
    out = subprocess.run(["git", "-C", str(ROOT.parent), "diff", "--name-only"], capture_output=True, text=True).stdout
    changed = [l for l in out.splitlines() if l.strip()]
    assert changed, "expected a working-tree diff to inspect"
    for f in changed:
        low = f.lower()
        assert "smart_move" not in low and "smlive" not in low, f
        assert "mt5" not in low, f
        assert "intraday" not in low, f
