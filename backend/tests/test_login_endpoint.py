"""
The login endpoint answers about the password, never about itself.

A wrong password is a 401. A body the app cannot read is a 400. Neither is a
500: the fallback for form posts needs a parser that is not in this app's
requirements, so an unreadable body raised inside it and surfaced as a
server error -- which on a fresh deployment reads as "the app is broken"
when the truth was "that body was not JSON".
"""

import json

from fastapi.testclient import TestClient

import auth_service as auth
import main


def _client(monkeypatch, password="letmein"):
    monkeypatch.setenv("ACCESS_PASSWORD", password)
    monkeypatch.setenv("SESSION_SECRET", "")
    return TestClient(main.app)


def test_the_right_password_opens_a_session(monkeypatch):
    client = _client(monkeypatch)
    out = client.post("/auth/login", json={"password": "letmein"})
    assert out.status_code == 200
    assert auth.COOKIE_NAME in out.cookies


def test_a_wrong_password_is_a_401_not_a_500(monkeypatch):
    client = _client(monkeypatch)
    out = client.post("/auth/login", json={"password": "nope"})
    assert out.status_code == 401


def test_an_unreadable_body_is_a_400_not_a_500(monkeypatch):
    """A shell that mangles the quotes must not read as a broken server."""
    client = _client(monkeypatch)
    out = client.post("/auth/login",
                      content="{password:letmein}",
                      headers={"Content-Type": "application/json"})
    assert out.status_code == 400, "a malformed body is the caller's mistake"
    assert "password" in out.json()["detail"].lower()


def test_an_empty_body_is_not_a_server_error(monkeypatch):
    client = _client(monkeypatch)
    assert client.post("/auth/login").status_code in (400, 401)


def test_a_session_survives_an_empty_session_secret(monkeypatch):
    """
    With no SESSION_SECRET the key is derived from the password, so a login
    still holds. This is the deployment that looks broken from the browser:
    logging in appears to succeed and every later request bounces back.
    """
    client = _client(monkeypatch)
    client.post("/auth/login", json={"password": "letmein"})
    assert client.get("/auth/status").status_code == 200
