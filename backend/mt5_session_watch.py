"""C.2a: keep the OPERATOR's broker session alive, and say so in mt5.log when it drops.

Why this exists. `mt5_autotrade` keeps its own `connected` flag, while the actual session token lives in
`mt5_bridge._session`. The two can disagree: the token can be lost - the broker expires it, the provider
restarts, or (before C.2a) a user's connect blanked it - while the engine still believes it is connected. The
engine then refuses every order with "NOT_CONNECTED: no session token: connect first" and waits for somebody
to press Reconnect by hand. That happened on 2026-11-16.

This module changes no trading logic and places no orders. It does one thing: when the engine thinks it is
connected but the bridge has no token, it calls the engine's OWN `reconnect_from_saved()`, backing off between
attempts, and writes every attempt and outcome to mt5.log.

Orders while disconnected were never possible and still are not: `mt5_autotrade._entry_allowed` refuses with
NOT_CONNECTED before anything is sent. There is a test for that, so it cannot be lost quietly.

Switch: MT5_SESSION_WATCH=0 turns the watcher off.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Optional

import app_logging as _al

_log = _al.file_logger("mt5")

POLL_S = 30.0
BACKOFF_S = (5.0, 15.0, 60.0, 300.0)         # after this many failures in a row, keep waiting 300 s

_thread: Optional[threading.Thread] = None
_stop = False
_state: dict = {"checks": 0, "drops": 0, "reconnects": 0, "failures": 0, "last_drop": None,
                "last_reconnect": None, "last_error": None, "consecutive_failures": 0}


def enabled() -> bool:
    return (os.getenv("MT5_SESSION_WATCH") or "1").strip() in ("1", "true", "TRUE", "yes")


def state() -> dict:
    d = dict(_state)
    d["running"] = bool(_thread and _thread.is_alive())
    d["enabled"] = enabled()
    return d


def check_once() -> dict:
    """One check. Returns what it found and did; never raises.

    Only acts when the engine BELIEVES it is connected: if the operator deliberately disconnected, or never
    connected, reconnecting behind their back would be wrong.
    """
    try:
        import mt5_autotrade as AT
        import mt5_bridge as bridge
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": "IMPORT_FAILED", "detail": str(exc)}

    _state["checks"] += 1
    if not bridge.available():
        return {"ok": True, "action": "NONE", "reason": "PROVIDER_UNAVAILABLE"}
    if not AT.is_connected():
        return {"ok": True, "action": "NONE", "reason": "ENGINE_NOT_CONNECTED"}
    if bridge.connected():
        return {"ok": True, "action": "NONE", "reason": "SESSION_HEALTHY"}

    # the engine thinks it is connected and the bridge has no token: the session was lost under us
    _state["drops"] += 1
    _state["last_drop"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _log.warning("MT5 session lost while the engine believed it was connected - reconnecting from the saved "
                 "credentials (drop %d)", _state["drops"])
    try:
        res = AT.reconnect_from_saved()
    except Exception as exc:  # noqa: BLE001
        _state["failures"] += 1
        _state["consecutive_failures"] += 1
        _state["last_error"] = "%s: %s" % (type(exc).__name__, exc)
        _log.exception("MT5 reconnect raised")
        return {"ok": False, "action": "RECONNECT_FAILED", "detail": _state["last_error"]}

    if res.get("ok"):
        _state["reconnects"] += 1
        _state["consecutive_failures"] = 0
        _state["last_error"] = None
        _state["last_reconnect"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _log.warning("MT5 session reconnected (reconnect %d)", _state["reconnects"])
        return {"ok": True, "action": "RECONNECTED"}

    _state["failures"] += 1
    _state["consecutive_failures"] += 1
    _state["last_error"] = "%s: %s" % (res.get("reason"), res.get("detail"))
    _log.error("MT5 reconnect failed: %s (%d in a row)", _state["last_error"], _state["consecutive_failures"])
    return {"ok": False, "action": "RECONNECT_FAILED", "detail": _state["last_error"]}


def _wait_for() -> float:
    """POLL_S normally; after failures, the backoff step, so a dead provider is not hammered."""
    n = int(_state["consecutive_failures"])
    if n <= 0:
        return POLL_S
    return BACKOFF_S[min(n, len(BACKOFF_S)) - 1]


def _loop() -> None:
    while not _stop:
        try:
            check_once()
        except Exception:  # noqa: BLE001 - the watcher must outlive any single failure
            _log.exception("MT5 session watcher check failed")
        waited = 0.0
        step = _wait_for()
        while waited < step and not _stop:
            time.sleep(min(1.0, step - waited))
            waited += 1.0


def start() -> None:
    global _thread, _stop
    if not enabled():
        _log.warning("MT5 session watcher is switched off (MT5_SESSION_WATCH)")
        return
    if _thread and _thread.is_alive():
        return
    _stop = False
    _thread = threading.Thread(target=_loop, name="mt5-session-watch", daemon=True)
    _thread.start()
    _log.info("MT5 session watcher started (every %.0fs)", POLL_S)


def stop() -> None:
    global _stop
    _stop = True
