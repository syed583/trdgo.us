"""
A fresh database gets its tables before anything asks for them.

Each service used to create its own the first time it ran. That works on a
machine that has been up for months and fails on a new one: the earnings
calendar asked for a table no service had created yet and the screen showed
Internal Server Error -- on a server whose database was minutes old.
"""

import main
from database import Base


def test_every_model_module_is_registered():
    """A model nobody imports is a table nobody creates."""
    main._create_schema()
    tables = set(Base.metadata.tables)
    for expected in ("earnings_calendar", "trade_calls", "companies",
                     "institutional_holdings", "company_profiles"):
        assert expected in tables, f"{expected} would be missing on a new install"


def test_a_database_that_is_down_does_not_stop_startup(monkeypatch, capsys):
    """Every screen that needs no database must still come up."""
    class _Engine:
        pass

    def refuse(engine):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(Base.metadata, "create_all", refuse)
    main._create_schema()          # must not raise
    assert "database unavailable" in capsys.readouterr().out


def test_running_it_twice_is_harmless():
    """It runs on every boot, so it has to be a no-op once tables exist."""
    main._create_schema()
    main._create_schema()
