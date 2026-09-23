"""
The access gate, which is what makes the app safe to expose beyond localhost.

Without a password the app is open, which is correct for a localhost-only tool.
With one set, everything must be refused until a session exists -- including
endpoints added after the gate was written, which is why it is middleware
rather than a per-route dependency.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

PASSWORD = "test-access-password"


@pytest.fixture
def gated(monkeypatch):
    """A client with the access gate switched on."""
    monkeypatch.setenv("ACCESS_PASSWORD", PASSWORD)
    monkeypatch.setenv("SESSION_SECRET", "fixed-secret-for-tests")
    import main

    return TestClient(main.app)


def test_api_is_refused_without_a_session(gated):
    assert gated.get("/api/health").status_code == 401


def test_legacy_routes_are_refused_too(gated):
    """The /market/* endpoints predate the gate and must not bypass it."""
    assert gated.get("/market/score/AAPL").status_code == 401


def test_wrong_password_is_rejected(gated):
    response = gated.post("/auth/login", json={"password": "not-it"})
    assert response.status_code == 401


def test_correct_password_opens_a_session(gated):
    login = gated.post("/auth/login", json={"password": PASSWORD})
    assert login.status_code == 200
    # TestClient keeps the cookie, so the next call should now pass the gate.
    assert gated.get("/api/health").status_code == 200


def test_health_stays_public_for_uptime_checks(gated):
    """/health is the liveness probe; gating it would break monitoring."""
    assert gated.get("/health").status_code == 200


def test_changing_the_password_invalidates_existing_sessions(gated, monkeypatch):
    """The documented revoke path: rotate the password, everyone is signed out."""
    assert gated.post("/auth/login", json={"password": PASSWORD}).status_code == 200
    assert gated.get("/api/health").status_code == 200

    # Rotate both, as a real revocation would.
    monkeypatch.setenv("ACCESS_PASSWORD", "a-different-password")
    monkeypatch.setenv("SESSION_SECRET", "a-different-secret")

    assert gated.get("/api/health").status_code == 401


def test_bearer_token_is_accepted_for_scripts(gated):
    response = gated.get(
        "/api/health", headers={"Authorization": f"Bearer {PASSWORD}"}
    )
    assert response.status_code == 200
