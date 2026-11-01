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
    return SCH.tick(now, events, captured, set(), SCH.Budget(stopped=False), calendar_age_h=age, outcome_fetch_enabled=True, collection_enabled=collection_enabled), events


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


def run_actions(result, run_root, now):
    """Carries out COLLECT in a new folder via full_universe_run.py; calendar refresh happens inside that program. Outcome fetching runs through the governor-bound RefreshService."""
    import subprocess
    for act in result["actions"]:
        if act["action"] in ("COLLECT", "REFRESH_CALENDAR"):
            out = Path(run_root) / ("run_%s_auto" % now.strftime("%Y%m%d_%H%M"))
            cmd = [sys.executable, str(HERE / "full_universe_run.py"), "--out-dir", str(out), "--seed-store", str(Path(run_root).parent / "seed_store"),
                   "--seed-report", str(Path(run_root).parent / "seed_report.json"), "--cap", str(act.get("requests", 700) + 50), "--batch-size", "20"]
            rc = subprocess.run(cmd, check=False).returncode
            if rc not in (0,):
                return 5
    return 0


if __name__ == "__main__":
    sys.exit(main())
