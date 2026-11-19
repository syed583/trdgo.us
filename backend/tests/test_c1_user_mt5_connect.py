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

    # C.2 reads and writes the copy settings through the same store. Stubbed here with the same in-memory rows,
    # so these tests stay what they are - route and privacy tests that never touch a database.
    def settings_of(u):
        row = rows.get(u)
        if not row:
            return {"shares": 10, "max_positions": 20, "daily_loss_pct": 1.0, "auto_copy": False,
                    "enabled": False, "admin_paused": False, "has_account": False}
        return {"shares": row.get("max_shares", 10), "max_positions": 20, "daily_loss_pct": 1.0,
                "auto_copy": bool(row.get("auto_copy")), "enabled": bool(row.get("enabled")),
                "admin_paused": False, "has_account": True}

    def update_settings(u, patch):
        row = rows.get(u)
        if not row:
            return {"ok": False, "reason": "NO_ACCOUNT"}
        if "shares" in patch:
            try:
                n = int(patch["shares"])
            except (TypeError, ValueError):
                return {"ok": False, "reason": "BAD_VALUE"}
            if not (1 <= n <= 1000):
                return {"ok": False, "reason": "OUT_OF_RANGE"}
            row["max_shares"] = n
        if "auto_copy" in patch:
            row["auto_copy"] = bool(patch["auto_copy"])
        return dict(settings_of(u), ok=True)

    monkeypatch.setattr(store, "settings_of", settings_of)
    monkeypatch.setattr(store, "update_settings", update_settings)
    monkeypatch.setattr(store, "open_tickets", lambda u: [])
    monkeypatch.setattr(store, "realized_today", lambda u: 0.0)
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


# --------------------------------------------------------------------------- no route PLACES an order
def test_no_user_route_places_or_closes_an_order_on_request():
    """C.2 made the system trade, but not from a request.

    Orders exist only as a consequence of OUR tracker's own entries and exits, on the copy service's thread.
    No HTTP route a user can call may open or close a position - that would be a trading terminal, which is a
    different product with different duties. /orders is a read-only log, and the GET is asserted here.
    """
    import user_mt5_routes as r
    for route in r.router.routes:
        methods = set(getattr(route, "methods", set()) or set())
        path = route.path.lower()
        for forbidden in ("buy", "sell", "/close", "/trade"):
            assert forbidden not in path, "a user route looks like an order route: %s" % route.path
        # /orders and /positions exist, and both must be reads: a POST to either would be a way to trade.
        if "order" in path or "position" in path:
            assert methods <= {"GET", "HEAD"}, "%s must be read-only, it is %s" % (route.path, methods)


def test_the_read_only_service_still_places_nothing():
    """user_mt5_service is the READ half and stays that way; the ordering lives in user_copy_service."""
    src = (BACKEND / "user_mt5_service.py").read_text(encoding="utf-8")
    for forbidden in ("market_order", "close_position", "modify_position"):
        assert forbidden not in src, "the read-only service must not reference %s" % forbidden


def test_a_user_session_never_disturbs_the_operator_session():
    """mt5_bridge keeps ONE session for the operator; a user's connect must not touch it at all.

    C.2a replaced what this test used to assert. It required the user path to blank `_session` and set it
    back - and that WAS the defect: `_call` reads `_session["token"]` without the lock, so for the length of
    the ConnectEx call the operator had no token and every admin request failed with "no session token:
    connect first". The guarantee is not "restore it afterwards", it is "never write to it", which is what
    `bridge.connect(..., session=...)` makes possible. Behaviour is tested in
    test_c2a_worker_and_session.py; this file keeps the structural half.
    """
    src = (BACKEND / "user_mt5_service.py").read_text(encoding="utf-8")
    assert "_session" not in src, "the user path must not read or write the operator's session"
    assert "session=sess" in src, "the user path must connect into its own session dict"


# --------------------------------------------------------------------------- C.1a: the page must be reachable
def _sidebar() -> str:
        # The menu moved into navModel.tsx when the redesigned shell was added, so both shells read
    # ONE list. This follows it.
    return (BACKEND.parent / "frontend" / "src" / "components" / "navModel.tsx").read_text(encoding="utf-8")


def test_my_broker_is_in_the_non_admin_menu():
    """It was first added beside /settings, which lives in the ADMIN branch - so the only people the page is
    for never saw it. NAV_GROUPS is the non-admin menu, and the entry has to be in there."""
    s = _sidebar()
    nav = s[:s.index("export const ADMIN_GROUP")]      # the user list, before the admin one
    assert "to: '/my-broker'" in nav, "My Broker is missing from the ordinary user's menu"


def test_my_broker_is_also_in_the_admin_menu():
    s = _sidebar()
    admin = s[s.index("export const ADMIN_GROUP"):]     # the admin list
    assert "to: '/my-broker'" in admin, "an administrator should reach it from the menu too"


def test_the_connect_form_refuses_browser_autofill():
    """A bare text + password pair looks like a sign-in form, so the browser filled Server with a saved phone
    number and Password with a saved website password."""
    page = (BACKEND.parent / "frontend" / "src" / "pages" / "MyBrokerPage.tsx").read_text(encoding="utf-8")
    assert 'autoComplete="new-password"' in page, "the password field must opt out of autofill"
    assert page.count('autoComplete="off"') >= 2, "account number and server must opt out of autofill"
    assert 'placeholder="STGlobalMarketsLtd-Live-1"' in page, "Server needs an example so its format is obvious"
