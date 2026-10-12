"""Live swing positions for the Trdgo Stock page, persisted to a JSON file.

Reuses the pure rules in swing_manager; this module owns the state: one open
position per symbol (entry, stop, best price, the session it opened), the
open/manage/close transitions, and a capped feed of alert events for the UI.

No DB schema, nothing outside the Trdgo Stock path. In-memory dict mirrored to
.cache/swing_positions.json so it survives a restart (same pattern as the
board's own row cache).
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import date
from pathlib import Path
from typing import Optional

import swing_manager as sm

_STORE = Path(__file__).parent / ".cache" / "swing_positions.json"
_LOCK = threading.Lock()
_POSITIONS: dict = {}       # symbol -> position dict
_EVENTS: list = []          # most-recent-first alert feed
_EVENTS_CAP = 300
_CLOSED: list = []          # positions that exited, with realised P/L
_CLOSED_TTL = 24 * 3600     # keep exit records for 24h, then drop them
_CLOSED_CAP = 400


def _pl_pct(bias: str, entry: float, price: float) -> Optional[float]:
    if not entry:
        return None
    g = (price - entry) if bias == "LONG" else (entry - price)
    return round(g / entry * 100, 2)


def _prune_closed(now: float) -> None:
    _CLOSED[:] = [c for c in _CLOSED if now - (c.get("closed_ts") or 0) < _CLOSED_TTL][:_CLOSED_CAP]


def _save() -> None:
    if os.environ.get("PYTEST_CURRENT_TEST") and _STORE == (
            Path(__file__).parent / ".cache" / "swing_positions.json"):
        return
    try:
        _STORE.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(_STORE.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"positions": _POSITIONS, "events": _EVENTS[:_EVENTS_CAP],
                       "closed": _CLOSED[:_CLOSED_CAP]}, fh)
        os.replace(tmp, _STORE)
    except Exception:  # noqa: BLE001
        pass


def load() -> None:
    try:
        if _STORE.is_file():
            data = json.loads(_STORE.read_text(encoding="utf-8"))
            with _LOCK:
                _POSITIONS.clear()
                _POSITIONS.update(data.get("positions") or {})
                _EVENTS.clear()
                _EVENTS.extend(data.get("events") or [])
                _CLOSED.clear()
                _CLOSED.extend(data.get("closed") or [])
                _prune_closed(__import__("time").time())
    except Exception:  # noqa: BLE001
        pass


def _emit(symbol: str, bias: str, alert: str, reason: str, price: float) -> None:
    _EVENTS.insert(0, {"symbol": symbol, "bias": bias, "alert": alert,
                       "reason": reason, "price": round(price, 2),
                       "at": date.today().isoformat()})
    del _EVENTS[_EVENTS_CAP:]


def _sessions_between(opened: Optional[str], today: str) -> int:
    """Trading-day count is approximated by weekdays between the two dates."""
    if not opened:
        return 0
    try:
        a = date.fromisoformat(opened)
        b = date.fromisoformat(today)
    except ValueError:
        return 0
    days = 0
    cur = a
    from datetime import timedelta
    while cur < b:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            days += 1
    return days


def step(symbol: str, *, decision: Optional[str], price: Optional[float],
         score: Optional[float], atr: Optional[float],
         hi_52w: Optional[float] = None, lo_52w: Optional[float] = None,
         rvol: Optional[float] = None, rsi_new_extreme: Optional[bool] = None,
         session_date: Optional[str] = None) -> Optional[dict]:
    """Advance one symbol: open on a fresh BUY/SELL, otherwise manage/close an
    open position. Returns the current open position (or None)."""
    if not price:
        return _POSITIONS.get(symbol)
    today = session_date or date.today().isoformat()
    dec = (decision or "").upper()
    with _LOCK:
        pos = _POSITIONS.get(symbol)

        # ---- manage an open position -------------------------------------
        if pos:
            long_ = pos["bias"] == "LONG"
            pos["best_price"] = (max(pos.get("best_price", price), price) if long_
                                 else min(pos.get("best_price", price), price))
            pos["last_price"] = round(price, 2)
            days = _sessions_between(pos.get("opened_date"), today)
            r = sm.manage(bias=pos["bias"], entry=pos["entry"], price=price,
                          best_price=pos["best_price"], stop=pos.get("stop"),
                          score=score, atr=atr or pos.get("atr"),
                          days_held=days, near_52w=pos.get("near_52w", False))
            pos["stop"] = r["stop"]
            pos["days_held"] = days
            if r["exit"]:
                import time as _t
                _emit(symbol, pos["bias"], r["alert"], r["reason"] or "", price)
                _prune_closed(_t.time())
                _CLOSED.insert(0, {
                    "symbol": symbol, "bias": pos["bias"], "entry": pos["entry"],
                    "exit": round(price, 2), "target": pos.get("target"),
                    "stop": pos.get("stop"), "pl_pct": _pl_pct(pos["bias"], pos["entry"], price),
                    "reason": r["alert"], "exit_code": r["exit"],
                    "opened_date": pos.get("opened_date"),
                    "closed_at": date.today().isoformat(), "closed_ts": _t.time()})
                _POSITIONS.pop(symbol, None)
                _save()
                return None
            if r["alert"]:                       # breakeven / trail update
                _emit(symbol, pos["bias"], r["alert"], r["reason"] or "", price)
            _save()
            return pos

        # ---- open a new position on a fresh signal -----------------------
        bias = "LONG" if "BUY" in dec else "SHORT" if "SELL" in dec else None
        if bias is None:
            return None
        filt = sm.hi_lo_filter(bias=bias, price=price, hi_52w=hi_52w,
                               lo_52w=lo_52w, rvol=rvol,
                               rsi_new_extreme=rsi_new_extreme)
        near = filt["near"]
        stop = sm.initial_stop(bias, price, atr, near_52w=near)
        tgt = sm.target(bias, price, atr)
        pos = {"symbol": symbol, "bias": bias, "entry": round(price, 2),
               "stop": stop, "target": tgt, "best_price": round(price, 2),
               "last_price": round(price, 2), "atr": atr,
               "near_52w": near, "flag": filt["flag"], "opened_date": today,
               "days_held": 0, "decision": dec}
        _POSITIONS[symbol] = pos
        label = ("STRONG BUY" if "STRONG BUY" in dec else "BUY") if bias == "LONG" \
            else ("STRONG SELL" if "STRONG SELL" in dec else "SELL")
        if filt["flag"]:
            label += f" ({filt['flag']})"
        _emit(symbol, bias, label, f"Entry {price}, stop {stop}", price)
        _save()
        return pos


def positions() -> list:
    with _LOCK:
        out = []
        for p in _POSITIONS.values():
            q = dict(p)
            q["pl_pct"] = _pl_pct(p["bias"], p.get("entry"),
                                  p.get("last_price") or p.get("entry"))
            # `stop` is the trailing stop -- surface it under a clear name too.
            q["trail_stop"] = p.get("stop")
            out.append(q)
        return out


def recent_events(limit: int = 60) -> list:
    with _LOCK:
        return _EVENTS[:limit]


def recent_closed(limit: int = 100) -> list:
    """Positions that exited in the last 24h, with realised P/L (then dropped)."""
    import time as _t
    with _LOCK:
        _prune_closed(_t.time())
        return _CLOSED[:limit]
