"""
Shared test setup.

The access gate reads ACCESS_PASSWORD from the environment at request time, so
a developer's real .env would otherwise make every API test fail with 401 -
a deployment setting silently breaking the suite. Tests run unauthenticated by
default; test_auth.py sets the password explicitly to exercise the gate.
"""

from __future__ import annotations

import os

import pytest


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
