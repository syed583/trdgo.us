"""The release checker must catch every kind of unsafe build, so a deploy or a rollback can never silently allow
trade entries without the mandatory 15-minute confirmation, the stale-data protection or the connection cap."""
import logging
import subprocess

import pytest

import intraday_15m as c15
import release_check as rc
import tab_settings as cfg

REAL = rc.read_tree()


def tree(**changes):
    """A reader over the real working tree with some files replaced (value) or removed (None)."""
    def read(path):
        key = path.replace("/", "__").replace(".", "_")
        return changes[key] if key in changes else REAL(path)
    return read


def edited(path, fn):
    text = REAL(path)
    assert text is not None
    new = fn(text)
    assert new != text, f"the edit did not change {path}"
    return {path.replace("/", "__").replace(".", "_"): new}


def failed(read):
    return {c["check"] for c in rc.check(read)["checks"] if not c["ok"]}


def test_the_working_tree_passes_every_check():
    r = rc.check(REAL)
    assert r["ok"], [c for c in r["checks"] if not c["ok"]]


def test_an_old_build_with_none_of_it_fails_everything_present():
    r = rc.check(lambda path: None)
    assert not r["ok"] and all(not c["ok"] for c in r["checks"])


# --------------------------------------------------- unsafe build / missing module ---
def test_a_missing_15_minute_module_is_detected():
    bad = failed(tree(backend__intraday_15m_py=None))
    assert {"15-minute candle module present", "one entry gate for every path (entry_check)"} <= bad


@pytest.mark.parametrize("path, fn, expect", [
    # ----------------------------------------------- the confirmation disabled or bypassable
    ("backend/tab_settings.py", lambda t: t.replace('"bar_minutes": 15,', '"bar_minutes": 15,\n    "require_confirmation": False,'),
     "no settings switch can disable the confirmation"),
    ("backend/intraday_15m.py", lambda t: t + '\n# ALLOW_UNCONFIRMED_ENTRIES == "I_UNDERSTAND"\n',
     "no environment override can disable the confirmation"),
    ("backend/intraday_15m.py", lambda t: t.replace('if not c.get("confirmed"):', 'if False:'),
     "the gate refuses unless the reading is CONFIRMED"),
    ("backend/intraday_15m.py", lambda t: t.replace('if phase != "rth":', 'if False:'),
     "the gate refuses outside the regular session"),
    # ----------------------------------------------- an entry path that does not ask the gate
    ("backend/demo_trade_service.py", lambda t: t.replace("execution=True", ""),
     "paper-trade tracker checks at the moment of opening"),
    ("backend/intraday_signal_v2.py", lambda t: t.replace("execution=True", ""),
     "live engine checks before opening and again at the insert"),
    ("backend/intraday_safety.py", lambda t: t.replace("entry_check(", "entry_chck("),
     "tradable() asks the gate directly"),
    ("backend/intraday_safety.py", lambda t: t.replace("entry_check(sym, sig, phase)", "None"),
     "boards demote an unconfirmed BUY/SELL to WAIT"),
    ("backend/entry_lock.py", lambda t: t.replace("def install(", "def setup("),
     "operator entry lock with database triggers"),
    # ----------------------------------------------- the position lifecycle
    ("backend/intraday_signal_v2.py", lambda t: t.replace("rows = _open_rows(db, symbol, now)", "rows = _open_rows(db, symbol, now)[-1:]"),
     "every open live signal row is managed, not only the newest"),
    ("backend/intraday_signal_v2.py", lambda t: t.replace("if _open_rows(db, symbol, now):", "if False:"),
     "a second open live signal for a symbol is refused"),
    ("backend/release_tool.py", lambda t: t.replace("signal_recovery.assess(engine, now=now)", "{'ok': True, 'problems': []}"),
     "a cut-over validates every open signal before it proceeds"),
    ("backend/release_tool.py", lambda t: t.replace("open paper positions exist", "open things exist"),
     "open paper positions block a cut-over"),
    ("backend/release_tool.py", lambda t: t.replace("set_locked(True, reason, engine, for_rollback=True)", "set_locked(True, reason, engine)"),
     "a rollback needs an empty book"),
    ("backend/signal_recovery.py", lambda t: t.replace("def validate(", "def check_rows("),
     "the signal recovery validator exists"),
    ("backend/intraday_signal_v2.py", lambda t: t.replace("def expire_stale(apply: bool = False", "def expire_old(apply: bool = False"),
     "stale open signals are never closed automatically"),
    ("backend/demo_trade_service.py", lambda t: t.replace('filter_by(status="OPEN").order_by', 'filter_by(session_date=today, status="OPEN").order_by'),
     "paper positions are carried across dates"),
    ("backend/demo_trade_service.py", lambda t: t.replace("    if not market_calendar.is_trading_day(n.date()) or n < market_calendar.close_datetime(n.date()):",
                                                       "    if t.session_date < n.date():\n        return True\n"
                                                       "    if not market_calendar.is_trading_day(n.date()) or n < market_calendar.close_datetime(n.date()):"),
     "a position is never closed merely because the date changed"),
    ("backend/demo_trade_service.py", lambda t: t.replace('DemoTrade.status == "OPEN")', 'True)'),
     "paper exits are guarded against a double close"),
    ("backend/intraday_signal_v2.py", lambda t: t.replace("WHERE symbol=:s AND is_open=TRUE AND session_date>=:c ORDER BY id ASC",
                                                         "WHERE symbol=:s AND session_date=:d AND is_open=TRUE AND session_date>=:c ORDER BY id ASC"),
     "live signals are managed whichever session opened them"),
    ("backend/intraday_signal_v2.py", lambda t: t.replace("WHERE id=:id AND is_open=TRUE", "WHERE id=:id"),
     "live exits are guarded against a double close"),
    ("backend/market_calendar.py", lambda t: t.replace("def first_close_after", "def first_close"),
     "exchange-holiday-aware close rule"),
    # ----------------------------------------------- stale-data protection removed
    ("backend/intraday_safety.py", lambda t: t.replace("def gate(", "def gate_removed("), "stale-data gate present"),
    ("backend/intraday_feed.py", lambda t: t.replace("def effective_inputs(", "def inputs("),
     "per-signal stale-data limits"),
    ("backend/intraday_feed.py", lambda t: t.replace("missing_critical", "missing_x"), "critical inputs missing -> WAIT"),
    # ----------------------------------------------- concurrency controls removed
    ("backend/unusualwhales_service.py", lambda t: t.replace("PROVIDER_CONCURRENCY = 3", "PROVIDER_CONCURRENCY = 8"),
     "three-connection cap"),
    ("backend/uw_slots.py", lambda t: t.replace("class MachineSlots", "class Other"), "machine-wide connection slots"),
    ("backend/uw_priority.py", lambda t: t.replace("def brake(", "def other("), "priority gate with adaptive brake"),
])
def test_each_kind_of_unsafe_change_is_detected(path, fn, expect):
    bad = failed(tree(**edited(path, fn)))
    assert expect in bad, bad


def test_a_deleted_file_fails_its_own_checks():
    for path, expect in (("backend/uw_slots.py", "machine-wide connection slots"),
                         ("backend/entry_lock.py", "operator entry lock with database triggers"),
                         ("backend/intraday_safety.py", "stale-data gate present")):
        assert expect in failed(tree(**{path.replace("/", "__").replace(".", "_"): None}))


# --------------------------------------------------------------- real git revisions ---
def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def test_a_git_revision_is_judged_by_its_own_files_not_the_working_tree(tmp_path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "tab_settings.py").write_text("X = 1\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "-c", "user.email=a@b", "-c", "user.name=t", "add", "-A")
    _git(tmp_path, "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-qm", "old build")
    assert not rc.check(rc.read_rev("HEAD", tmp_path))["ok"]


def test_identify_reports_the_checked_out_commit_and_its_relation_to_main(tmp_path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "x.py").write_text("1\n")
    _git(tmp_path, "init", "-q", "-b", "main")
    ident = ["-c", "user.email=a@b", "-c", "user.name=t"]
    _git(tmp_path, *ident, "add", "-A")
    _git(tmp_path, *ident, "commit", "-qm", "one")
    _git(tmp_path, "checkout", "-q", "-b", "feature")
    (tmp_path / "backend" / "x.py").write_text("2\n")
    _git(tmp_path, *ident, "commit", "-qam", "two")
    info = rc.identify(tmp_path)
    assert info["branch"] == "feature" and info["head"] and info["subject"] == "two"
    assert info["relation"]["main"]["same_commit"] is False and info["relation"]["main"]["this_build_is_ahead_by"] == "1"
    assert info["relation"]["origin/main"] is None                    # never assume origin/main exists or matches
    assert info["passes_release_check"] is False and info["uncommitted_changes"] is False
    (tmp_path / "backend" / "x.py").write_text("3\n")
    assert rc.identify(tmp_path)["uncommitted_changes"] is True


# ------------------------------------------------------- the running build / the boot ---
def test_no_override_exists_and_trying_to_configure_around_it_is_reported(monkeypatch, caplog):
    monkeypatch.setenv("ALLOW_UNCONFIRMED_ENTRIES", "I_UNDERSTAND")
    monkeypatch.setitem(cfg.INTRADAY_15M, "require_confirmation", False)
    with caplog.at_level(logging.CRITICAL):
        assert c15.assert_enforced() is True
    assert any("no such override" in r.message for r in caplog.records)
    assert c15.status()["confirm15_required"] is True and c15.status()["can_be_disabled"] is False


def test_the_status_route_reports_what_a_deploy_check_needs():
    import api_routes
    s = api_routes.safety_status()
    assert s["confirm15_required"] is True and s["can_be_disabled"] is False and s["static_checks"] is True
    assert {"build", "entry_lock", "bar_minutes"} <= set(s)


def test_the_startup_guard_is_registered_in_the_app():
    import main
    assert any(getattr(f, "__name__", "") == "_enforce_fifteen_minute_confirmation" for f in main._startup_handlers)
