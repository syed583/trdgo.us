"""F: a user's order can never reach the operator's account.

The incident, 2026-11-16: 25 positions opened in the OPERATOR account 250086 carrying "TRDGO copy #...",
with the same ticket numbers recorded in user_mt5_orders. Root cause, found 2026-11-17: C.2 threaded `token`
into symbol(), tick(), positions() and close_position() - and MISSED `_call("OrderSendSafe", req)`, the one
call that places the order. `_call` falls back to the operator's session when no token is given, so every
user order was built from the user's data and sent on the admin's session, silently, reported as FILLED.

The closes DID carry the token, so they went to the user's account, found nothing, and returned "already
closed" with no price - which we recorded as CLOSED. The book read flat while the broker held 25 positions.

These tests fail on the old code and pass on the new.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import mt5_bridge as bridge                                      # noqa: E402
import user_copy_service as CP                                   # noqa: E402
import user_mt5_service as usvc                                  # noqa: E402

OPERATOR_TOKEN = "OPERATOR-TOKEN-250086"
USER_TOKEN = "USER-TOKEN-000094"


# --------------------------------------------------------------------------- the regression itself
def test_the_order_send_carries_the_token():
    """The one line that caused the incident. Asserted on the source so it cannot be dropped again."""
    src = (BACKEND / "mt5_bridge.py").read_text(encoding="utf-8")
    line = [l for l in src.split("\n") if "_call(\"OrderSendSafe\"" in l]
    assert len(line) == 1, "expected one OrderSendSafe call, found %d" % len(line)
    assert "token=token" in line[0], "the order send must carry the session token: %s" % line[0].strip()


def test_every_token_taking_bridge_call_also_takes_strict_token():
    src = (BACKEND / "mt5_bridge.py").read_text(encoding="utf-8")
    for fn in ("def symbol(", "def tick(", "def positions(", "def close_position(", "def market_order("):
        sig = src.split(fn)[1].split("-> dict:")[0]
        assert "strict_token" in sig, "%s cannot refuse a missing user session" % fn


# --------------------------------------------------------------------------- the bridge refuses to guess
@pytest.fixture
def operator_session(monkeypatch):
    sess = {"base_url": "https://b", "token": OPERATOR_TOKEN, "login": 250086, "server": "Live-1"}
    monkeypatch.setattr(bridge, "_session", sess)
    sent = []

    def fake_http_get(url):
        sent.append(url)
        return 200, '{"ok": true}'

    monkeypatch.setattr(bridge, "_http_get", fake_http_get)
    return sent


def test_a_strict_call_without_a_token_is_an_error_not_the_admin_session(operator_session):
    res = bridge._call("OrderSendSafe", {"symbol": "AAPL"}, token=None, strict_token=True)
    assert res["ok"] is False and res["reason"] == "NO_USER_SESSION"
    assert operator_session == [], "a request was sent despite having no user session"


def test_a_strict_call_with_a_token_uses_that_token(operator_session):
    bridge._call("OrderSendSafe", {"symbol": "AAPL"}, token=USER_TOKEN, strict_token=True)
    assert len(operator_session) == 1
    assert USER_TOKEN in operator_session[0]
    assert OPERATOR_TOKEN not in operator_session[0], "the operator's token reached a user call"


def test_the_admin_path_is_unchanged(operator_session):
    """Without strict_token the operator's own session is used exactly as before."""
    bridge._call("OrderSendSafe", {"symbol": "AAPL"})
    assert len(operator_session) == 1 and OPERATOR_TOKEN in operator_session[0]


# --------------------------------------------------------------------------- the session knows its account
def _creds(monkeypatch, *, stored_login="94", session_login="94", operator=False):
    monkeypatch.setattr(usvc.store, "get", lambda u: {"username": u, "login_masked": "..94"})
    monkeypatch.setattr(usvc.store, "secret_of", lambda u: (stored_login, "Live-1", "pw"))
    monkeypatch.setattr(usvc, "_token_for", lambda u: (USER_TOKEN, None))
    monkeypatch.setattr(usvc, "_cached_entry", lambda u: (USER_TOKEN, session_login))
    monkeypatch.setattr(usvc, "is_operator_account", lambda login, server: operator)


def test_a_verified_session_is_returned(monkeypatch):
    _creds(monkeypatch)
    token, err = usvc.session_for("ann")
    assert err is None and token == USER_TOKEN


def test_a_session_for_a_different_account_is_refused(monkeypatch):
    """The account was replaced; the old session must never be reused."""
    _creds(monkeypatch, stored_login="94", session_login="250086")
    forgotten = []
    monkeypatch.setattr(usvc, "forget", lambda u: forgotten.append(u))
    token, err = usvc.session_for("ann")
    assert token is None and err["reason"] == "SESSION_ACCOUNT_MISMATCH"
    assert forgotten == ["ann"], "the mismatched session must be dropped, not kept"


def test_a_session_we_cannot_name_is_refused(monkeypatch):
    _creds(monkeypatch, session_login="")
    monkeypatch.setattr(usvc, "forget", lambda u: None)
    token, err = usvc.session_for("ann")
    assert token is None and err["reason"] == "SESSION_UNVERIFIED"


def test_the_operators_own_account_is_refused_even_if_already_stored(monkeypatch):
    """C.2a blocked this at CONNECT time only. A row stored before C.2a existed must also be refused."""
    _creds(monkeypatch, operator=True)
    token, err = usvc.session_for("ann")
    assert token is None and err["reason"] == "OPERATOR_ACCOUNT"


def test_no_account_is_refused(monkeypatch):
    monkeypatch.setattr(usvc.store, "get", lambda u: None)
    token, err = usvc.session_for("ann")
    assert token is None and err["reason"] == "NO_ACCOUNT"


# --------------------------------------------------------------------------- the copy path uses it
def test_the_copy_path_asks_for_a_verified_session():
    src = (BACKEND / "user_copy_service.py").read_text(encoding="utf-8")
    assert "usvc.session_for" in src
    assert "usvc._token_for" not in src, "the copy path must not use the unverified token helper"


def test_every_user_broker_call_is_strict():
    src = (BACKEND / "user_copy_service.py").read_text(encoding="utf-8")
    # G removed bridge.symbol( from this module: the volume rules now come from the user's stored mapping,
    # verified at map time. The calls that remain must still carry the user's own session.
    for call in ("bridge.tick(", "bridge.market_order(", "bridge.close_position(",
                 'bridge._call("AccountSummary"'):
        idx = src.find(call)
        assert idx > 0, "%s not found" % call
        window = src[idx:idx + 400]
        assert "strict_token=True" in window, "%s can still fall back to the operator session" % call


def test_a_refused_session_stops_the_order(monkeypatch):
    logged = []
    monkeypatch.setattr(CP, "_safe_log", lambda u, e, **k: logged.append((e, k.get("reason"))))
    monkeypatch.setattr(CP.bridge, "available", lambda: True)
    monkeypatch.setattr(CP.usvc, "session_for",
                        lambda u: (None, {"ok": False, "reason": "SESSION_ACCOUNT_MISMATCH", "detail": "x"}))
    out = CP._open_for({"username": "ann", "shares": 10, "max_positions": 20, "daily_loss_pct": 1.0},
                       {"type": "ENTRY", "trade_id": "T1", "symbol": "AAPL", "side": "BUY", "entry": 100.0,
                        "stop": 99.0, "target": 101.0, "strategy": "gemini"})
    assert out["ok"] is False and out["reason"] == "SESSION_ACCOUNT_MISMATCH"
    assert logged and logged[0][0] == "SKIPPED"


# --------------------------------------------------------------------------- honest closes
def test_a_close_with_no_fill_price_is_not_recorded_as_closed():
    """The phantom close: every CLOSED row on 2026-11-16 had price None, and the position stayed open."""
    src = (BACKEND / "user_copy_service.py").read_text(encoding="utf-8")
    assert 'if last.get("ok") and last.get("price") is not None:' in src
    assert "CLOSE_UNCONFIRMED" in src


def test_close_unconfirmed_leaves_the_row_open_for_the_sweep():
    """open_tickets() counts a FILLED row with no matching CLOSED row, so an unconfirmed close is retried."""
    src = (BACKEND / "user_mt5_store.py").read_text(encoding="utf-8")
    block = src.split("def open_tickets")[1].split("def ")[0]
    assert "event = 'FILLED'" in block and "event = 'CLOSED'" in block
    assert "CLOSE_UNCONFIRMED" not in block, "an unconfirmed close must NOT clear the position"


# --------------------------------------------------------------------------- margin
def test_an_account_with_no_equity_is_skipped(monkeypatch):
    logged = []
    monkeypatch.setattr(CP, "_safe_log", lambda u, e, **k: logged.append((e, k.get("reason"))))
    monkeypatch.setattr(CP.bridge, "available", lambda: True)
    monkeypatch.setattr(CP.usvc, "session_for", lambda u: (USER_TOKEN, None))
    monkeypatch.setattr(CP, "_entry_refused", lambda u, ev, token: None)
    monkeypatch.setattr(CP, "_broker_symbol", lambda n, t, s: ("AAPL", {"contract_size": 100.0,
                                                                        "volume_min": 0.01, "volume_step": 0.01,
                                                                        "volume_max": 50.0}))
    monkeypatch.setattr(CP, "_equity", lambda n, t: 0.0)
    out = CP._open_for({"username": "ann", "shares": 10, "max_positions": 20, "daily_loss_pct": 1.0},
                       {"type": "ENTRY", "trade_id": "T1", "symbol": "AAPL", "side": "BUY", "entry": 100.0,
                        "stop": 99.0, "target": 101.0, "strategy": "gemini"})
    assert out["reason"] == "NO_MARGIN"
    assert any(r and r.startswith("NO_MARGIN") for _, r in logged)


# --------------------------------------------------------------------------- the shared broker rate gate
def test_both_books_pass_through_one_gate():
    """Two limiters that cannot see each other do not add up to a limit."""
    admin = (BACKEND / "mt5_autotrade.py").read_text(encoding="utf-8")
    user = (BACKEND / "user_copy_service.py").read_text(encoding="utf-8")
    assert "broker_rate.acquire" in admin, "the operator's order send is not behind the shared gate"
    assert "broker_rate.acquire" in user, "the user path is not behind the shared gate"


def test_the_admin_change_is_one_line():
    """The owner authorised a throttle line in the admin path and nothing else."""
    admin = (BACKEND / "mt5_autotrade.py").read_text(encoding="utf-8")
    assert admin.count("broker_rate") == 2, "more than the single approved admin line touches broker_rate"


def test_the_gate_spaces_orders_out(monkeypatch):
    import broker_rate
    import time as _t
    monkeypatch.setattr(broker_rate, "MAX_PER_SECOND", 20.0)   # a real rate; tests run the gate wide open
    broker_rate.reset()
    t0 = _t.time()
    for _ in range(4):
        broker_rate.acquire("test")
    elapsed = _t.time() - t0
    assert elapsed >= 3 * broker_rate.gap_seconds() * 0.9, "four orders went out faster than the gate allows"
    assert broker_rate.stats()["orders"] == 4


def test_the_gate_never_refuses():
    """A caller is made to wait, never turned away: dropping a trade to keep a rate limit is worse."""
    import broker_rate
    broker_rate.reset()
    assert broker_rate.acquire("test") >= 0.0
    src = (BACKEND / "broker_rate.py").read_text(encoding="utf-8")
    assert "raise" not in src and "return False" not in src


def test_the_rate_is_under_the_brokers_three_per_second():
    import broker_rate
    assert broker_rate.DEFAULT_MAX_PER_SECOND <= 3.0   # the POLICY, not the test override
