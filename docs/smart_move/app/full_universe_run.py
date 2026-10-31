"""ONE-TIME bounded FULL-UNIVERSE collection for Smart Move (owner-approved 2026-11-01): every eligible event whose capture window is open, in batches.

    python full_universe_run.py --out-dir <NEW folder> --seed-store <earlier run store> --seed-report <earlier audit_report.json> [--plan-only] [--cap N] [--batch-size N] [--resume]

* The frozen 40-point engine, capture-window and evidence rules are used unchanged (LiveScorer / RefreshService); this file only batches, accounts and checkpoints.
* Reuses the earlier run's saved calendar and evidence (no duplicate request): stocks already scored there are copied into this report with their original capture time and are not requested again;
  fresh SPY / sector-ETF candles in the seeded store are reused by the planner (a request is made only for what is missing or stale).
* Never writes into the seed folders. The output folder must be new (or --resume continues THIS run from its checkpoint).
* Kill switch: engaged outside a batch; released only inside a batch and engaged again after it (also on any error). A 401/403/429 or any stop/denial ends the run at once.
* After every batch a partial audit_report.json is written (atomically) so the read-only Earnings Board shows each completed stock.
* --plan-only sends nothing and prints the exact stock count and request count.
"""
import argparse
import json
import os
import shutil
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(HERE))
ENV = Path(r"C:\Users\initi\Downloads\US-STOCKS\backend\.env")
UTC = timezone.utc
TASK = "SmartMove_FullUniverse_20261101"
STOP_PREFIXES = ("STOP", "DENIED_NOT_AUTHORIZED", "DENIED_STOPPED", "DENIED_DAILY_CAP", "DENIED_HOURLY_SHARED_CAP")


def result_row(r, svc, L):
    return {"symbol": r["symbol"], "company": r["company"], "earnings_date": r["earnings_date"], "kind": r["kind"], "calendar_status": r["calendar"]["status_now"],
            "available_points": r["available_points"], "missing_factors": [m["factor"] + ":" + m["reason"] for m in r["missing_factors"]], "blocked_by": r["blocked_by"],
            "continuation": {k: r["engine"]["continuation"][k] for k in ("signal", "bull", "bear", "available", "reason_codes")},
            "contrarian": {k: r["engine"]["contrarian"][k] for k in ("signal", "bull", "bear", "available", "reason_codes")}, "evidence": r["evidence"],
            "capture_ts": r.get("capture_ts"),
            "hours_to_cutoff": round((datetime.fromisoformat(r["plan"]["cutoff_utc"]) - svc.clock.now()).total_seconds() / 3600, 1) if r.get("plan") else None}


def write_atomic(path: Path, text: str):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def markdown(rep):
    L = ["# Smart Move one-time FULL-UNIVERSE collection: audit report", "", "Task: %s | started %s | updated %s | status **%s**" % (rep["task"], rep.get("started_utc"), rep.get("updated_utc"), rep["status"]), "",
         "- requests this run: **%s** (cap %s; by provider %s); earlier run reused: %s names (52 requests, not repeated)" % (rep.get("attempts_total"), rep.get("cap_total"), json.dumps(rep.get("requests_by_provider")), rep.get("reused_from_earlier_run")),
         "- batches done: %s of %s; stop events: %s; errors: %s" % (rep.get("batches_done"), rep.get("batches_planned"), json.dumps(rep.get("stop_events")), json.dumps(rep.get("errors"))),
         "- final kill switch: %s" % json.dumps(rep.get("kill_switch_final")), "", "Stocks in the universe: %s; evaluated (earlier run + this run): %s; not evaluated: %s" % (rep.get("universe"), len(rep.get("results") or []), len(rep.get("not_evaluated") or [])), ""]
    L += ["| Symbol | Kind | Points /40 | Continuation | Contrarian | Missing | Captured (UTC) |", "|---|---|---|---|---|---|---|"]
    for r in rep.get("results") or []:
        c, k = r["continuation"], r["contrarian"]
        L.append("| %s | %s | %s | %s (%s/%s) | %s (%s/%s) | %s | %s |" % (r["symbol"], r["kind"], r["available_points"], c["signal"], c["bull"], c["bear"], k["signal"], k["bull"], k["bear"], ", ".join(r["missing_factors"]) or "none", r["capture_ts"]))
    if rep.get("not_evaluated"):
        L += ["", "## Not evaluated", ""] + ["- %s: %s" % (n["symbol"], n["reason"]) for n in rep["not_evaluated"]]
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--seed-store", required=True)
    ap.add_argument("--seed-report", required=True)
    ap.add_argument("--cap", type=int, default=700)
    ap.add_argument("--batch-size", type=int, default=20)
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--lift-hourly-cap", action="store_true", help="owner-approved for THIS run only: lifts our own shared hourly cap; the overall --cap, 2 s pacing and the stop on 401/429 stay")
    ap.add_argument("--resume", action="store_true")
    a = ap.parse_args()
    out, seed_store, seed_rep = Path(a.out_dir), Path(a.seed_store), json.loads(Path(a.seed_report).read_text(encoding="utf-8"))
    store = out / "store"
    ckpt_path = out / "checkpoint.json"
    if out.exists() and not a.resume:
        print("STOPPED: output folder exists; a run never reuses or overwrites a folder (use --resume only to continue this same run)")
        sys.exit(3)
    if a.resume and not ckpt_path.exists():
        print("STOPPED: nothing to resume")
        sys.exit(3)
    out.mkdir(parents=True, exist_ok=True)
    if not a.resume:
        store.mkdir(parents=True)
        for name in ("cal_obs.jsonl", "evidence.jsonl"):                       # copies; the seed folder is never written
            shutil.copy2(seed_store / name, store / name)
        shutil.copytree(seed_store / "payloads", store / "payloads")
        (out / "STARTED.flag").write_text(datetime.now(UTC).isoformat() + " pid=%d plan_only=%s\n" % (os.getpid(), a.plan_only), encoding="utf-8")
    log = open(out / "run.log", "a", encoding="utf-8")
    sys.stdout = sys.stderr = log
    print("start", datetime.now(UTC).isoformat(), "PLAN ONLY" if a.plan_only else "REAL RUN", flush=True)
    from dotenv import load_dotenv
    load_dotenv(ENV)
    import smart_move_live as L
    import unusualwhales_service as uw
    import finnhub_service as fh
    import finviz_service as fz
    creds = {"UNUSUAL_WHALES": uw.configured(), "FINNHUB": bool(fh._key()), "FINVIZ": fz.configured()}
    print("credentials present (values never shown):", creds)
    cfg = L.LiveConfig(mode="LIVE", store_dir=store, refresh_enabled=False, authorized_daily=0, spacing_s=2.0)
    svc = L.RefreshService(cfg, None if a.plan_only else L.InfraFetcher(), L.SystemClock())
    if not svc.store.kill_file.exists():
        svc.engage_kill_switch("full-universe run: engaged outside batches")
    now = svc.clock.now()
    reused = {r["symbol"]: r for r in seed_rep.get("results") or []}
    events = svc.eligible_events(now)                                           # vendor-confirmed, window open now
    universe = len(events)
    todo = [e for e in events if e["symbol"] not in reused]
    todo.sort(key=lambda e: (e["plan"]["cutoff_utc"], -e["marketcap"], e["symbol"]))          # windows that close soonest first, then the largest market cap
    prior = {r["symbol"] for r in (json.loads((out / "rows.json").read_text(encoding="utf-8")) if (out / "rows.json").exists() else [])}   # published by an earlier part of THIS run
    left_todo = [e for e in todo if e["symbol"] not in prior]
    batches = [left_todo[i:i + a.batch_size] for i in range(0, len(left_todo), a.batch_size)]
    # exact plan: what the planner would request for these names, given the seeded evidence (no request is made here)
    svc.focus = {e["symbol"] for e in left_todo}
    plan = svc.plan_evidence(now)
    by_kind = {}
    for t in plan:
        by_kind[t["kind"]] = by_kind.get(t["kind"], 0) + 1
    calendar_age_h = None
    obs = svc.store.read("cal_obs")
    if obs:
        calendar_age_h = round((now - max(datetime.fromisoformat(o["observed_at"]) for o in obs)).total_seconds() / 3600, 2)
    summary = {"now_utc": now.isoformat(), "universe_open_window_events": universe, "already_evaluated_reused": len(reused), "remaining": len(left_todo), "published_earlier_in_this_run": len(prior),
               "remaining_by_kind": {k: sum(1 for e in todo if e["kind"] == k) for k in ("AMC", "BMO")}, "planned_requests": len(plan), "planned_by_kind": by_kind,
               "batches": len(batches), "cap": a.cap, "calendar_age_hours": calendar_age_h, "credentials_present": creds}
    print(json.dumps(summary, default=str), flush=True)
    write_atomic(out / "plan.json", json.dumps(summary, indent=1, default=str))
    if a.plan_only:
        return
    if not all(creds.values()):
        print("STOPPED: a provider credential is missing")
        sys.exit(4)
    if calendar_age_h is None or calendar_age_h > 5.0 or len(plan) > a.cap:
        print("STOPPED: calendar older than 5 h (6 h freshness limit) or the plan exceeds the cap")
        sys.exit(6)
    ck = json.loads(ckpt_path.read_text(encoding="utf-8")) if a.resume else {"done_symbols": [], "batches_done": 0}
    rep = {"task": TASK, "started_utc": now.isoformat(), "status": "IN_PROGRESS", "mode": "LIVE", "cap_total": a.cap, "universe": universe, "reused_from_earlier_run": len(reused),
           "batches_planned": ck.get("batches_done", 0) + len(batches), "batches_done": ck["batches_done"], "errors": [], "stop_events": [], "attempts_total": 0, "requests_by_provider": {}, "plan": summary, "kill_switch_final": {"engaged": True}}
    new_rows = {r["symbol"]: r for r in (json.loads((out / "rows.json").read_text(encoding="utf-8")) if (out / "rows.json").exists() else [])}

    def publish(status):
        allrows = list(reused.values()) + [new_rows[s] for s in new_rows]
        done = {r["symbol"] for r in allrows}
        rep.update(status=status, updated_utc=svc.clock.now().isoformat(), results=allrows, not_evaluated=[{"symbol": e["symbol"], "kind": e["kind"], "reason": "not reached (run ended: %s)" % status} for e in todo if e["symbol"] not in done] if status != "IN_PROGRESS" else [],
                   attempts_total=len(svc.store.read("requests")), requests_by_provider=svc.feed()["requests_today_by_provider"])
        write_atomic(out / "audit_report.json", json.dumps(rep, indent=1, default=str))
        write_atomic(out / "audit_report.md", markdown(rep))
        write_atomic(out / "rows.json", json.dumps(list(new_rows.values()), default=str))
        write_atomic(ckpt_path, json.dumps(ck))

    status = "OK"
    try:
        publish("IN_PROGRESS")
        for i, batch in enumerate(batches):
            names = {e["symbol"] for e in batch}
            used = len(svc.store.read("requests"))
            ledger0 = len(svc.store.read("ledger"))                       # only stop/denial events raised inside THIS batch count
            if used >= a.cap:
                status = "CAP_REACHED"
                break
            try:
                if svc.store.kill_file.exists():                              # released only for this batch, by this process
                    svc.store.kill_file.unlink()
                svc.cfg.refresh_enabled, svc.cfg.authorized_daily = True, a.cap
                svc.gov.authorized_daily, svc.gov.enabled = a.cap, True
                if a.lift_hourly_cap:
                    svc.gov.hourly_shared_cap = svc.gov.offhours_hourly_cap = 10 ** 6; svc.gov.reserve_existing_hourly = svc.gov.reserve_existing_offhours = 0
                svc.focus = names
                left = a.cap - used
                svc.refresh_evidence([left])
                rows = [r for r in L.LiveScorer(svc).score_all(svc.clock.now(), persist=True) if r["symbol"] in names]
                stops_now = [e["type"] for e in svc.store.read("ledger")[ledger0:] if e["type"].startswith(STOP_PREFIXES)]
                if not stops_now:                                            # a batch interrupted by a stop is not published: its evidence is incomplete and the batch is redone on resume
                    for r in rows:
                        new_rows[r["symbol"]] = result_row(r, svc, L)
            finally:
                svc.engage_kill_switch("batch %d finished" % (i + 1))
                svc.cfg.refresh_enabled = False
            stops = [e["type"] for e in svc.store.read("ledger")[ledger0:] if e["type"].startswith(STOP_PREFIXES)]
            ck["batches_done"] = ck.get("batches_done", 0) + (0 if stops else 1)
            rep.update(batches_done=ck["batches_done"], stop_events=stops)
            last = svc.store.state().get("last_error")
            if stops:
                rep["errors"].append(last or {"error": "stop event"})
                status = "PAUSED_HOURLY_CAP" if set(stops) == {"DENIED_HOURLY_SHARED_CAP"} else "STOPPED_ON_PROVIDER_ERROR"      # our own hourly cap is a pause, not a provider failure
                publish(status)
                break
            publish("IN_PROGRESS")
            print("batch", i + 1, "of", len(batches), "done; requests", len(svc.store.read("requests")), flush=True)
    except Exception as exc:  # noqa: BLE001
        status = "ERROR"
        rep["errors"].append({"error": type(exc).__name__, "message": str(exc)[:300], "trace_tail": traceback.format_exc().splitlines()[-3:]})
    finally:
        if not svc.store.kill_file.exists():
            svc.engage_kill_switch("full-universe run finished: " + status)
        kill = svc.store.kill_file
        rep["kill_switch_final"] = {"engaged": svc.kill_engaged and kill.exists(), "kill_file": str(kill), "content": json.loads(kill.read_text()) if kill.exists() else None}
        publish(status if status != "OK" else "OK")
    print("finished", status, "requests", len(svc.store.read("requests")), flush=True)
    sys.exit(0 if status in ("OK", "CAP_REACHED", "PAUSED_HOURLY_CAP") else 5)


if __name__ == "__main__":
    main()
