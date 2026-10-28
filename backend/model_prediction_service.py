"""Earnings model-prediction capture + grading + accuracy scoreboard (read-only).

PHASE 1 of the Accuracy Scoreboard. This module ONLY records what the existing
10 earnings models already decide and grades those records once the real
post-earnings move is known. It never changes a model, a weight, a cutoff, or a
live signal -- it reads `all_models_service.scan()` (which itself reads the
warmed caches) and writes a separate history table.

Safety rules honoured here (owner's spec):
  * Original predictions are preserved -- capture is INSERT ... ON CONFLICT DO
    NOTHING, so the first call recorded for a model/symbol/earnings-event/day is
    never overwritten.
  * Predictions are only captured BEFORE the earnings announcement
    (earnings_date >= today).
  * Grading uses the real post-earnings move (earnings_trade_service.
    _historical_moves), which is look-ahead-safe (prior close -> reaction close,
    AMC lands on the next session). Nothing published after the prediction is
    used to reconstruct it.
  * Accuracy dedupes multiple daily snapshots of the same event to ONE
    prediction -- the last snapshot on/before the earnings date -- so repeated
    captures are not counted as independent predictions.
  * Every accuracy figure carries its sample size (n). Unreached events stay
    UNVERIFIED (never guessed).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

SOURCE = "Earnings Model Accuracy Scoreboard"

# A real post-earnings move inside +/-this band counts as no clear reaction.
_FLAT_PCT = 0.5

_ready = False


def _model_labels() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    try:
        import all_models_service as am
        out = [(_STORED_ID.get(k, k), lbl) for k, lbl in am.MODELS]
    except Exception:  # noqa: BLE001
        out = []
    # PED (Pre-Earnings Direction tab) is tracked too -- it is the one earnings
    # model with real stored history (ped_scores), backfilled below.
    if not any(k == "ped" for k, _ in out):
        out.append(("ped", "Pre-Earnings Direction"))
    return out


def backfill_claude_pm() -> int:
    """Seed model_predictions for **Claude +-** from its real stored history.

    `earnings_signal_snapshots.stock` is the Claude earnings decision captured
    daily (BUY/SELL/NEUTRAL) -- exactly what the Claude +- card shows (the
    decision bucket). That table has no earnings_date, so each snapshot is tied
    to a real earnings event using the known event dates (ped_scores +
    earnings_calendar), taking the LAST snapshot on/before the earnings date as
    the pre-earnings call (look-ahead-safe). One prediction per event. Genuine
    recorded history -- not reconstructed from anything after the fact."""
    if not _ensure_table():
        return 0
    from sqlalchemy import text
    from database import SessionLocal
    db = SessionLocal()
    try:
        n = db.execute(text(
            "INSERT INTO model_predictions "
            "(model, symbol, earnings_date, report_timing, snapshot_date, "
            " predicted_at, signal, score) "
            "SELECT DISTINCT ON (ev.symbol, ev.earnings_date) "
            "  'claude_pm', e.symbol, ev.earnings_date, NULL, e.snapshot_date, "
            "  e.snapshot_date::timestamptz, e.stock, NULL "
            "FROM earnings_signal_snapshots e "
            "JOIN ( "
            "  SELECT DISTINCT symbol, earnings_date FROM ped_scores WHERE earnings_date IS NOT NULL "
            "  UNION "
            "  SELECT DISTINCT symbol, earnings_date FROM earnings_calendar WHERE earnings_date IS NOT NULL "
            ") ev ON ev.symbol = e.symbol AND e.snapshot_date <= ev.earnings_date "
            "WHERE e.stock IN ('BUY','SELL','NEUTRAL') "
            "ORDER BY ev.symbol, ev.earnings_date, e.snapshot_date DESC "
            "ON CONFLICT (model, symbol, earnings_date, snapshot_date) DO NOTHING"
        )).rowcount or 0
        db.commit()
        return n
    except Exception:  # noqa: BLE001
        db.rollback()
        return 0
    finally:
        db.close()


def backfill_ped() -> int:
    """Seed model_predictions from the real stored Pre-Earnings Direction history
    (ped_scores: symbol, snapshot_date, signal, score, earnings_date). This is
    genuine recorded history -- not fabricated -- and is the only model with a
    pre-existing track record. INSERT ... DO NOTHING preserves anything already
    captured. Returns rows added."""
    if not _ensure_table():
        return 0
    from sqlalchemy import text
    from database import SessionLocal
    db = SessionLocal()
    try:
        n = db.execute(text(
            "INSERT INTO model_predictions "
            "(model, symbol, earnings_date, report_timing, snapshot_date, "
            " predicted_at, signal, score) "
            "SELECT 'ped', symbol, earnings_date, NULL, snapshot_date, "
            "       snapshot_date::timestamptz, signal, score "
            "FROM ped_scores "
            "WHERE signal IN ('BUY','SELL','NEUTRAL') AND earnings_date IS NOT NULL "
            "ON CONFLICT (model, symbol, earnings_date, snapshot_date) DO NOTHING"
        )).rowcount or 0
        db.commit()
        return n
    except Exception:  # noqa: BLE001
        db.rollback()
        return 0
    finally:
        db.close()


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
                "CREATE TABLE IF NOT EXISTS model_predictions ("
                "  id             BIGSERIAL PRIMARY KEY,"
                "  model          VARCHAR(24) NOT NULL,"
                "  symbol         VARCHAR(20) NOT NULL,"
                "  earnings_date  DATE,"
                "  report_timing  VARCHAR(16),"
                "  snapshot_date  DATE        NOT NULL,"
                "  predicted_at   TIMESTAMPTZ NOT NULL DEFAULT now(),"
                "  signal         VARCHAR(10) NOT NULL,"
                "  score          DOUBLE PRECISION,"
                "  actual_move_pct   DOUBLE PRECISION,"
                "  actual_direction  VARCHAR(10),"
                "  correct           SMALLINT,"
                "  evaluated_at      TIMESTAMPTZ,"
                "  UNIQUE (model, symbol, earnings_date, snapshot_date)"
                ")"))
            db.execute(text("CREATE INDEX IF NOT EXISTS ix_modpred_model "
                            "ON model_predictions(model)"))
            db.execute(text("CREATE INDEX IF NOT EXISTS ix_modpred_earn "
                            "ON model_predictions(earnings_date)"))
            db.execute(text("CREATE INDEX IF NOT EXISTS ix_modpred_ungraded "
                            "ON model_predictions(symbol) "
                            "WHERE evaluated_at IS NULL"))
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


# --------------------------------------------------------------------------- #
# Capture                                                                     #
# --------------------------------------------------------------------------- #
# Sheet column id -> the model id its history was always stored under.
_STORED_ID = {"claude": "claude_new"}


def capture(days: int = 15) -> int:
    """Record today's BUY/SELL/NEUTRAL + score for every one of the 10 models,
    for each upcoming earnings name. Reads the warmed caches via all_models.
    Returns the number of prediction rows written (new rows only)."""
    if not _ensure_table():
        return 0
    try:
        import all_models_service as am
        res = am.scan(days)
    except Exception:  # noqa: BLE001
        return 0
    rows = (res or {}).get("rows") or []
    today = date.today()

    payload: list[dict] = []
    for r in rows:
        sym = (r.get("symbol") or "").upper()
        ed = r.get("date")
        try:
            edate = datetime.fromisoformat(str(ed)[:10]).date() if ed else None
        except Exception:  # noqa: BLE001
            edate = None
        # Only record predictions made BEFORE the announcement.
        if edate is None or edate < today:
            continue
        timing = r.get("report_timing")
        for model, cell in (r.get("cells") or {}).items():
            sig = (cell or {}).get("signal")
            if sig not in ("BUY", "SELL", "NEUTRAL"):
                continue
            # The sheet's "claude" column is the stored history's "claude_new" model.
            model = _STORED_ID.get(model, model)
            payload.append({"model": model, "symbol": sym, "edate": edate,
                            "timing": timing, "snap": today,
                            "signal": sig, "score": (cell or {}).get("score")})
    if not payload:
        return 0

    from sqlalchemy import text
    from database import SessionLocal
    db = SessionLocal()
    try:
        n = 0
        stmt = text(
            "INSERT INTO model_predictions "
            "(model, symbol, earnings_date, report_timing, snapshot_date, signal, score) "
            "VALUES (:model, :symbol, :edate, :timing, :snap, :signal, :score) "
            "ON CONFLICT (model, symbol, earnings_date, snapshot_date) DO NOTHING")
        for p in payload:
            res = db.execute(stmt, p)
            n += res.rowcount or 0
        db.commit()
        return n
    except Exception:  # noqa: BLE001
        db.rollback()
        return 0
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Grading                                                                      #
# --------------------------------------------------------------------------- #
LEGACY_OUTCOMES_NOTE = (
    "LEGACY outcome data: actual_move_pct / actual_direction / correct on model_predictions were computed by the "
    "old grader, which read pre-market / after-hours candle rows instead of regular-session closes. In the "
    "2026-10-29 audit sample 28 of 33 stored values differed from the verified move and 14 changed the "
    "BUY/SELL/NEUTRAL class. Do not use them for verified performance reporting; use /model-accuracy/scoreboard.")


def legacy_sync_enabled() -> bool:
    """Writing verified outcomes into the legacy columns is OFF unless EARNINGS_LEGACY_SYNC is explicitly truthy."""
    import os
    return os.environ.get("EARNINGS_LEGACY_SYNC", "").strip().lower() in ("1", "true", "yes", "on")


def grade(max_symbols: int = 120) -> int:
    """Grade reported earnings events and return how many were newly graded.

    Phase 2A lifecycle (earnings_outcomes): an event is graded only after its reaction session has
    closed (Before Open: the earnings-day session; After Close: the next valid trading session; US
    holidays, early closes and daylight saving respected) and both closing prices exist. Until then it
    stays pending and is retried on a back-off schedule that never stops -- an outcome that is simply
    not available yet is never recorded as permanently unverifiable. Outcomes live in
    `earnings_event_outcomes`. The legacy columns of model_predictions are NOT written unless
    EARNINGS_LEGACY_SYNC is explicitly enabled (and even then only for never-evaluated rows)."""
    if not _ensure_table():
        return 0
    import earnings_outcomes as eo
    from database import SessionLocal
    db = SessionLocal()
    try:
        summary = eo.run(max_symbols=max_symbols, db=db)
        if legacy_sync_enabled():
            eo.sync_legacy_columns(db)
        return int(summary.get("graded_new", 0))
    except Exception:  # noqa: BLE001
        db.rollback()
        return 0
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Scoreboard (read-only analytics)                                             #
# --------------------------------------------------------------------------- #
def _latest_per_event_sql() -> str:
    """One prediction per (model, symbol, earnings_date): the last snapshot on or
    before the earnings date. Dedupes repeated daily captures of one event."""
    return (
        "SELECT DISTINCT ON (model, symbol, earnings_date) "
        "  model, symbol, earnings_date, signal, score, "
        "  actual_move_pct, actual_direction, correct, evaluated_at "
        "FROM model_predictions "
        "WHERE earnings_date IS NOT NULL "
        "  AND snapshot_date <= earnings_date "
        "ORDER BY model, symbol, earnings_date, snapshot_date DESC")


def scoreboard_legacy() -> dict:
    """LEGACY scoreboard (kept for comparison, not shown by default): every snapshot class, the
    latest snapshot on/before the earnings date, and legacy outcomes that may be unverifiable.
    Per-model accuracy over graded events, with 7/30/90-day cuts, BUY/SELL
    splits, average move, direction-aware return, confidence buckets, consensus
    accuracy -- every figure with its sample size. Read-only."""
    if not _ensure_table():
        return {"status": "NO_TABLE", "source": SOURCE, "models": []}
    from sqlalchemy import text
    from database import SessionLocal
    db = SessionLocal()
    try:
        rows = db.execute(text(
            f"SELECT * FROM ({_latest_per_event_sql()}) q")).all()
    except Exception:  # noqa: BLE001
        return {"status": "ERROR", "source": SOURCE, "models": []}
    finally:
        db.close()

    today = date.today()
    labels = dict(_model_labels())

    # ---- per model ----
    per: dict[str, dict] = {}

    def _blank() -> dict:
        return {"n_total": 0,
                # record buckets
                "verified": 0, "incorrect_bucket": 0, "pending": 0, "unverifiable": 0,
                # directional (BUY/SELL only) -- the headline accuracy
                "buy_n": 0, "buy_ok": 0, "sell_n": 0, "sell_ok": 0,
                # NEUTRAL kept entirely separate (flat-band match)
                "neutral_n": 0, "neutral_ok": 0,
                "move_sum": 0.0, "move_n": 0, "ret_sum": 0.0, "ret_n": 0,
                # time windows track DIRECTIONAL correctness only
                "w7": [0, 0], "w30": [0, 0], "w90": [0, 0],
                "conf": {b: [0, 0] for b in ("50-59", "60-69", "70-79", "80-89", "90-100")}}

    # consensus accumulation per event (directional votes only)
    events: dict[tuple, dict] = {}

    for m in rows:
        mdl = m.model
        d = per.setdefault(mdl, _blank())
        d["n_total"] += 1
        sig = m.signal
        reported = (m.earnings_date is not None and m.earnings_date < today)
        graded = m.correct is not None
        # Record-state bucketing (req 1): VERIFIED/INCORRECT vs PENDING vs UNVERIFIABLE.
        if graded:
            d["verified" if int(m.correct) == 1 else "incorrect_bucket"] += 1
        elif reported and m.evaluated_at is not None:
            d["unverifiable"] += 1          # reported, but no usable outcome
        else:
            d["pending"] += 1               # not reported yet (or not graded yet)

        # consensus: only directional votes, against the one real outcome.
        ev = events.setdefault((m.symbol, str(m.earnings_date)), {"votes": {}, "actual": m.actual_direction})
        if sig in ("BUY", "SELL"):
            ev["votes"][mdl] = sig
        if m.actual_direction is not None:
            ev["actual"] = m.actual_direction
        if not graded:
            continue
        ok = int(m.correct)
        mv = float(m.actual_move_pct) if m.actual_move_pct is not None else None
        if mv is not None:
            d["move_sum"] += mv; d["move_n"] += 1
        if sig == "BUY":
            d["buy_n"] += 1; d["buy_ok"] += ok
            if mv is not None:
                d["ret_sum"] += mv; d["ret_n"] += 1
        elif sig == "SELL":
            d["sell_n"] += 1; d["sell_ok"] += ok
            if mv is not None:
                d["ret_sum"] += -mv; d["ret_n"] += 1
        else:
            d["neutral_n"] += 1; d["neutral_ok"] += ok
        # time windows -- DIRECTIONAL only (BUY/SELL), by earnings date.
        if m.earnings_date is not None and sig in ("BUY", "SELL"):
            age = (today - m.earnings_date).days
            if age <= 7:
                d["w7"][0] += 1; d["w7"][1] += ok
            if age <= 30:
                d["w30"][0] += 1; d["w30"][1] += ok
            if age <= 90:
                d["w90"][0] += 1; d["w90"][1] += ok
        # confidence bucket by score (directional calls only)
        sc = m.score
        if sc is not None and sig in ("BUY", "SELL"):
            conf = 100 - sc if sc < 50 else sc
            b = ("90-100" if conf >= 90 else "80-89" if conf >= 80 else "70-79" if conf >= 70
                 else "60-69" if conf >= 60 else "50-59")
            d["conf"][b][0] += 1; d["conf"][b][1] += ok

    def pct(ok, n):
        return round(ok / n * 100, 1) if n else None

    out_models = []
    for mdl, d in per.items():
        dir_n = d["buy_n"] + d["sell_n"]
        dir_ok = d["buy_ok"] + d["sell_ok"]
        graded_n = dir_n + d["neutral_n"]
        out_models.append({
            "model": mdl, "label": labels.get(mdl, mdl),
            "predictions_total": d["n_total"],
            # record buckets (req 1)
            "verified": d["verified"], "incorrect": d["incorrect_bucket"],
            "pending": d["pending"], "unverifiable": d["unverifiable"],
            # headline = DIRECTIONAL accuracy (BUY/SELL only)
            "evaluated": graded_n,
            "directional_n": dir_n, "directional_correct": dir_ok,
            "directional_accuracy": pct(dir_ok, dir_n),
            "accuracy": pct(dir_ok, dir_n),   # headline alias
            "buy_n": d["buy_n"], "buy_correct": d["buy_ok"], "buy_accuracy": pct(d["buy_ok"], d["buy_n"]),
            "sell_n": d["sell_n"], "sell_correct": d["sell_ok"], "sell_accuracy": pct(d["sell_ok"], d["sell_n"]),
            # NEUTRAL kept separate -- a no-trade call, flat-band match
            "neutral_n": d["neutral_n"], "neutral_flat_hits": d["neutral_ok"],
            "neutral_flat_accuracy": pct(d["neutral_ok"], d["neutral_n"]),
            "avg_move_pct": round(d["move_sum"] / d["move_n"], 2) if d["move_n"] else None,
            "avg_return_pct": round(d["ret_sum"] / d["ret_n"], 2) if d["ret_n"] else None,
            "acc_7d": pct(d["w7"][1], d["w7"][0]), "n_7d": d["w7"][0],
            "acc_30d": pct(d["w30"][1], d["w30"][0]), "n_30d": d["w30"][0],
            "acc_90d": pct(d["w90"][1], d["w90"][0]), "n_90d": d["w90"][0],
            "confidence": {b: {"n": v[0], "accuracy": pct(v[1], v[0])}
                           for b, v in d["conf"].items()},
        })
    # rank by accuracy (models with no evaluated events sort last)
    out_models.sort(key=lambda m: (m["accuracy"] is not None, m["accuracy"] or -1,
                                   m["evaluated"]), reverse=True)

    # ---- consensus accuracy ----
    # An event's "agreement" is how many of the 10 models made the SAME
    # directional (BUY/SELL) call -- NEUTRAL is never counted as agreement
    # (req 4). A threshold is only populated by events where that many models
    # actually voted the same way. Verified = the event has a real outcome;
    # pending = it has not reported / not graded yet (req 5/7).
    consensus = {k: {"signals": 0, "verified": 0, "pending": 0,
                     "correct": 0, "incorrect": 0}
                 for k in ("6", "7", "8", "9", "10")}
    for (_sym, _ed), ev in events.items():
        votes = list(ev["votes"].values())          # directional votes only
        buys = sum(1 for v in votes if v == "BUY")
        sells = sum(1 for v in votes if v == "SELL")
        if buys == 0 and sells == 0:
            continue
        side, agree = ("BUY", buys) if buys >= sells else ("SELL", sells)
        actual = ev["actual"]
        for thr in (6, 7, 8, 9, 10):
            if agree >= thr:
                c = consensus[str(thr)]
                c["signals"] += 1
                if actual is None:
                    c["pending"] += 1
                else:
                    c["verified"] += 1
                    if side == actual:
                        c["correct"] += 1
                    else:
                        c["incorrect"] += 1
    consensus_out = {k: {"agree_at_least": int(k),
                         "signals": v["signals"], "verified": v["verified"],
                         "pending": v["pending"], "correct": v["correct"],
                         "incorrect": v["incorrect"],
                         "accuracy": pct(v["correct"], v["verified"])}
                     for k, v in consensus.items()}
    consensus_has_history = any(v["verified"] for v in consensus.values())

    total_eval = sum(m["evaluated"] for m in out_models)
    best = next((m for m in out_models if m["accuracy"] is not None), None)
    worst = next((m for m in reversed(out_models) if m["accuracy"] is not None), None)

    return {"status": "OK", "source": SOURCE,
            "as_of": today.isoformat(),
            "models": out_models,
            "consensus": consensus_out,
            "consensus_has_history": consensus_has_history,
            "totals": {"models": len(out_models), "evaluated_events": total_eval,
                       "best_model": best["label"] if best else None,
                       "best_accuracy": best["accuracy"] if best else None,
                       "worst_model": worst["label"] if worst else None,
                       "worst_accuracy": worst["accuracy"] if worst else None},
            "note": ("Accuracy is measured only on earnings that have already "
                     "reported; each figure shows its sample size (n). Models "
                     "added recently have little or no evaluated history yet.")}


def scoreboard(definition: str = "last") -> dict:
    """Verified earnings accuracy scoreboard (read-only, provisional): one eligible pre-release prediction
    per stock / earnings event / model, graded outcomes only, BUY / SELL / NEUTRAL separate. See
    earnings_scoreboard for the exact definition."""
    import earnings_scoreboard as sb
    models = dict(_model_labels())
    # only the live earnings models are scored; retired ids stay in the table untouched
    return sb.scoreboard(models, definition=definition)


def history(model: Optional[str] = None, symbol: Optional[str] = None,
            limit: int = 500) -> dict:
    """Flat list of graded predictions for the verification table (read-only)."""
    if not _ensure_table():
        return {"status": "NO_TABLE", "source": SOURCE, "rows": []}
    from sqlalchemy import text
    from database import SessionLocal
    db = SessionLocal()
    try:
        where = ["earnings_date IS NOT NULL", "snapshot_date <= earnings_date"]
        params: dict = {"lim": max(1, min(int(limit or 500), 2000))}
        if model:
            where.append("model = :model"); params["model"] = model
        if symbol:
            where.append("symbol = :symbol"); params["symbol"] = symbol.upper()
        sql = (f"SELECT DISTINCT ON (model, symbol, earnings_date) "
               f"  model, symbol, earnings_date, report_timing, snapshot_date, "
               f"  predicted_at, signal, score, actual_move_pct, actual_direction, "
               f"  correct, evaluated_at "
               f"FROM model_predictions WHERE {' AND '.join(where)} "
               f"ORDER BY model, symbol, earnings_date, snapshot_date DESC "
               f"LIMIT :lim")
        rows = db.execute(text(sql), params).all()
    except Exception:  # noqa: BLE001
        return {"status": "ERROR", "source": SOURCE, "rows": []}
    finally:
        db.close()
    labels = dict(_model_labels())
    today = date.today()
    out = []
    for r in rows:
        reported = (r.earnings_date is not None and r.earnings_date < today)
        if r.correct is not None:
            result = "CORRECT" if r.correct else "INCORRECT"
        elif reported and r.evaluated_at is not None:
            result = "UNVERIFIABLE"       # reported, no usable outcome
        else:
            result = "PENDING"            # not reported yet
        out.append({"model": r.model, "model_label": labels.get(r.model, r.model),
                    "symbol": r.symbol, "earnings_date": str(r.earnings_date),
                    "report_timing": r.report_timing,
                    "snapshot_date": str(r.snapshot_date),
                    "predicted_at": r.predicted_at.isoformat() if r.predicted_at else None,
                    "signal": r.signal, "score": r.score,
                    "actual_move_pct": r.actual_move_pct,
                    "actual_direction": r.actual_direction, "result": result})
    return {"status": "OK", "source": SOURCE, "rows": out, "count": len(out),
            "legacy_outcomes_invalid": True, "legacy_note": LEGACY_OUTCOMES_NOTE}


def overlay(symbols: list[str]) -> dict:
    """Historical overlay for the All Models page (req 2). Returns, for the given
    upcoming names:
      model_stats : per model -> verified directional accuracy + sample size +
                    30/90-day cuts (so each column can show how it has performed).
      prev        : per (model, symbol) -> the model's LAST graded call on that
                    company's previous earnings (signal, actual move, result).
      last_move   : per symbol -> the company's most recent real post-earnings
                    move % (what happened last time).
    All read from the stored graded predictions -- no extra API calls, no model
    recomputation. Missing history is simply absent (never shown as 0%)."""
    syms = [s.upper() for s in (symbols or []) if s]
    out = {"model_stats": {}, "prev": {}, "last_move": {},
           "legacy_outcomes_invalid": True, "legacy_note": LEGACY_OUTCOMES_NOTE}
    # Per-model stats straight from the scoreboard (directional headline).
    try:
        sb = scoreboard()
        for m in sb.get("models", []):
            out["model_stats"][m["model"]] = {
                "directional_accuracy": m["directional_accuracy"],
                "directional_n": m["directional_n"],
                "acc_30d": m["acc_30d"], "n_30d": m["n_30d"],
                "acc_90d": m["acc_90d"], "n_90d": m["n_90d"],
                "verified": m["verified"], "pending": m["pending"],
                "unverifiable": m["unverifiable"]}
    except Exception:  # noqa: BLE001
        pass
    if not syms:
        return out
    from sqlalchemy import text
    from database import SessionLocal
    db = SessionLocal()
    try:
        today = date.today()
        prows = db.execute(text(
            "SELECT DISTINCT ON (model, symbol) model, symbol, earnings_date, "
            "  signal, actual_move_pct, actual_direction, correct "
            "FROM model_predictions "
            "WHERE symbol = ANY(:s) AND correct IS NOT NULL AND earnings_date < :t "
            "ORDER BY model, symbol, earnings_date DESC"),
            {"s": syms, "t": today}).all()
        for r in prows:
            out["prev"].setdefault(r.model, {})[r.symbol] = {
                "signal": r.signal, "actual_move_pct": r.actual_move_pct,
                "actual_direction": r.actual_direction,
                "earnings_date": str(r.earnings_date),
                "result": "CORRECT" if r.correct else "INCORRECT"}
        lrows = db.execute(text(
            "SELECT DISTINCT ON (symbol) symbol, earnings_date, actual_move_pct "
            "FROM model_predictions "
            "WHERE symbol = ANY(:s) AND actual_move_pct IS NOT NULL AND earnings_date < :t "
            "ORDER BY symbol, earnings_date DESC"),
            {"s": syms, "t": today}).all()
        for r in lrows:
            out["last_move"][r.symbol] = {"move_pct": r.actual_move_pct,
                                          "earnings_date": str(r.earnings_date)}
    except Exception:  # noqa: BLE001
        pass
    finally:
        db.close()
    return out
