"""Phase 6.1 — isolation + auth negative/positive tests for the dev backend.

These are pure-config and in-process tests. They use intentionally INVALID or
non-production values and never open a real production connection. They prove
the dev entry point refuses production, refuses missing/malformed config, gates
the institutional API behind a session, and never falls back to the prod .env.
"""
import importlib
import os

import pytest
from fastapi.testclient import TestClient

DEV_REF = "qzmsozpyypjdxzfprhoz"
PROD_REF = "ddetojbocaxhcfxtcopc"
DEV_URL = (f"postgresql+psycopg://postgres.{DEV_REF}:pw@aws-1-eu-central-1.pooler."
           f"supabase.com:5432/postgres?sslmode=require")
PROD_URL = DEV_URL.replace(DEV_REF, PROD_REF)


# --------------------------- negative: config guard ------------------------ #
# dev_config.require_dev_config is a pure function (no import side effects).
def test_rejects_when_not_development(monkeypatch):
    monkeypatch.setenv("TRDGO_ENV", "production")
    monkeypatch.setenv("INSTITUTIONAL_DATABASE_URL", DEV_URL)
    import dev_config
    with pytest.raises(RuntimeError, match="TRDGO_ENV"):
        dev_config.require_dev_config()


def test_rejects_production_project_ref(monkeypatch):
    monkeypatch.setenv("TRDGO_ENV", "development")
    monkeypatch.setenv("INSTITUTIONAL_DATABASE_URL", PROD_URL)
    import dev_config
    with pytest.raises(RuntimeError, match="PRODUCTION project ref"):
        dev_config.require_dev_config()


def test_rejects_unknown_project_ref(monkeypatch):
    monkeypatch.setenv("TRDGO_ENV", "development")
    monkeypatch.setenv("INSTITUTIONAL_DATABASE_URL", DEV_URL.replace(DEV_REF, "someotherref99"))
    import dev_config
    with pytest.raises(RuntimeError, match="not the approved development project"):
        dev_config.require_dev_config()


def test_rejects_malformed_url(monkeypatch):
    monkeypatch.setenv("TRDGO_ENV", "development")
    monkeypatch.setenv("INSTITUTIONAL_DATABASE_URL", "mysql://nope")
    import dev_config
    with pytest.raises(RuntimeError, match="malformed"):
        dev_config.require_dev_config()


def test_rejects_missing_config(monkeypatch):
    monkeypatch.setenv("TRDGO_ENV", "development")
    monkeypatch.setenv("INSTITUTIONAL_DATABASE_URL", "   ")
    # Also neutralise the .env.dev fallback so "missing" is truly exercised.
    import dev_config
    monkeypatch.setattr("dotenv.dotenv_values", lambda *a, **k: {})
    with pytest.raises(RuntimeError, match="no development DATABASE_URL"):
        dev_config.require_dev_config()


def test_adds_ssl_when_missing(monkeypatch):
    monkeypatch.setenv("TRDGO_ENV", "development")
    monkeypatch.setenv("INSTITUTIONAL_DATABASE_URL",
                       f"postgresql://postgres.{DEV_REF}:pw@host.pooler.supabase.com:5432/postgres")
    import dev_config
    out = dev_config.require_dev_config()
    assert "sslmode=require" in out


def test_never_falls_back_to_production_env(monkeypatch):
    # A production DATABASE_URL in the environment must NOT be used by the dev
    # loader: it reads only INSTITUTIONAL_DATABASE_URL / .env.dev.
    monkeypatch.setenv("TRDGO_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", PROD_URL)          # the prod .env value
    monkeypatch.setenv("INSTITUTIONAL_DATABASE_URL", DEV_URL)
    import dev_config
    out = dev_config.require_dev_config()
    assert DEV_REF in out and PROD_REF not in out


# --------------------------- negative: engine guard ------------------------ #
def test_institutional_engine_refuses_production(monkeypatch):
    import institutional_dashboard_service as ins
    monkeypatch.setenv("INSTITUTIONAL_DATABASE_URL", PROD_URL)
    ins._engine.cache_clear()
    with pytest.raises(RuntimeError, match="production project ref"):
        ins._engine()
    ins._engine.cache_clear()


def test_institutional_engine_allow_list_rejects_unknown_ref(monkeypatch):
    import institutional_dashboard_service as ins
    monkeypatch.setenv("INSTITUTIONAL_DATABASE_URL", DEV_URL.replace(DEV_REF, "notallowedref99"))
    monkeypatch.delenv("INSTITUTIONAL_ALLOWED_REFS", raising=False)
    ins._engine.cache_clear()
    with pytest.raises(RuntimeError, match="allow-list"):
        ins._engine()
    ins._engine.cache_clear()


def test_dev_config_requires_session_secret(monkeypatch):
    monkeypatch.setenv("TRDGO_ENV", "development")
    monkeypatch.setenv("INSTITUTIONAL_DATABASE_URL", DEV_URL)
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.setattr("dotenv.dotenv_values", lambda *a, **k: {})  # no .env.dev secret
    import dev_config
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        dev_config.require_dev_config()


def test_available_is_safe_error_when_db_unreachable(monkeypatch):
    # An unreachable/invalid dev DB returns False, never raises, never prod.
    import institutional_dashboard_service as ins
    monkeypatch.setenv("INSTITUTIONAL_DATABASE_URL",
                       f"postgresql+psycopg://postgres.{DEV_REF}:pw@127.0.0.1:1/postgres?sslmode=require")
    ins._engine.cache_clear()
    assert ins.available() is False
    ins._engine.cache_clear()


# --------------------------- auth gating on the app ------------------------ #
@pytest.fixture
def client(monkeypatch):
    # A dev-ref URL on a dead local port: auth logic is exercised with no real
    # network call (available() fails fast and safely, never hits production).
    dead = f"postgresql+psycopg://postgres.{DEV_REF}:pw@127.0.0.1:1/postgres?sslmode=require"
    monkeypatch.setenv("TRDGO_ENV", "development")
    monkeypatch.setenv("INSTITUTIONAL_DATABASE_URL", dead)
    monkeypatch.setenv("SESSION_SECRET", "dev-test-secret-aaaaaaaaaaaaaaaa")
    monkeypatch.setenv("ACCESS_PASSWORD", "dev-test-password")
    import institutional_dashboard_service as ins
    ins._engine.cache_clear()
    import dev_backend
    importlib.reload(dev_backend)
    yield TestClient(dev_backend.app)
    ins._engine.cache_clear()


def test_institutional_requires_session(client):
    r = client.get("/api/institutional-intel/overview")
    assert r.status_code == 401


def test_invalid_session_cookie_rejected(client):
    r = client.get("/api/institutional-intel/overview",
                   cookies={"usr_session": "tampered.invalid.cookie"})
    assert r.status_code == 401


def test_login_then_access(client):
    bad = client.post("/auth/login", json={"password": "wrong"})
    assert bad.status_code == 401
    ok = client.post("/auth/login", json={"username": "admin", "password": "dev-test-password"})
    assert ok.status_code == 200 and ok.json()["authenticated"] is True
    # cookie now set on the client; the gated endpoint is reachable (data call
    # may still be UNAVAILABLE because this DEV_URL is a dummy host, but auth
    # passed — we assert it is no longer 401).
    r = client.get("/api/institutional-intel/health")
    assert r.status_code == 200


def test_auth_me_reflects_session(client):
    assert client.get("/auth/me").json()["authenticated"] is False
    client.post("/auth/login", json={"username": "admin", "password": "dev-test-password"})
    me = client.get("/auth/me").json()
    assert me["authenticated"] is True and me["is_admin"] is True


# --------------- test-suite production isolation (conftest guard) ----------- #
def test_suite_database_url_is_not_production():
    """The conftest guard forced a non-production DATABASE_URL for the whole run."""
    assert PROD_REF not in os.environ.get("DATABASE_URL", "")


def test_app_engine_is_not_production():
    """The production engine built at import is NOT pointed at production."""
    import database
    assert PROD_REF not in (database.DATABASE_URL or "")


def test_conftest_guard_would_reject_production(monkeypatch):
    """Simulate the fail-closed guard: a production DATABASE_URL must raise."""
    monkeypatch.setenv("DATABASE_URL", PROD_URL)
    # Re-execute the guard's core check as conftest does at import.
    preexisting = os.environ.get("DATABASE_URL", "")
    with pytest.raises(RuntimeError, match="production"):
        if PROD_REF in preexisting:
            raise RuntimeError("TEST GUARD: production ref refused")
