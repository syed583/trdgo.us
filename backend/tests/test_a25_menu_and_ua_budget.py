"""A.25 (owner decisions 2026-11-13 after the Earnings & news audit):
  * the seven per-model boards leave the MENU while their routes and modules stay;
  * Unusual Activity keeps refreshing in market hours (option B) but is bounded so the live intraday boards and the
    quotes the tracker and MT5 depend on always come first: labelled calls, at most ONE connection at a time, a
    stand-aside rule that applies even inside the essential class, its own 600/hour ceiling, and a 900 s series cache;
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
def test_unusual_activity_calls_are_labelled_and_run_in_the_essential_class():
    assert ua.MODULE == "unusual_activity"
    assert ua._priority() == pri.ESSENTIAL                      # option B: it keeps refreshing while the market is open
    with ua._labelled():
        prio, module, _ = pri.current()
    assert (prio, module) == (pri.ESSENTIAL, "unusual_activity")
    assert pri.is_explicit() is False                            # the label is gone again outside the block


def test_the_tab_may_hold_only_one_connection_so_a_slot_is_always_free_for_the_boards():
    """The guarantee that matters: with two connections in production, this tab can never hold both."""
    import threading
    assert ua._max_inflight() == 1
    inside, blocked = threading.Event(), threading.Event()

    def hold():
        with ua._labelled():
            inside.set()
            blocked.wait(2.0)

    t = threading.Thread(target=hold, daemon=True)
    t.start()
    assert inside.wait(2.0)
    second = threading.Event()

    def race():
        with ua._labelled():
            second.set()

    t2 = threading.Thread(target=race, daemon=True)
    t2.start()
    assert second.wait(0.4) is False, "a second call got through: the tab could hold both connections"
    blocked.set()
    t.join(2.0)
    assert second.wait(2.0) is True                               # ... and it proceeds as soon as the first finishes
    t2.join(2.0)


def test_the_label_replaces_the_anonymous_promotion_with_a_named_one():
    """These paths were promoted to ESSENTIAL anonymously, under the shared "market_data" name. They still run in the
    essential class, but now under this tab's own name, which is what the caps and the stand-aside rule key on."""
    import uw_freshness as fresh
    import unusualwhales_service as uwsvc
    for path in ("/api/option-activity/unusual", "/api/stock/AAPL/options-volume"):
        assert fresh.classify(path) in uwsvc._ESSENTIAL_TYPES
    with ua._labelled():
        assert pri.is_explicit() is True and pri.current()[1] == "unusual_activity"


def test_the_tab_stands_aside_while_live_work_is_queueing_even_in_the_essential_class():
    b = uw_budget.Controller(dict(cfg.UW_BUDGET))
    wait = float(cfg.UW_BUDGET["yield_wait_s"]) + 1.0
    for _ in range(int(cfg.UW_BUDGET["yield_min_samples"]) + 2):
        b.note_wait(pri.LIVE, wait)
    d = b.admit(pri.ESSENTIAL, "unusual_activity")
    assert d.ok is False and d.code == "YIELD_TO_LIVE_QUEUE"
    assert b.admit(pri.ESSENTIAL, "ai_boards").ok is True         # the boards themselves never stand aside
    assert b.admit(pri.LIVE, "intraday_engine").ok is True


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


def test_the_cap_binds_in_the_essential_class_where_the_tab_now_runs():
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


def test_the_steady_request_rate_is_a_third_of_what_it_was_and_fits_the_hourly_cap():
    """Part B's sustained rate is (universe / series TTL), plus one market-wide pull per scan cache."""
    before, after = 300.0, cfg.UA_OV_TTL_SEC
    cap = cfg.UW_BUDGET["module_hourly_caps"]["unusual_activity"]
    for universe in (40, 80, 120):
        per_min = universe / after * 60 + 60.0 / cfg.UA_REFRESH_SEC
        assert round(universe / after * 60, 4) == round(universe / before * 60 / 3, 4)
        assert per_min * 60 <= cap, "universe %d would exceed the tab's own hourly ceiling" % universe


def test_refresh_state_tells_the_page_what_is_happening(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr(uw_budget.get(), "in_session", lambda: True, raising=False)
    st = ua.refresh_state()
    assert st["priority"] == "essential" and st["hourly_cap"] == 600 and st["max_inflight"] == 1
    assert st["paused"] is False and st["note"] == "refreshing normally"     # option B: it refreshes in session
    monkeypatch.setattr(uw_budget.get(), "live_wait_p95", lambda: 99.0, raising=False)
    assert ua.refresh_state()["standing_aside"] is True
    monkeypatch.setattr(uw_budget.get(), "live_wait_p95", lambda: 0.0, raising=False)
    monkeypatch.setattr(uw_budget.get(), "module_hour_calls", lambda m: 600, raising=False)
    st = ua.refresh_state()
    assert st["capped"] is True and "hourly allowance" in st["note"]


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
    """Everything A.25 changed, measured against the release it replaces (A.24 = 38a030fd), plus anything still
    uncommitted. Independent of whether the work has been committed yet."""
    import subprocess
    names = set()
    for args in (["diff", "--name-only", "38a030fd..HEAD"], ["diff", "--name-only"]):
        out = subprocess.run(["git", "-C", str(ROOT.parent), *args], capture_output=True, text=True).stdout
        names.update(l.strip() for l in out.splitlines() if l.strip())
    changed = sorted(n for n in names if not n.startswith("deploy/intraday_cleanup/"))   # the built package mirrors the source
    assert changed, "expected a diff against A.24 to inspect"
    for f in changed:
        low = f.lower()
        assert "smart_move" not in low and "smlive" not in low, f
        assert "mt5" not in low, f
        assert "intraday" not in low, f
