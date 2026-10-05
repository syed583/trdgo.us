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


def get_grid(symbols: list[str], days: int = 10,
             end: Optional[str] = None, include_live: bool = True) -> dict:
    """
    symbol x last-N-sessions grid of the Earnings Trade signal, with BOTH the
    stock (equity) call and the options positioning lean per cell -- so the page
    can stack a Stock row and an Options row per ticker, like Signal History.

    Today's cell is filled live from the cached analysis when `include_live`, so
    the grid is useful before the daily capture has run.
    """
    days = max(1, min(int(days or 10), 30))
    syms = [s.upper() for s in (symbols or []) if s]
    if not syms:
        return {"status": "NO_SYMBOLS", "dates": [], "rows": [], "source": SOURCE}
    if not _ensure_table():
        return {"status": "ERROR", "dates": [], "rows": [], "source": SOURCE}

    try:
        from datetime import date as _date
        from sqlalchemy import text
        from database import SessionLocal
        db = SessionLocal()
        try:
            from datetime import timedelta
            today = _date.today()
            anchor = today
            if end:
                try:
                    anchor = _date.fromisoformat(end)
                except ValueError:
                    anchor = today

            # The column axis is the last N trading days (Mon-Fri) ending at the
            # anchor, so "Last 30" always shows a 30-column grid that fills in as
            # each session is captured -- rather than only the days we happen to
            # have so far. Any captured weekend day in range is merged in too.
            axis: list = []
            cur = anchor
            while len(axis) < days:
                if cur.weekday() < 5:
                    axis.append(cur)
                cur -= timedelta(days=1)
            axis_set = set(axis)
            floor = min(axis)

            where_end = " AND snapshot_date <= :end" if end else ""
            cap_dates = db.execute(text(
                "SELECT DISTINCT snapshot_date FROM earnings_signal_snapshots "
                f"WHERE symbol = ANY(:syms) AND snapshot_date >= :floor{where_end}"),
                {"syms": syms, "floor": floor,
                 **({"end": anchor} if end else {})}).all()
            for (cd,) in cap_dates:
                if cd <= anchor:
                    axis_set.add(cd)
            dates = sorted(axis_set)[-days:]

            # Live (today) column, when viewing up to now.
            live = _live(syms) if include_live and not end else {}

            cells_q = db.execute(text(
                "SELECT symbol, snapshot_date, stock, options FROM earnings_signal_snapshots "
                "WHERE symbol = ANY(:syms) AND snapshot_date = ANY(:dates)"),
                {"syms": syms, "dates": dates}).all()
            by: dict[str, dict] = {}
            for sym, d, st, op in cells_q:
                by.setdefault(sym, {})[d] = (st, op)

            out_rows = []
            for sym in syms:
                last_st = last_op = None
                captured = 0
                cells = []
                for d in dates:
                    hit = by.get(sym, {}).get(d)
                    st, op = (hit if hit else (None, None))
                    is_live = False
                    if (st is None and op is None) and d == today and sym in live:
                        lst, lop = live[sym]
                        if lst or lop:
                            st, op, is_live = lst, lop, True
                    if st or op:
                        captured += 1
                        if st:
                            last_st = st
                        if op:
                            last_op = op
                        cells.append({"date": d.isoformat(),
                                      "stock": st or last_st, "options": op or last_op,
                                      "carried": False, "live": is_live})
                    else:
                        cells.append({"date": d.isoformat(),
                                      "stock": last_st, "options": last_op,
                                      "carried": last_st is not None or last_op is not None,
                                      "live": False})
                if any(c["stock"] or c["options"] for c in cells):
                    out_rows.append({
                        "symbol": sym, "cells": cells, "captured": captured,
                        "latest_stock": next((c["stock"] for c in reversed(cells)
                                              if c["stock"]), None),
                        "latest_options": next((c["options"] for c in reversed(cells)
                                                if c["options"]), None),
                    })

            out_rows.sort(key=lambda r: -r["captured"])
            return {"status": "OK",
                    "dates": [d.isoformat() for d in dates],
                    "rows": out_rows, "source": SOURCE}
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        return {"status": "ERROR", "detail": type(exc).__name__,
                "dates": [], "rows": [], "source": SOURCE}
