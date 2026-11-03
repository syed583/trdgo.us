"""Read-only API for the Earnings monitor dashboard. No write, no provider, no scoring: it reports what the monitor stored. It sits behind the app's existing session gate like every /api route."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from itertools import combinations
from typing import Optional

from fastapi import APIRouter, Query

from . import config as C
from . import logic as L
from .adapters import LABELS
from .gate import evaluate
from .runtime import data_dir, now
from .scheduler import status_report
from .service import HORIZON_DAYS, SESSIONS, today_et
from .store import Store, parse

router = APIRouter(prefix="/api/earnings-monitor", tags=["earnings-monitor"])
FLAT_PCT = 0.5            # same flat band the existing scoreboard uses for the real post-earnings move


def _store() -> Optional[Store]:
    d = data_dir()
    return Store(d / "monitor.sqlite") if (d / "monitor.sqlite").exists() else None


def _empty(msg="The monitor has not run yet: no data store exists."):
    return {"status": "NO_DATA", "detail": msg, "rows": []}


def _age_h(ts: Optional[str], t: datetime) -> Optional[float]:
    return round((t - parse(ts)).total_seconds() / 3600, 2) if ts else None


@router.get("/status")
def status():
    t = now()
    d = data_dir()
    cfg = C.Config.load(str(d / "config.json"))
    gate = evaluate(cfg, d, t)
    s = _store()
    base = {"read_only": True, "now": t.isoformat(), "gate_now": {"mode": gate.mode, "reasons": gate.reasons}, "estimate_20_events_per_day": C.estimate(cfg, 20),
            "estimate_if_approved": C.estimate(cfg, int(gate.approval.get("expected_events_per_day") or 20), purposes=gate.purposes) if gate.purposes else None}
    if s is None:
        return dict(base, status="NOT_STARTED", scheduling="OFF", detail="The monitor has never run on this server.")
    rep = status_report(s, cfg, t)
    rep["auto_refresh_seconds"] = 60
    rep["smart_move_plan"] = s.kv_get("smart_move:plan")
    return dict(base, status="OK", **rep)


@router.get("/upcoming")
def upcoming(include_history: bool = False):
    s = _store()
    if s is None:
        return _empty()
    t = now()
    rows = s.q("SELECT * FROM events" + ("" if include_history else " WHERE status='ACTIVE'") + " ORDER BY earnings_date, symbol")
    out = []
    for e in rows:
        days = (date.fromisoformat(e["earnings_date"]) - today_et(t)).days
        reasons = []
        if e["status"] != "ACTIVE":
            reasons.append(e["status"])
        if e["session"] not in SESSIONS:
            reasons.append("SESSION_NOT_CONFIRMED")
        if e["verification"] == "CONFLICT":
            reasons.append("CALENDAR_VENDORS_DISAGREE")
        if days > HORIZON_DAYS:
            reasons.append("BEYOND_%d_DAY_HORIZON" % HORIZON_DAYS)
        last = s.one("SELECT detected_at, kind FROM changes WHERE event_id=? ORDER BY id DESC LIMIT 1", (e["id"],))
        versions = s.q("SELECT earnings_date, session, observed_at, reason FROM event_versions WHERE event_id=? ORDER BY id", (e["id"],))
        valid = (s.one("SELECT COUNT(*) n FROM model_state WHERE event_id=? AND status='VALID'", (e["id"],)) or {}).get("n", 0)
        out.append({"id": e["id"], "symbol": e["symbol"], "company": e["company"], "earnings_date": e["earnings_date"], "session": e["session"], "status": e["status"], "days_to": days,
                    "verification": e["verification"], "sources": json.loads(e["sources"]) if e["sources"] else [], "eligible": not reasons, "ineligible_reasons": reasons, "first_seen": e["first_seen"],
                    "last_seen": e["last_seen"], "last_changed": e["last_changed"], "latest_change": last, "versions": versions, "valid_predictions": valid,
                    "outcome": json.loads(e["outcome_json"]) if e["outcome_json"] else None})
    return {"status": "OK", "now": t.isoformat(), "rows": out, "count": len(out), "horizon_days": HORIZON_DAYS}


@router.get("/models")
def models():
    """Every model's real stored call per upcoming event with its timestamp, source, freshness, coverage and validation status. PENDING / NO VALID PREDICTION / LOW DATA are shown as such, never as a call."""
    s = _store()
    if s is None:
        return _empty()
    t = now()
    cfg = C.Config.load(str(data_dir() / "config.json"))
    out = []
    for e in s.q("SELECT * FROM events WHERE status='ACTIVE' ORDER BY earnings_date, symbol"):
        cells = {}
        for m in s.q("SELECT * FROM model_state WHERE event_id=?", (e["id"],)):
            pred = s.one("SELECT recorded_at, as_of, source, engine_version FROM predictions WHERE id=?", (m["prediction_id"],)) if m["prediction_id"] else None
            cov = json.loads(m["coverage"]) if m["coverage"] else None
            n = (s.one("SELECT COUNT(*) n FROM predictions WHERE event_id=? AND model=?", (e["id"], m["model"])) or {}).get("n", 0)
            st = {"status": m["status"], "signal": m["signal"], "stale": bool(m["stale"])}
            cells[m["model"]] = {"status": m["status"], "label": L.display_status(st), "signal": m["signal"], "score": m["score"], "coverage": cov, "coverage_fraction": L.coverage_fraction(cov), "as_of": m["as_of"],
                                 "age_h": _age_h(m["as_of"], t), "stale": bool(m["stale"]), "source": (pred or {}).get("source") or m["source"], "engine_version": (pred or {}).get("engine_version"),
                                 "prediction_recorded_at": pred["recorded_at"] if pred else None, "predictions_recorded": n,
                                 "validation": "VALID" if m["status"] == "VALID" and not m["stale"] else ("STALE" if m["status"] == "VALID" else m["status"])}
        out.append({"id": e["id"], "symbol": e["symbol"], "company": e["company"], "earnings_date": e["earnings_date"], "session": e["session"], "verification": e["verification"], "cells": cells})
    return {"status": "OK", "models": [{"id": k, "label": v} for k, v in LABELS.items()], "stale_after_h": cfg.stale_after_h, "rows": out,
            "note": "A cell is a model's own call. PENDING, LOW DATA and NO VALID PREDICTION mean it made no call; they are never a neutral view. NO TRADE is a real call that says do nothing."}


@router.get("/history")
def history(symbol: str = Query(..., max_length=12), model: Optional[str] = Query(None, max_length=24)):
    """Every stored version of every prediction for a stock: signal, score, coverage, source, engine version and the times. Nothing is overwritten, so this is the full audit trail."""
    s = _store()
    if s is None:
        return _empty()
    sql, args = "SELECT p.*, e.earnings_date, e.session FROM predictions p JOIN events e ON e.id=p.event_id WHERE e.symbol=?", [symbol.upper()]
    if model:
        sql += " AND p.model=?"
        args.append(model)
    rows = s.q(sql + " ORDER BY p.id", args)
    for r in rows:
        r["coverage"] = json.loads(r["coverage"]) if r["coverage"] else None
    return {"status": "OK", "symbol": symbol.upper(), "rows": rows, "count": len(rows)}


@router.get("/changes")
def changes(limit: int = Query(200, ge=1, le=1000), kind: Optional[str] = Query(None, max_length=40), symbol: Optional[str] = Query(None, max_length=12)):
    s = _store()
    if s is None:
        return _empty()
    sql, args = "SELECT * FROM changes WHERE 1=1", []
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    if symbol:
        sql += " AND symbol=?"
        args.append(symbol.upper())
    rows = s.q(sql + " ORDER BY id DESC LIMIT ?", args + [limit])
    for r in rows:
        r["detail"] = json.loads(r["detail"] or "{}")
    last = s.one("SELECT MAX(detected_at) t FROM changes")
    return {"status": "OK", "latest_change_at": (last or {}).get("t"), "kinds": list(L.CHANGE_KINDS), "rows": rows, "count": len(rows)}


@router.get("/consensus")
def consensus():
    s = _store()
    if s is None:
        return _empty()
    cfg = C.Config.load(str(data_dir() / "config.json"))
    out = []
    for e in s.q("SELECT * FROM events WHERE status='ACTIVE' ORDER BY earnings_date, symbol"):
        states = s.q("SELECT model, status, signal, as_of, stale FROM model_state WHERE event_id=?", (e["id"],))
        c = L.consensus(states, cfg)
        out.append(dict(c, id=e["id"], symbol=e["symbol"], earnings_date=e["earnings_date"], session=e["session"]))
    return {"status": "OK", "rows": out, "rule": {"min_comparable_models": cfg.strong_agreement_min_models, "strong_share": cfg.strong_agreement_share, "max_spread_h": cfg.comparable_max_spread_h},
            "note": "Only VALID, fresh calls built within the spread window count. A model with no call is excluded, not counted as neutral."}


def _scored(s: Store) -> list:
    """(event, verified outcome, {model: last pre-earnings call}) for every event whose outcome is VERIFIED. Unverified or missing outcomes never enter."""
    out = []
    for e in s.q("SELECT * FROM events WHERE outcome_json IS NOT NULL AND outcome_at IS NOT NULL"):
        o = json.loads(e["outcome_json"])
        if o.get("verified") is not True or o.get("move_pct") is None:
            continue
        calls = {r["model"]: r["signal"] for r in s.q("SELECT model, signal, MAX(id) FROM predictions WHERE event_id=? AND substr(as_of,1,10) <= ? GROUP BY model", (e["id"], e["earnings_date"]))}
        out.append((e, o, calls))
    return out


def _actual(mv: float) -> str:
    return "BUY" if mv > FLAT_PCT else "SELL" if mv < -FLAT_PCT else "FLAT"


@router.get("/accuracy")
def accuracy():
    """Per model: BUY/SELL calls scored against the VERIFIED real post-earnings move (flat band +/-0.5%). Neutral / no-trade calls are not scored. A percentage is shown only from accuracy_min_sample scored calls."""
    s = _store()
    if s is None:
        return _empty()
    cfg = C.Config.load(str(data_dir() / "config.json"))
    stats = {}
    for e, o, calls in _scored(s):
        actual = _actual(o["move_pct"])
        for model, sig in calls.items():
            st = stats.setdefault(model, {"model": model, "label": LABELS.get(model, model), "decisive": 0, "correct": 0, "incorrect": 0, "flat_outcome": 0, "abstained": 0})
            if sig in ("BUY", "SELL"):
                if actual == "FLAT":
                    st["flat_outcome"] += 1
                else:
                    st["decisive"] += 1
                    st["correct" if sig == actual else "incorrect"] += 1
            else:
                st["abstained"] += 1
    rows = []
    for st in stats.values():
        enough = st["decisive"] >= cfg.accuracy_min_sample
        st["accuracy_pct"] = round(100 * st["correct"] / st["decisive"], 1) if enough else None
        st["sample_status"] = "OK" if enough else "TOO_SMALL (%d of %d scored calls needed)" % (st["decisive"], cfg.accuracy_min_sample)
        rows.append(st)
    unverified = (s.one("SELECT COUNT(*) n FROM events WHERE outcome_at IS NULL AND status IN ('ACTIVE','REPORTED')") or {}).get("n", 0)
    return {"status": "OK", "rows": sorted(rows, key=lambda r: r["model"]), "events_with_verified_outcome": len(_scored(s)), "events_unverified": unverified, "flat_band_pct": FLAT_PCT,
            "min_sample": cfg.accuracy_min_sample,
            "note": "Verified outcomes only (real prior-close to reaction-close move from daily bars). Descriptive figures with their sample size; not the frozen Smart Move grader and not a trading result."}


@router.get("/comparison")
def comparison():
    """Head-to-head on events where BOTH models made a BUY/SELL call and the verified outcome was decisive. Shown only from comparison_min_shared shared events."""
    s = _store()
    if s is None:
        return _empty()
    cfg = C.Config.load(str(data_dir() / "config.json"))
    data = []
    for e, o, calls in _scored(s):
        a = _actual(o["move_pct"])
        if a != "FLAT":
            data.append((a, {m: g for m, g in calls.items() if g in ("BUY", "SELL")}))
    models_seen = sorted({m for _, c in data for m in c})
    rows = []
    for m1, m2 in combinations(models_seen, 2):
        shared = [(a, c[m1], c[m2]) for a, c in data if m1 in c and m2 in c]
        n = len(shared)
        row = {"a": m1, "b": m2, "shared_events": n, "status": "OK" if n >= cfg.comparison_min_shared else "TOO_SMALL (%d of %d shared events needed)" % (n, cfg.comparison_min_shared)}
        if n >= cfg.comparison_min_shared:
            row.update(both_right=sum(1 for a, x, y in shared if x == a and y == a), only_a_right=sum(1 for a, x, y in shared if x == a and y != a),
                       only_b_right=sum(1 for a, x, y in shared if y == a and x != a), both_wrong=sum(1 for a, x, y in shared if x != a and y != a), agree=sum(1 for _, x, y in shared if x == y))
        rows.append(row)
    return {"status": "OK", "rows": rows, "min_shared": cfg.comparison_min_shared, "labels": LABELS,
            "note": "Models are compared only on events both called and the market answered decisively. Below the minimum sample nothing is claimed."}


@router.get("/sources")
def sources():
    """Which model sources exist, what they read, whether they hold a genuine stored prediction now, and what needs separately authorised execution. Read-only (cache and database reads)."""
    from .adapters import ProdAdapters
    return {"status": "OK", **ProdAdapters().source_report()}


@router.get("/alerts")
def alerts(limit: int = Query(100, ge=1, le=500), status: Optional[str] = Query(None, pattern="^(PENDING|DELIVERED|SUPPRESSED)$")):
    s = _store()
    if s is None:
        return _empty()
    sql, args = "SELECT id, rule, symbol, model, payload, status, detail, created_at, delivered_at FROM alerts", []
    if status:
        sql += " WHERE status=?"
        args.append(status)
    rows = s.q(sql + " ORDER BY id DESC LIMIT ?", args + [limit])
    for r in rows:
        r["payload"] = json.loads(r["payload"])
    return {"status": "OK", "rows": rows}
