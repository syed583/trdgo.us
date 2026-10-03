"""
Signal History -- the last N sessions of Buy / Sell / Neutral per stock.

Reads the daily score snapshots the app already captures (ScoreSnapshot: one row
per symbol per session with the model's decision) and lays them out as a grid:
rows are stocks, columns are the last N session dates, each cell the decision
that day. Nothing is recomputed -- it is the record of what the model actually
said, so a day with no snapshot shows blank rather than a guess.
"""

from __future__ import annotations

from typing import Optional

SOURCE = "Trdgo model"


def _signal(decision: Optional[str]) -> str:
    d = (decision or "").upper()
    if "BUY" in d:
        return "BUY"
    if "SELL" in d:
        return "SELL"
    return "NEUTRAL"


def get_grid(symbols: list[str], days: int = 10,
             end: Optional[str] = None) -> dict:
    """
    A symbol x last-N-sessions grid of Buy/Sell/Neutral decisions.

    `days` is how many sessions to show (max 30); `end` (YYYY-MM-DD) caps the
    window so the user can look back from a chosen date instead of today.
    """
    days = max(1, min(int(days or 10), 30))
    syms = [s.upper() for s in (symbols or []) if s]
    if not syms:
        return {"status": "NO_SYMBOLS", "dates": [], "rows": [], "source": SOURCE}

    try:
        from database import SessionLocal
        from models_snapshots import ScoreSnapshot
        db = SessionLocal()
        try:
            # The last `days` session dates for which any of these symbols has a
            # snapshot (on/before `end` if given), newest first -> shown oldest..newest.
            dq = (db.query(ScoreSnapshot.snapshot_date)
                  .filter(ScoreSnapshot.symbol.in_(syms)))
            if end:
                from datetime import date as _date
                try:
                    dq = dq.filter(ScoreSnapshot.snapshot_date <= _date.fromisoformat(end))
                except ValueError:
                    pass
            date_rows = (dq.distinct()
                         .order_by(ScoreSnapshot.snapshot_date.desc())
                         .limit(days).all())
            dates = sorted({r[0] for r in date_rows})
            if not dates:
                return {"status": "NO_DATA", "dates": [], "rows": [],
                        "detail": "No score snapshots captured yet.", "source": SOURCE}

            rows = (db.query(ScoreSnapshot.symbol, ScoreSnapshot.snapshot_date,
                             ScoreSnapshot.decision, ScoreSnapshot.direction_score)
                    .filter(ScoreSnapshot.symbol.in_(syms),
                            ScoreSnapshot.snapshot_date.in_(dates)).all())
            # symbol -> {date: (signal, score)}
            by: dict[str, dict] = {}
            for sym, d, dec, sc in rows:
                by.setdefault(sym, {})[d] = (_signal(dec), sc)

            out_rows = []
            for sym in syms:
                cells = []
                counts = {"BUY": 0, "SELL": 0, "NEUTRAL": 0}
                for d in dates:
                    hit = by.get(sym, {}).get(d)
                    if hit:
                        counts[hit[0]] += 1
                        cells.append({"date": d.isoformat(), "signal": hit[0],
                                      "score": hit[1]})
                    else:
                        cells.append({"date": d.isoformat(), "signal": None,
                                      "score": None})
                if any(c["signal"] for c in cells):
                    out_rows.append({"symbol": sym, "cells": cells, "counts": counts,
                                     "latest": next((c["signal"] for c in reversed(cells)
                                                     if c["signal"]), None)})

            return {"status": "OK",
                    "dates": [d.isoformat() for d in dates],
                    "rows": out_rows, "source": SOURCE}
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        return {"status": "ERROR", "detail": type(exc).__name__,
                "dates": [], "rows": [], "source": SOURCE}
