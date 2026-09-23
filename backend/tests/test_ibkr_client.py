"""
Connectivity bookkeeping for the shared IBKR client.

These exercise the error-notice handling directly; nothing here opens a
socket. The behaviour matters because TWS can keep the API socket open while
losing its own uplink to IBKR - requests then hang instead of failing, and
without these notices the dashboard would show a green "LIVE" badge over
stalled panels.
"""

import pytest

import ibkr_client
from ibkr_client import (
    BLOCKING_DOWN_CODES, BLOCKING_UP_CODES,
    DEGRADED_DOWN_CODES, DEGRADED_UP_CODES, IBKRUnavailable,
)


@pytest.fixture
def worker():
    return ibkr_client._IBKRWorker()


class FakeIB:
    def __init__(self, connected=True):
        self._connected = connected

    def isConnected(self):
        return self._connected


def test_a_fresh_worker_assumes_the_farm_is_up(worker):
    assert worker._farm_ok is True
    assert worker._farm_note is None


@pytest.mark.parametrize("code", sorted(BLOCKING_DOWN_CODES))
def test_connectivity_loss_notices_mark_the_farm_down(worker, code):
    worker._on_error(-1, code, "connection lost", None)
    assert worker._farm_ok is False
    assert str(code) in worker._farm_note


@pytest.mark.parametrize("code", sorted(BLOCKING_UP_CODES))
def test_restore_notices_clear_the_flag(worker, code):
    worker._on_error(-1, 1100, "lost", None)
    assert worker._farm_ok is False
    worker._on_error(-1, code, "restored", None)
    assert worker._farm_ok is True
    assert worker._farm_note is None


def test_ordinary_errors_do_not_change_connectivity(worker):
    # 200 = no security definition, 162 = no market data permissions.
    for code in (200, 162, 354, 321):
        worker._on_error(1, code, "some request problem", None)
    assert worker._farm_ok is True


def test_status_reports_connected_when_socket_and_farm_are_both_up(worker):
    worker._ib = FakeIB(connected=True)
    status = worker.status()
    assert status["connected"] is True
    assert status["socket_connected"] is True
    assert status["data_farm_ok"] is True
    assert status["last_error"] is None


def test_status_is_not_connected_when_the_farm_is_down(worker):
    """A live socket with a dead uplink must not read as healthy."""
    worker._ib = FakeIB(connected=True)
    worker._on_error(-1, 1100, "Connectivity between IBKR and TWS has been lost.", None)

    status = worker.status()
    assert status["socket_connected"] is True     # the socket really is open
    assert status["data_farm_ok"] is False
    assert status["connected"] is False           # but we do not claim to be live
    assert "1100" in status["last_error"]


def test_status_reports_the_connection_failure_when_the_socket_is_down(worker):
    worker._ib = FakeIB(connected=False)
    worker._last_failure = "Cannot reach IBKR at 127.0.0.1:7496"
    status = worker.status()
    assert status["connected"] is False
    assert status["socket_connected"] is False
    assert status["last_error"] == "Cannot reach IBKR at 127.0.0.1:7496"


def test_no_serving_error_while_the_farm_is_healthy(worker):
    assert worker.serving_error() is None


def test_serving_error_explains_a_down_farm(worker):
    """
    The point of the flag: dispatch is refused with an explanation in
    milliseconds instead of blocking until the request timeout expires.
    """
    worker._on_error(-1, 1100, "Connectivity lost", None)
    message = worker.serving_error()
    assert message is not None
    assert "not serving data" in message
    assert "1100" in message


def test_serving_error_and_status_agree(worker):
    worker._ib = FakeIB(connected=True)
    assert worker.serving_error() is None
    assert worker.status()["connected"] is True

    worker._on_error(-1, 1100, "Connectivity lost", None)
    assert worker.serving_error() is not None
    assert worker.status()["connected"] is False


@pytest.mark.parametrize("code", sorted(DEGRADED_DOWN_CODES))
def test_a_single_broken_farm_is_reported_but_does_not_block(worker, code):
    """
    2103/2105/2157 name one farm, often a regional one such as "secdefeu".
    US requests usually keep working, so these must not veto dispatch.
    """
    worker._ib = FakeIB(connected=True)
    worker._on_error(-1, code, "data farm connection is broken:secdefeu", None)

    assert worker.serving_error() is None          # requests still go out
    status = worker.status()
    assert status["connected"] is True             # still usable
    assert str(code) in status["degraded_note"]    # but the problem is visible


@pytest.mark.parametrize("code", sorted(DEGRADED_UP_CODES))
def test_a_farm_coming_back_clears_the_degraded_note(worker, code):
    worker._on_error(-1, 2103, "broken", None)
    assert worker._degraded_note is not None
    worker._on_error(-1, code, "is OK", None)
    assert worker._degraded_note is None


def test_reconnecting_resets_the_farm_flag(worker):
    worker._on_error(-1, 1100, "lost", None)
    assert worker._farm_ok is False
    # _ensure_connected clears both on a successful connect; emulate that step.
    worker._farm_ok = True
    worker._farm_note = None
    worker._ib = FakeIB(connected=True)
    assert worker.status()["connected"] is True


# --- a connected but unresponsive TWS must not cost 20s a call -------------

def test_repeated_timeouts_open_the_breaker():
    """
    The breaker only ever opened on a failed connect. When TWS was up but its
    data farm was broken, every single request paid the full timeout before
    falling back -- which is what made the whole app feel slow.
    """
    import ibkr_client

    client = ibkr_client._IBKRWorker()
    assert not client.is_offline()

    for _ in range(ibkr_client.IBKR_TIMEOUT_STRIKES - 1):
        client._note_timeout()
    assert not client.is_offline(), "one slow request is not an outage"

    client._note_timeout()
    assert client.is_offline(), "a run of timeouts must stop further waiting"
    assert "timed out" in (client._last_failure or "")


def test_a_success_clears_the_strikes():
    import ibkr_client

    client = ibkr_client._IBKRWorker()
    client._note_timeout()
    client._note_timeout()
    client._timeout_strikes = 0          # what run() does on a good result
    client._note_timeout()
    assert not client.is_offline()


def test_force_retry_clears_the_strikes():
    import ibkr_client

    client = ibkr_client._IBKRWorker()
    for _ in range(ibkr_client.IBKR_TIMEOUT_STRIKES):
        client._note_timeout()
    assert client.is_offline()

    client.force_retry()
    assert not client.is_offline()
