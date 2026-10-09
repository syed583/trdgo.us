"""
Persistent memory for the intraday signal engine.

Two things have to be remembered between board passes:

* Which names are currently ACTIVE (and on which side). The state ladder
  (WEAKENING / EXIT / REVERSE) only runs for a name that was already ACTIVE; a
  name that was never ACTIVE must read NO TRADE, never yellow.
* The last 5-minute candle that was processed per name, so the engine can obey
  "the state only changes when a 5-min candle closes".

Kept in a small JSON file so a restart does not forget open signals.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

_FILE = Path(__file__).with_name(".sec_cache") / "signal_state.json"
_lock = threading.Lock()
_state: dict[str, dict] = {}     # "HORIZON:SYMBOL" -> {active, state, candle}
_loaded = False


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        if _FILE.exists():
            _state.update(json.loads(_FILE.read_text("utf-8")))
    except Exception:  # noqa: BLE001
        pass


def _save() -> None:
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        _FILE.write_text(json.dumps(_state), "utf-8")
    except Exception:  # noqa: BLE001
        pass


def _key(horizon: str, symbol: str) -> str:
    return f"{horizon}:{symbol}"


def get(horizon: str, symbol: str) -> dict:
    """{'active': 'buy'|'sell'|None, 'state': str, 'candle': iso|None}."""
    _load()
    with _lock:
        v = _state.get(_key(horizon, symbol))
        if not v:
            return {"active": None, "state": "NO TRADE", "candle": None}
        return dict(v)


def update(horizon: str, symbol: str, *, active: Optional[str],
           state: str, candle: Optional[str]) -> None:
    _load()
    with _lock:
        _state[_key(horizon, symbol)] = {
            "active": active, "state": state, "candle": candle,
        }
        _save()


def active_names(horizon: str) -> set[str]:
    """Symbols currently holding an ACTIVE buy or sell on this horizon."""
    _load()
    with _lock:
        pre = f"{horizon}:"
        return {k.split(":", 1)[1] for k, v in _state.items()
                if k.startswith(pre) and (v or {}).get("active") in ("buy", "sell")}
