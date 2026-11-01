"""One scheduler tick. Safe to run every 30 minutes: by default it only PLANS (reads saved reports, sends nothing, writes one line to tick_log.jsonl).

    python scheduler_tick.py --run-root <out folder> [--tick-log <file>] [--now ISO]            # plan only (default)
    python scheduler_tick.py ... --execute                                                      # executes ONLY if live collection is enabled (see below)

Live execution needs ALL of: the --execute flag, the environment variable SMART_MOVE_LIVE_COLLECTION=1, and a flag file `LIVE_COLLECTION_ENABLED` in the run root.
None of these exists by default, so a registered task does nothing but plan until the owner creates them. A COLLECT action is carried out by full_universe_run.py in a NEW
output folder (never overwriting); the kill switch is engaged again by that program after every batch. Exit code 0 = planned/executed, 2 = execution refused, 5 = provider stop.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "backend"))
UTC = timezone.utc


def live_enabled(run_root) -> bool:
    return os.environ.get("SMART_MOVE_LIVE_COLLECTION") == "1" and (Path(run_root) / "LIVE_COLLECTION_ENABLED").exists()


def plan(run_root, now, *, collection_enabled=False):
    import smart_move_board as B
    import smart_move_routes as R
    import smart_move_scheduler as SCH
    reports = B.load_run_reports(run_root)
    reg = B.derive_registry(R.current_rows(), reports)
    cur = reg.current()
    events = [{"symbol": s, "earnings_date": v["earnings_date"], "kind": v.get("kind")} for s, v in cur.items()]
    captured = set(B.load_run_results(run_root).keys())
    obs = max((v["observed_ts"] for v in cur.values()), default=None)
    age = (now - datetime.fromisoformat(obs)).total_seconds() / 3600 if obs else None
    import smart_move_v2_collect as V2C
    v2_store = _v2_store(run_root)
    v2_tasks = V2C.plan([e["symbol"] for e in events], v2_store, now)
    reports = B.load_run_reports(run_root)
    audits = Path(run_root).parent / "audits"
    last_audit = max((p.stat().st_mtime for p in audits.glob("audit_*.json")), default=0) if audits.exists() else 0
    newest_run = max((Path(run_root, n, "audit_report.json").stat().st_mtime for n, _ in reports), default=0)
    return SCH.tick(now, events, captured, set(), SCH.Budget(stopped=False), calendar_age_h=age, outcome_fetch_enabled=True, collection_enabled=collection_enabled,
                    v2_tasks=v2_tasks, audit_due=newest_run > last_audit), events


def _v2_store(run_root):
    """The V2 evidence store (own folder next to the run folders; created lazily by the first executed V2 collection). None when it does not exist yet: then every V2 task is due."""
    import smart_move_live as L
    d = Path(run_root).parent / "v2_store"
    return L.LiveStore(d) if d.exists() else _EmptyStore()


class _EmptyStore:
    def read(self, name):
        return []


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-root", required=True)
    ap.add_argument("--tick-log")
    ap.add_argument("--now")
    ap.add_argument("--execute", action="store_true")
    a = ap.parse_args(argv)
    now = datetime.fromisoformat(a.now).astimezone(UTC) if a.now else datetime.now(UTC)
    enabled = a.execute and live_enabled(a.run_root)
    result, events = plan(a.run_root, now, collection_enabled=enabled)
    entry = {"tick_at": now.isoformat(), "mode": "EXECUTE" if enabled else "PLAN_ONLY", "actions": result["actions"], "alerts": result["alerts"], "open_windows": result["open_windows"]}
    if a.execute and not enabled:
        entry["refused"] = "live collection is not enabled (needs SMART_MOVE_LIVE_COLLECTION=1 and the LIVE_COLLECTION_ENABLED flag file)"
    log = Path(a.tick_log or Path(a.run_root) / "tick_log.jsonl")
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")
    print(json.dumps(entry, indent=1, default=str))
    if a.execute and not enabled:
        return 2
    if enabled and result["execute"]:
        return run_actions(result, a.run_root, now)
    return 0


def seed_from_latest(run_root):
    """The seed for a new collection is the newest earlier run folder that has a store and a report (copied, never written). None when there is none."""
    import smart_move_board as B
    for name, rep in reversed(B.load_run_reports(run_root)):
        d = Path(run_root) / name
        if (d / "store" / "cal_obs.jsonl").exists() and (d / "store" / "evidence.jsonl").exists() and (d / "store" / "payloads").is_dir() and rep.get("results"):
            return d / "store", d / "audit_report.json"
    return None


def build_collect_command(act, run_root, now):
    seed = seed_from_latest(run_root)
    if seed is None:
        return None
    out = Path(run_root) / ("run_%s_auto" % now.strftime("%Y%m%d_%H%M"))
    # cap: the planned requests plus a small allowance for retries; never above the governor's own planning ceiling
    cap = min(int(act.get("requests", 0)) + 25, 1700)
    return [sys.executable, str(HERE / "full_universe_run.py"), "--out-dir", str(out), "--seed-store", str(seed[0]), "--seed-report", str(seed[1]), "--cap", str(cap), "--batch-size", "20"], out


def build_live_service(run_root, now, cap):
    """The production RefreshService for V2 evidence and outcome requests: the real governor (cap, spacing, hourly caps, 401/429 stop), a store of its own, the kill switch engaged
    unless this execution enables it. Only reached when live collection is fully enabled; tests inject a fake factory instead."""
    import smart_move_live as L
    cfg = L.LiveConfig(mode="LIVE", store_dir=Path(run_root).parent / "v2_store", refresh_enabled=True, authorized_daily=cap, spacing_s=2.0)
    svc = L.RefreshService(cfg, L.InfraFetcher(), L.SystemClock())
    svc.gov.authorized_daily, svc.gov.enabled = cap, True
    svc.store.kill_file.unlink(missing_ok=True)               # released only for this authorized execution; re-engaged in run_service_actions' finally block
    return svc


def run_service_actions(result, run_root, now, factory=None, audit_fn=None):
    """COLLECT_V2, FETCH_OUTCOMES and GENERATE_AUDIT. Every provider request goes through svc._call (governor + kill switch); the kill switch is engaged again afterwards, also on error."""
    import smart_move_outcome_collect as OC
    import smart_move_v2_collect as V2C
    factory = factory or build_live_service
    rc = 0
    for act in result["actions"]:
        if act["action"] in ("COLLECT_V2", "FETCH_OUTCOMES"):
            svc = factory(run_root, now, max(act.get("requests", 0) + 10, 10))
            try:
                if act["action"] == "COLLECT_V2":
                    acc = V2C.run(svc, act["tasks"], max_requests=act["requests"], checkpoint=Path(run_root).parent / "v2_store" / "checkpoint.json", stopped=lambda: bool(getattr(svc.gov, "stopped_until", None) and svc.clock.now() < svc.gov.stopped_until))
                    rc = 5 if acc.get("stopped") else rc
                else:
                    events = act.get("event_list", [])
                    call = lambda kind, sym, params: svc._call(kind, sym, params, "out-%s-%s" % (sym, now.strftime("%Y%m%d")))  # noqa: E731
                    out = OC.collect(events, Path(run_root).parent / "tracking", now, call, max_requests=act["requests"], stopped=lambda: bool(getattr(svc.gov, "stopped_until", None) and svc.clock.now() < svc.gov.stopped_until))
                    rc = 5 if out.get("stopped") else rc
            finally:
                try:
                    svc.store.kill_file.write_text("engaged after tick %s" % now.isoformat())
                except Exception:  # noqa: BLE001
                    pass
        elif act["action"] == "GENERATE_AUDIT":
            audit_fn = audit_fn or (lambda: __import__("smart_move_audit_run").generate(run_root, Path(run_root).parent / "audits", now=now))
            audit_fn()
    return rc


def run_actions(result, run_root, now, runner=None, factory=None, audit_fn=None):
    """Carries out COLLECT / REFRESH_CALENDAR through full_universe_run.py in a NEW folder (it refuses an existing one, resumes only with --resume, engages the kill switch after every batch).
    A folder left unfinished by an interrupted tick is resumed instead of restarted. `runner` replaces subprocess.run in tests."""
    import subprocess
    runner = runner or (lambda cmd: subprocess.run(cmd, check=False).returncode)
    done = False
    for act in result["actions"]:
        if act["action"] not in ("COLLECT", "REFRESH_CALENDAR") or done:
            continue
        built = build_collect_command(act, run_root, now)
        if built is None:
            print(json.dumps({"refused": "no earlier run folder with a store to seed from"}))
            return 2
        cmd, out = built
        if (out / "checkpoint.json").exists() and not (out / "audit_report.json").exists():
            cmd = cmd + ["--resume"]
        elif out.exists():
            return 0                                              # this minute's folder already exists and is finished: a repeated tick does nothing
        rc = runner(cmd)
        done = True                                               # one collection per tick
        if rc not in (0,):
            return 5
    return run_service_actions(result, run_root, now, factory=factory, audit_fn=audit_fn)


if __name__ == "__main__":
    sys.exit(main())
