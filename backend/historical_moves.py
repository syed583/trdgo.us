"""Historical post-earnings price moves -- the input the earnings models read as ``trade["historical_moves"]``.

Two methods, selected by ``HISTORICAL_MOVES_METHOD`` (default ``legacy`` = the behaviour production has always had):

* ``legacy``        -- takes rows by POSITION in the provider's daily candles. The feed has several rows per date
                       (pre-market ``pr``, post-market ``po``, regular ``r``), so After Close reactions are measured
                       from the report day's pre-market row to its own post-market row, and Before Open reactions end
                       at the pre-market row. Kept byte-for-byte so nothing changes until the flag is flipped.
* ``regular_close`` -- only rows labelled ``r`` count (fail-closed: a missing regular close skips that event, nothing is
                       guessed) and the base / reaction sessions come from the same trading calendar the Phase 2A grader
                       uses (``earnings_outcomes.reaction_window``).

``HISTORICAL_MOVES_SHADOW`` (default off): while the legacy method is served, ALSO compute the other method from the same
fetched rows (no extra provider requests) and keep both in the cache (``moves:shadow:<SYMBOL>``) for comparison.
``log_method_if_changed`` records every change of the served method so the scoreboard can keep the two periods apart.
Neither flag changes any scoring formula, weight or threshold.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import date, datetime, timezone
from typing import Optional

LEGACY = "legacy"
REGULAR = "regular_close"
SHADOW_KEY = "moves:shadow:{}"
HISTORY_KEY = "moves:method:history"
SHADOW_MIN_INTERVAL = 6 * 3600.0
_shadow_last: dict = {}
_shadow_lock = threading.Lock()


def _f(v) -> Optional[float]:
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def method() -> str:
    v = os.environ.get("HISTORICAL_MOVES_METHOD", "").strip().lower()
    return REGULAR if v in ("regular_close", "regular", "r") else LEGACY


def shadow_enabled() -> bool:
    return _truthy("HISTORICAL_MOVES_SHADOW")


def timing_map(hist_rows) -> dict:
    """report date -> lower-cased report time, for reported quarters only (same rule as before)."""
    timing: dict = {}
    for r in hist_rows or []:
        d = r.get("report_date")
        if d and r.get("reported_eps") not in (None, ""):
            timing[d] = (r.get("report_time") or "").strip().lower()
    return timing


def legacy_moves(timing: dict, candle_rows) -> list:
    """The original position-based computation, unchanged."""
    dates = sorted(timing)
    bars = [b for b in (candle_rows or []) if b.get("date") and _f(b.get("close")) is not None]
    bars.sort(key=lambda b: b["date"])
    closes = [(b["date"], _f(b["close"])) for b in bars]
    out: list = []
    for d in dates:
        idx = next((i for i, (bd, _) in enumerate(closes) if bd >= d), None)
        if idx is None or idx == 0:
            continue
        after_close = "post" in timing.get(d, "")
        if after_close:
            if idx + 1 >= len(closes):
                continue
            base, react = closes[idx][1], closes[idx + 1][1]
        else:
            base, react = closes[idx - 1][1], closes[idx][1]
        if base:
            out.append({"date": d, "label": d[:7], "move_pct": round((react - base) / base * 100, 2)})
    return out


def regular_close_moves(timing: dict, candle_rows) -> list:
    """Regular-session close to regular-session close, explicit sessions, fail-closed."""
    import earnings_outcomes as eo
    closes = eo.regular_session_closes(candle_rows)
    out: list = []
    for d in sorted(timing):
        try:
            dd = date.fromisoformat(str(d)[:10])
            kind = eo.AMC if "post" in timing.get(d, "") else eo.BMO
            base_d, react_d = eo.reaction_window(dd, kind)
        except (ValueError, TypeError):
            continue
        base, react = closes.get(base_d), closes.get(react_d)
        if not base or react is None:
            continue
        out.append({"date": d, "label": str(d)[:7], "move_pct": round((react - base) / base * 100, 2)})
    return out


def compute(timing: dict, candle_rows, which: str) -> list:
    return regular_close_moves(timing, candle_rows) if which == REGULAR else legacy_moves(timing, candle_rows)


def historical_moves(symbol: str, limit: int = 0) -> list:
    """The 1-day price move around the reports. limit<=0 => all history available."""
    try:
        import unusualwhales_service as uw
        timing = timing_map(uw._rows(uw.earnings_history(symbol)))
        if not timing:
            return []
        # UW caps the daily candle request at ~2500; 3000 returns nothing at all.
        candle_rows = uw._rows(uw.candles(symbol, "1d", limit=2500))
        chosen = method()
        out = compute(timing, candle_rows, chosen)
        if shadow_enabled():
            _record_shadow(symbol, timing, candle_rows, chosen, out)
        return out[-limit:] if limit and limit > 0 else out
    except Exception:  # noqa: BLE001
        return []


def _pairs(moves):
    return [[m["date"], m["move_pct"]] for m in moves]


def _record_shadow(symbol: str, timing: dict, candle_rows, served: str, served_moves: list) -> None:
    """Keep BOTH methods' answers for one symbol (throttled; never raises, never blocks a request)."""
    try:
        now = time.time()
        with _shadow_lock:
            if now - _shadow_last.get(symbol, 0.0) < SHADOW_MIN_INTERVAL:
                return
            _shadow_last[symbol] = now
        other = REGULAR if served == LEGACY else LEGACY
        both = {served: served_moves, other: compute(timing, candle_rows, other)}
        import db_cache
        db_cache.put(SHADOW_KEY.format(symbol), {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "served": served,
                                                  LEGACY: _pairs(both[LEGACY]), REGULAR: _pairs(both[REGULAR])})
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------------------------------ which method produced which inputs
def method_history() -> list:
    """[{method, at}] oldest first; empty = the legacy method has always been in force."""
    try:
        import db_cache
        held = db_cache.get(HISTORY_KEY)
        if held and isinstance(held[1], list):
            return held[1]
    except Exception:  # noqa: BLE001
        pass
    return []


def method_at(when: datetime, history: Optional[list] = None) -> str:
    """The method in force at ``when`` (UTC datetime); legacy before the first recorded change."""
    cur = LEGACY
    for h in (history if history is not None else method_history()):
        try:
            at = datetime.fromisoformat(str(h["at"]).replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if at <= when:
            cur = h.get("method", cur)
    return cur


def log_method_if_changed() -> bool:
    """Append {method, at} when the served method differs from the last recorded one. Best effort."""
    try:
        import db_cache
        hist = method_history()
        now_method = method()
        last = hist[-1]["method"] if hist else LEGACY
        if now_method == last:
            return False
        hist = list(hist) + [{"method": now_method, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}]
        db_cache.put(HISTORY_KEY, hist)
        return True
    except Exception:  # noqa: BLE001
        return False
