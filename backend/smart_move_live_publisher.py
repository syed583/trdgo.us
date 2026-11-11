"""Smart Money Move LIVE publisher: turns the frozen engine's own output, at the moment a collection pass finishes, into an append-only prediction ledger and a status file that the dashboard reads.

What it does NOT do (by construction):
  * it never scores anything itself: every signal comes from smart_move_engine20.classify() on rows produced by the frozen LiveScorer (score40-v1); no weight, threshold or rule is touched here;
  * it never writes a prediction for an event whose prediction window is not OPEN at the moment of writing (a closed window gets a WINDOW_MISSED marker, never a retroactive prediction);
  * it never back-dates: `generated_at` is the clock of the pass; evidence stamped after that instant aborts the row (look-ahead);
  * it never rewrites or deletes: the ledger is JSON lines, each carrying the SHA-256 of the previous line, so an edit or a removed line is detected by the reader;
  * a DRY RUN pass writes the status file (so the dashboard shows it) but no prediction.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import smart_move_engine20 as E20

UTC, ET = timezone.utc, ZoneInfo("America/New_York")
SCHEMA = "smart_move.live_prediction.v1"
STATUS_SCHEMA = "smart_move.live_status.v1"
LEDGER, STATUS = "live_predictions.jsonl", "live_status.json"
CATEGORY = {"BUY": "BUY", "SELL": "SELL", "NO TRADE (genuine)": "NO_TRADE", "NO VALID PREDICTION": "NO_VALID_PREDICTION", "PENDING FINAL REFRESH": "PENDING_REFRESH"}
MISSED_LOOKBACK = timedelta(days=10)
SCHEDULE_MINUTES = {"LIVE": 360, "DRY_RUN": 60}          # owner decision 2026-11-05: LIVE every 6 h; the hourly task is DRY_RUN (status only)
FUTURE_TOLERANCE = timedelta(seconds=5)


class LedgerError(Exception):
    """The ledger's hash chain is broken or unreadable: nothing is appended and the dashboard must not show LIVE."""


def _dt(x) -> datetime:
    d = x if isinstance(x, datetime) else datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _canon(o) -> bytes:
    return json.dumps(o, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def record_hash(rec: dict) -> str:
    return hashlib.sha256(_canon({k: v for k, v in rec.items() if k != "record_sha256"})).hexdigest()


def read_ledger(path) -> dict:
    """{records, ok, problem}. A trailing line that is still being written is ignored; any other damage (bad JSON, wrong previous hash, wrong own hash, sequence gap) makes ok False."""
    p = Path(path)
    out = {"records": [], "ok": True, "problem": None}
    if not p.exists():
        return out
    lines = p.read_text(encoding="utf-8").split("\n")
    complete = lines[:-1] if lines and lines[-1] != "" else lines                    # a last line without its newline is a write in progress
    prev, seq = "0" * 64, 0
    for i, line in enumerate(l for l in complete if l.strip()):
        try:
            rec = json.loads(line)
        except ValueError:
            return dict(out, ok=False, problem="line %d is not valid JSON" % (i + 1))
        seq += 1
        if rec.get("seq") != seq or rec.get("prev_sha256") != prev or record_hash(rec) != rec.get("record_sha256"):
            return dict(out, ok=False, problem="line %d does not continue the hash chain (edited, removed or reordered)" % (i + 1))
        prev = rec["record_sha256"]
        out["records"].append(rec)
    return out


def _append(path: Path, rec: dict, prev: str, seq: int) -> dict:
    rec = dict(rec, seq=seq, prev_sha256=prev)
    rec["record_sha256"] = record_hash(rec)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return rec


def _evidence(row: dict) -> list:
    items = []
    for x in row.get("evidence") or []:
        if x.get("retrieved_at"):
            items.append({"kind": x.get("kind"), "symbol": x.get("symbol"), "retrieved_at": _dt(x["retrieved_at"]).isoformat(), "sha256": x.get("raw_sha256") or x.get("sha256")})
    return sorted(items, key=lambda i: (i["retrieved_at"], str(i["kind"]), str(i["symbol"])))


def _requests_today(store, now: datetime) -> Optional[int]:
    try:
        day = now.astimezone(ET).date()
        return sum(1 for r in store.read("requests") if r.get("at") and _dt(r["at"]).astimezone(ET).date() == day)
    except Exception:  # noqa: BLE001 - the count is informational; a store problem must not stop publishing
        return None


def _atomic_json(path: Path, obj) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True, default=str), encoding="utf-8")
    os.replace(tmp, path)


def publish_rows(rows: list, now, out_dir, *, scan_id: str, mode: str, scan_ok: bool, run: Optional[dict] = None, limits: Optional[dict] = None, requests_today: Optional[int] = None,
                 calendar_age_h: Optional[float] = None, unfunded: int = 0) -> dict:
    """Publish one pass. `rows` are LiveScorer.score_all rows; `mode` is LIVE or DRY_RUN."""
    now = _dt(now)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    led = read_ledger(out / LEDGER)
    if not led["ok"]:
        raise LedgerError(led["problem"])
    recs = led["records"]
    prev = recs[-1]["record_sha256"] if recs else "0" * 64
    seq = len(recs)
    latest = {(r["symbol"], r["event_date"]): r for r in recs}                         # the last record per event decides what "unchanged" means
    seen_pred = {(r["symbol"], r["event_date"]) for r in recs if r["record_type"] == "PREDICTION"}
    seen_missed = {(r["symbol"], r["event_date"]) for r in recs if r["record_type"] == "WINDOW_MISSED"}
    c, skipped, max_date = Counter(), Counter(), None
    for r in rows:
        if r.get("earnings_date"):
            max_date = max(max_date or r["earnings_date"], r["earnings_date"])
        if r.get("kind") not in ("BMO", "AMC"):
            cal = r.get("calendar") or {}
            if cal.get("status_now") == "STALE" or "ONLY_STALE_VENDOR_ROWS" in (cal.get("reasons") or []):
                skipped["calendar observation stale"] += 1              # the vendors did label the session; the observation is just too old
            else:
                skipped["announcement session unknown"] += 1
            continue
        if r.get("blocked_by"):
            skipped["calendar not confirmed by two vendors"] += 1
            continue
        cl = E20.classify(r, now)
        key = (cl["symbol"], cl["event_date"])
        window = cl["window"]
        if window == "NOT_YET_OPEN":
            c["upcoming"] += 1
            continue
        if window == "CLOSED":
            c["closed"] += 1
            cutoff = _dt(cl["window_cutoff_utc"])
            only_pending = key in seen_pred and (latest.get(key) or {}).get("record_type") == "PREDICTION" and (latest.get(key) or {}).get("result") == "PENDING_REFRESH"
            if mode == "LIVE" and (key not in seen_pred or only_pending) and key not in seen_missed and now - cutoff <= MISSED_LOOKBACK:
                rec = _append(out / LEDGER, {"schema": SCHEMA, "record_type": "WINDOW_MISSED", "symbol": cl["symbol"], "company": cl.get("company"), "event_date": cl["event_date"], "session": cl["session"],
                                             "window_cutoff_utc": cutoff.isoformat(), "noted_at": now.isoformat(), "predicted": False, "scan_id": scan_id, "model_version": cl["model_version"],
                                             "reason": "ONLY_PENDING_REFRESH_BEFORE_CLOSE" if only_pending else "NO_PREDICTION_BEFORE_THE_WINDOW_CLOSED", "evidence_items_at_close": len(_evidence(r))}, prev, seq + 1)
                prev, seq = rec["record_sha256"], seq + 1
                seen_missed.add(key)
                c["window_missed_noted"] += 1
            continue
        c["open"] += 1                                                    # window OPEN: this event is being scanned
        if mode != "LIVE":
            continue
        ev = _evidence(r)
        if any(_dt(i["retrieved_at"]) > now + FUTURE_TOLERANCE for i in ev):
            skipped["evidence stamped after the scan instant (look-ahead refused)"] += 1
            continue
        if now > _dt(cl["window_cutoff_utc"]):
            skipped["window closed at publication time"] += 1                # cannot happen after classify(), kept as a hard guard
            continue
        digest = hashlib.sha256(_canon([[i["kind"], i["symbol"], i["retrieved_at"], i["sha256"]] for i in ev] + [cl["result"]])).hexdigest()
        last = latest.get(key)
        if last is not None and last["record_type"] == "PREDICTION" and last["evidence_digest"] == digest:
            c["unchanged"] += 1
            continue
        rec = _append(out / LEDGER, {"schema": SCHEMA, "record_type": "PREDICTION", "symbol": cl["symbol"], "company": cl.get("company"), "event_date": cl["event_date"], "session": cl["session"],
                                     "result": CATEGORY[cl["result"]], "result_text": cl["result"], "reason": cl["reason"], "reason_codes": cl["reason_codes"], "bull": cl["bull"], "bear": cl["bear"],
                                     "coverage_points": cl["coverage_points"], "coverage_max": cl["coverage_max"], "liquidity": cl["liquidity"], "adv20_usd": cl.get("adv20_usd"),
                                     "contrarian_signal": cl.get("contrarian_signal"), "missing_factors": cl["missing_factors"], "window": "OPEN", "window_cutoff_utc": cl["window_cutoff_utc"],
                                     "evidence": ev, "evidence_latest": ev[-1]["retrieved_at"] if ev else None, "evidence_digest": digest, "generated_at": now.isoformat(),
                                     "generated_at_et": now.astimezone(ET).strftime("%Y-%m-%d %H:%M:%S %Z"), "scan_id": scan_id, "model_version": cl["model_version"], "mode": mode}, prev, seq + 1)
        prev, seq = rec["record_sha256"], seq + 1
        latest[key] = rec
        c["published"] += 1
        c["published_" + rec["result"]] += 1
    old = {}
    sp = out / STATUS
    if sp.exists():
        try:
            old = json.loads(sp.read_text(encoding="utf-8"))
        except ValueError:
            old = {}
    last_ok = now.isoformat() if (scan_ok and mode == "LIVE") else old.get("last_successful_scan_at")
    limits = limits or {}
    used = requests_today
    status = {"schema": STATUS_SCHEMA, "written_at": now.isoformat(), "written_at_et": now.astimezone(ET).strftime("%Y-%m-%d %H:%M:%S %Z"), "scan_id": scan_id, "mode": mode, "scan_ok": bool(scan_ok),
              "last_successful_scan_at": last_ok, "run": run or {}, "limits": {"run_limit": limits.get("run_limit"), "daily_limit": limits.get("daily_limit"), "concurrency": 1},
              "requests": {"used_today": used, "remaining_today": (max(0, limits["daily_limit"] - used) if used is not None and limits.get("daily_limit") is not None else None),
                           "ny_date": now.astimezone(ET).date().isoformat()},
              "events": {"open_scanned": c["open"], "published_new": c["published"], "unchanged": c["unchanged"], "upcoming": c["upcoming"], "window_closed": c["closed"], "window_missed_noted": c["window_missed_noted"],
                         "unfunded_this_pass": unfunded, "skipped": dict(skipped), "published_by_result": {k[10:]: v for k, v in c.items() if k.startswith("published_") and k != "published"}},
              "calendar": {"age_h": calendar_age_h, "through": max_date}, "schedule": {"every_minutes": SCHEDULE_MINUTES.get(mode, 60), "live_every_minutes": SCHEDULE_MINUTES["LIVE"], "dry_run_every_minutes": SCHEDULE_MINUTES["DRY_RUN"]}, "ledger": {"records": seq, "head_sha256": prev}}
    _atomic_json(sp, status)
    return status


def publish(svc, now, out_dir, *, scan_id: str, mode: str, scan_ok: bool, run: Optional[dict] = None, limits: Optional[dict] = None, unfunded: int = 0) -> dict:
    """Score every event of the store with the FROZEN LiveScorer at `now`, then publish. Reads the store; sends nothing."""
    import smart_move_calendar_guard as G
    import smart_move_live as L
    rows = L.LiveScorer(svc).score_all(_dt(now))
    return publish_rows(rows, now, out_dir, scan_id=scan_id, mode=mode, scan_ok=scan_ok, run=run, limits=limits, requests_today=_requests_today(svc.store, _dt(now)),
                        calendar_age_h=G.calendar_age_h(svc.store, _dt(now)), unfunded=unfunded)
