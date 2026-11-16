"""C.1: a user's own MT5 account - connect and read, never an order.

The tests that matter here are not the happy path. They are: the password never comes back out, one user
cannot reach another's account, no consent means no storage, no key means no storage in the clear, and
nothing in C.1 can place an order.
"""
import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

import user_secret_box as box  # noqa: E402

pytest.importorskip("cryptography", reason="C.1 stores the password encrypted; the library is required")

KEY_ENV = "MT5_USER_KEY"
SECRET = "hunter2-the-brokers-password"


@pytest.fixture()
def key(monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv(KEY_ENV, Fernet.generate_key().decode())
    return True


# --------------------------------------------------------------------------- the secret box
def test_without_a_key_nothing_can_be_stored(monkeypatch):
    monkeypatch.delenv(KEY_ENV, raising=False)
    assert box.available() is False
    assert "not set" in (box.why_unavailable() or "")
    with pytest.raises(RuntimeError):
        box.seal(SECRET)                    # a refusal, never a fallback to plaintext


def test_a_malformed_key_disables_the_feature_rather_than_crashing(monkeypatch):
    monkeypatch.setenv(KEY_ENV, "this-is-not-a-fernet-key")
    assert box.available() is False
    assert box.why_unavailable()
    with pytest.raises(RuntimeError):
        box.seal(SECRET)


def test_a_sealed_password_round_trips(key):
    sealed = box.seal(SECRET)
    assert SECRET not in sealed, "the ciphertext must not contain the plaintext"
    assert box.open_(sealed) == SECRET


def test_a_rotated_key_means_reconnect_not_a_crash(key, monkeypatch):
    sealed = box.seal(SECRET)
    from cryptography.fernet import Fernet
    monkeypatch.setenv(KEY_ENV, Fernet.generate_key().decode())
    assert box.open_(sealed) is None        # unreadable, and it says so quietly


def test_an_empty_password_is_refused(key):
    with pytest.raises(ValueError):
        box.seal("")


@pytest.mark.parametrize("login,expect", [("250086", "••••86"), ("12", "••"), ("", "••"), (7, "••")])
def test_an_account_number_is_masked(login, expect):
    assert box.mask(login) == expect


# --------------------------------------------------------------------------- the routes
class _Req:
    """Just enough of a Request for the route functions."""


@pytest.fixture()
def routes(monkeypatch, key):
    import user_mt5_routes as r
    import user_mt5_service as svc
    import user_mt5_store as store

    rows: dict = {}

    def save(username, login, server, password, *, consent):
        if not consent:
            return {"ok": False, "reason": "CONSENT_REQUIRED"}
        if not login or not server or not password:
            return {"ok": False, "reason": "INCOMPLETE"}
        rows[username] = {"login": login, "server": server, "secret": box.seal(password),
                          "enabled": False, "max_shares": 1}
        return {"ok": True, "login_masked": box.mask(login), "server": server, "enabled": False}

    def get(username):
        row = rows.get(username)
        if not row:
            return None
        d = dict(row)
        d.pop("secret", None)
        d["login_masked"] = box.mask(d.pop("login", None))
        d["connected"] = False
        return d

    monkeypatch.setattr(store, "save", save)
    monkeypatch.setattr(store, "get", get)
    monkeypatch.setattr(store, "set_enabled",
                        lambda u, e: ({"ok": True, "enabled": e} if u in rows else {"ok": False, "reason": "NO_ACCOUNT"}))
    monkeypatch.setattr(store, "set_max_shares",
                        lambda u, n: ({"ok": True, "max_shares": n} if 1 <= n <= 1000 else {"ok": False, "reason": "OUT_OF_RANGE"}))
    monkeypatch.setattr(store, "disconnect", lambda u: (rows.pop(u, None), {"ok": True})[1])
    monkeypatch.setattr(svc, "verify", lambda u: {"ok": True, "account": {"balance": 1000.0, "currency": "USD"}})
    return r, rows


def _as(monkeypatch, username):
    import auth_service as auth
    monkeypatch.setattr(auth, "current_user", lambda request: ({"username": username, "role": "user"} if username else None))


def test_a_signed_out_caller_is_refused(routes, monkeypatch):
    from fastapi import HTTPException
    r, _ = routes
    _as(monkeypatch, None)
    with pytest.raises(HTTPException) as e:
        r.my_mt5(_Req())
    assert e.value.status_code == 401


def test_connecting_without_consent_stores_nothing(routes, monkeypatch):
    r, rows = routes
    _as(monkeypatch, "alice")
    out = r.connect(_Req(), payload={"login": "250086", "server": "Demo", "password": SECRET, "consent": False})
    assert out["ok"] is False and out["reason"] == "CONSENT_REQUIRED"
    assert rows == {}, "a password was stored without consent"


def test_the_password_never_comes_back_out(routes, monkeypatch):
    r, _ = routes
    _as(monkeypatch, "alice")
    r.connect(_Req(), payload={"login": "250086", "server": "Demo", "password": SECRET, "consent": True})
    # ensure_ascii=False so the mask characters appear as themselves, not as • escapes
    blob = json.dumps(r.my_mt5(_Req()), default=str, ensure_ascii=False)
    assert SECRET not in blob
    assert "250086" not in blob, "the full account number must not leave the server"
    assert "••86" in blob


def test_one_user_cannot_see_or_change_another_users_account(routes, monkeypatch):
    """The username comes from the session, so there is no identifier in the request to tamper with."""
    r, rows = routes
    _as(monkeypatch, "alice")
    r.connect(_Req(), payload={"login": "111111", "server": "A", "password": SECRET, "consent": True})
    _as(monkeypatch, "bob")
    assert r.my_mt5(_Req())["account"] is None, "bob can see alice's account"
    assert r.set_enabled(_Req(), payload={"enabled": True})["ok"] is False
    r.disconnect(_Req())
    assert "alice" in rows, "bob's disconnect removed alice's account"


def test_disconnect_removes_the_stored_secret(routes, monkeypatch):
    r, rows = routes
    _as(monkeypatch, "alice")
    r.connect(_Req(), payload={"login": "250086", "server": "Demo", "password": SECRET, "consent": True})
    assert "alice" in rows
    r.disconnect(_Req())
    assert "alice" not in rows


def test_the_size_setting_is_bounded(routes, monkeypatch):
    r, _ = routes
    _as(monkeypatch, "alice")
    r.connect(_Req(), payload={"login": "1", "server": "Demo", "password": SECRET, "consent": True})
    assert r.set_size(_Req(), payload={"max_shares": 0})["ok"] is False
    assert r.set_size(_Req(), payload={"max_shares": 5000})["ok"] is False
    assert r.set_size(_Req(), payload={"max_shares": "ten"})["ok"] is False
    assert r.set_size(_Req(), payload={"max_shares": 10})["ok"] is True


def test_the_risk_notice_is_served_with_the_tab(routes, monkeypatch):
    r, _ = routes
    _as(monkeypatch, "alice")
    notice = r.my_mt5(_Req())["risk_notice"]
    for phrase in ("voluntary", "responsible", "lose money", "financial advice"):
        assert phrase in notice.lower()


# --------------------------------------------------------------------------- C.1 cannot trade
def test_c1_exposes_no_route_that_places_or_closes_an_order():
    import user_mt5_routes as r
    paths = {route.path for route in r.router.routes}
    for forbidden in ("order", "buy", "sell", "close", "trade"):
        assert not [p for p in paths if forbidden in p.lower()], "C.1 must not expose %r" % forbidden


def test_c1_service_never_calls_the_order_functions():
    src = (BACKEND / "user_mt5_service.py").read_text(encoding="utf-8")
    for forbidden in ("market_order", "close_position", "modify_position"):
        assert forbidden not in src, "C.1 must not reference %s" % forbidden


def test_a_user_session_never_disturbs_the_operator_session():
    """mt5_bridge keeps ONE session for the operator; a user's connect must hand it back untouched."""
    src = (BACKEND / "user_mt5_service.py").read_text(encoding="utf-8")
    assert "_session.update(token=None" in src, "the operator's session must be restored after a user connects"
