"""
Earnings Signal History -- the last N sessions of the Earnings Trade decision.

Two tabs under Earnings & news (Stock History, Options History) want to show, per
upcoming-earnings name, what the Earnings Trade engine said on each recent day:
Buy / Sell / Neutral for the stock, and the options positioning lean.

Unlike the general Signal History (which reads the directional ScoreSnapshot), the
earnings decision was never recorded over time -- so this module captures it daily
into its own small table. Today's column is also filled live from the cached
earnings-equity analysis, so the grid is useful immediately and the history then
accumulates one session at a time.
"""

from __future__ import annotations

from typing import Optional

SOURCE = "Trdgo earnings engine"

# Options lean is read from these earnings-equity parameters (their bias).
_OPT_PARAMS = ("options_positioning", "unusual_options")

_ready = False


def _ensure_table() -> bool:
    global _ready
    if _ready:
        return True
    try:
        from sqlalchemy import text
        from database import SessionLocal
        db = SessionLocal()
        try:
            db.execute(text(
                "CREATE TABLE IF NOT EXISTS earnings_signal_snapshots ("
                "  symbol        VARCHAR(20) NOT NULL,"
                "  snapshot_date DATE        NOT NULL,"
                "  stock         VARCHAR(10),"
                "  options       VARCHAR(10),"
                "  PRIMARY KEY (symbol, snapshot_date)"
                ")"))
            db.commit()
            _ready = True
            return True
        except Exception:  # noqa: BLE001
            db.rollback()
            return False
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        return False


def _stock_signal(decision: Optional[str]) -> Optional[str]:
    d = (decision or "").upper()
    if not d:
        return None
    return "BUY" if "BUY" in d else "SELL" if "SELL" in d else "NEUTRAL"


def _options_signal(params: Optional[list]) -> Optional[str]:
    """Buy/Sell/Neutral lean from the options-positioning parameters' bias."""
    if not params:
        return None
    vals = []
    for p in params:
        if (p.get("name") in _OPT_PARAMS and p.get("available")
                and p.get("bias") is not None):
            try:
                vals.append(float(p["bias"]))
            except (TypeError, ValueError):
                pass
    if not vals:
        return None
    avg = sum(vals) / len(vals)
    return "BUY" if avg > 0.15 else "SELL" if avg < -0.15 else "NEUTRAL"


def _from_equity(v: Optional[dict]) -> tuple[Optional[str], Optional[str]]:
    """(stock, options) signal from a cached earnings-equity analysis dict."""
    if not isinstance(v, dict):
        return None, None
    return _stock_signal(v.get("decision")), _options_signal(v.get("params"))


def _live(symbols: list[str]) -> dict[str, tuple[Optional[str], Optional[str]]]:
    """Current (stock, options) per symbol, from the cached earnings analysis."""
    out: dict[str, tuple[Optional[str], Optional[str]]] = {}
    try:
        import swr
        cached = swr.peek_many([f"earn:equity:{s}" for s in symbols])
        for s in symbols:
            out[s] = _from_equity(cached.get(f"earn:equity:{s}"))
    except Exception:  # noqa: BLE001
        pass
    return out


def capture(symbols: list[str]) -> int:
    """Write today's Earnings Trade signal for each symbol that is cached."""
    syms = [s.upper() for s in (symbols or []) if s]
    if not syms or not _ensure_table():
        return 0
    live = _live(syms)
    rows = [(s, st, op) for s, (st, op) in live.items() if st or op]
    if not rows:
        return 0
    try:
        from datetime import date
        from sqlalchemy import text
        from database import SessionLocal
        db = SessionLocal()
        try:
            today = date.today()
            for s, st, op in rows:
                db.execute(text(
                    "INSERT INTO earnings_signal_snapshots "
                    "(symbol, snapshot_date, stock, options) "
                    "VALUES (:s, :d, :st, :op) "
                    "ON CONFLICT (symbol, snapshot_date) "
                    "DO UPDATE SET stock = :st, options = :op"),
                    {"s": s, "d": today, "st": st, "op": op})
            db.commit()
            return len(rows)
        except Exception:  # noqa: BLE001
            db.rollback()
            return 0
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        return 0


# Real post-earnings move -> direction. A move inside this band is treated as no
# clear reaction (Neutral); above it the stock rose (Buy) or fell (Sell).
_FLAT_PCT = 0.5


def _direction(move_pct) -> Optional[str]:
    try:
        v = float(move_pct)
    except (TypeError, ValueError):
        return None
    if v > _FLAT_PCT:
        return "BUY"
    if v < -_FLAT_PCT:
        return "SELL"
    return "NEUTRAL"


def _moves(symbol: str) -> list:
    """Cached real post-earnings moves for a symbol (newest last), or []."""
    try:
        import swr
        v = swr.peek(f"earnrx:{symbol}")
        return v if isinstance(v, list) else []
    except Exception:  # noqa: BLE001
        return []


def warm_reactions(symbols: list[str]) -> None:
    """Compute+cache each symbol's real past-earnings moves (background use)."""
    try:
        import swr
        import earnings_trade_service as ets
        from concurrent.futures import ThreadPoolExecutor

        def one(sym: str) -> None:
            try:
                swr.serve(f"earnrx:{sym}",
                          lambda: ets._historical_moves(sym, limit=8), 86400.0)
            except Exception:  # noqa: BLE001
                pass

        targets = [s.upper() for s in (symbols or []) if s][:300]
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(one, targets))
    except Exception:  # noqa: BLE001
        pass


def get_grid(symbols: list[str], n: int = 4, include_live: bool = True) -> dict:
    """
    Per upcoming-earnings name, its REAL past earnings events: `n` columns, each
    the actual post-earnings move as a Buy (up) / Sell (down) / Neutral (flat)
    direction, newest on the right, plus a Now column with the current live call.

    Nothing is invented -- a stock with fewer past reports has empty leading
    cells (it simply has no earlier earnings), and the move % and date ride along
    on each cell.
    """
    n = max(1, min(int(n or 4), 12))
    syms = [s.upper() for s in (symbols or []) if s]
    if not syms:
        return {"status": "NO_SYMBOLS", "n": n, "rows": [], "source": SOURCE}

    live = _live(syms) if include_live else {}
    out_rows = []
    for sym in syms:
        moves = _moves(sym)
        # Newest last; keep the last n and left-pad so the most recent aligns right.
        tail = moves[-n:] if moves else []
        pad = [None] * (n - len(tail))
        cells = []
        for m in pad:
            cells.append({"date": None, "label": None, "move_pct": None, "signal": None})
        for m in tail:
            mv = m.get("move_pct") if isinstance(m, dict) else None
            cells.append({"date": (m or {}).get("date"), "label": (m or {}).get("label"),
                          "move_pct": mv, "signal": _direction(mv)})
        now = live.get(sym, (None, None))[0]
        events = len(tail)
        if events or now:
            out_rows.append({"symbol": sym, "cells": cells, "events": events, "now": now})

    # Most history first.
    out_rows.sort(key=lambda r: -r["events"])
    return {"status": "OK", "n": n, "rows": out_rows, "source": SOURCE}
