"""Pins for the 2026-11-07 security-hardening change (20-item audit).

Covers: per-account login lockout, X-Forwarded-For trust, admin-only operator routes, security headers,
CSRF on /auth, throttled OTP/register/reset routes, staff MFA regardless of LOGIN_MFA, password minimum,
production credential guard, and the symbol guard on the legacy app routes.
"""
from __future__ import annotations

import importlib
import os

import pytest
from fastapi.testclient import TestClient

import auth_service as auth
import main
import user_service


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ACCESS_PASSWORD", "correct-horse-battery-staple")
    auth._fails.clear()
    auth._fails_global.clear()
    with TestClient(main.app) as c:
        yield c
    auth._fails.clear()
    auth._fails_global.clear()


def _as_user(monkeypatch, user: dict) -> None:
    """Make every request look like a valid session for `user` (bypasses cookie + DB lookup)."""
    monkeypatch.setattr(auth, "current_user", lambda request: user)
    monkeypatch.setattr(auth, "require_session", lambda request: user)


def _admin(client: TestClient) -> TestClient:
    r = client.post("/auth/login", json={"username": "admin", "password": "correct-horse-battery-staple"},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 200, r.text
    return client


# ---------------------------------------------------------------- item 6: throttling
def test_account_lockout_independent_of_client_key():
    auth._fails.clear(); auth._fails_global.clear()
    assert auth.account_blocked("alice") is None
    for _ in range(auth._MAX_FAILS):
        auth.note_account_failure("alice")
    assert auth.account_blocked("alice")            # locked
    assert auth.account_blocked("bob") is None      # other accounts unaffected
    assert auth.login_blocked("1.2.3.4") is None    # per-account failures never touch the global ceiling
    auth._fails.clear()


def test_login_locks_the_account_across_rotating_forwarded_for(client):
    for i in range(auth._MAX_FAILS):
        r = client.post("/auth/login", json={"username": "admin", "password": "wrong"},
                        headers={"origin": "http://testserver", "x-forwarded-for": f"10.0.0.{i}"})
        assert r.status_code == 401
    r = client.post("/auth/login", json={"username": "admin", "password": "correct-horse-battery-staple"},
                    headers={"origin": "http://testserver", "x-forwarded-for": "10.0.0.99"})
    assert r.status_code == 429


def test_forwarded_for_ignored_unless_peer_is_a_trusted_proxy(monkeypatch):
    class _Req:
        def __init__(self, host, fwd):
            self.headers = {"x-forwarded-for": fwd} if fwd else {}
            self.client = type("C", (), {"host": host})()
    monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
    assert main._client_key(_Req("203.0.113.9", "1.1.1.1")) == "203.0.113.9"
    monkeypatch.setenv("TRUSTED_PROXIES", "127.0.0.1, 203.0.113.9")
    assert main._client_key(_Req("203.0.113.9", "1.1.1.1, 9.9.9.9")) == "1.1.1.1"
    assert main._client_key(_Req("198.51.100.1", "1.1.1.1")) == "198.51.100.1"


@pytest.mark.parametrize("path,body", [
    ("/auth/otp/request", {"phone": "+15550000000"}),
    ("/auth/register", {"phone": "+15550000000", "code": "000000", "password": "x" * 12}),
    ("/auth/reset/request", {"phone": "+15550000000"}),
    ("/auth/reset", {"phone": "+15550000000", "code": "000000", "password": "x" * 12}),
])
def test_otp_routes_share_the_login_throttle(client, path, body):
    for _ in range(auth._MAX_FAILS):
        auth.note_login_failure("testclient")
    r = client.post(path, json=body, headers={"origin": "http://testserver"})
    assert r.status_code == 429, (path, r.text)


# ---------------------------------------------------------------- item 3: CSRF now covers /auth
def test_cross_site_login_post_is_blocked(client):
    r = client.post("/auth/login", json={"username": "admin", "password": "correct-horse-battery-staple"},
                    headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.post("/auth/logout", headers={"origin": "https://evil.example"})
    assert r.status_code == 403


# ---------------------------------------------------------------- item 2: headers
def test_security_headers_present(client):
    r = client.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert "strict-transport-security" not in r.headers          # plain http in tests
    r = client.get("/health", headers={"x-forwarded-proto": "https"})
    assert r.headers["strict-transport-security"].startswith("max-age=")


# ---------------------------------------------------------------- items 5/13: operator routes are admin-only
OPS_ROUTES = ["/api/snapshots/capture", "/api/snapshots/fill", "/api/calls/evaluate",
              "/api/institutional-ingest/run", "/api/cache/clear"]
# /api/signals/scan (own watchlist refresh) and /api/backtest (Backtest page) stay available to full-access users:
# they are per-user features, gated by the existing full-access rule in main._gate.


@pytest.mark.parametrize("path", OPS_ROUTES)
def test_full_access_user_cannot_run_operator_routes(client, monkeypatch, path):
    # a signed-in non-staff account that holds full access
    _as_user(monkeypatch, {"username": "phoneuser", "role": "user"})
    monkeypatch.setattr(user_service, "has_full_access", lambda u: True)
    r = client.post(path, json={}, headers={"origin": "http://testserver"})
    assert r.status_code == 403, (path, r.text)


def test_providers_usage_needs_dashboard_permission(client, monkeypatch):
    _as_user(monkeypatch, {"username": "phoneuser", "role": "user"})
    assert client.get("/api/providers/usage").status_code == 403


def test_ingest_count_is_bounded(monkeypatch, client):
    import api_routes
    import institutional_service as inst
    seen = {}
    monkeypatch.setattr(inst, "ingest_latest", lambda count: (seen.__setitem__("count", count), {"status": "OK"})[1])
    _admin(client)
    client.post("/api/institutional-ingest/run", json={"count": 500}, headers={"origin": "http://testserver"})
    assert seen["count"] == 8


# ---------------------------------------------------------------- item 10: staff MFA is not optional
def test_staff_login_requires_mfa_even_with_flag_off(client, monkeypatch):
    monkeypatch.delenv("LOGIN_MFA", raising=False)
    monkeypatch.setattr(auth, "authenticate", lambda u, p: {"username": "+15551234567", "role": "operations"})
    monkeypatch.setattr(user_service, "record_login", lambda *a, **k: None)
    import otp_service
    monkeypatch.setattr(otp_service, "request_code", lambda phone: {"status": "SENT"})
    r = client.post("/auth/login", json={"username": "+15551234567", "password": "x"},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 200 and r.json()["status"] == "MFA_REQUIRED"
    assert auth.PENDING_COOKIE in r.cookies and auth.COOKIE_NAME not in r.cookies


def test_plain_user_login_without_flag_has_no_mfa(client, monkeypatch):
    monkeypatch.delenv("LOGIN_MFA", raising=False)
    monkeypatch.setattr(auth, "authenticate", lambda u, p: {"username": "+15557654321", "role": "user"})
    monkeypatch.setattr(user_service, "record_login", lambda *a, **k: None)
    r = client.post("/auth/login", json={"username": "+15557654321", "password": "x"},
                    headers={"origin": "http://testserver"})
    assert r.status_code == 200 and r.json()["status"] == "OK"


# ---------------------------------------------------------------- item 9: password policy
def test_password_minimum_is_ten():
    assert user_service.MIN_PASSWORD_LEN == 10
    assert not user_service.valid_password("short9ch")
    assert user_service.valid_password("long-enough")


# ---------------------------------------------------------------- item 18: production credential guard
@pytest.mark.parametrize("pw", ["", "change-me", "Password", "admin"])
def test_production_refuses_placeholder_password(monkeypatch, pw):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ACCESS_PASSWORD", pw)
    with pytest.raises(SystemExit):
        main._refuse_weak_production_credentials()


def test_production_accepts_real_password(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ACCESS_PASSWORD", "a-genuinely-long-password")
    main._refuse_weak_production_credentials()


def test_guard_is_inert_outside_production(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("ACCESS_PASSWORD", "")
    main._refuse_weak_production_credentials()


# ---------------------------------------------------------------- items 1/16: legacy {symbol} routes are guarded
def test_legacy_company_route_rejects_crafted_symbol(client):
    _admin(client)
    assert client.get("/companies/1BADSTART").status_code == 422          # must start with a letter
    assert client.get("/market/history/ABCDEFGHIJKLMNOP").status_code == 422   # too long
    assert client.get("/market/technicals/AA%3Fx").status_code == 422     # '?' inside the segment
