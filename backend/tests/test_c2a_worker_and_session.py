"""C.2a: the four faults the owner reported on 2026-11-16, each with a test that fails without the fix.

 1. the operator page said "worker: NOT running" - the admin router was never mounted, so the page was
    rendering a made-up status from a failed request;
 2. connecting a user's account took the OPERATOR's broker session away;
 3. a user could connect the operator's own account;
 4. My Broker offered "1 share a trade" instead of 10.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import mt5_bridge as bridge                                      # noqa: E402
import user_copy_service as CP                                   # noqa: E402
import user_mt5_routes as R                                      # noqa: E402
import user_mt5_service as svc                                   # noqa: E402


# --------------------------------------------------------------------------- 1. the route exists
def test_the_operator_copy_routes_are_mounted():
    """C.2 defined admin_router and nothing included it, so every operator call answered 404."""
    import main
    paths = {r.path for r in main.app.routes}
    for p in ("/api/admin/copy-trading/users", "/api/admin/copy-trading/pause",
              "/api/admin/copy-trading/orders"):
        assert p in paths, "%s is not mounted - the operator page cannot read the status" % p


def test_main_mounts_both_routers_from_that_module():
    """Read main.py: a second router in a module is easy to forget, so the line is asserted."""
    src = (BACKEND / "main.py").read_text(encoding="utf-8")
    assert "admin_router as _copy_admin_router" in src
    assert "app.include_router(_copy_admin_router)" in src


# --------------------------------------------------------------------------- 2. the operator's session is sacred
class FakeOperatorSession(dict):
    """The operator's live session. The test fails if anything blanks it."""


@pytest.fixture
def operator_connected(monkeypatch):
    """A connected operator, and a bridge whose connect() behaves like the real one."""
    session = {"base_url": "https://b", "token": "OPERATOR-TOKEN", "login": 250086, "server": "Live-1"}
    monkeypatch.setattr(bridge, "_session", session)

    def fake_call(path, params=None, *, with_token=True, base_url=None, token=None):
        if path == "ConnectEx":
            return {"ok": True, "data": {"id": "USER-TOKEN"}}
        if path == "AccountSummary":
            # the real one refuses when there is no token, which is how the owner's error appeared
            if not token and not session.get("token"):
                return {"ok": False, "reason": "NOT_CONNECTED", "detail": "no session token: connect first"}
            return {"ok": True, "data": {"balance": 1000.0, "equity": 1000.0, "currency": "USD"}}
        return {"ok": False, "reason": "UNEXPECTED", "detail": path}

    monkeypatch.setattr(bridge, "_call", fake_call)
    return session


def test_a_user_connect_leaves_the_operator_session_untouched(operator_connected, monkeypatch):
    monkeypatch.setattr(svc.store, "secret_of", lambda u: ("999999", "Other-Server", "pw"))
    monkeypatch.setattr(svc.store, "record_check", lambda *a, **k: None)
    out = svc._connect("ann")
    assert out["ok"] is True, out
    assert operator_connected["token"] == "OPERATOR-TOKEN", "the operator's token was taken away"
    assert operator_connected["login"] == 250086
    assert operator_connected["server"] == "Live-1"


def test_the_user_gets_its_own_token_not_the_operators(operator_connected, monkeypatch):
    monkeypatch.setattr(svc.store, "secret_of", lambda u: ("999999", "Other-Server", "pw"))
    monkeypatch.setattr(svc.store, "record_check", lambda *a, **k: None)
    svc.forget("ann")
    svc._connect("ann")
    assert svc._cached_token("ann") == "USER-TOKEN"
    assert svc._cached_token("ann") != operator_connected["token"]


def test_an_admin_call_during_a_user_connect_still_works(operator_connected, monkeypatch):
    """The real failure: the admin called AccountSummary while the user's connect was in flight."""
    monkeypatch.setattr(svc.store, "record_check", lambda *a, **k: None)
    seen = {}

    def secret_of(u):
        # while we are inside the connect path, check what the operator would see
        seen["admin"] = bridge.account()
        return ("999999", "Other-Server", "pw")

    monkeypatch.setattr(svc.store, "secret_of", secret_of)
    svc._connect("ann")
    assert seen["admin"]["ok"] is True, "the operator lost the session mid-connect: %s" % seen["admin"]


def test_the_bridge_connect_can_be_given_its_own_session(operator_connected):
    mine = {"base_url": None, "token": None, "login": None, "server": None}
    res = bridge.connect(999999, "pw", "Other-Server", session=mine)
    assert res["ok"] is True
    assert mine["token"] == "USER-TOKEN"
    assert operator_connected["token"] == "OPERATOR-TOKEN"


def test_the_operator_still_connects_the_old_way(operator_connected):
    """No `session` argument must behave exactly as before: the module session is filled in."""
    res = bridge.connect(250086, "pw", "Live-1")
    assert res["ok"] is True
    assert operator_connected["token"] == "USER-TOKEN"      # the fake returns one token for everyone
    assert operator_connected["login"] == 250086


def test_the_user_service_never_mentions_the_operator_session():
    src = (BACKEND / "user_mt5_service.py").read_text(encoding="utf-8")
    assert "_session" not in src, "the user path must not read or write the operator's session"


# --------------------------------------------------------------------------- 3. not our own account
def test_the_operators_own_account_is_refused(monkeypatch):
    monkeypatch.setattr(R, "_me", lambda request: "ann")
    monkeypatch.setattr(svc, "is_operator_account", lambda login, server: True)
    called = {}
    monkeypatch.setattr(R.store, "save", lambda *a, **k: called.setdefault("saved", True))
    out = R.connect(None, payload={"login": "250086", "server": "Live-1", "password": "x", "consent": True})
    assert out["ok"] is False and out["reason"] == "OPERATOR_ACCOUNT"
    assert "saved" not in called, "the password was stored before the account was refused"


@pytest.mark.parametrize("login,server,expect", [
    ("250086", "STGlobal-Live-1", True),
    (" 250086 ", "stglobal-live-1", True),        # whitespace and case must not get past it
    ("250087", "STGlobal-Live-1", False),
    ("250086", "Someone-Else-Live", False),
    ("not-a-number", "STGlobal-Live-1", False),
])
def test_is_operator_account(monkeypatch, login, server, expect):
    import mt5_autotrade as AT
    monkeypatch.setattr(AT, "load_credentials",
                        lambda: {"login": 250086, "server": "STGlobal-Live-1", "password": "never-read"})
    assert svc.is_operator_account(login, server) is expect


def test_no_saved_credentials_means_nothing_to_clash_with(monkeypatch):
    import mt5_autotrade as AT
    monkeypatch.setattr(AT, "load_credentials", lambda: None)
    assert svc.is_operator_account("250086", "Live-1") is False


# --------------------------------------------------------------------------- 4. the worker reports itself
def test_a_running_worker_reports_no_error(monkeypatch):
    alive = threading.Thread(target=lambda: None)
    monkeypatch.setattr(CP, "_worker", type("T", (), {"is_alive": staticmethod(lambda: True)})())
    assert CP.worker_error() is None


def test_a_worker_that_never_started_says_so(monkeypatch):
    monkeypatch.setattr(CP, "_worker", None)
    monkeypatch.setattr(CP, "_start_error", None)
    assert "never started" in CP.worker_error()


def test_a_failed_start_keeps_the_reason_and_raises(monkeypatch):
    monkeypatch.setattr(CP, "_worker", None)
    monkeypatch.setattr(CP, "_start_error", None)

    def boom(*a, **k):
        raise RuntimeError("no threads today")

    monkeypatch.setattr(CP.threading, "Thread", boom)
    with pytest.raises(RuntimeError):
        CP.start()
    assert "no threads today" in CP.worker_error()


def test_the_startup_hook_writes_to_mt5_log():
    """A failure logged to a logger with no file handler leaves no trace; mt5.log is read by the operator."""
    src = (BACKEND / "main.py").read_text(encoding="utf-8")
    block = src.split("def _start_user_copy_trading")[1].split("\n@_startup")[0]
    assert 'file_logger("mt5")' in block
    assert "COPY TRADING WORKER FAILED TO START" in block
    assert 'getLogger("user_copy")' not in block


def test_the_operator_route_returns_the_worker_error(monkeypatch):
    monkeypatch.setattr(R, "_operator", lambda request: None)
    monkeypatch.setattr(R.store, "all_accounts", lambda: [])
    monkeypatch.setattr(R.copysvc, "_worker", None)
    monkeypatch.setattr(R.copysvc, "_start_error", "boom")
    out = R.copy_users(None)
    assert out["worker_running"] is False and out["worker_error"] == "boom"


def test_the_page_does_not_call_an_unanswered_request_not_running():
    src = (BACKEND.parent / "frontend" / "src" / "pages" / "CopyTradingPage.tsx").read_text(encoding="utf-8")
    assert "const answered" in src
    assert "d.worker_running ? 'running' : 'NOT running'" not in src, \
        "the page can still print NOT running for a failed request"


# --------------------------------------------------------------------------- 5. the share default
def test_the_code_default_is_ten():
    import user_mt5_store as store
    assert store.DEFAULT_SHARES == 10


def test_the_new_table_default_is_ten():
    import user_mt5_store as store
    src = (BACKEND / "user_mt5_store.py").read_text(encoding="utf-8")
    assert "ALTER COLUMN max_shares SET DEFAULT %d" in src
    assert "max_shares = 1 AND auto_copy = FALSE" in src, \
        "the migration must only lift rows still on the old default with Auto-copy off"


def test_the_migration_leaves_a_deliberate_choice_alone():
    """A user already copying at 1 share chose that; the WHERE clause must exclude them."""
    src = (BACKEND / "user_mt5_store.py").read_text(encoding="utf-8")
    clause = src.split("UPDATE %s SET max_shares")[1].split('"""')[0]
    assert "auto_copy = FALSE" in clause
    assert "NOT IN (SELECT DISTINCT username FROM %s)" in clause, \
        "a user with order history must not be rewritten"


# --------------------------------------------------------------------------- 6. the session watcher
@pytest.fixture
def watcher(monkeypatch):
    import mt5_session_watch as SW
    monkeypatch.setattr(SW, "_state", {"checks": 0, "drops": 0, "reconnects": 0, "failures": 0,
                                       "last_drop": None, "last_reconnect": None, "last_error": None,
                                       "consecutive_failures": 0})
    return SW


def _fake_engine(monkeypatch, *, engine_connected, bridge_token, reconnect=None):
    import mt5_autotrade as AT
    monkeypatch.setattr(AT, "is_connected", lambda: engine_connected)
    monkeypatch.setattr(bridge, "connected", lambda: bool(bridge_token))
    monkeypatch.setattr(bridge, "available", lambda: True)
    if reconnect is not None:
        monkeypatch.setattr(AT, "reconnect_from_saved", reconnect)


def test_a_lost_session_is_reconnected(watcher, monkeypatch):
    calls = []
    _fake_engine(monkeypatch, engine_connected=True, bridge_token=None,
                 reconnect=lambda: (calls.append(1), {"ok": True})[1])
    out = watcher.check_once()
    assert out["action"] == "RECONNECTED" and len(calls) == 1
    assert watcher.state()["reconnects"] == 1


def test_a_healthy_session_is_left_alone(watcher, monkeypatch):
    calls = []
    _fake_engine(monkeypatch, engine_connected=True, bridge_token="T",
                 reconnect=lambda: (calls.append(1), {"ok": True})[1])
    assert watcher.check_once()["reason"] == "SESSION_HEALTHY"
    assert calls == [], "a healthy session must not be reconnected"


def test_a_deliberate_disconnect_is_not_undone(watcher, monkeypatch):
    calls = []
    _fake_engine(monkeypatch, engine_connected=False, bridge_token=None,
                 reconnect=lambda: (calls.append(1), {"ok": True})[1])
    assert watcher.check_once()["reason"] == "ENGINE_NOT_CONNECTED"
    assert calls == [], "the operator disconnected on purpose; reconnecting behind them would be wrong"


def test_a_failed_reconnect_is_recorded_and_backs_off(watcher, monkeypatch):
    _fake_engine(monkeypatch, engine_connected=True, bridge_token=None,
                 reconnect=lambda: {"ok": False, "reason": "NO_CREDENTIALS", "detail": "none saved"})
    first = watcher._wait_for()
    watcher.check_once()
    second = watcher._wait_for()
    watcher.check_once()
    third = watcher._wait_for()
    assert watcher.state()["failures"] == 2
    assert "NO_CREDENTIALS" in watcher.state()["last_error"]
    assert first == watcher.POLL_S and second == watcher.BACKOFF_S[0] and third == watcher.BACKOFF_S[1]


def test_a_raising_reconnect_does_not_kill_the_watcher(watcher, monkeypatch):
    def boom():
        raise RuntimeError("provider down")

    _fake_engine(monkeypatch, engine_connected=True, bridge_token=None, reconnect=boom)
    out = watcher.check_once()
    assert out["ok"] is False and "provider down" in out["detail"]


def test_the_watcher_places_no_orders():
    src = (BACKEND / "mt5_session_watch.py").read_text(encoding="utf-8")
    for forbidden in ("market_order", "close_position", "on_paper_event", "sizing("):
        assert forbidden not in src, "the watcher must not touch trading: found %s" % forbidden


def test_no_order_is_sent_while_disconnected(monkeypatch):
    """The existing guarantee, asserted here so C.2a cannot be read as having relaxed it."""
    import mt5_autotrade as AT
    monkeypatch.setattr(AT, "auto_trade_enabled", lambda: True)
    monkeypatch.setattr(AT, "paused", lambda: False)
    monkeypatch.setattr(AT, "halted", lambda: False)
    monkeypatch.setattr(AT.B, "available", lambda: True)
    monkeypatch.setattr(AT, "_conn", {"connected": False, "account": None})
    assert AT._entry_allowed({"symbol": "AAPL"}) == "NOT_CONNECTED"


def test_the_watcher_can_be_switched_off(monkeypatch):
    import mt5_session_watch as SW
    monkeypatch.setenv("MT5_SESSION_WATCH", "0")
    assert SW.enabled() is False
    monkeypatch.delenv("MT5_SESSION_WATCH")
    assert SW.enabled() is True
