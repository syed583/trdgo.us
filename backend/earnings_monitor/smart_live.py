"""Read-only view of the Smart Money Move LIVE collector for the dashboard.

It reads only files the separate collector writes (never the evidence store, never a provider):
  * live_predictions.jsonl  append-only prediction ledger (each line carries the SHA-256 of the previous one; the chain is verified on EVERY read, a broken chain means no row is shown as LIVE)
  * live_status.json        what the last pass did (mode, success, requests, events scanned / skipped, calendar age)
  * collection_runs.jsonl   the scheduled wrapper's own log (every trigger, including skips and blocks)
  * KILL                    the wrapper's kill-switch file
Nothing is scored here. A row's label is decided from facts only:
  LIVE            window still open, the ledger is intact, and the last SUCCESSFUL live scan is no older than the stale limit
  STALE           window still open but the last successful live scan is older than the stale limit (or there was none): the call may no longer reflect the evidence
  HISTORICAL      the prediction window has closed (the call is a record of what was predicted, not a current signal)
  WINDOW MISSED   the window closed and the collector never produced a prediction for the event; nothing is back-filled
  UNAVAILABLE     the ledger failed its integrity check
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

UTC, ET = timezone.utc, ZoneInfo("America/New_York")
LEDGER, STATUS, RUNS = "live_predictions.jsonl", "live_status.json", "collection_runs.jsonl"
STALE_MIN_DEFAULT = 390            # LIVE cadence (6 h, owner decision 2026-11-05) + 30 minutes of grace; a status file that states its
                                   # cadence (schedule.live_every_minutes, kit v6.4+) overrides this (cadence + GRACE_MIN)
GRACE_MIN = 30
LIVE_EVERY_FALLBACK_MIN = 360


def live_cadence_minutes(st: Optional[dict]) -> int:
    """How often a LIVE pass is scheduled, as the status file states it; the hourly DRY_RUN rewrites `every_minutes`,
    so only `live_every_minutes` counts; without it the owner-approved 6-hour cadence is assumed."""
    sch = (st or {}).get("schedule") or {}
    try:
        if sch.get("live_every_minutes"):
            return int(sch["live_every_minutes"])
        # `every_minutes` alone is NOT trusted: the installed publisher (kit <= v6.3) writes 60 even for a LIVE pass
    except (TypeError, ValueError):
        pass
    return LIVE_EVERY_FALLBACK_MIN
UNIVERSE = "universe.json"
UNIVERSE_STALE_H = 8.0             # the collector rewrites it on every pass; older than this means no pass has finished recently
MONDAY_BMO_SUBDIR = "monday_bmo_friday"       # the SEPARATE Friday-window variant: its own ledger, status, outcomes and accuracy under this folder (never mixed with the frozen ledger above)
MONDAY_BMO_ACCURACY = "accuracy.json"
MONDAY_BMO_VARIANT = "MONDAY_BMO_FRIDAY"
EVIDENCE_WARN_H = 30.0             # evidence older than this is flagged (the engine itself enforces its own freshness rules; this only warns)
TEXT = {"BUY": "BUY", "SELL": "SELL", "NO_TRADE": "NO TRADE", "NO_VALID_PREDICTION": "NO VALID PREDICTION", "PENDING_REFRESH": "PENDING FINAL REFRESH"}


def _dt(x) -> Optional[datetime]:
    if not x:
        return None
    d = datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _et(d: Optional[datetime]) -> Optional[str]:
    return d.astimezone(ET).strftime("%Y-%m-%d %H:%M %Z") if d else None


def _canon(o) -> bytes:
    return json.dumps(o, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def read_ledger(path) -> dict:
    """Same chain rule the writer applies. A last line without its newline is a write in progress and is ignored."""
    p = Path(path)
    out = {"records": [], "ok": True, "problem": None}
    if not p.exists():
        return out
    lines = p.read_text(encoding="utf-8").split("\n")
    complete = lines[:-1] if lines and lines[-1] != "" else lines
    prev, seq = "0" * 64, 0
    for i, line in enumerate(l for l in complete if l.strip()):
        try:
            rec = json.loads(line)
        except ValueError:
            return dict(out, ok=False, problem="line %d is not valid JSON" % (i + 1))
        seq += 1
        body = {k: v for k, v in rec.items() if k != "record_sha256"}
        if rec.get("seq") != seq or rec.get("prev_sha256") != prev or hashlib.sha256(_canon(body)).hexdigest() != rec.get("record_sha256"):
            return dict(out, ok=False, problem="line %d does not continue the hash chain (edited, removed or reordered)" % (i + 1))
        prev = rec["record_sha256"]
        out["records"].append(rec)
    return out


def _json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _last_runs(p: Path) -> list:
    try:
        lines = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()][-200:]
    except OSError:
        return []
    out = []
    for l in lines:
        try:
            out.append(json.loads(l))
        except ValueError:
            pass
    return out


def _label(rec: dict, now: datetime, ledger_ok: bool, scan_fresh: bool) -> str:
    if not ledger_ok:
        return "UNAVAILABLE"
    if rec["record_type"] == "WINDOW_MISSED":
        return "WINDOW MISSED"
    cutoff = _dt(rec.get("window_cutoff_utc"))
    if cutoff and now > cutoff:
        return "HISTORICAL"
    return "LIVE" if scan_fresh else "STALE"


def load(live_dir: Optional[str], now: datetime, *, kill_file: Optional[str] = None, stale_min: int = STALE_MIN_DEFAULT, snapshot_meta: Optional[dict] = None) -> dict:
    now = now if now.tzinfo else now.replace(tzinfo=UTC)
    if not live_dir:
        return {"configured": False, "now": now.isoformat(), "now_et": _et(now), "rows": [], "note": "Smart Money Move LIVE is not installed here (SMART_MOVE_LIVE_DIR is not set). "
                "Nothing on this page is a live signal.", "historical_snapshot": snapshot_meta}
    d = Path(live_dir)
    led = read_ledger(d / LEDGER)
    st = _json(d / STATUS)
    runs = _last_runs(d / RUNS)
    kill = Path(kill_file) if kill_file else d.parent / "KILL"
    killed = kill.exists()
    last_ok = _dt((st or {}).get("last_successful_scan_at"))
    every = live_cadence_minutes(st)
    stale_min = max(int(stale_min), every + GRACE_MIN)                      # never call a scan stale before its next pass is even due
    scan_age_min = round((now - last_ok).total_seconds() / 60) if last_ok else None
    scan_fresh = bool(st and last_ok and scan_age_min is not None and scan_age_min <= stale_min and not killed)
    last_run = runs[-1] if runs else None
    last_trigger = _dt(last_run["at"]) if last_run and last_run.get("at") else None
    last_scan = _dt((st or {}).get("written_at"))
    blocked = bool(last_run and last_run.get("event") in ("BLOCKED",))
    if killed:
        state, why = "STOPPED", "The kill-switch file is present: the collector will not run."
    elif not st:
        state, why = "NEVER_RAN", "No scan has been recorded yet."
    elif blocked:
        state, why = "BLOCKED", "The last trigger was blocked: %s" % (last_run.get("reason") or "unknown reason")
    elif st.get("mode") == "DRY_RUN" and not last_ok:
        state, why = "DRY_RUN", "Only dry-run scans have happened: no provider request has been sent and no prediction has been published."
    elif not scan_fresh:
        state, why = "STALE", "The last successful live scan is %s old (limit %d minutes)." % (("%d min" % scan_age_min) if scan_age_min is not None else "unknown", stale_min)
    else:
        state, why = "OK", "The last successful live scan is %d minutes old." % scan_age_min
    nxt = None
    if state in ("OK", "STALE", "DRY_RUN") and last_trigger:
        nxt = last_trigger + timedelta(minutes=every)
        while nxt <= now:
            nxt += timedelta(minutes=every)                                     # the schedule keeps its rhythm; a missed trigger is not "overdue", it is simply the next slot
    # latest record per event
    latest = {}
    for r in (led["records"] if led["ok"] else []):                                  # a ledger that failed its integrity check shows NO row at all (fail closed)
        key = (r["symbol"], r["event_date"])
        if r["record_type"] == "PREDICTION" or key not in latest:
            latest[key] = r
    rows = []
    for (sym, edate), r in latest.items():
        label = _label(r, now, led["ok"], scan_fresh)
        gen = _dt(r.get("generated_at") or r.get("noted_at"))
        ev_latest = _dt(r.get("evidence_latest"))
        ev_age = round((now - ev_latest).total_seconds() / 3600, 1) if ev_latest else None
        cutoff = _dt(r.get("window_cutoff_utc"))
        miss = [("%s: %s" % (m[0], m[1])) if isinstance(m, (list, tuple)) and len(m) == 2 else str(m) for m in (r.get("missing_factors") or [])]
        if r["record_type"] == "WINDOW_MISSED":
            miss = ["no prediction was produced before the window closed (nothing is back-filled)"]
        rows.append({"symbol": sym, "company": r.get("company"), "earnings_date": edate, "session": r.get("session"), "label": label, "signal": TEXT.get(r.get("result")) if r["record_type"] == "PREDICTION" else None,
                     "signal_code": r.get("result"), "generated_at": gen.isoformat() if gen else None, "generated_at_et": _et(gen), "evidence_latest": r.get("evidence_latest"), "evidence_latest_et": _et(ev_latest),
                     "evidence_age_h": ev_age, "evidence_warning": bool(ev_age is not None and ev_age > EVIDENCE_WARN_H), "evidence_items": len(r.get("evidence") or []),
                     "coverage": "%s/%s" % (r["coverage_points"], r["coverage_max"]) if r.get("coverage_points") is not None else None, "liquidity": r.get("liquidity"), "bull": r.get("bull"), "bear": r.get("bear"), "adv20_usd": r.get("adv20_usd"),
                     "missing": miss, "reason": r.get("reason"), "window_cutoff_utc": r.get("window_cutoff_utc"), "window_cutoff_et": _et(cutoff),
                     "hours_to_cutoff": round((cutoff - now).total_seconds() / 3600, 1) if cutoff and cutoff > now else None, "record_sha256": r["record_sha256"][:12], "mode": r.get("mode")})
    order = {"LIVE": 0, "STALE": 1, "WINDOW MISSED": 2, "HISTORICAL": 3, "UNAVAILABLE": 4}
    rows.sort(key=lambda x: (order.get(x["label"], 9), x["window_cutoff_utc"] or "", x["symbol"]))
    ev = (st or {}).get("events") or {}
    rq = (st or {}).get("requests") or {}
    lim = (st or {}).get("limits") or {}
    today_et = now.astimezone(ET).date().isoformat()
    used = rq.get("used_today") if rq.get("ny_date") == today_et else (0 if st else None)
    daily = lim.get("daily_limit")
    counts = {}
    for r in rows:
        counts[r["label"]] = counts.get(r["label"], 0) + 1
    return {"configured": True, "now": now.isoformat(), "now_et": _et(now), "state": state, "state_text": why, "kill_switch": killed,
            "ledger": {"ok": led["ok"], "problem": led["problem"], "records": len(led["records"]) if led["ok"] else None},
            "scan": {"mode": ("LIVE" if last_ok else (st or {}).get("mode")),            # the scanner's mode; the hourly DRY_RUN only rewrites the status file
                     "status_written_by": (st or {}).get("mode"), "live_every_minutes": every, "last_successful_scan_at": last_ok.isoformat() if last_ok else None, "last_successful_scan_et": _et(last_ok), "minutes_since_success": scan_age_min,
                     "last_scan_at": last_scan.isoformat() if last_scan else None, "last_scan_et": _et(last_scan), "last_scan_ok": (st or {}).get("scan_ok"), "last_run": (st or {}).get("run"),
                     "next_scan_expected_at": nxt.isoformat() if nxt else None, "next_scan_expected_et": _et(nxt), "stale_after_min": stale_min,
                     "last_trigger": {"at": last_run.get("at"), "event": last_run.get("event"), "reason": last_run.get("reason")} if last_run else None},
            "events": {"open_scanned": ev.get("open_scanned"), "published_new": ev.get("published_new"), "unchanged": ev.get("unchanged"), "upcoming": ev.get("upcoming"), "skipped": ev.get("skipped") or {},
                       "window_missed_noted": ev.get("window_missed_noted"), "unfunded_this_pass": ev.get("unfunded_this_pass")},
            "requests": {"used_today": used, "remaining_today": (max(0, daily - used) if daily is not None and used is not None else None), "daily_limit": daily, "run_limit": lim.get("run_limit"),
                         "concurrency": lim.get("concurrency"), "counted_for": rq.get("ny_date"), "as_of": (st or {}).get("written_at"), "provider": "Unusual Whales (collector only; the application's own requests are in the provider ledger)"},
            "calendar": (st or {}).get("calendar"), "counts": counts, "rows": rows, "historical_snapshot": snapshot_meta,
            "note": "Rows are the frozen score40-v1 engine's own output, recorded when each scan finished. A historical snapshot is never shown as a live signal; a missed window is never back-filled."}


def load_universe(live_dir: Optional[str], now: datetime) -> dict:
    """The per-event universe table the collector writes next to its ledger (universe.json): every earnings event in the rolling horizon with its calendar state, evidence state and what is being done about it.
    READ ONLY: this reads one derived file, sends no request, scores nothing and never edits a ledger or store. A missing, unreadable or unrecognised file is reported, never invented; an old file is flagged stale."""
    now = now if now.tzinfo else now.replace(tzinfo=UTC)
    if not live_dir:
        return {"configured": False, "available": False, "rows": [], "note": "Smart Money Move LIVE is not installed here (SMART_MOVE_LIVE_DIR is not set), so there is no event universe."}
    obj = _json(Path(live_dir) / UNIVERSE)
    if obj is None:
        return {"configured": True, "available": False, "rows": [], "note": "The collector has not written an event universe yet (no universe.json). It appears after a pass that uses the rolling-horizon option."}
    if not isinstance(obj, dict) or obj.get("schema") != "smart-move-universe-1" or not isinstance(obj.get("rows"), list):
        return {"configured": True, "available": False, "rows": [], "note": "universe.json is not in the expected format, so nothing from it is shown."}
    gen = _dt(obj.get("generated_utc"))
    age_h = round((now - gen).total_seconds() / 3600, 2) if gen else None
    keep = ("symbol", "company", "event_date", "session", "market_cap", "priority_proxy_tier", "excluded_by_proxy", "vendor_status", "prediction_eligible", "ineligible_reasons", "window", "window_start", "cutoff", "hours_to_cutoff",
            "calendar_tier", "calendar_slot", "calendar_slot_state", "calendar_change", "evidence", "collection_status", "tradable", "status_note", "date_confirmed", "deferred", "deferred_reason", "latest_result")
    rows = [{k: r.get(k) for k in keep} for r in obj["rows"] if isinstance(r, dict)]
    return {"configured": True, "available": True, "generated_utc": gen.isoformat() if gen else None, "generated_et": _et(gen), "age_h": age_h, "stale": bool(age_h is None or age_h > UNIVERSE_STALE_H),
            "stale_after_h": UNIVERSE_STALE_H, "horizon_days": obj.get("horizon_days"), "ledger_ok": obj.get("ledger_ok"), "ledger_problem": obj.get("ledger_problem"), "calendar_completeness": obj.get("calendar_completeness"),
            "summary": obj.get("summary"), "rows": rows, "note": obj.get("note")}


def load_monday_bmo(live_dir: Optional[str], now: datetime, *, stale_min: int = STALE_MIN_DEFAULT) -> dict:
    """The SEPARATE MONDAY_BMO_FRIDAY variant: predictions for Monday-before-open reports made on Friday before the US close (its own hash-chained ledger under <live>/monday_bmo_friday), its status, and its
    own accuracy file (graded after Monday's close with the existing outcome methodology). READ ONLY. Nothing here is mixed with the frozen model's ledger or accuracy; a broken chain shows no row (fail closed)."""
    now = now if now.tzinfo else now.replace(tzinfo=UTC)
    base = {"configured": bool(live_dir), "variant": MONDAY_BMO_VARIANT, "now": now.isoformat(), "now_et": _et(now), "rows": [], "accuracy": None,
            "note": "Separate variant: the frozen score40-v1 scorer, factors, thresholds and liquidity gate applied to a Friday window (Thursday close to Friday 15:30 ET) for reports released before "
                    "the open on a day with no session in between (a Monday, or the day after a market holiday). The entry is a Friday close order exposed to the weekend gap. Its results are "
                    "tracked separately from the frozen model and are never combined with it."}
    if not live_dir:
        return dict(base, available=False, note="Smart Money Move LIVE is not installed here (SMART_MOVE_LIVE_DIR is not set).")
    d = Path(live_dir) / MONDAY_BMO_SUBDIR
    led = read_ledger(d / LEDGER)
    st = _json(d / STATUS)
    acc = _json(d / MONDAY_BMO_ACCURACY)
    if not st and not (d / LEDGER).exists():
        return dict(base, available=False, note=base["note"] + " No variant pass has run yet (no monday_bmo_friday folder).")
    last_ok = _dt((st or {}).get("last_successful_scan_at"))
    scan_age_min = round((now - last_ok).total_seconds() / 60) if last_ok else None
    scan_fresh = bool(last_ok and scan_age_min is not None and scan_age_min <= stale_min)
    latest = {}
    for r in (led["records"] if led["ok"] else []):
        key = (r["symbol"], r["event_date"])
        if r["record_type"] == "PREDICTION" or key not in latest:
            latest[key] = r
    acc_rows = {(r.get("symbol"), r.get("monday_earnings_date")): r for r in ((acc or {}).get("rows") or []) if isinstance(r, dict)}
    rows = []
    for (sym, edate), r in latest.items():
        label = _label(r, now, led["ok"], scan_fresh)
        gen = _dt(r.get("generated_at") or r.get("noted_at"))
        cutoff = _dt(r.get("window_cutoff_utc"))
        miss = [("%s: %s" % (m[0], m[1])) if isinstance(m, (list, tuple)) and len(m) == 2 else str(m) for m in (r.get("missing_factors") or [])]
        if r["record_type"] == "WINDOW_MISSED":
            miss = ["no prediction was produced before the Friday deadline (nothing is back-filled)"]
        o = acc_rows.get((sym, edate)) or {}
        rows.append({"symbol": sym, "company": r.get("company"), "monday_earnings_date": edate, "session": r.get("session"), "label": label, "variant": r.get("variant") or MONDAY_BMO_VARIANT,
                     "signal": TEXT.get(r.get("result")) if r["record_type"] == "PREDICTION" else None, "signal_code": r.get("result"), "bull": r.get("bull"), "bear": r.get("bear"),
                     "coverage": "%s/%s" % (r["coverage_points"], r["coverage_max"]) if r.get("coverage_points") is not None else None, "liquidity": r.get("liquidity"), "adv20_usd": r.get("adv20_usd"),
                     "predicted_at": gen.isoformat() if gen else None, "predicted_at_et": _et(gen), "friday_entry_deadline_utc": r.get("friday_entry_deadline_utc") or r.get("window_cutoff_utc"),
                     "friday_entry_deadline_et": r.get("friday_entry_deadline_et") or _et(cutoff), "hours_to_deadline": round((cutoff - now).total_seconds() / 3600, 1) if cutoff and cutoff > now else None,
                     "base_session": r.get("base_session"), "reaction_session": r.get("reaction_session"), "evidence_latest": r.get("evidence_latest"), "evidence_items": len(r.get("evidence") or []), "missing": miss,
                     "reason": r.get("reason"), "outcome_status": o.get("outcome_status"), "move_pct": o.get("move_pct"), "gap_pct": o.get("gap_pct"), "outcome_class": o.get("outcome_class"), "hit": o.get("hit"),
                     "record_sha256": r["record_sha256"][:12], "mode": r.get("mode")})
    order = {"LIVE": 0, "STALE": 1, "WINDOW MISSED": 2, "HISTORICAL": 3, "UNAVAILABLE": 4}
    rows.sort(key=lambda x: (order.get(x["label"], 9), x["friday_entry_deadline_utc"] or "", x["symbol"]))
    counts = {}
    for r in rows:
        counts[r["label"]] = counts.get(r["label"], 0) + 1
    summ = (acc or {}).get("summary") if isinstance(acc, dict) else None
    keep = ("variant", "generated_utc", "total_predictions", "by_signal", "graded_events", "decided_calls", "buy_accuracy", "sell_accuracy", "overall_directional_accuracy", "average_post_earnings_move_pct",
            "average_abs_move_pct", "weekend_gap_risk", "hypothetical_trading_performance", "earnings_weeks", "minimum_sample_met", "claim_allowed", "claim_text", "separate_from_frozen_model")
    return dict(base, available=True, ledger={"ok": led["ok"], "problem": led["problem"], "records": len(led["records"]) if led["ok"] else None},
                scan={"mode": (st or {}).get("mode"), "last_successful_scan_at": last_ok.isoformat() if last_ok else None, "last_successful_scan_et": _et(last_ok), "minutes_since_success": scan_age_min,
                      "last_scan_at": (st or {}).get("written_at"), "last_scan_et": _et(_dt((st or {}).get("written_at"))), "last_scan_ok": (st or {}).get("scan_ok"), "last_run": (st or {}).get("run"), "stale_after_min": stale_min},
                events=(st or {}).get("events") or {}, counts=counts, rows=rows, accuracy=({k: summ.get(k) for k in keep} if isinstance(summ, dict) else None))
