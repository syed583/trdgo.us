"""
Earnings model accuracy scoreboard (verified definition, read-only).

What counts as a verified pre-earnings prediction:
  * only snapshots classified BEFORE_RELEASE (captured before the earnings announcement);
  * RETROSPECTIVE (date-only / backfilled), AFTER_RELEASE and AMBIGUOUS snapshots are excluded;
  * ONE prediction per (model, stock, earnings event): the last eligible snapshot (an alternative
    "first" definition is available for lead-time comparison);
  * the outcome comes from `earnings_event_outcomes` (graded only after the reaction session closed);
  * BUY, SELL and NEUTRAL are reported separately, each with its sample size; NEUTRAL is a no-trade
    call, not a flat-move prediction, so it has no accuracy figure.

Capture classes use PROVISIONAL release windows in America/New_York, because the database stores a
timing label (Before Open / After Close), not the actual release timestamp:
  Before Open: before 06:00 ET on the report day = before release, 06:00-09:30 = ambiguous, after 09:30 =
  after release. After Close: before 16:00 ET on the report day = before release, from 16:00 = after.
  An earlier ET date is before release, a later ET date is after release. A capture stamped at exactly
  00:00 UTC is a date-only (retrospective) record. All figures are therefore labelled provisional until
  actual release timestamps and the original input values are stored.

The original snapshots are never modified; this module only reads.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import earnings_outcomes as eo

NY = ZoneInfo("America/New_York")
BMO_EARLIEST = time(6, 0)
BMO_LATEST = time(9, 30)
AMC_RELEASE = time(16, 0)

BEFORE = "BEFORE_RELEASE"
AFTER = "AFTER_RELEASE"
AMBIGUOUS = "AMBIGUOUS"
RETRO = "RETROSPECTIVE"

PROVISIONAL_REASONS = [
    "Actual earnings-release timestamps are not stored; Before Open / After Close windows are estimates.",
    "Original input values, coverage and data timestamps are not stored, so a prediction cannot be re-derived.",
    "History starts 2026-10-20 (about one week of reports); samples are small and intervals wide.",
]


def classify(earnings_date: date, report_timing: Optional[str], predicted_at: Optional[datetime]) -> str:
    """Capture class of one snapshot (provisional windows)."""
    if predicted_at is None:
        return RETRO
    if predicted_at.tzinfo is None:
        predicted_at = predicted_at.replace(tzinfo=timezone.utc)
    utc = predicted_at.astimezone(timezone.utc)
    if (utc.hour, utc.minute, utc.second, utc.microsecond) == (0, 0, 0, 0):
        return RETRO
    et = predicted_at.astimezone(NY)
    if et.date() < earnings_date:
        return BEFORE
    if et.date() > earnings_date:
        return AFTER
    kind = eo.timing_kind(report_timing)
    t = et.time()
    if kind == eo.BMO:
        return BEFORE if t < BMO_EARLIEST else AMBIGUOUS if t < BMO_LATEST else AFTER
    if kind == eo.AMC:
        return BEFORE if t < AMC_RELEASE else AFTER
    return AMBIGUOUS


def wilson(ok: int, n: int, z: float = 1.96):
    """95% Wilson interval in percent (None when n == 0)."""
    if not n:
        return None
    p = ok / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return [round(max(0.0, centre - half) * 100, 1), round(min(1.0, centre + half) * 100, 1)]


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _pct(ok: int, n: int):
    return round(ok / n * 100, 1) if n else None


def _blank_stats() -> dict:
    return {"graded": 0, "correct": 0}


def build(rows: list, outcomes: dict, models: dict, now: Optional[datetime] = None,
          definition: str = "last") -> dict:
    """The scoreboard from raw snapshot rows and event outcomes (pure; no database access).

    rows     : dicts with model, symbol, earnings_date, report_timing, predicted_at, signal, score
    outcomes : {(symbol, earnings_date): outcome dict} as returned by earnings_outcomes.load_outcomes
    models   : {stored model id: label} -- only these models are scored
    """
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(NY).date()
    per_model: dict[str, dict] = {m: {"events": {}, "rows_by_class": {}} for m in models}
    for r in rows:
        m = r["model"]
        if m not in per_model:
            continue
        cls = classify(r["earnings_date"], r.get("report_timing"), r.get("predicted_at"))
        pm = per_model[m]
        pm["rows_by_class"][cls] = pm["rows_by_class"].get(cls, 0) + 1
        ev = pm["events"].setdefault((r["symbol"], r["earnings_date"]), {"classes": set(), "eligible": [], "label": None})
        ev["classes"].add(cls)
        if r.get("report_timing"):
            ev["label"] = r["report_timing"]
        if cls == BEFORE and r["signal"] in ("BUY", "SELL", "NEUTRAL"):
            ev["eligible"].append(r)

    out_models = []
    chosen: dict = {}                       # model -> {(sym, date): row}   (for consensus)
    for m, label in models.items():
        pm = per_model[m]
        sig = {"BUY": _blank_stats(), "SELL": _blank_stats(), "NEUTRAL": {"graded": 0, "flat_hits": 0}}
        counts = {"events_total": len(pm["events"]), "events_eligible": 0, "events_graded": 0,
                  "events_pending": 0, "events_missing_outcome": 0}
        excluded = {"post_release_only": 0, "ambiguous_only": 0, "retrospective_only": 0}
        base = {"up": 0, "down": 0, "flat": 0}
        ret_sum, ret_n = 0.0, 0
        win = {30: [0, 0], 90: [0, 0]}
        chosen[m] = {}
        for (sym, ed), ev in pm["events"].items():
            if not ev["eligible"]:
                if AFTER in ev["classes"]:
                    excluded["post_release_only"] += 1
                elif AMBIGUOUS in ev["classes"]:
                    excluded["ambiguous_only"] += 1
                else:
                    excluded["retrospective_only"] += 1
                continue
            counts["events_eligible"] += 1
            pick = (min if definition == "first" else max)(ev["eligible"], key=lambda r: _aware(r["predicted_at"]))
            chosen[m][(sym, ed)] = pick
            o = outcomes.get((sym, ed))
            if o and o.get("status") == eo.GRADED and o.get("move_pct") is not None:
                counts["events_graded"] += 1
                mv, actual = float(o["move_pct"]), o["direction"]
                base["up" if actual == "BUY" else "down" if actual == "SELL" else "flat"] += 1
                s = pick["signal"]
                if s == "NEUTRAL":
                    sig["NEUTRAL"]["graded"] += 1
                    sig["NEUTRAL"]["flat_hits"] += 1 if actual == "NEUTRAL" else 0
                else:
                    ok = 1 if s == actual else 0
                    sig[s]["graded"] += 1
                    sig[s]["correct"] += ok
                    ret_sum += mv if s == "BUY" else -mv
                    ret_n += 1
                    age = (today - ed).days
                    for w in win:
                        if age <= w:
                            win[w][0] += 1
                            win[w][1] += ok
            else:
                kind = eo.timing_kind(ev["label"]) or eo.timing_kind((o or {}).get("timing"))
                reported = ed <= today
                ready = False
                if kind and reported:
                    ready = now >= eo.ready_at(eo.reaction_window(ed, kind)[1])
                if ready or (reported and not kind):
                    counts["events_missing_outcome"] += 1      # should be gradable, outcome not stored yet
                else:
                    counts["events_pending"] += 1               # not reported, or reaction session still open
        dir_n = sig["BUY"]["graded"] + sig["SELL"]["graded"]
        dir_ok = sig["BUY"]["correct"] + sig["SELL"]["correct"]
        g = counts["events_graded"]
        entry = {
            "model": m, "label": label, **counts,
            "excluded": excluded, "excluded_events": sum(excluded.values()),
            "rows_by_class": pm["rows_by_class"],
            "buy": {**sig["BUY"], "accuracy": _pct(sig["BUY"]["correct"], sig["BUY"]["graded"]),
                    "ci95": wilson(sig["BUY"]["correct"], sig["BUY"]["graded"])},
            "sell": {**sig["SELL"], "accuracy": _pct(sig["SELL"]["correct"], sig["SELL"]["graded"]),
                     "ci95": wilson(sig["SELL"]["correct"], sig["SELL"]["graded"])},
            "neutral": {**sig["NEUTRAL"], "note": "No-trade call; flat-move hits are informational, not accuracy."},
            "directional": {"graded": dir_n, "correct": dir_ok, "accuracy": _pct(dir_ok, dir_n),
                            "ci95": wilson(dir_ok, dir_n)},
            "baseline": {"graded": g, **base, "up_pct": _pct(base["up"], g), "down_pct": _pct(base["down"], g),
                         "flat_pct": _pct(base["flat"], g), "always_buy_accuracy": _pct(base["up"], g),
                         "always_buy_ci95": wilson(base["up"], g)},
            "avg_return_pct": round(ret_sum / ret_n, 2) if ret_n else None,
            # keys the existing scoreboard page already reads
            "directional_accuracy": _pct(dir_ok, dir_n), "directional_n": dir_n, "directional_correct": dir_ok,
            "buy_accuracy": _pct(sig["BUY"]["correct"], sig["BUY"]["graded"]), "buy_n": sig["BUY"]["graded"],
            "buy_correct": sig["BUY"]["correct"],
            "sell_accuracy": _pct(sig["SELL"]["correct"], sig["SELL"]["graded"]), "sell_n": sig["SELL"]["graded"],
            "sell_correct": sig["SELL"]["correct"],
            "neutral_n": sig["NEUTRAL"]["graded"], "neutral_flat_hits": sig["NEUTRAL"]["flat_hits"],
            "neutral_flat_accuracy": None,
            "evaluated": g, "verified": g, "pending": counts["events_pending"],
            "unverifiable": counts["events_missing_outcome"],
            "acc_30d": _pct(win[30][1], win[30][0]), "n_30d": win[30][0],
            "acc_90d": _pct(win[90][1], win[90][0]), "n_90d": win[90][0],
        }
        out_models.append(entry)
    out_models.sort(key=lambda e: (e["directional"]["accuracy"] is not None, e["directional"]["accuracy"] or -1,
                                   e["directional"]["graded"]), reverse=True)

    # Consensus: only eligible, one-per-event predictions of the scored models.
    consensus = {k: {"agree_at_least": int(k), "signals": 0, "verified": 0, "pending": 0, "correct": 0,
                     "incorrect": 0, "accuracy": None} for k in ("5", "6", "7", "8")}
    all_events: dict = {}
    for m, picks in chosen.items():
        for key, r in picks.items():
            if r["signal"] in ("BUY", "SELL"):
                all_events.setdefault(key, {})[m] = r["signal"]
    for key, votes in all_events.items():
        buys = sum(1 for v in votes.values() if v == "BUY")
        sells = sum(1 for v in votes.values() if v == "SELL")
        side, agree = ("BUY", buys) if buys >= sells else ("SELL", sells)
        o = outcomes.get(key)
        graded = bool(o and o.get("status") == eo.GRADED)
        for thr in (5, 6, 7, 8):
            if agree >= thr:
                c = consensus[str(thr)]
                c["signals"] += 1
                if not graded:
                    c["pending"] += 1
                else:
                    c["verified"] += 1
                    c["correct" if o["direction"] == side else "incorrect"] += 1
    for c in consensus.values():
        c["accuracy"] = _pct(c["correct"], c["verified"])

    total_graded = max((e["events_graded"] for e in out_models), default=0)
    return {
        "status": "OK", "source": "Earnings Model Accuracy Scoreboard (verified definition)",
        "as_of": today.isoformat(), "provisional": True, "provisional_reasons": PROVISIONAL_REASONS,
        "method": {"version": eo.METHOD_VERSION, "selection": f"{definition} eligible pre-release snapshot per "
                   "stock / earnings event / model", "eligible_class": BEFORE, "flat_band_pct": eo.FLAT_PCT,
                   "windows_et": {"before_open_before": "06:00", "before_open_ambiguous_until": "09:30",
                                  "after_close_release": "16:00"}},
        "models": out_models, "consensus": consensus, "consensus_has_history": any(
            v["verified"] for v in consensus.values()),
        "totals": {"models": len(out_models), "evaluated_events": total_graded,
                   "eligible_events": max((e["events_eligible"] for e in out_models), default=0),
                   "missing_outcome": max((m["events_missing_outcome"] for m in out_models), default=0),
                   "excluded_events": max((m["excluded_events"] for m in out_models), default=0),
                   "ranking_withheld": True,
                   "best_model": None, "best_accuracy": None, "worst_model": None, "worst_accuracy": None},
        "note": ("PROVISIONAL. One prediction per stock, earnings event and model, using only snapshots captured "
                 "before the estimated release; retrospective, post-release and ambiguous snapshots are excluded. "
                 "Outcomes are graded only after the reaction session has closed. Samples are small: read each "
                 "figure with its interval and the market-direction baseline; no ranking is implied."),
    }


# --------------------------------------------------------------------------------------------- #
# Database access                                                                                #
# --------------------------------------------------------------------------------------------- #
def load_rows(db, model_ids) -> list:
    from sqlalchemy import text
    ids = list(model_ids)
    if not ids:
        return []
    ph = ",".join(f":m{i}" for i in range(len(ids)))
    res = db.execute(text(
        "SELECT model, symbol, earnings_date, report_timing, predicted_at, signal, score FROM model_predictions"
        f" WHERE earnings_date IS NOT NULL AND model IN ({ph})"), {f"m{i}": v for i, v in enumerate(ids)})
    out = []
    for r in res:
        out.append({"model": r.model, "symbol": r.symbol, "earnings_date": eo._d(r.earnings_date),
                    "report_timing": r.report_timing, "predicted_at": eo._dt(r.predicted_at),
                    "signal": r.signal, "score": r.score})
    return out


def scoreboard(models: dict, db=None, now: Optional[datetime] = None, definition: str = "last") -> dict:
    """Read-only scoreboard from the database (model_predictions + earnings_event_outcomes)."""
    own = db is None
    if own:
        from database import SessionLocal
        db = SessionLocal()
    try:
        try:
            outcomes = eo.load_outcomes(db)
        except Exception:  # noqa: BLE001 - table not created yet: every event is simply pending
            db.rollback()
            outcomes = {}
        rows = load_rows(db, models.keys())
        return build(rows, outcomes, models, now=now, definition=definition)
    finally:
        if own:
            db.close()
