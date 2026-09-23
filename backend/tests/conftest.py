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
    yield
