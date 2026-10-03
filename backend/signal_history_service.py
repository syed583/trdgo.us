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


# The options-positioning parameters stored in each snapshot's signals blob.
_OPT_KEYS = ("options_flow", "unusual_activity", "oi_positioning",
             "gamma_exposure", "volume_pcr", "flow_by_expiry",
             "daily_oi_change", "disparity")


def _opt_signal(signals_text: Optional[str]) -> Optional[str]:
    """A Buy/Sell/Neutral lean from the day's options parameters, or None."""
    if not signals_text:
        return None
    try:
        import json
        sigs = json.loads(signals_text)
        if not isinstance(sigs, dict):
            return None
        vals = []
        for k in _OPT_KEYS:
            s = sigs.get(k)
            if isinstance(s, dict) and s.get("available") and s.get("bias") is not None:
                vals.append(float(s["bias"]))
        if not vals:
            return None
        avg = sum(vals) / len(vals)
        return "BUY" if avg > 0.15 else "SELL" if avg < -0.15 else "NEUTRAL"
    except Exception:  # noqa: BLE001
        return None


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
                             ScoreSnapshot.decision, ScoreSnapshot.direction_score,
                             ScoreSnapshot.signals)
                    .filter(ScoreSnapshot.symbol.in_(syms),
                            ScoreSnapshot.snapshot_date.in_(dates)).all())
            # symbol -> {date: (stock_signal, options_signal, score)}
            by: dict[str, dict] = {}
            for sym, d, dec, sc, sigs in rows:
                by.setdefault(sym, {})[d] = (_signal(dec), _opt_signal(sigs), sc)

            out_rows = []
            for sym in syms:
                cells = []
                for d in dates:
                    hit = by.get(sym, {}).get(d)
                    if hit:
                        cells.append({"date": d.isoformat(), "stock": hit[0],
                                      "options": hit[1], "score": hit[2]})
                    else:
                        cells.append({"date": d.isoformat(), "stock": None,
                                      "options": None, "score": None})
                if any(c["stock"] or c["options"] for c in cells):
                    out_rows.append({
                        "symbol": sym, "cells": cells,
                        "latest_stock": next((c["stock"] for c in reversed(cells)
                                              if c["stock"]), None),
                        "latest_options": next((c["options"] for c in reversed(cells)
                                                if c["options"]), None),
                    })

            return {"status": "OK",
                    "dates": [d.isoformat() for d in dates],
                    "rows": out_rows, "source": SOURCE}
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        return {"status": "ERROR", "detail": type(exc).__name__,
                "dates": [], "rows": [], "source": SOURCE}
