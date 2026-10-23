"""
Shared test setup.

The access gate reads ACCESS_PASSWORD from the environment at request time, so
a developer's real .env would otherwise make every API test fail with 401 -
a deployment setting silently breaking the suite. Tests run unauthenticated by
default; test_auth.py sets the password explicitly to exercise the gate.

PRODUCTION ISOLATION (fail-closed): some tests build ``TestClient(main.app)``,
whose startup runs ``_create_schema`` -> ``create_all(engine)``. ``database.py``
builds that engine at import from ``DATABASE_URL`` (defaulting to the developer's
production ``.env``), so without this guard the suite would connect to
production. The block below runs at conftest import -- BEFORE any test module
imports ``database``/``main`` -- and forces a NON-production ``DATABASE_URL``.
``database.py``/``unusualwhales_service.py`` call ``load_dotenv()`` with the
default ``override=False``, so a value set here is never overwritten by the real
``.env``. The guard refuses to run if a production project ref is already in the
environment, and never loads production credentials.
"""

from __future__ import annotations

import os

# --- fail-closed production isolation (must run before app imports) --------- #
_PROD_REF = "ddetojbocaxhcfxtcopc"
_preexisting = os.environ.get("DATABASE_URL", "")
if _PROD_REF in _preexisting:
    raise RuntimeError(
        "TEST GUARD: DATABASE_URL points at the production project "
        f"({_PROD_REF}). Refusing to run the test suite against production.")
# A dedicated, non-production test URL. Override with TEST_DATABASE_URL to point
# at a real local/test Postgres; the default is an unreachable sentinel so that
# startup schema creation fails closed (logged, never connecting to production)
# and DB-independent tests (which mock their services) run normally.
# Default test DB: a local SQLite file. It is a dedicated, isolated database
# that needs no server, no credentials and -- crucially -- no network, so it can
# never reach production, while still giving DB-backed tests real tables to read
# and write (created by the _ensure_test_schema fixture below). Point
# TEST_DATABASE_URL at a local Postgres instead to exercise the production
# dialect; set it to the unreachable sentinel to test fail-closed behavior.
import tempfile as _tempfile  # noqa: E402
import pathlib as _pathlib  # noqa: E402
_DEFAULT_TEST_DB = "sqlite+pysqlite:///" + (
    _pathlib.Path(_tempfile.gettempdir()) / "trdgo_test.db").as_posix()
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or _DEFAULT_TEST_DB
os.environ.setdefault("TRDGO_ENV", "test")
# The institutional dev engine must never be the production project either; if a
# dev URL is present it is validated by institutional_dashboard_service's own
# guard. Leave it unset here so institutional tests skip unless a dev DB is
# explicitly configured.

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _ensure_test_schema():
    """Create the full schema once in the configured test database.

    When TEST_DATABASE_URL points at a real (local) test DB, DB-backed tests
    need their tables to exist -- normally ``main.py``'s startup creates them,
    but service-level tests never boot the app. This mirrors that startup for
    the test DB. Tables are created per-module so a dialect-specific type in one
    model never blocks the rest, and with the default unreachable sentinel the
    connection simply fails fast (connect_timeout) and this is a no-op.
    """
    try:
        from database import Base, engine
        for name in ("models", "models_calls", "models_earnings", "models_etf",
                     "models_institutional", "models_nport", "models_profiles",
                     "models_snapshots", "models_user"):
            try:
                __import__(name)
            except Exception:  # noqa: BLE001
                pass
        for table in list(Base.metadata.tables.values()):
            try:
                table.create(bind=engine, checkfirst=True)
            except Exception:  # noqa: BLE001 - skip tables a test dialect can't build
                pass
    except Exception:  # noqa: BLE001 - unreachable sentinel DB: no-op
        pass
    yield


@pytest.fixture(autouse=True)
def _no_access_gate(monkeypatch):
    """Run the suite with the access gate off unless a test opts in."""
    monkeypatch.delenv("ACCESS_PASSWORD", raising=False)
    # Clear the serve-stale and provider caches between tests. Routes now hold
    # their answers across calls (e.g. the ticker strip), so without this a
    # value cached by one test would be served to the next -- and a test that
    # asserts its mock was called would see it skipped.
    try:
        import swr
        swr._values.clear()
        swr._fns.clear()
        swr._asked.clear()
        swr._refreshing.clear()
    except Exception:  # noqa: BLE001
        pass
    try:
        import live_market_service as _m
        _m.cache.clear()
    except Exception:  # noqa: BLE001
        pass
    yield


@pytest.fixture(autouse=True)
def _isolate_uw_client_state():
    """Each test starts with an empty request memo, no in-flight requests and a fresh, unpersisted
    budget controller -- the client reuses identical requests across modules (by design), and that
    must never leak from one test into the next."""
    try:
        import unusualwhales_service as uw
        import uw_budget
        uw._memo.clear()
        uw._req_flights.clear()
        uw_budget.reset_for_tests()
    except Exception:  # noqa: BLE001
        pass
    yield
