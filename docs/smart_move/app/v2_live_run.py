"""ONE-TIME, owner-authorized (2026-11-01) V2 evidence collection for the open-window Smart Move universe.

    python v2_live_run.py --phase probe --out-dir <NEW folder> --v1-run <run folder> [--cap 700]    # calendar refresh + 1-stock endpoint probe, then stops
    python v2_live_run.py --phase full  --out-dir <same folder> --v1-run <run folder> [--cap 700]    # collects the rest (resumable)

Rules enforced here (all through RefreshService: governor, kill switch, request ledger):
  * total provider attempts for the whole run, calendar refresh and probe included, never exceed --cap (700); 2 s spacing, ONE request in flight; our own hourly share (about 100/h in live
    hours) is respected by WAITING for the next hour, never lifted;
  * the earnings calendar is refreshed first (vendor confirmed events only); a stock is requested only while its capture window is open, with a 5-minute margin before the cut-off;
  * stocks without a V1 gate (tradable entry, liquidity known and >= $20M) go first; stocks V2 can never signal (V1 gate) go last and only if budget and window remain;
  * 401/403/429 or any stop ends the run; the kill switch is engaged outside of work and again at the end, also on error;
  * nothing is requested twice (V2 planner de-duplication, checkpoint), the news endpoint is a 3-stock sample only.
No credential is printed. No outcome request is made. V1 files are only read (the seed is copied).
"""
import argparse
import json
import os
import shutil
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(HERE))
ENV = Path(r"C:\Users\initi\Downloads\US-STOCKS\backend\.env")
UTC = timezone.utc
GATES = ("NOT_TRADABLE", "ILLIQUID_OR_UNKNOWN", "UNKNOWN_TIMING", "TIMING_CONTRADICTED", "STALE_EVIDENCE", "BLOCKED_BY_GATE", "SUSPECT_WINDOW")
STOP_PREFIXES = ("STOP", "DENIED_NOT_AUTHORIZED", "DENIED_STOPPED", "DENIED_DAILY_CAP", "DENIED_KILL_SWITCH")
MARGIN = timedelta(minutes=5)


def atomic(path: Path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)


def attempts(svc) -> int:
    """Every governor-granted attempt today (successful or failed, retries included)."""
    return sum(svc.gov.used_day.values()) if svc.gov.used_day else 0


def classify(events, v1_by_symbol):
    elig, gated = [], []
    for e in events:
        codes = set((v1_by_symbol.get(e["symbol"]) or {}).get("continuation", {}).get("reason_codes") or [])
        (gated if codes & set(GATES) else elig).append(e)
    key = lambda e: (e["plan"]["cutoff_utc"], -e["marketcap"], e["symbol"])  # noqa: E731
    return sorted(elig, key=key), sorted(gated, key=key)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=("probe", "full"), required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--v1-run", required=True)
    ap.add_argument("--cap", type=int, default=700)
    ap.add_argument("--news-sample", type=int, default=3)
    ap.add_argument("--calendar-budget", type=int, default=0, help="full phase only: re-run the vendor calendar refresh with this many requests (counted in the cap); the probe's 12 requests were too few to reach the Finviz/Finnhub confirmation")
    a = ap.parse_args(argv)
    out, v1 = Path(a.out_dir), Path(a.v1_run)
    store = out / "store"
    if a.phase == "probe":
        if out.exists():
            print("STOPPED: output folder exists; a probe never reuses a folder")
            return 3
        store.mkdir(parents=True)
        for n in ("cal_obs.jsonl", "evidence.jsonl"):
            shutil.copy2(v1 / "store" / n, store / n)
        shutil.copytree(v1 / "store" / "payloads", store / "payloads")
    elif not (out / "probe.json").exists():
        print("STOPPED: run the probe phase first")
        return 3
    log = open(out / ("run_%s.log" % a.phase), "a", encoding="utf-8")
    sys.stdout = sys.stderr = log
    print("start", datetime.now(UTC).isoformat(), a.phase, flush=True)
    from dotenv import load_dotenv
    load_dotenv(ENV)
    import smart_move_live as L
    import smart_move_v2_collect as C
    import smart_move_calendar_guard as G
    import unusualwhales_service as uw
    import finnhub_service as fh
    import finviz_service as fz
    creds = {"UNUSUAL_WHALES": uw.configured(), "FINNHUB": bool(fh._key()), "FINVIZ": fz.configured()}
    print("credentials present (values never shown):", creds, flush=True)
    if not creds["UNUSUAL_WHALES"]:
        print("STOPPED: Unusual Whales credential missing")
        return 4
    v1rep = json.loads((v1 / "audit_report.json").read_text(encoding="utf-8"))
    v1_by = {r["symbol"]: r for r in v1rep["results"]}
    cfg = L.LiveConfig(mode="LIVE", store_dir=store, refresh_enabled=False, authorized_daily=0, spacing_s=2.0)
    svc = L.RefreshService(cfg, L.InfraFetcher(), L.SystemClock())
    report_path = out / "report.json"
    rep = json.loads(report_path.read_text()) if report_path.exists() else {"task": "SmartMove_V2_OneTime_20261101", "cap": a.cap, "phases": {}, "kinds": {}, "errors": [], "stop_events": []}
    import smart_move_keep_awake as KA
    awake = KA.KeepAwake()
    rep["keep_awake"] = {"supported": awake.supported, "granted": awake.request()}    # per-process idle-sleep block; no system setting is changed; a closed lid still sleeps
    status = "OK"
    stopped = lambda: bool(svc.gov.stopped_until and svc.clock.now() < svc.gov.stopped_until)  # noqa: E731

    def release():
        if svc.store.kill_file.exists():
            svc.store.kill_file.unlink()
        svc.cfg.refresh_enabled, svc.cfg.authorized_daily = True, a.cap
        svc.gov.authorized_daily, svc.gov.enabled = a.cap, True        # our own hourly caps stay at their defaults

    def engage(why):
        svc.engage_kill_switch(why)
        svc.cfg.refresh_enabled = False

    try:
        if not svc.store.kill_file.exists():
            svc.engage_kill_switch("v2 run: engaged outside work")
        release()
        now = svc.clock.now()
        if a.phase == "probe":
            budget = [12]
            cal_done = svc.refresh_calendar(budget)                       # refreshes the vendor calendar inside the cap (a few requests)
            rep["phases"]["calendar_refresh"] = {"requests": cal_done, "at": svc.clock.now().isoformat()}
            print("calendar refresh requests", cal_done, "stopped" if stopped() else "ok", flush=True)
            if stopped() or cal_done == 0:
                status = "STOPPED_CALENDAR"
                raise RuntimeError("calendar refresh failed or provider stop")
        if a.phase == "full" and a.calendar_budget and "calendar_refresh_2" not in rep["phases"]:
            budget = [min(a.calendar_budget, a.cap - attempts(svc))]
            cal_done = svc.refresh_calendar(budget)
            rep["phases"]["calendar_refresh_2"] = {"requests": cal_done, "budget_left": budget[0], "at": svc.clock.now().isoformat()}
            atomic(report_path, rep)
            print("calendar refresh 2 requests", cal_done, "budget left", budget[0], flush=True)
            if stopped() or cal_done == 0:
                status = "STOPPED_CALENDAR"
                raise RuntimeError("calendar refresh failed or provider stop")
        events = svc.eligible_events(svc.clock.now())
        elig, gated = classify(events, v1_by)
        order = elig + gated
        closing = {e["symbol"]: e["plan"]["cutoff_utc"] for e in events}
        rep["universe"] = {"open_window_events": len(events), "ungated": len(elig), "v1_gated": len(gated), "v1_results": len(v1_by),
                           "window_closed_since_v1_run": sorted(s for s in v1_by if s not in closing)}
        if a.phase == "probe":
            sym = elig[0]["symbol"] if elig else order[0]["symbol"]
            tasks = C.plan([sym], svc.store, svc.clock.now(), include_news=True)
            acc = C.run(svc, tasks, max_requests=len(tasks), checkpoint=out / "probe_checkpoint.json", stopped=stopped)
            by = {}
            for r in svc.store.read(C.EVIDENCE):
                by[r["kind"]] = {"status": r["status"], "rows": r["rows"], "invalid_reason": r.get("invalid_reason")}
            fails = [f for f in acc["failures"]]
            rep["phases"]["probe"] = {"symbol": sym, "accounting": acc, "by_kind": by, "failures": fails, "attempts_total_so_far": attempts(svc)}
            atomic(out / "probe.json", rep["phases"]["probe"])
            print("probe", sym, json.dumps(by), flush=True)
        else:
            probe = json.loads((out / "probe.json").read_text())
            good_kinds = [k for k in C.CORE if probe["by_kind"].get(k, {}).get("status") == "OK"]
            rep["phases"]["full_start"] = {"at": svc.clock.now().isoformat(), "kinds_used": good_kinds, "kinds_excluded_after_probe": [k for k in C.CORE if k not in good_kinds], "attempts_before": attempts(svc)}
            done_symbols = set()
            gaps = G.GapWatcher()
            rep["calendar_refreshes"] = rep.get("calendar_refreshes", [])
            news_left = a.news_sample
            for e in order:
                sym = e["symbol"]
                if stopped() or attempts(svc) >= a.cap:
                    status = "STOPPED_ON_PROVIDER_ERROR" if stopped() else "CAP_REACHED"
                    break
                cut = datetime.fromisoformat(closing[sym]) if isinstance(closing[sym], str) else closing[sym]
                tasks = [t for t in C.plan([sym], svc.store, svc.clock.now()) if t["kind"] in good_kinds]
                if news_left > 0 and probe["by_kind"].get("uw_news", {}).get("status") == "OK" and sym != probe["symbol"]:
                    tasks += [t for t in C.plan([sym], svc.store, svc.clock.now(), include_news=True) if t["kind"] == "uw_news"]
                    news_left -= 1
                if not tasks:
                    done_symbols.add(sym)
                    continue
                while svc.gov.hourly_allowance() < len(tasks) and svc.clock.now() < cut - MARGIN and not stopped():      # our own hourly share: wait, never lift
                    time.sleep(30)
                gaps.tick(svc.clock.now())
                if gaps.gaps:
                    rep["sleep_gaps"] = gaps.gaps                                   # suspended-PC gaps are recorded, not hidden
                # the calendar must be fresh AT THE MOMENT of capture (after any pause, sleep or resume): refresh inside the cap, or stop; never capture against a stale calendar
                g = G.ensure_fresh(svc, budget_left=a.cap - attempts(svc), stopped=stopped)
                if g["refreshed"]:
                    rep["calendar_refreshes"].append(g)
                    live = {x["symbol"]: x["plan"]["cutoff_utc"] for x in svc.eligible_events(svc.clock.now())}
                    closing.update(live)
                    atomic(report_path, rep)
                if not g["ok"]:
                    status = "STOPPED_STALE_CALENDAR"
                    rep["errors"].append({"error": "STALE_CALENDAR", "detail": g})
                    break
                if sym not in {x["symbol"] for x in svc.eligible_events(svc.clock.now())}:
                    rep.setdefault("not_eligible_after_refresh", []).append(sym)      # the refreshed calendar no longer confirms this event in an open window
                    continue
                cut = datetime.fromisoformat(closing[sym]) if isinstance(closing[sym], str) else closing[sym]
                if svc.clock.now() >= cut - MARGIN:
                    rep.setdefault("expired_before_collection", []).append(sym)
                    continue
                cal_stale = lambda: G.needs_refresh(svc.store, svc.clock.now(), G.MAX_AGE_H + 0.5)  # noqa: E731  checked before EVERY request, so a sleep in the middle of a stock is caught too
                for _round in range(3):
                    room = a.cap - attempts(svc)
                    acc = C.run(svc, tasks, max_requests=min(len(tasks), room), checkpoint=out / "checkpoint.json", stopped=lambda: stopped() or cal_stale(), calendar_ok=lambda at: not G.needs_refresh(svc.store, at, 6.0))
                    for k, v in acc["by_kind"].items():
                        rep["kinds"][k] = rep["kinds"].get(k, 0) + v
                    rep.setdefault("failures", []).extend(acc["failures"])
                    if not (acc["stopped"] and not stopped()):
                        break
                    gaps.tick(svc.clock.now())
                    if gaps.gaps:
                        rep["sleep_gaps"] = gaps.gaps
                    g = G.ensure_fresh(svc, budget_left=a.cap - attempts(svc), stopped=stopped)          # the calendar aged inside this stock: refresh, then finish only the remaining tasks
                    if g["refreshed"]:
                        rep["calendar_refreshes"].append(g)
                    if not g["ok"]:
                        break
                    tasks = [t for t in C.plan([sym], svc.store, svc.clock.now()) if t["kind"] in good_kinds]
                if acc["stopped"] and not stopped():
                    status = "STOPPED_STALE_CALENDAR"
                    rep["errors"].append({"error": "STALE_CALENDAR", "detail": "calendar aged past the limit during a capture and could not be refreshed"})
                    atomic(report_path, rep)
                    break
                done_symbols.add(sym)
                rep["collected_symbols"] = sorted(set(rep.get("collected_symbols", [])) | {sym})
                rep["attempts_total"] = attempts(svc)
                atomic(report_path, rep)
                print(sym, "attempts", attempts(svc), flush=True)
            rep["phases"]["full_end"] = {"at": svc.clock.now().isoformat(), "attempts_after": attempts(svc), "symbols_done": len(done_symbols)}
    except Exception as exc:  # noqa: BLE001
        if status == "OK":
            status = "ERROR"
        rep["errors"].append({"error": type(exc).__name__, "message": str(exc)[:300], "trace_tail": traceback.format_exc().splitlines()[-3:]})
    finally:
        rep["stop_events"] = [e["type"] for e in svc.store.read("ledger") if e["type"].startswith(STOP_PREFIXES)]
        rep["status_" + a.phase] = status
        rep["attempts_total"] = attempts(svc)
        rep["requests_by_provider"] = svc.feed()["requests_today_by_provider"]
        if not svc.store.kill_file.exists():
            svc.engage_kill_switch("v2 run finished: " + status)
        rep["kill_switch_final"] = {"engaged": svc.store.kill_file.exists() and svc.kill_engaged}
        awake.release()
        atomic(report_path, rep)
        print("finished", status, "attempts", attempts(svc), flush=True)
    return 0 if status in ("OK", "CAP_REACHED") else 5


if __name__ == "__main__":
    sys.exit(main())
