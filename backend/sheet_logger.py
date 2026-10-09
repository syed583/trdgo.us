"""
Log Trdgo Stock signal changes to a Google Sheet.

Each time a name's intraday call changes -- a new BUY or SELL, or a BUY/SELL
that goes NO TRADE -- one row is POSTed to a Google Apps Script web-app URL
(set in GOOGLE_SHEET_WEBHOOK_URL), which appends it to the sheet. The row
carries the price at the moment of the change, so the sheet shows the price a
BUY or SELL came in at and the price it was closed/flattened at.

Design notes
------------
* No Google OAuth or service account: a published Apps Script web app is a
  plain webhook, so the only secret is its URL.
* State (the last side seen per symbol) is kept in a small JSON file so a
  restart does not re-log the whole board, and the first time a name is seen
  its current BUY / SELL is logged once (NO TRADE is not logged as a first
  sighting -- there is nothing to open).
* Posting is fire-and-forget on a daemon thread, so the board response never
  waits on the sheet.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# Timestamps are kept in Dubai time (UTC+4, no daylight saving), so the log and
# the sheet read in the operator's local time.
_DUBAI = timezone(timedelta(hours=4))

_STATE_FILE = Path(__file__).with_name(".sec_cache") / "sheet_state.json"
_LOG_FILE = Path(__file__).with_name(".sec_cache") / "signal_log.json"
_LOG_CAP = 1000
_lock = threading.Lock()
_last: dict[str, str] = {}      # "HORIZON:SYMBOL" -> side ("buy"/"sell"/"none")
_log: list[dict] = []           # recent events, newest last; shown in the app
_loaded = False

_LABEL = {"buy": "BUY", "sell": "SELL", "none": "NO TRADE"}


def _webhook() -> str:
    return (os.getenv("GOOGLE_SHEET_WEBHOOK_URL") or "").strip()


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        if _STATE_FILE.exists():
            _last.update(json.loads(_STATE_FILE.read_text("utf-8")))
    except Exception:  # noqa: BLE001
        pass
    try:
        if _LOG_FILE.exists():
            _log.extend(json.loads(_LOG_FILE.read_text("utf-8")))
    except Exception:  # noqa: BLE001
        pass


def _save() -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(_last), "utf-8")
    except Exception:  # noqa: BLE001
        pass


def _save_log() -> None:
    try:
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        _LOG_FILE.write_text(json.dumps(_log[-_LOG_CAP:]), "utf-8")
    except Exception:  # noqa: BLE001
        pass


def recent(horizon: Optional[str] = None, limit: int = 200) -> list[dict]:
    """Recent signal-change events, newest first, for the in-app log tab."""
    _load()
    with _lock:
        rows = [e for e in _log if (not horizon or e.get("horizon") == horizon)]
    return list(reversed(rows))[:max(1, min(limit, _LOG_CAP))]


def _post(payload: dict) -> None:
    url = _webhook()
    if not url:
        return
    try:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=10).read()
    except Exception:  # noqa: BLE001 - the sheet is a nicety, never break the board
        pass


def _event(sym: str, horizon: str, prev: Optional[str], side: str, row: dict) -> dict:
    return {
        "time": datetime.now(_DUBAI).strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": sym,
        "horizon": horizon,
        "event": _LABEL.get(side, "NO TRADE"),
        "from": _LABEL.get(prev or "none", ""),
        "to": _LABEL.get(side, "NO TRADE"),
        "price": row.get("spot"),
        "decision": row.get("im_decision"),
        "buy_score": row.get("buy_score"),
        "sell_score": row.get("sell_score"),
        "target": row.get("target"),
        "stop": row.get("stop"),
    }


def process(horizon: str, board: dict) -> None:
    """Compare this board pass to the last and log any call changes.

    Events are always recorded to the in-app log; they are also POSTed to the
    Google Sheet when the webhook is configured.
    """
    _load()

    rows = []
    for k in ("buyers", "sellers", "held"):
        rows.extend(board.get(k) or [])

    events: list[dict] = []
    changed = False
    with _lock:
        for r in rows:
            sym = r.get("symbol")
            if not sym:
                continue
            side = r.get("im_side") or "none"
            key = f"{horizon}:{sym}"
            prev = _last.get(key)
            if prev == side:
                continue
            _last[key] = side
            changed = True
            # First sighting: log an opening BUY/SELL once; skip a first NO TRADE.
            if prev is None and side == "none":
                continue
            events.append(_event(sym, horizon, prev, side, r))
        if events:
            _log.extend(events)
            del _log[:-_LOG_CAP]
            _save_log()
        if changed:
            _save()

    if _webhook():
        for e in events:
            threading.Thread(target=_post, args=(e,), daemon=True).start()
