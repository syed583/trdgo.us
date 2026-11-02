"""Per-event data-completeness and lifecycle status for the Smart Move reporting API. Built from SAVED records only (no provider, no write to a store).

For every upcoming event: calendar freshness, BMO/AMC/UNKNOWN, news (INITIAL / FINAL), analyst Snapshot A and B, options flow, price and sector evidence, directional coverage, missing reasons,
prediction status and outcome status. Missing is never neutral: every absent item carries its own state (NOT_ATTEMPTED, FAILED, EMPTY, OK ...).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

import smart_move_collection_plan as CP

UTC = timezone.utc
STATUS_FILE = "event_status.json"


def _t(x) -> datetime:
    return CP._t(x)


def _state(recs: list, lo, hi) -> str:
    """State of one collection stage from the saved records retrieved inside [lo, hi]."""
    inside = [r for r in recs if lo <= _t(r["retrieved_at"]) <= hi]
    if any(r.get("status") == "OK" and r.get("rows", 1) > 0 for r in inside):
        return "OK"
    if any(r.get("status") == "OK" for r in inside):
        return "EMPTY"
    if inside:
        return "FAILED"
    return "NOT_ATTEMPTED"


def event_rows(events: Iterable[dict], records: Iterable[dict], live_evidence: Iterable[dict], preds: list, outcome_state: dict, calendar_age_h: Optional[float], now) -> list:
    """events: [{symbol, event_date, kind, plan{window_start_utc, cutoff_utc, assumed_release_utc}}]. records: v2_evidence rows. live_evidence: V1 live evidence rows (ohlc ...).
    preds: saved prediction records (latest version per event is used). outcome_state: {(symbol, date): {"state": ...}} from smart_move_outcomes.readiness."""
    now = _t(now)
    by_kind = {}
    for r in records:
        by_kind.setdefault((r["kind"], r["symbol"]), []).append(r)
    live_by = {}
    for r in live_evidence:
        live_by.setdefault((r["kind"], r["symbol"]), []).append(r)
    latest = {}
    for p in preds:
        k = (p["symbol"], str(p["earnings_date"])[:10])
        if k not in latest or p.get("version", 1) >= latest[k].get("version", 1):
            latest[k] = p
    cal = ("UNKNOWN" if calendar_age_h is None else "STALE" if calendar_age_h > CP_FRESH_H else "FRESH")
    rows = []
    for e in events:
        sym, ed, pl = e["symbol"], str(e["event_date"])[:10], e["plan"]
        ws, co, rel = _t(pl["window_start_utc"]), _t(pl["cutoff_utc"]), _t(pl["assumed_release_utc"])
        news = by_kind.get(("uw_news", sym), [])
        est = by_kind.get(("uw_estimates", sym), [])
        a_lo, a_hi = rel - timedelta(days=CP.SNAP_A_TO_DAYS + CP.SNAP_A_LOOKBACK_DAYS), rel - timedelta(days=CP.SNAP_A_TO_DAYS)
        flow = by_kind.get(("uw_flow_alerts", sym), [])
        pr = latest.get((sym, ed))
        eng = (pr or {}).get("engine", {}).get("continuation", {}) if pr else {}
        missing = []
        stages = {"news_initial": _state(news, ws, co - timedelta(hours=CP.FINAL_PASS_START_H)), "news_final": _state(news, co - timedelta(hours=CP.FINAL_PASS_START_H), co),
                  "analyst_snapshot_a": _state(est, a_lo, a_hi), "analyst_snapshot_b": _state(est, ws, co), "options_flow": _state(flow, ws - timedelta(days=7), co)}
        price = "OK" if live_by.get(("ohlc", sym)) else "NOT_COLLECTED"
        for k, v in stages.items():
            if v != "OK":
                missing.append("%s:%s" % (k, v))
        if price != "OK":
            missing.append("price:" + price)
        if cal != "FRESH":
            missing.append("calendar:" + cal)
        if e.get("kind") not in ("BMO", "AMC"):
            missing.append("timing:UNKNOWN")
        sig = eng.get("signal")
        avail = eng.get("available")
        status = ("NO_PREDICTION_YET" if pr is None else "DATA_INCOMPLETE" if sig == "NO_TRADE" and ("INSUFFICIENT_COVERAGE" in (eng.get("reason_codes") or []) or (pr or {}).get("blocked_by")) else sig or "NO_PREDICTION_YET")
        rows.append({"symbol": sym, "earnings_date": ed, "kind": e.get("kind") if e.get("kind") in ("BMO", "AMC") else "UNKNOWN", "window_start_utc": ws.isoformat(), "cutoff_utc": co.isoformat(),
                     "window_open_now": ws <= now <= co, "calendar": {"state": cal, "age_h": calendar_age_h}, **stages, "price_sector_evidence": price,
                     "directional_coverage": {"available_points": avail, "max_points": eng.get("max"), "fraction": round(avail / eng["max"], 3) if isinstance(avail, (int, float)) and eng.get("max") else None},
                     "missing_reasons": missing + list(eng.get("reason_codes") or []) + list((pr or {}).get("blocked_by") or []), "prediction_status": status,
                     "outcome_status": (outcome_state.get((sym, ed)) or {}).get("state", "NOT_DUE")})
    return rows


CP_FRESH_H = 6.0


def summarize(rows: list) -> dict:
    n = len(rows)
    cnt = lambda k, v: sum(1 for r in rows if r[k] == v)  # noqa: E731
    return {"events": n, "window_open_now": sum(1 for r in rows if r["window_open_now"]), "news_initial_ok": cnt("news_initial", "OK"), "news_final_ok": cnt("news_final", "OK"),
            "snapshot_a_ok": cnt("analyst_snapshot_a", "OK"), "snapshot_b_ok": cnt("analyst_snapshot_b", "OK"), "options_flow_ok": cnt("options_flow", "OK"),
            "prediction_status": {s: cnt("prediction_status", s) for s in sorted({r["prediction_status"] for r in rows})}, "outcome_status": {s: cnt("outcome_status", s) for s in sorted({r["outcome_status"] for r in rows})}}


def write(path, payload: dict) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
    tmp.replace(p)
    return p
