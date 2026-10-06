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


def _live_signals(symbol: str):
    """The current (live) Stock + Options lean, matching the rest of the app."""
    try:
        import directional_score_service as dss
        out = dss.get_directional_score(symbol) or {}
        stock = _signal(out.get("decision")) if out.get("decision") else None
        vals = []
        for s in out.get("signals", []):
            if (s.get("name") in _OPT_KEYS and s.get("available")
                    and s.get("bias") is not None):
                vals.append(float(s["bias"]))
        opt = None
        if vals:
            a = sum(vals) / len(vals)
            opt = "BUY" if a > 0.15 else "SELL" if a < -0.15 else "NEUTRAL"
        return stock, opt, out.get("direction_score")
    except Exception:  # noqa: BLE001
        return None, None, None


def latest_signals(symbols: list[str], compute_missing: bool = True) -> dict:
    """
    {symbol: BUY/SELL/NEUTRAL} for each symbol -- from its most recent snapshot,
    and (when compute_missing) the live decision for any symbol without one, so a
    list can be fully coloured. Live reads run in parallel and the whole result
    is cached by the caller.
    """
    syms = [s.upper() for s in (symbols or []) if s]
    if not syms:
        return {"status": "OK", "signals": {}, "source": SOURCE}
    out: dict[str, str] = {}
    try:
        from database import SessionLocal
        from models_snapshots import ScoreSnapshot
        db = SessionLocal()
        try:
            rows = (db.query(ScoreSnapshot.symbol, ScoreSnapshot.snapshot_date,
                             ScoreSnapshot.decision)
                    .filter(ScoreSnapshot.symbol.in_(syms))
                    .order_by(ScoreSnapshot.snapshot_date.desc()).all())
            for sym, _d, dec in rows:
                if sym not in out:          # first = latest (desc order)
                    out[sym] = _signal(dec)
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        pass

    missing = [s for s in syms if s not in out]
    if compute_missing and missing:
        try:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=8) as pool:
                for sym, res in zip(missing, pool.map(_live_signals, missing[:80])):
                    st = res[0]
                    if st:
                        out[sym] = st
        except Exception:  # noqa: BLE001
            pass

    return {"status": "OK", "signals": out, "source": SOURCE}


def earnings_signals(symbols: list[str]) -> dict:
    """
    {symbol: BUY/SELL/NEUTRAL} from the EARNINGS equity engine's decision -- the
    same call shown on the Earnings Trade screen -- read from its cache so the
    list colour matches the detail page. Uncached symbols are reported as missing
    for the caller to warm in the background.
    """
    syms = [s.upper() for s in (symbols or []) if s]
    out: dict[str, str] = {}
    missing: list[str] = []
    try:
        import swr
        # One bulk read for every symbol -- peeking key-by-key was one DB round
        # trip per symbol and took >60s for a full ~200-stock list.
        cached = swr.peek_many([f"earn:equity:{sym}" for sym in syms])
        for sym in syms:
            v = cached.get(f"earn:equity:{sym}")
            dec = (v or {}).get("decision") if isinstance(v, dict) else None
            if dec:
                out[sym] = "BUY" if "BUY" in dec else "SELL" if "SELL" in dec else "NEUTRAL"
            else:
                missing.append(sym)
    except Exception:  # noqa: BLE001
        missing = syms

    # Previous session's call per symbol (most recent capture before today), but
    # only when it differs from the current one -- so the list can show a stock
    # whose earnings call CHANGED (e.g. Buy -> Neutral) as a split colour.
    prev: dict[str, str] = {}
    if out:
        try:
            from datetime import date as _date
            from sqlalchemy import text
            from database import SessionLocal
            db = SessionLocal()
            try:
                # "Previous session's call" = the value captured on the snapshot
                # date just BEFORE the latest one for that symbol. Compare the
                # current call to THAT, and flag a change only when they differ.
                #
                # Not "the latest snapshot <= today": today's own daily capture
                # equals the current call, so that always cancelled out and nothing
                # ever showed. And not "the most recent value that differs in the
                # last 30 days": that over-reports -- a flip a month ago that has
                # since been steady would be flagged as if it just changed. Anchor
                # to the immediately preceding capture so "changed" means changed
                # since the last session, with a 30-day cap just to bound the scan.
                from datetime import timedelta as _td
                rows = db.execute(text(
                    "SELECT symbol, stock, snapshot_date "
                    "FROM earnings_signal_snapshots "
                    "WHERE symbol = ANY(:syms) AND snapshot_date >= :since "
                    "ORDER BY symbol, snapshot_date DESC"),
                    {"syms": list(out.keys()),
                     "since": _date.today() - _td(days=30)}).all()
                # Group each symbol's captures newest-first.
                hist: dict[str, list] = {}
                for sym, st, d in rows:
                    hist.setdefault(sym, []).append((d, st))
                for sym, series in hist.items():
                    cur = out.get(sym)
                    if not cur:
                        continue
                    latest_d, latest_v = series[0]
                    if latest_v and latest_v != cur:
                        # Same-day flip: the live call has moved away from today's
                        # own capture since it was taken.
                        prev[sym] = latest_v
                    else:
                        # Steady vs the latest capture -> compare to the value on the
                        # immediately preceding capture date (the previous session).
                        # Only that one date, so a long-ago flip that has since been
                        # steady is NOT re-reported.
                        prior = next((v for d, v in series if d != latest_d), None)
                        if prior and prior != cur:
                            prev[sym] = prior
            finally:
                db.close()
        except Exception:  # noqa: BLE001 - no history just means no change shown
            pass

    return {"status": "OK", "signals": out, "prev": prev,
            "missing": missing, "source": SOURCE}


def warm_earnings(symbols: list[str]) -> None:
    """Compute+cache the earnings equity decision for symbols (background use)."""
    try:
        import swr
        import earnings_equity_service as eq
        from concurrent.futures import ThreadPoolExecutor

        def one(sym: str) -> None:
            try:
                # 6h cache: an earnings decision is driven by estimates /
                # guidance / history that don't move intraday, so re-warming the
                # whole upcoming universe a few times a day stays well within the
                # UW daily budget while keeping every card coloured.
                swr.serve(f"earn:equity:{sym}", lambda: eq.get_analysis(sym), 21600.0)
            except Exception:  # noqa: BLE001
                pass

        # Cover the whole ~3-week upcoming list, not just the soonest names.
        # Keep the worker count low: each get_analysis is CPU+network heavy, and
        # too many at once starves the web server so the very page that triggered
        # the warm times out. Fewer workers = page stays responsive, colors fill
        # in a bit more gradually.
        targets = [s.upper() for s in (symbols or []) if s][:300]
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(one, targets))
    except Exception:  # noqa: BLE001
        pass


def warm_trade(symbols: list[str]) -> None:
    """Pre-build+cache the full Earnings Trade detail so clicking a card is instant."""
    try:
        import swr
        import earnings_trade_service as ets
        from concurrent.futures import ThreadPoolExecutor

        def one(sym: str) -> None:
            try:
                swr.serve(f"earn:trade:{sym}", lambda: ets.get_summary(sym), 1800.0)
            except Exception:  # noqa: BLE001
                pass

        targets = [s.upper() for s in (symbols or []) if s][:80]
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(one, targets))
    except Exception:  # noqa: BLE001
        pass


def get_grid(symbols: list[str], days: int = 10,
             end: Optional[str] = None, include_live: bool = False) -> dict:
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
                # Forward-fill: a decision stays in effect until it changes, so a
                # gap day carries the last known call (marked carried) rather than
                # reading blank. Days before the symbol's first snapshot stay empty.
                last_stock = last_opt = None
                captured = 0
                for d in dates:
                    hit = by.get(sym, {}).get(d)
                    if hit:
                        captured += 1
                        st, op, sc = hit
                        if st:
                            last_stock = st
                        if op:
                            last_opt = op
                        cells.append({"date": d.isoformat(), "stock": st or last_stock,
                                      "options": op or last_opt, "score": sc,
                                      "carried": False})
                    else:
                        cells.append({"date": d.isoformat(), "stock": last_stock,
                                      "options": last_opt, "score": None,
                                      "carried": last_stock is not None})
                if any(c["stock"] or c["options"] for c in cells):
                    out_rows.append({
                        "symbol": sym, "cells": cells, "captured": captured,
                        "latest_stock": next((c["stock"] for c in reversed(cells)
                                              if c["stock"]), None),
                        "latest_options": next((c["options"] for c in reversed(cells)
                                                if c["options"]), None),
                    })
            # When viewing up to today, make the latest column reflect the live
            # current decision for any symbol that has no fresh snapshot that day,
            # so the grid agrees with the Trade Plan / analysis screens.
            if include_live and dates:
                last_i = len(dates) - 1
                for row in out_rows:
                    cell = row["cells"][last_i]
                    if cell.get("carried") or cell.get("stock") is None:
                        st, op, sc = _live_signals(row["symbol"])
                        if st or op:
                            cell.update({"stock": st or cell.get("stock"),
                                         "options": op or cell.get("options"),
                                         "score": sc, "carried": False, "live": True})
                            if st:
                                row["latest_stock"] = st
                            if op:
                                row["latest_options"] = op
                # Symbols that had no snapshot at all but now have a live read.
                have = {r["symbol"] for r in out_rows}
                for sym in syms:
                    if sym in have:
                        continue
                    st, op, sc = _live_signals(sym)
                    if st or op:
                        cells = [{"date": d.isoformat(), "stock": None,
                                  "options": None, "score": None, "carried": False}
                                 for d in dates]
                        cells[-1] = {"date": dates[-1].isoformat(), "stock": st,
                                     "options": op, "score": sc, "carried": False, "live": True}
                        out_rows.append({"symbol": sym, "cells": cells, "captured": 0,
                                         "latest_stock": st, "latest_options": op})

            # Most-covered symbols first, so filled rows lead the grid.
            out_rows.sort(key=lambda r: -r["captured"])

            return {"status": "OK",
                    "dates": [d.isoformat() for d in dates],
                    "rows": out_rows, "source": SOURCE}
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        return {"status": "ERROR", "detail": type(exc).__name__,
                "dates": [], "rows": [], "source": SOURCE}
