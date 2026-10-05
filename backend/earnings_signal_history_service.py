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


def get_grid(symbols: list[str], kind: str = "stock", days: int = 10,
             end: Optional[str] = None, include_live: bool = True) -> dict:
    """
    symbol x last-N-sessions grid of the Earnings Trade signal.

    `kind` picks the column shown: "stock" (the equity Buy/Sell/Neutral) or
    "options" (the options positioning lean). Today's cell is filled live from
    the cached analysis when `include_live`, so the grid is useful before the
    daily capture has run.
    """
    col = "options" if kind == "options" else "stock"
    days = max(1, min(int(days or 10), 30))
    syms = [s.upper() for s in (symbols or []) if s]
    if not syms:
        return {"status": "NO_SYMBOLS", "dates": [], "rows": [], "kind": col, "source": SOURCE}
    if not _ensure_table():
        return {"status": "ERROR", "dates": [], "rows": [], "kind": col, "source": SOURCE}

    try:
        from datetime import date as _date
        from sqlalchemy import text
        from database import SessionLocal
        db = SessionLocal()
        try:
            params: dict = {"syms": syms}
            where_end = ""
            if end:
                try:
                    params["end"] = _date.fromisoformat(end)
                    where_end = " AND snapshot_date <= :end"
                except ValueError:
                    pass
            date_rows = db.execute(text(
                "SELECT DISTINCT snapshot_date FROM earnings_signal_snapshots "
                f"WHERE symbol = ANY(:syms){where_end} "
                "ORDER BY snapshot_date DESC LIMIT :lim"),
                {**params, "lim": days}).all()
            dates = sorted({r[0] for r in date_rows})

            # Always include today when viewing up to now, so the live column shows
            # even before any capture has been written.
            live = _live(syms) if include_live and not end else {}
            if live and (not dates or dates[-1] != _date.today()):
                dates = sorted(set(dates) | {_date.today()})
                dates = dates[-days:]

            if not dates:
                return {"status": "NO_DATA", "dates": [], "rows": [],
                        "detail": "No earnings signals captured yet.",
                        "kind": col, "source": SOURCE}

            cells_q = db.execute(text(
                f"SELECT symbol, snapshot_date, {col} FROM earnings_signal_snapshots "
                "WHERE symbol = ANY(:syms) AND snapshot_date = ANY(:dates)"),
                {"syms": syms, "dates": dates}).all()
            by: dict[str, dict] = {}
            for sym, d, val in cells_q:
                by.setdefault(sym, {})[d] = val

            today = _date.today()
            out_rows = []
            for sym in syms:
                last = None
                captured = 0
                cells = []
                for d in dates:
                    val = by.get(sym, {}).get(d)
                    is_live = False
                    if val is None and d == today and sym in live:
                        lv = live[sym][1 if col == "options" else 0]
                        if lv:
                            val = lv
                            is_live = True
                    if val:
                        captured += 1
                        last = val
                        cells.append({"date": d.isoformat(), "signal": val,
                                      "carried": False, "live": is_live})
                    else:
                        cells.append({"date": d.isoformat(), "signal": last,
                                      "carried": last is not None, "live": False})
                if any(c["signal"] for c in cells):
                    out_rows.append({
                        "symbol": sym, "cells": cells, "captured": captured,
                        "latest": next((c["signal"] for c in reversed(cells)
                                        if c["signal"]), None),
                    })

            out_rows.sort(key=lambda r: -r["captured"])
            return {"status": "OK", "kind": col,
                    "dates": [d.isoformat() for d in dates],
                    "rows": out_rows, "source": SOURCE}
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        return {"status": "ERROR", "detail": type(exc).__name__,
                "dates": [], "rows": [], "kind": col, "source": SOURCE}
