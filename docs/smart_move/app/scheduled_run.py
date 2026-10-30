"""ONE-TIME UNATTENDED EXECUTION of the approved Smart Move supervised window collection (60 request attempts, 10 stocks), started by the Windows Task Scheduler.

    python scheduled_run.py --out-dir <new folder> --precheck-store <saved calendar store> [--not-before ISO] [--not-after ISO] [--selftest]

Safeguards (each is covered by a test):
  * runs at most once: it refuses to start if the output folder already exists, and creates it (with a flag file) before anything else;
  * refuses before --not-before and after --not-after (UTC); outside that range it sends nothing;
  * rechecks the calendar, vendor confirmation, release timing and the 48 h capture window itself (supervised_window_run.collect): nothing is collected for an event whose window is not open;
  * the governor's authorization is the cap (60 attempts including retries and the calendar refresh); a 429 or authorization failure stops everything for the day;
  * the kill switch is engaged again in a `finally` block (also on any error) and its final state is verified and written to the audit report;
  * the audit report (JSON and Markdown) lists the stocks checked and selected, the API attempts by provider, predictions, missing data, errors and the final kill-switch status.
No key or token is printed or stored. No live trade, no recurring job, no deployment.
--selftest sends nothing: it only proves the scheduler can start the program unattended (python, environment file, precheck store, calendar and window precheck).
"""
import argparse
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(HERE))
ENV = Path(r"C:\Users\initi\Downloads\US-STOCKS\backend\.env")
TASK_NAME = os.environ.get("SMART_MOVE_TASK_NAME", "SmartMove_OneTime_20261031_1705ET")
UTC = timezone.utc


def parse(t):
    return datetime.fromisoformat(t).astimezone(UTC)


def audit_markdown(rep):
    L = ["# Smart Move one-time prospective test: audit report", "",
         "Scheduled task: %s | started %s | finished %s | status **%s**" % (rep.get("task"), rep.get("started_utc"), rep.get("finished_utc"), rep.get("status")), "",
         "## Safety", "- request attempts used: **%s of %s** (by provider: %s); failed attempts: %s" % (rep.get("attempts_total"), rep.get("cap_total"), json.dumps(rep.get("requests_by_provider")), rep.get("failed_attempts")),
         "- stop events: %s" % (json.dumps(rep.get("stop_events")) or "none"), "- errors: %s" % (json.dumps(rep.get("errors")) or "none"),
         "- **final kill switch: %s**" % json.dumps(rep.get("kill_switch_final")), "",
         "## Stocks checked", "- events with an open window by the saved-calendar precheck: %s; vendor-confirmed with an open window after the fresh calendar: %s" % (rep.get("precheck_events_with_open_window"), rep.get("eligible_open_window_events_after_calendar")), ""]
    sel = rep.get("selected") or []
    L += ["Selected (largest market caps): " + (", ".join("%s (%s)" % (s["symbol"], s["kind"]) for s in sel) or "none"), "", "## Predictions (frozen 40-point engine; shadow only, not trading advice)", "",
          "| Symbol | Calendar | Points /40 | Continuation | Contrarian | Missing | Captured (UTC) | Hours to cutoff |", "|---|---|---|---|---|---|---|---|"]
    for r in rep.get("results") or []:
        c, k = r["continuation"], r["contrarian"]
        L.append("| %s | %s | %s | %s (%s up / %s down) | %s (%s up / %s down) | %s | %s | %s |" % (r["symbol"], r["calendar_status"], r["available_points"], c["signal"], c["bull"], c["bear"], k["signal"], k["bull"], k["bear"],
                                                                                                 ", ".join(r["missing_factors"]) or "none", r["capture_ts"], r["hours_to_cutoff"]))
    return "\n".join(L) + "\n"


def execute(out_dir: Path, svc, *, not_before=None, not_after=None, task=TASK_NAME, total_cap=60, max_names=10):
    """The whole one-time run on a built service. Returns the audit dict. Never raises: every failure ends in the report and the kill switch."""
    import supervised_window_run as W
    rep = {"task": task, "started_utc": svc.clock.now().isoformat(), "status": "STARTED", "errors": [], "cap_total": total_cap}
    try:
        now = svc.clock.now()
        if not_before and now < not_before:
            rep.update(status="TOO_EARLY_NOTHING_SENT", requests_sent=0)
        elif not_after and now > not_after:
            rep.update(status="TOO_LATE_NOTHING_SENT", requests_sent=0)
        else:
            rep.update(W.collect(svc, max_names=max_names, total_cap=total_cap))
            rep["stop_events"] = [e["type"] for e in svc.store.read("ledger") if e["type"].startswith(("STOP", "DENIED_NOT_AUTHORIZED", "DENIED_STOPPED", "DENIED_DAILY_CAP"))]
            last = svc.store.state().get("last_error")
            if last:
                rep["errors"].append(last)
    except Exception as exc:  # noqa: BLE001
        rep["status"] = "ERROR"
        rep["errors"].append({"error": type(exc).__name__, "message": str(exc)[:300], "trace_tail": traceback.format_exc().splitlines()[-3:]})
    finally:
        svc.engage_kill_switch("one-time scheduled run finished: " + rep.get("status", "?"))
        kill = svc.store.kill_file
        rep["kill_switch_final"] = {"engaged": svc.kill_engaged and kill.exists(), "kill_file": str(kill), "content": json.loads(kill.read_text()) if kill.exists() else None}
        rep["finished_utc"] = svc.clock.now().isoformat()
        rep["requests_logged"] = len(svc.store.read("requests"))
        (out_dir / "audit_report.json").write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
        (out_dir / "audit_report.md").write_text(audit_markdown(rep), encoding="utf-8")
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--precheck-store", required=True)
    ap.add_argument("--not-before", default="2026-10-31T21:00:00+00:00")
    ap.add_argument("--not-after", default="2026-11-01T19:00:00+00:00")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    out = Path(a.out_dir)
    if out.exists():
        print("STOPPED: output folder exists, a run already started here (exactly one execution)")
        sys.exit(3)
    out.mkdir(parents=True)
    (out / "STARTED.flag").write_text(datetime.now(UTC).isoformat() + " pid=%d user=%s\n" % (os.getpid(), os.environ.get("USERNAME", "?")), encoding="utf-8")
    log = open(out / "run.log", "a", encoding="utf-8")
    sys.stdout = sys.stderr = log
    print("start", datetime.now(UTC).isoformat(), "selftest" if a.selftest else "REAL RUN")
    from dotenv import load_dotenv
    load_dotenv(ENV)
    import smart_move_live as L
    import unusualwhales_service as uw
    import finnhub_service as fh
    import finviz_service as fz
    creds = {"UNUSUAL_WHALES": uw.configured(), "FINNHUB": bool(fh._key()), "FINVIZ": fz.configured()}
    print("credentials present (values never shown):", creds)
    store = out / "store"
    cfg = L.LiveConfig(mode="LIVE", store_dir=store, refresh_enabled=False, authorized_daily=0, spacing_s=2.0)
    svc = L.RefreshService(cfg, L.InfraFetcher() if not a.selftest else None, L.SystemClock())
    pre = L.RefreshService(L.LiveConfig(mode="LIVE", store_dir=Path(a.precheck_store), refresh_enabled=False), None, L.SystemClock())
    for r in pre.store.read("cal_obs"):
        svc.store.append("cal_obs", r)
    if a.selftest:
        import supervised_window_run as W
        rep = W.collect(svc)
        rep.update(selftest=True, credentials_present=creds, python=sys.version.split()[0], user=os.environ.get("USERNAME"), requests_logged=len(svc.store.read("requests")), cwd=os.getcwd())
        (out / "selftest_result.json").write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
        print(json.dumps({k: rep[k] for k in rep if k != "results"}, default=str))
        sys.exit(0)
    if not all(creds.values()):
        rep = {"task": TASK_NAME, "status": "CREDENTIAL_MISSING_NOTHING_SENT", "errors": [{"error": "credential missing", "present": creds}], "started_utc": datetime.now(UTC).isoformat(), "cap_total": 60}
        svc.engage_kill_switch("credential missing")
        rep["kill_switch_final"] = {"engaged": svc.store.kill_file.exists()}
        rep["finished_utc"] = datetime.now(UTC).isoformat()
        (out / "audit_report.json").write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
        (out / "audit_report.md").write_text(audit_markdown(rep), encoding="utf-8")
        sys.exit(4)
    rep = execute(out, svc, not_before=parse(a.not_before), not_after=parse(a.not_after))
    print("finished", rep["status"], "attempts", rep.get("attempts_total"), "kill switch engaged:", rep["kill_switch_final"]["engaged"])
    sys.exit(0 if rep["status"] in ("OK", "WINDOWS_NOT_OPEN", "TOO_EARLY_NOTHING_SENT", "TOO_LATE_NOTHING_SENT") else 5)


if __name__ == "__main__":
    main()
