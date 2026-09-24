"""
Capture daily scores, fill in what happened next, and measure each parameter.

Three jobs, in the order they matter
------------------------------------
``capture`` stores today's reading for a set of symbols, including what every
individual parameter said. ``fill_forward_returns`` comes back later and
records the move that followed. ``evaluate_parameters`` compares the two and
reports which parameters actually preceded the move they predicted.

The third is the point of the other two. Right now the weight table is
intuition: options flow has ten points and price action five because that
seemed reasonable, not because anything measured it. This is the machinery
that replaces the guess with a number.

Why not just backtest
---------------------
Most of the model has no reconstructable past. The options provider keeps
fifteen days of prints, the SEC helpers fetch "latest" rather than "as of
date", and estimates are current-only -- roughly sixty of the hundred points
cannot be replayed. Recording forward from today is slower but it is the only
route to evidence that covers the whole model rather than the price half.

A measurement that says "this parameter has no edge" is as valuable as one
that says it does, and rather more likely.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import and_, or_

from database import SessionLocal, engine
from models_snapshots import ScoreSnapshot, create_all

# Trading sessions ahead to measure. Five is a week, twenty about a month --
# long enough for a positioning signal to play out, short enough that the
# reading is still the reason for the move rather than one input among a
# hundred later ones.
HORIZONS = (5, 10, 20)

# A parameter needs this many completed observations before its hit rate is
# worth reading. Below it the number is noise wearing a percentage sign.
MIN_OBSERVATIONS = 30

# How far from neutral a bias has to be to count as a call. A parameter
# reading 0.02 has not predicted anything, and scoring it as a miss or a hit
# would measure rounding.
MIN_BIAS = 0.15


def _today() -> date:
    return datetime.now(timezone.utc).date()


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------


def capture(symbols: list[str], on: Optional[date] = None) -> dict:
    """
    Store today's reading for each symbol.

    Re-running overwrites the day rather than adding to it, so a retry after a
    partial failure does not double-weight that session.
    """
    import directional_score_service as ds
    import live_market_service as market

    create_all(engine)
    day = on or _today()
    session = SessionLocal()
    stored, skipped = 0, []

    try:
        for symbol in symbols:
            symbol = (symbol or "").upper().strip()
            if not symbol:
                continue

            try:
                scored = ds.get_directional_score(symbol)
            except Exception as exc:  # noqa: BLE001
                skipped.append({"symbol": symbol, "reason": str(exc)[:120]})
                continue

            if scored.get("status") != "OK":
                skipped.append({"symbol": symbol,
                                "reason": scored.get("status", "NO_DATA")})
                continue

            price = None
            try:
                quote = market.get_quote(symbol)
                price = (quote or {}).get("price")
            except Exception:  # noqa: BLE001
                pass

            # Only the fields a later measurement needs. Storing the full
            # payload would bloat the table with explanations that are useful
            # on screen and useless to a regression.
            signals = {
                s["name"]: {
                    "bias": s.get("bias"),
                    "points": s.get("points"),
                    "available": s.get("available"),
                    "directional": s.get("directional"),
                }
                for s in scored.get("signals", [])
            }

            row = (session.query(ScoreSnapshot)
                   .filter_by(symbol=symbol, snapshot_date=day).one_or_none())
            if row is None:
                row = ScoreSnapshot(symbol=symbol, snapshot_date=day)
                session.add(row)

            row.direction_score = scored.get("direction_score")
            row.confidence = scored.get("confidence")
            row.decision = scored.get("decision")
            row.lean = scored.get("lean")
            row.actionable = scored.get("actionable")
            row.coverage_pct = scored.get("coverage_pct")
            row.agreement_pct = scored.get("agreement_pct")
            row.conviction_pct = scored.get("conviction_pct")
            row.signals = json.dumps(signals)
            row.price = price
            stored += 1

        session.commit()
        return {"status": "OK", "date": day.isoformat(),
                "stored": stored, "skipped": skipped}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        return {"status": "ERROR", "detail": type(exc).__name__}
    finally:
        session.close()


# ---------------------------------------------------------------------------
# outcomes
# ---------------------------------------------------------------------------


def fill_forward_returns(limit: int = 200) -> dict:
    """
    Record the move that followed each snapshot.

    Returns are measured from the close on the snapshot date to the close N
    sessions later, using stored daily bars rather than the live quote -- the
    question is what happened after the reading, not what is happening now.
    """
    import live_market_service as market

    create_all(engine)
    session = SessionLocal()
    filled = 0

    try:
        cutoff = _today() - timedelta(days=max(HORIZONS) + 10)
        rows = (session.query(ScoreSnapshot)
                .filter(ScoreSnapshot.forward_filled_at.is_(None))
                .filter(ScoreSnapshot.snapshot_date <= cutoff)
                .order_by(ScoreSnapshot.snapshot_date)
                .limit(limit).all())
        if not rows:
            return {"status": "NOTHING_DUE", "filled": 0,
                    "detail": (f"No snapshot is older than {max(HORIZONS)} "
                               f"sessions yet.")}

        by_symbol: dict[str, list] = {}
        for row in rows:
            by_symbol.setdefault(row.symbol, []).append(row)

        for symbol, pending in by_symbol.items():
            bars, _ = market._fallback_bars(symbol, "1 Y")
            if not bars:
                continue
            closes = {str(b["date"])[:10]: float(b["close"])
                      for b in bars if b.get("close")}
            ordered = sorted(closes)

            for row in pending:
                start_key = row.snapshot_date.isoformat()
                if start_key not in closes:
                    # The snapshot landed on a non-trading day; use the next
                    # session, which is when the reading could first be acted
                    # on anyway.
                    later = [d for d in ordered if d >= start_key]
                    if not later:
                        continue
                    start_key = later[0]

                start_index = ordered.index(start_key)
                start_price = closes[start_key]
                if not start_price:
                    continue

                for horizon in HORIZONS:
                    target = start_index + horizon
                    if target >= len(ordered):
                        continue
                    end_price = closes[ordered[target]]
                    change = (end_price - start_price) / start_price * 100
                    setattr(row, f"forward_{horizon}d_pct", round(change, 3))

                if row.forward_20d_pct is not None:
                    row.forward_filled_at = datetime.now(timezone.utc)
                    filled += 1

        session.commit()
        return {"status": "OK", "filled": filled, "examined": len(rows)}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        return {"status": "ERROR", "detail": type(exc).__name__}
    finally:
        session.close()


# ---------------------------------------------------------------------------
# measurement
# ---------------------------------------------------------------------------


def evaluate_parameters(horizon: int = 10,
                        min_observations: int = MIN_OBSERVATIONS) -> dict:
    """
    Per-parameter hit rate and average forward return.

    For each parameter: when it leaned bullish, did price rise more often than
    the base rate across all snapshots? A parameter that matches the base rate
    has no edge regardless of how sensible it looks, and its weight should
    come down.

    The base rate matters. In a rising market most readings are followed by a
    rise, so a 60% hit rate means nothing if the base rate is 62% -- ``edge``
    is the difference, and it is the only column worth acting on.
    """
    create_all(engine)
    session = SessionLocal()
    field = f"forward_{horizon}d_pct"

    try:
        rows = (session.query(ScoreSnapshot)
                .filter(getattr(ScoreSnapshot, field).isnot(None))
                .all())
        if not rows:
            return {"status": "NO_DATA", "horizon_days": horizon,
                    "detail": ("No snapshot has a forward return yet. Capture "
                               "runs daily; the first measurement is possible "
                               f"{max(HORIZONS)} sessions after the first "
                               "capture.")}

        moves = [getattr(r, field) for r in rows]
        base_rate = sum(1 for m in moves if m > 0) / len(moves) * 100
        base_return = sum(moves) / len(moves)

        stats: dict[str, dict] = {}
        for row in rows:
            move = getattr(row, field)
            try:
                signals = json.loads(row.signals or "{}")
            except ValueError:
                continue

            for name, data in signals.items():
                bias = data.get("bias")
                if bias is None or abs(bias) < MIN_BIAS:
                    continue
                slot = stats.setdefault(name, {
                    "calls": 0, "correct": 0, "returns": [],
                    "bullish_calls": 0, "bearish_calls": 0,
                })
                slot["calls"] += 1
                if bias > 0:
                    slot["bullish_calls"] += 1
                    # A bullish call is measured on the move as it stands.
                    slot["returns"].append(move)
                    if move > 0:
                        slot["correct"] += 1
                else:
                    slot["bearish_calls"] += 1
                    # A bearish call is correct when price falls, so the sign
                    # is flipped to keep "return when followed" comparable.
                    slot["returns"].append(-move)
                    if move < 0:
                        slot["correct"] += 1

        results = []
        for name, slot in sorted(stats.items()):
            calls = slot["calls"]
            hit_rate = slot["correct"] / calls * 100 if calls else 0.0
            average = sum(slot["returns"]) / len(slot["returns"]) if slot["returns"] else 0.0
            results.append({
                "parameter": name,
                "calls": calls,
                "hit_rate_pct": round(hit_rate, 1),
                "edge_vs_base_pct": round(hit_rate - base_rate, 1),
                "avg_return_when_followed_pct": round(average, 3),
                "bullish_calls": slot["bullish_calls"],
                "bearish_calls": slot["bearish_calls"],
                "sufficient_sample": calls >= min_observations,
            })

        results.sort(key=lambda r: r["edge_vs_base_pct"], reverse=True)
        ready = [r for r in results if r["sufficient_sample"]]

        return {
            "status": "OK",
            "horizon_days": horizon,
            "snapshots": len(rows),
            "base_rate_pct": round(base_rate, 1),
            "base_return_pct": round(base_return, 3),
            "min_observations": min_observations,
            "parameters": results,
            "ready_to_judge": len(ready),
            "detail": (
                f"{len(ready)} of {len(results)} parameters have at least "
                f"{min_observations} observations. Edge is hit rate minus the "
                f"{base_rate:.1f}% base rate -- a parameter matching the base "
                f"rate has no edge, whatever its hit rate looks like."),
        }
    finally:
        session.close()


def status() -> dict:
    """What has been captured so far."""
    create_all(engine)
    session = SessionLocal()
    try:
        total = session.query(ScoreSnapshot).count()
        filled = (session.query(ScoreSnapshot)
                  .filter(ScoreSnapshot.forward_filled_at.isnot(None)).count())
        first = (session.query(ScoreSnapshot)
                 .order_by(ScoreSnapshot.snapshot_date).first())
        last = (session.query(ScoreSnapshot)
                .order_by(ScoreSnapshot.snapshot_date.desc()).first())
        symbols = session.query(ScoreSnapshot.symbol).distinct().count()

        return {
            "status": "OK",
            "snapshots": total,
            "with_outcomes": filled,
            "awaiting_outcomes": total - filled,
            "symbols": symbols,
            "first_capture": first.snapshot_date.isoformat() if first else None,
            "latest_capture": last.snapshot_date.isoformat() if last else None,
            "horizons": list(HORIZONS),
            "detail": (
                "Weights can be measured once parameters reach "
                f"{MIN_OBSERVATIONS} completed observations each."
                if total else
                "Nothing captured yet. Run a capture to start building the "
                "record; the first measurement is possible about a month "
                "later."),
        }
    finally:
        session.close()
