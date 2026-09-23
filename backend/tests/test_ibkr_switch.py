"""
TWS can be switched off, and being off is not being broken.

A server deployment has no TWS and never will. Left on, every call attempts
a connection, is refused, logs two lines about opening the API port, and
repeats a minute later for as long as the app runs. Off, the app stops
asking -- and the Settings screen has to say "switched off" rather than
reporting a deliberate choice as a fault.
"""

import pytest

import ibkr_client


def test_it_is_on_unless_turned_off(monkeypatch):
    monkeypatch.delenv("IBKR_ENABLED", raising=False)
    assert ibkr_client.enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "OFF", " 0 "])
def test_the_usual_spellings_of_off_all_work(monkeypatch, value):
    monkeypatch.setenv("IBKR_ENABLED", value)
    assert ibkr_client.enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", ""])
def test_anything_else_leaves_it_on(monkeypatch, value):
    monkeypatch.setenv("IBKR_ENABLED", value)
    assert ibkr_client.enabled() is True


def test_a_call_while_off_never_touches_the_network(monkeypatch):
    monkeypatch.setenv("IBKR_ENABLED", "0")
    worker = ibkr_client._IBKRWorker()

    def forbidden(*a, **k):
        raise AssertionError("a connection was attempted while switched off")

    monkeypatch.setattr(worker, "_ensure_thread", forbidden)

    with pytest.raises(ibkr_client.IBKRUnavailable) as caught:
        worker.run(lambda ib: None)
    assert "IBKR_ENABLED" in str(caught.value), \
        "the message has to name the setting that caused it"


def test_switched_off_reports_as_a_choice_not_a_failure(monkeypatch):
    monkeypatch.setenv("IBKR_ENABLED", "0")
    status = ibkr_client._IBKRWorker().status()
    assert status["enabled"] is False
    assert status["connected"] is False
    assert status["last_error"] is None, "off is not an error"
    assert "Switched off" in status["detail"]
