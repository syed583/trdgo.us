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
from .service import SESSIONS, prediction_cutoff, today_et
from .store import Store, parse

router = APIRouter(prefix="/api/earnings-monitor", tags=["earnings-monitor"])
FLAT_PCT = 0.5            # same flat band the existing scoreboard uses for the real post-earnings move


_STORES: dict = {}


def _store() -> Optional[Store]:
    f = data_dir() / "monitor.sqlite"
    if not f.exists():
        return None
    key = str(f)
    if key not in _STORES:                                    # one Store per file: its per-thread connections are reused by every request
        _STORES[key] = Store(f)
    return _STORES[key]


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


SCOPES = ("all", "window", "outside", "withcall", "nocall")
FORMULA_MODELS = [m for m in LABELS if m != "smart_move"]
REASON_HELP = {
    "OUTSIDE_MODEL_UNIVERSE": "The eight formula models scored other stocks but not this one: it is outside their stock universe (the most liquid names). No existing cache holds a prediction for it. Coverage can grow only by widening that universe in the application and fetching the evidence for these stocks (about 61 provider requests per stock, Stage D); no scoring formula changes.",
    "CALENDAR_VENDORS_DISAGREE": "The two calendar vendors disagree about this report's date or session, so no prediction is made until they agree. A calendar refresh (Stage D) re-checks both.",
    "SESSION_NOT_CONFIRMED": "The report time (before open or after close) is not confirmed, so no model is run for it.",
    "ALL_CALLS_STALE": "Calls exist but are older than the freshness limit. The application's own warmers refresh them; nothing to do here.",
    "MODEL_PENDING": "A model that covers this stock has not produced a call yet (it needs data).",
    "MODELS_REPORT_LOW_DATA": "The models that cover this stock returned too little data to call.",
    "SOURCE_NOT_AVAILABLE": "The models' scan caches are empty (just restarted or still warming). They refill on the application's own schedule.",
    "NOT_YET_EVALUATED": "The monitor has not evaluated this event yet (it is new; the next scan does).",
}


def _why_no_call(ev: dict, states: dict) -> Optional[str]:
    """The verified reason an in-window event has no fresh formula-model call, from the stored per-model state (None when it has one). Only reasons the stored facts support are returned."""
    if ev.get("verification") == "CONFLICT":
        return "CALENDAR_VENDORS_DISAGREE"
    if ev.get("session") not in SESSIONS:
        return "SESSION_NOT_CONFIRMED"
    st = [states.get(m) for m in FORMULA_MODELS]
    if any(x and x[0] == "VALID" and not x[1] for x in st):
        return None
    if any(x and x[0] == "VALID" for x in st):
        return "ALL_CALLS_STALE"
    present = [x[0] for x in st if x]
    if not present:
        return "NOT_YET_EVALUATED"
    scored = [x for x in present if x != "ABSENT"]                                  # models whose scan exists
    if not scored:
        return "SOURCE_NOT_AVAILABLE"
    if all(x == "NOT_COVERED" for x in scored):                                      # every existing scan scored other stocks but not this one
        return "OUTSIDE_MODEL_UNIVERSE"
    if "PENDING" in scored:
        return "MODEL_PENDING"
    if "INSUFFICIENT_DATA" in scored:
        return "MODELS_REPORT_LOW_DATA"
    return "MODEL_PENDING"
    if "INSUFFICIENT_DATA" in present:
        return "MODELS_REPORT_LOW_DATA"
    if all(x in ("ABSENT", "NOT_COVERED") for x in present) and "ABSENT" in present:
        return "SOURCE_NOT_AVAILABLE"
    return "MODEL_PENDING"


def _overview(s: Store, cfg, t: datetime, include_history: bool = False) -> dict:
    """Cheap facts about EVERY tracked event inside the horizon, from a few grouped queries (never one query per event): per market date, per scope and per model. This is the one definition of 'scope' that the
    Upcoming page, the model grid, the Smart Money Move view and the verification script all share."""
    today = today_et(t)
    counts = {r["event_id"]: r for r in s.q("SELECT event_id, SUM(CASE WHEN status='VALID' THEN 1 ELSE 0 END) valid, SUM(CASE WHEN status='VALID' AND stale=0 THEN 1 ELSE 0 END) fresh FROM model_state GROUP BY event_id")}
    states: dict = {}
    for r in s.q("SELECT event_id, model, status, stale FROM model_state"):
        states.setdefault(r["event_id"], {})[r["model"]] = (r["status"], bool(r["stale"]))
    evs = []
    for e in s.q("SELECT id, symbol, earnings_date, status, session, verification FROM events" + ("" if include_history else " WHERE status='ACTIVE'") + " ORDER BY earnings_date, symbol"):
        days = (date.fromisoformat(e["earnings_date"]) - today).days
        if not include_history and days > cfg.horizon_days:
            continue
        c = counts.get(e["id"]) or {}
        win = days <= cfg.model_window_days
        evs.append({"id": e["id"], "symbol": e["symbol"], "date": e["earnings_date"], "days": days, "window": win, "fresh": int(c.get("fresh") or 0), "valid": int(c.get("valid") or 0),
                    "why": (_why_no_call(e, states.get(e["id"], {})) if win and not int(c.get("fresh") or 0) else None)})
    by_date: dict = {}
    for e in evs:
        d = by_date.setdefault(e["date"], {"date": e["date"], "events": 0, "in_window": 0, "with_fresh_call": 0})
        d["events"] += 1
        d["in_window"] += 1 if e["window"] else 0
        d["with_fresh_call"] += 1 if e["fresh"] else 0
    per_model = {r["model"]: r["n"] for r in s.q("SELECT m.model model, COUNT(*) n FROM model_state m JOIN events e ON e.id=m.event_id WHERE e.status='ACTIVE' AND m.status='VALID' AND m.stale=0 GROUP BY m.model")}
    why: dict = {}
    for e in evs:
        if e["why"]:
            w = why.setdefault(e["why"], {"events": 0, "examples": []})
            w["events"] += 1
            if len(w["examples"]) < 12:
                w["examples"].append(e["symbol"])
    summary = {"no_call_reasons": why, "reason_help": {k: REASON_HELP[k] for k in why}, "events": len(evs), "dates": len(by_date), "in_window": sum(1 for e in evs if e["window"]), "outside_window": sum(1 for e in evs if not e["window"]),
               "with_fresh_call": sum(1 for e in evs if e["fresh"]), "in_window_without_call": sum(1 for e in evs if e["window"] and not e["fresh"]), "max_days_ahead": max([e["days"] for e in evs] or [0]),
               "fresh_calls_per_model": {m: per_model.get(m, 0) for m in LABELS}}
    return {"events": evs, "by_date": sorted(by_date.values(), key=lambda x: x["date"]), "summary": summary}


def _select(ov: dict, on: Optional[str], scope: str, limit: int, offset: int) -> tuple:
    """(ids of the requested page, total after the filters). Order is market date then symbol."""
    sel = [e for e in ov["events"] if (not on or e["date"] == on) and (scope == "all" or (scope == "window" and e["window"]) or (scope == "outside" and not e["window"]) or
                                                                       (scope == "withcall" and e["fresh"]) or (scope == "nocall" and e["window"] and not e["fresh"]))]
    return [e["id"] for e in sel[offset:offset + limit]], len(sel)


def _page_meta(ov: dict, total: int, limit: int, offset: int, on: Optional[str], scope: str) -> dict:
    return {"total": total, "limit": limit, "offset": offset, "date": on, "scope": scope, "by_date": ov["by_date"], "summary": ov["summary"]}


@router.get("/upcoming")
def upcoming(include_history: bool = False, limit: int = Query(200, ge=1, le=1000), offset: int = Query(0, ge=0), on: Optional[str] = Query(None, alias="date", pattern=r"^\d{4}-\d{2}-\d{2}$"),
             scope: str = Query("all", pattern="^(all|window|outside|withcall|nocall)$")):
    """The rolling calendar, a page at a time (default 200). `by_date` and `summary` always describe ALL tracked events, whatever page is asked for."""
    s = _store()
    if s is None:
        return _empty()
    t = now()
    cfg = C.Config.load(str(data_dir() / "config.json"))
    ov = _overview(s, cfg, t, include_history)
    ids, total = _select(ov, on, scope, limit, offset)
    byid = {r["id"]: r for r in s.q("SELECT * FROM events WHERE id IN (%s)" % ",".join(str(i) for i in ids))} if ids else {}
    rows = [byid[i] for i in ids if i in byid]
    whymap = {e["id"]: e["why"] for e in ov["events"] if e["why"]}
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
        if days > cfg.model_window_days:
            reasons.append("BEYOND_%d_DAY_MODEL_WINDOW" % cfg.model_window_days)
        last = s.one("SELECT detected_at, kind FROM changes WHERE event_id=? ORDER BY id DESC LIMIT 1", (e["id"],))
        versions = s.q("SELECT earnings_date, session, observed_at, reason FROM event_versions WHERE event_id=? ORDER BY id", (e["id"],))
        valid = (s.one("SELECT COUNT(*) n FROM model_state WHERE event_id=? AND status='VALID'", (e["id"],)) or {}).get("n", 0)
        fresh_models = (s.one("SELECT COUNT(*) n FROM model_state WHERE event_id=? AND status='VALID' AND stale=0", (e["id"],)) or {}).get("n", 0)
        ev_info = s.one("SELECT COUNT(*) n, MAX(last_seen) t FROM evidence_items WHERE event_id=?", (e["id"],)) or {}
        out.append({"id": e["id"], "symbol": e["symbol"], "company": e["company"], "earnings_date": e["earnings_date"], "session": e["session"], "status": e["status"], "days_to": days,
                    "verification": e["verification"], "last_verified": e.get("last_verified"), "out_of_window": days > cfg.model_window_days,
                    "sources": json.loads(e["sources"]) if e["sources"] else [], "eligible": not reasons, "ineligible_reasons": reasons, "first_seen": e["first_seen"],
                    "last_seen": e["last_seen"], "last_changed": e["last_changed"], "latest_change": last, "versions": versions, "valid_predictions": valid, "fresh_predictions": fresh_models, "models_total": len(LABELS), "no_call_reason": whymap.get(e["id"]),
                    "evidence_items": ev_info.get("n", 0), "last_evidence_at": ev_info.get("t"), "outcome": json.loads(e["outcome_json"]) if e["outcome_json"] else None})
    return dict({"status": "OK", "now": t.isoformat(), "rows": out, "count": len(out), "horizon_days": cfg.horizon_days, "model_window_days": cfg.model_window_days}, **_page_meta(ov, total, limit, offset, on, scope))


@router.get("/models")
def models(limit: int = Query(200, ge=1, le=1000), offset: int = Query(0, ge=0), on: Optional[str] = Query(None, alias="date", pattern=r"^\d{4}-\d{2}-\d{2}$"),
           scope: str = Query("all", pattern="^(all|window|outside|withcall|nocall)$")):
    """Every model's real stored call per upcoming event with its timestamp, source, freshness, coverage and validation status. PENDING / NO VALID PREDICTION / LOW DATA are shown as such, never as a call."""
    s = _store()
    if s is None:
        return _empty()
    t = now()
    cfg = C.Config.load(str(data_dir() / "config.json"))
    ov = _overview(s, cfg, t)
    ids, total = _select(ov, on, scope, limit, offset)
    byid = {r["id"]: r for r in s.q("SELECT * FROM events WHERE id IN (%s)" % ",".join(str(i) for i in ids))} if ids else {}
    out = []
    for e in [byid[i] for i in ids if i in byid]:
        days = (date.fromisoformat(e["earnings_date"]) - today_et(t)).days
        cells = {}
        for m in s.q("SELECT * FROM model_state WHERE event_id=?", (e["id"],)):
            pred = s.one("SELECT recorded_at, as_of, source, engine_version, reason FROM predictions WHERE id=?", (m["prediction_id"],)) if m["prediction_id"] else None
            two = s.q("SELECT id, signal, score, recorded_at, reason FROM predictions WHERE event_id=? AND model=? ORDER BY id DESC LIMIT 2", (e["id"], m["model"]))
            prior = two[1] if len(two) == 2 else None
            cov = json.loads(m["coverage"]) if m["coverage"] else None
            n = (s.one("SELECT COUNT(*) n FROM predictions WHERE event_id=? AND model=?", (e["id"], m["model"])) or {}).get("n", 0)
            st = {"status": m["status"], "signal": m["signal"], "stale": bool(m["stale"])}
            cells[m["model"]] = {"status": m["status"], "label": L.display_status(st), "signal": m["signal"], "score": m["score"], "coverage": cov, "coverage_fraction": L.coverage_fraction(cov), "as_of": m["as_of"],
                                 "age_h": _age_h(m["as_of"], t), "stale": bool(m["stale"]), "source": (pred or {}).get("source") or m["source"], "engine_version": (pred or {}).get("engine_version"),
                                 "prediction_recorded_at": pred["recorded_at"] if pred else None, "predictions_recorded": n, "reason": (pred or {}).get("reason"),
                                 "previous": {"signal": prior["signal"], "score": prior["score"], "recorded_at": prior["recorded_at"]} if prior else None,
                                 "changed_since_previous": bool(prior and prior["signal"] != (two[0]["signal"])),
                                 "validation": "VALID" if m["status"] == "VALID" and not m["stale"] else ("STALE" if m["status"] == "VALID" else m["status"])}
        sm_c = cells.get("smart_move")
        if sm_c and sm_c["status"] != "VALID" and not sm_c.get("source"):                              # no call exists: the truthful label and the real reason
            why = _sm_reason(s, e, cfg, t, False)
            sm_c["label"], sm_c["reason"], sm_c["reason_code"], sm_c["origin"] = "NO_PREDICTION_AVAILABLE", why["text"], why["code"], "NOT_GENERATED"
        elif sm_c:
            sm_c["origin"] = {"smart_move_snapshot": "HISTORICAL_SNAPSHOT", "smart_move_engine": "LIVE_ENGINE"}.get(sm_c.get("source"))
        out.append({"id": e["id"], "symbol": e["symbol"], "company": e["company"], "earnings_date": e["earnings_date"], "session": e["session"], "verification": e["verification"], "days_to": days,
                    "out_of_window": days > cfg.model_window_days, "cells": cells})
    return {"status": "OK", "models": [{"id": k, "label": v} for k, v in LABELS.items()], "stale_after_h": cfg.stale_after_h, "rows": out, "count": len(out), **_page_meta(ov, total, limit, offset, on, scope),
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
        if (date.fromisoformat(e["earnings_date"]) - today_et(now())).days > cfg.model_window_days:
            continue
        states = s.q("SELECT model, status, signal, as_of, stale FROM model_state WHERE event_id=?", (e["id"],))
        c = L.consensus(states, cfg)
        out.append(dict(c, id=e["id"], symbol=e["symbol"], earnings_date=e["earnings_date"], session=e["session"]))
    return {"status": "OK", "rows": out, "rule": {"min_comparable_models": cfg.strong_agreement_min_models, "strong_share": cfg.strong_agreement_share, "max_spread_h": cfg.comparable_max_spread_h},
            "note": "Only VALID, fresh calls built within the spread window count. A model with no call is excluded, not counted as neutral."}


def _calls_before_cutoff(s: Store, cfg, e: dict) -> dict:
    """{model: (signal, recorded_at)} using only versions that were BOTH built and stored before the start of the session's reporting window: nothing after the cutoff can grade the event (no look-ahead)."""
    cut = prediction_cutoff(cfg, e)
    out: dict = {}
    for r in s.q("SELECT model, signal, as_of, recorded_at FROM predictions WHERE event_id=? ORDER BY id", (e["id"],)):
        try:
            if parse(r["as_of"]) <= cut and parse(r["recorded_at"]) <= cut:
                out[r["model"]] = (r["signal"], r["recorded_at"])
        except (TypeError, ValueError):
            continue
    return out


def _scored(s: Store, cfg=None) -> list:
    """(event, verified outcome, {model: last pre-earnings call}) for every event whose outcome is VERIFIED. Unverified or missing outcomes never enter; calls made after the reporting window opened never count."""
    cfg = cfg or C.Config.load(str(data_dir() / "config.json"))
    out = []
    for e in s.q("SELECT * FROM events WHERE outcome_json IS NOT NULL AND outcome_at IS NOT NULL"):
        o = json.loads(e["outcome_json"])
        if o.get("verified") is not True or o.get("move_pct") is None:
            continue
        out.append((e, o, {m: v[0] for m, v in _calls_before_cutoff(s, cfg, e).items()}))
    return out


def _actual(mv: float) -> str:
    return "BUY" if mv > FLAT_PCT else "SELL" if mv < -FLAT_PCT else "FLAT"


@router.get("/accuracy")
def accuracy():
    """Per model: BUY/SELL calls scored against the VERIFIED real post-earnings move (flat band +/-0.5%), with BUY and SELL accuracy separately, the average real move and the number of verified outcomes. Neutral / no-trade
    calls are not scored. A percentage is shown only from accuracy_min_sample scored calls. Events with no verified outcome, or whose outcome was seen but could not be verified, are counted, never graded."""
    s = _store()
    if s is None:
        return _empty()
    cfg = C.Config.load(str(data_dir() / "config.json"))
    stats = {}
    scored = _scored(s, cfg)
    for e, o, calls in scored:
        actual = _actual(o["move_pct"])
        for model, sig in calls.items():
            st = stats.setdefault(model, {"model": model, "label": LABELS.get(model, model), "decisive": 0, "correct": 0, "incorrect": 0, "flat_outcome": 0, "abstained": 0, "verified_outcomes": 0,
                                          "buy_calls": 0, "buy_correct": 0, "sell_calls": 0, "sell_correct": 0, "_moves": []})
            st["verified_outcomes"] += 1
            st["_moves"].append(abs(o["move_pct"]))
            if sig in ("BUY", "SELL"):
                if actual == "FLAT":
                    st["flat_outcome"] += 1
                else:
                    st["decisive"] += 1
                    st["correct" if sig == actual else "incorrect"] += 1
                    st["buy_calls" if sig == "BUY" else "sell_calls"] += 1
                    if sig == actual:
                        st["buy_correct" if sig == "BUY" else "sell_correct"] += 1
            else:
                st["abstained"] += 1
    rows = []
    for st in stats.values():
        moves = st.pop("_moves")
        enough = st["decisive"] >= cfg.accuracy_min_sample
        st["accuracy_pct"] = round(100 * st["correct"] / st["decisive"], 1) if enough else None
        st["buy_accuracy_pct"] = round(100 * st["buy_correct"] / st["buy_calls"], 1) if st["buy_calls"] >= cfg.accuracy_min_sample else None
        st["sell_accuracy_pct"] = round(100 * st["sell_correct"] / st["sell_calls"], 1) if st["sell_calls"] >= cfg.accuracy_min_sample else None
        st["avg_abs_move_pct"] = round(sum(moves) / len(moves), 2) if moves else None
        st["sample_status"] = "OK" if enough else "TOO_SMALL (%d of %d scored calls needed)" % (st["decisive"], cfg.accuracy_min_sample)
        rows.append(st)
    unverified = (s.one("SELECT COUNT(*) n FROM events WHERE outcome_at IS NULL AND status IN ('ACTIVE','REPORTED')") or {}).get("n", 0)
    unverifiable = (s.one("SELECT COUNT(DISTINCT event_id) n FROM changes WHERE kind='OUTCOME_UNVERIFIED'") or {}).get("n", 0)
    # does the models' own agreement predict the real direction? Only events whose pre-earnings calls were comparable AND decisive
    agree = {"strong_agreement_events": 0, "strong_agreement_right": 0, "disagreement_events": 0}
    for e, o, calls in scored:
        actual = _actual(o["move_pct"])
        decisive = [g for g in calls.values() if g in ("BUY", "SELL")]
        if actual == "FLAT" or len(decisive) < cfg.strong_agreement_min_models:
            continue
        top = max(("BUY", "SELL"), key=decisive.count)
        if decisive.count(top) / len(decisive) >= cfg.strong_agreement_share:
            agree["strong_agreement_events"] += 1
            agree["strong_agreement_right"] += 1 if top == actual else 0
        elif "BUY" in decisive and "SELL" in decisive:
            agree["disagreement_events"] += 1
    agree["status"] = "OK" if agree["strong_agreement_events"] >= cfg.accuracy_min_sample else "TOO_SMALL (%d of %d events needed)" % (agree["strong_agreement_events"], cfg.accuracy_min_sample)
    agree["strong_agreement_accuracy_pct"] = round(100 * agree["strong_agreement_right"] / agree["strong_agreement_events"], 1) if agree["status"] == "OK" else None
    return {"status": "OK", "rows": sorted(rows, key=lambda r: r["model"]), "events_with_verified_outcome": len(scored), "events_unverified": unverified, "events_outcome_unverifiable": unverifiable,
            "agreement_vs_outcome": agree, "flat_band_pct": FLAT_PCT, "min_sample": cfg.accuracy_min_sample,
            "note": "Verified outcomes only (real prior-close to reaction-close move from daily bars). Descriptive figures with their sample size; not the frozen Smart Move grader and not a trading result."}


@router.get("/comparison")
def comparison():
    """Head-to-head on events where BOTH models made a BUY/SELL call and the verified outcome was decisive. Shown only from comparison_min_shared shared events."""
    s = _store()
    if s is None:
        return _empty()
    cfg = C.Config.load(str(data_dir() / "config.json"))
    data = []
    for e, o, calls in _scored(s, cfg):
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


@router.get("/evidence")
def evidence(symbol: Optional[str] = Query(None, max_length=12), limit: int = Query(300, ge=1, le=2000)):
    """The evidence ledger. Each item is shown at the stage it actually reached (collected / verified), with the models whose own code reads that KIND of evidence (relevant) and the models whose latest row carries
    it (used). News and filings are stored and shown here only; they never change a score. Items from the unsupported kinds are listed in `unsupported`."""
    s = _store()
    if s is None:
        return _empty()
    sql = "SELECT i.*, e.symbol, e.earnings_date FROM evidence_items i JOIN events e ON e.id=i.event_id"
    args: list = []
    if symbol:
        sql += " WHERE e.symbol=?"
        args.append(symbol.upper())
    rows = s.q(sql + " ORDER BY i.last_seen DESC, i.id DESC LIMIT ?", args + [limit])
    for r in rows:
        r["data"] = json.loads(r["data"] or "{}")
        r["relevant_models"] = json.loads(r["relevant_models"] or "[]")
        r["used_by_models"] = json.loads(r["used_by_models"] or "[]")
    counts = {(r["kind"], r["stage"]): r["n"] for r in s.q("SELECT kind, stage, COUNT(*) n FROM evidence_items GROUP BY kind, stage")}
    from .evidence import KINDS, NOT_MODEL_INPUT
    summary = [{"kind": k, "collected": counts.get((k, "COLLECTED"), 0), "verified": counts.get((k, "VERIFIED"), 0), "feeds_a_score": k not in NOT_MODEL_INPUT} for k in KINDS]
    last = s.kv_get("evidence_scan:last")
    return {"status": "OK", "rows": rows, "summary": summary, "last_scan": last,
            "unsupported": ["Licensed analyst-revision feeds and transcripts are not connected: no item of that type exists here, and none is inferred.", "No provider request is made by this page or by the scan that fills it."],
            "note": "discovered = present in a cache the application already holds; collected = stored here; verified = a provider-structured field, not interpreted text; relevant = a model's code reads this kind; used = the model's latest row carries it."}


def _sm_reason(s: Store, ev: dict, cfg, t: datetime, had_engine_row: bool) -> dict:
    """Why there is no Smart Money Move prediction for this event, from facts the monitor holds: the event's market date, what the latest snapshot / engine read covered, and whether either exists."""
    cov = s.kv_get("smd:coverage")
    d = ev["earnings_date"]
    ws = (s.kv_get("smd:wstate") or {}).get("%s|%s" % (ev["symbol"], d))
    if ws and ws.get("state") == "NOT_YET_OPEN":
        return {"code": "NOT_YET_IN_WINDOW", "text": "The frozen rules allow a prediction only 48 to 24 hours before the report. This event's window has not opened yet (it closes at %s)." % (ws.get("cutoff") or "an upcoming time")}
    if ws:
        return {"code": "WINDOW_CLOSED_BEFORE_RUN", "text": "The prediction window closed at %s and no evidence from inside the window exists for this event, so no prediction can be made: one is never made afterwards." % (ws.get("cutoff") or "its cutoff")}
    if not cov:
        return {"code": "NO_ENGINE_OR_SNAPSHOT", "text": "No Smart Money Move engine or snapshot is installed on this server, so nothing has been generated for any event."}
    gen = cov.get("generated_at")
    gen_d = None
    try:
        from zoneinfo import ZoneInfo
        gen_d = parse(gen).astimezone(ZoneInfo("America/New_York")).date().isoformat() if gen else None
    except Exception:  # noqa: BLE001
        gen_d = None
    src = "snapshot" if cov.get("source") == "HISTORICAL_SNAPSHOT" else "engine run"
    if gen_d and d <= gen_d:
        return {"code": "WINDOW_CLOSED_BEFORE_RUN", "text": "This report was dated %s, so it was less than 24 hours away when the %s of %s (New York date) was made. The frozen rules allow a prediction only 48 to 24 hours before the report, so none was allowed, and none has been generated since." % (d, src, gen_d)}
    dates = cov.get("event_dates") or []
    if dates and d > max(dates):
        return {"code": "NOT_YET_IN_WINDOW", "text": "Dated %s, later than the dates the latest %s covered (up to %s). Predictions are made 48 to 24 hours before the report." % (d, src, max(dates))}
    return {"code": "NOT_SELECTED_OR_NOT_COLLECTED", "text": "The latest %s scored %d event(s) dated %s; this one was not among them (its evidence was not collected under the authorised run)." % (src, cov.get("rows") or 0, ", ".join(dates) or "n/a")}


@router.get("/smart-move")
def smart_move(limit: int = Query(200, ge=1, le=1000), offset: int = Query(0, ge=0), on: Optional[str] = Query(None, alias="date", pattern=r"^\d{4}-\d{2}-\d{2}$"),
               scope: str = Query("all", pattern="^(all|window|outside|withcall|nocall)$")):
    """Smart Move score40-v1 per upcoming event: the engine's own category and coverage, its freshness, and what evidence is missing. Genuine NO TRADE, PENDING and NO VALID PREDICTION stay separate, and nothing is a call unless the engine made it."""
    s = _store()
    if s is None:
        return _empty()
    t = now()
    cfg = C.Config.load(str(data_dir() / "config.json"))
    import os
    eng = bool(os.getenv("SMART_MOVE_ENGINE_ROOT") and os.getenv("SMART_MOVE_EVIDENCE_STORE"))          # environment only: this page never starts the engine
    snap = bool(os.getenv("SMART_MOVE_ENGINE20_DIR"))
    info = {"engine_installed": eng, "snapshot_dir_configured": snap, "model_version": "score40-v1",
            "how_predictions_arise": "frozen engine on a private evidence store" if eng else ("verified snapshot only" if snap else "none: not installed")}
    out = []
    counts: dict = {}
    ov = _overview(s, cfg, t)
    ids, total = _select(ov, on, scope, limit, offset)
    byid = {r["id"]: r for r in s.q("SELECT * FROM events WHERE id IN (%s)" % ",".join(str(i) for i in ids))} if ids else {}
    for e in [byid[i] for i in ids if i in byid]:
        days = (date.fromisoformat(e["earnings_date"]) - today_et(t)).days
        m = s.one("SELECT * FROM model_state WHERE event_id=? AND model='smart_move'", (e["id"],))
        st = {"status": (m or {}).get("status", "PENDING"), "signal": (m or {}).get("signal"), "stale": bool((m or {}).get("stale"))}
        label = L.display_status(st)
        reason = None
        det = s.kv_get("smd:%d" % e["id"])
        has_row = bool(m and m.get("source"))                                                       # the engine / snapshot produced a row for exactly this event
        if label not in ("BUY", "SELL", "NEUTRAL", "NO_TRADE"):                                    # no call exists for this event: say so, with the real reason
            if has_row:                                                                             # the frozen engine's own "no valid prediction": keep its result and its reason
                reason = {"code": "ENGINE_RESULT", "text": str((det or {}).get("reason") or "The frozen engine returned no valid prediction for this event.")}
            else:
                reason = _sm_reason(s, e, cfg, t, False) if days <= cfg.model_window_days else {"code": "NOT_YET_IN_WINDOW", "text": "Dated %s, %d days ahead. Predictions are made 48 to 24 hours before the report." % (e["earnings_date"], days)}
                label = "NO_PREDICTION_AVAILABLE"
        counts[label] = counts.get(label, 0) + 1
        out.append({"id": e["id"], "symbol": e["symbol"], "company": e["company"], "earnings_date": e["earnings_date"], "session": e["session"], "days_to": days, "label": label, "signal": (m or {}).get("signal"),
                    "coverage": json.loads(m["coverage"]) if m and m["coverage"] else None, "as_of": (m or {}).get("as_of"), "age_h": _age_h((m or {}).get("as_of"), t), "stale": st["stale"],
                    "source": (m or {}).get("source"), "origin": {"smart_move_snapshot": "HISTORICAL_SNAPSHOT", "smart_move_engine": "LIVE_ENGINE"}.get((m or {}).get("source")) or "NOT_GENERATED", "reason": reason, "details": det})
    return {"status": "OK", "engine": info, "coverage": s.kv_get("smd:coverage"), "windows": s.kv_get("smd:windows"), "plan": s.kv_get("smart_move:plan"), "counts": counts, "counts_scope": "this page", "rows": out, "count": len(out), **_page_meta(ov, total, limit, offset, on, scope),
            "note": "The monitor never scores Smart Move: it reads the frozen engine's own result. Collecting the evidence is a separate, attested provider run; until it has run, the honest label is PENDING or NO VALID PREDICTION."}


@router.get("/health")
def health():
    """Scanner health from the durable queue and the run log: last duration of every job kind, running/stuck/dead jobs, duplicate events or predictions, lane heartbeats and today's provider requests."""
    s = _store()
    if s is None:
        return _empty()
    t = now()
    cfg = C.Config.load(str(data_dir() / "config.json"))
    runs = {}
    for k in ("CALENDAR_REFRESH", "ELIGIBILITY_CHECK", "EVIDENCE_SCAN", "NEWS_FLOW_CHECK", "EARNINGS_DAY_CHECK", "OUTCOME_VERIFY", "SMART_MOVE_PLAN", "ALERT_DISPATCH"):
        r = s.last_run(k, ok_only=False)
        if r:
            dur = (parse(r["finished_at"]) - parse(r["started_at"])).total_seconds() if r.get("finished_at") else None
            meta = (json.loads(r["summary"] or "{}") or {}).get("_run") or {}
            runs[k] = {"started_at": r["started_at"], "finished_at": r["finished_at"], "ok": bool(r["ok"]) if r.get("finished_at") else None, "duration_s": None if dur is None else round(dur, 2),
                       "duration_ms": meta.get("duration_ms"), "queue_wait_ms": meta.get("queue_wait_ms"), "events_processed": meta.get("events_processed")}
    recent = []
    for r in s.q("SELECT kind, started_at, finished_at, ok, summary FROM scan_runs ORDER BY id DESC LIMIT 30"):
        meta = (json.loads(r["summary"] or "{}") or {}).get("_run") or {}
        recent.append({"kind": r["kind"], "started_at": r["started_at"], "finished_at": r["finished_at"], "ok": None if r["finished_at"] is None else bool(r["ok"]),
                       "interrupted": r["finished_at"] is None and parse(r["started_at"]) < t - __import__("datetime").timedelta(seconds=cfg.lease_s),
                       "duration_ms": meta.get("duration_ms"), "queue_wait_ms": meta.get("queue_wait_ms"), "events_processed": meta.get("events_processed")})
    stuck = s.q("SELECT id, kind, locked_by, lease_until FROM jobs WHERE status='RUNNING' AND lease_until < ?", (t.isoformat(),))
    dead = s.q("SELECT id, kind, last_error FROM jobs WHERE status='DEAD' ORDER BY id DESC LIMIT 20")
    dup_events = s.q("SELECT symbol, COUNT(*) n FROM events WHERE status='ACTIVE' GROUP BY symbol HAVING n > 1")
    dup_preds = (s.one("SELECT COUNT(*) n FROM (SELECT input_hash h, LAG(input_hash) OVER (PARTITION BY event_id, model ORDER BY id) p FROM predictions) WHERE h = p") or {}).get("n", 0)      # consecutive repeats only: BUY, SELL, BUY is three genuine versions
    from .gate import day_key
    hb, hbl = s.kv_get("heartbeat"), s.kv_get("heartbeat_light")
    rep = status_report(s, cfg, t)
    exp = (rep.get("approval") or {}).get("expires_at")
    usage = s.q("SELECT day, SUM(requests) requests, SUM(failures) failures FROM usage GROUP BY day ORDER BY day DESC LIMIT 7")
    return {"status": "OK", "now": t.isoformat(), "approval_expires_at": exp, "approval_days_left": None if not exp else round((parse(exp) - t).total_seconds() / 86400, 1), "usage_by_day": usage,
            "refresh_plan": s.kv_get("refresh:plan"), "scheduling": rep["scheduling"], "queue": rep["queue"], "last_runs": runs, "recent_runs": recent, "stuck_jobs": stuck, "dead_jobs": dead,
            "duplicate_active_events": dup_events, "duplicate_prediction_rows": dup_preds, "heartbeat": {"scans": hb, "light": hbl, "scans_age_s": None if not hb else round((t - parse(hb)).total_seconds())},
            "provider_requests_today": rep["provider_requests_today"], "pending_alerts": rep["pending_alerts"], "breakers": rep["breakers"], "evidence_scan": s.kv_get("evidence_scan:last"), "news_flow": s.kv_get("news_flow:last"), "refresh_skipped": s.kv_get("refresh:skipped"),
            "healthy": not stuck and not dup_events and not dup_preds and not dead}


@router.get("/cache-probe")
def cache_probe():
    """Read-only compatibility check of the live caches the monitor reads: field names and types only, no values, no provider request. Run it once after a deployment to confirm the readers match production data."""
    from .adapters import ProdAdapters
    return {"status": "OK", "read_only": True, **ProdAdapters().cache_probe()}


@router.get("/outcomes")
def outcomes(limit: int = Query(200, ge=1, le=1000)):
    """Verified post-earnings reactions, each matched to the calls that existed BEFORE the session's reporting window opened (the cutoff is shown), and the events still waiting for a verified outcome."""
    s = _store()
    if s is None:
        return _empty()
    t = now()
    cfg = C.Config.load(str(data_dir() / "config.json"))
    rows = []
    for e in s.q("SELECT * FROM events WHERE outcome_json IS NOT NULL ORDER BY outcome_at DESC LIMIT ?", (limit,)):
        o = json.loads(e["outcome_json"])
        calls = _calls_before_cutoff(s, cfg, e)
        actual = _actual(o["move_pct"]) if o.get("verified") is True and o.get("move_pct") is not None else None
        results = {}
        for m, (sig, rec) in calls.items():
            results[m] = "NOT_GRADED" if actual is None else "FLAT_REACTION" if actual == "FLAT" else ("NO_DIRECTIONAL_CALL" if sig not in ("BUY", "SELL") else ("CORRECT" if sig == actual else "INCORRECT"))
        rows.append({"symbol": e["symbol"], "earnings_date": e["earnings_date"], "session": e["session"], "move_pct": o.get("move_pct"), "verified": o.get("verified") is True, "source": o.get("source"),
                     "prediction_cutoff": prediction_cutoff(cfg, e).isoformat(), "calls": {m: {"signal": v[0], "recorded_at": v[1]} for m, v in calls.items()}, "results": results})
    waiting = s.q("SELECT symbol, earnings_date, session FROM events WHERE status IN ('ACTIVE','REPORTED') AND outcome_at IS NULL AND earnings_date < ? ORDER BY earnings_date", (today_et(t).isoformat(),))
    unverifiable = s.q("SELECT DISTINCT symbol, detected_at, new_value FROM changes WHERE kind='OUTCOME_UNVERIFIED' ORDER BY id DESC LIMIT 50")
    return {"status": "OK", "rows": rows, "waiting_for_outcome": waiting, "seen_but_unverifiable": unverifiable, "flat_band_pct": FLAT_PCT,
            "note": "Calls made after the reporting window opened are excluded, so a late prediction can never grade itself. Unverified moves are listed, never scored."}
