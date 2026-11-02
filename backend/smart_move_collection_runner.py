"""Operator entry point for the Smart Move collection workflow. DRY-RUN IS THE DEFAULT: it plans, counts, writes the event-status report and sends nothing.

    python smart_move_collection_runner.py --store-dir <live store> --state-dir <state folder>                         # dry run (zero provider requests)
    python smart_move_collection_runner.py ... --as-of 2026-11-01T11:00:00+00:00                                         # dry run against a past clock (replay of the plan)

A LIVE run needs ALL of: --mode live, --arm, --authorize-requests N (N >= 1, also the daily cap), environment SMART_MOVE_COLLECTION_LIVE=1, a credential, and an existing kill-switch file that this
run releases only while it works and re-engages afterwards. Anything missing -> BLOCKED, nothing is sent. Nothing here registers a recurring job.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "docs" / "smart_move" / "shadow_impl"))
import smart_move_collection_jobs as J  # noqa: E402
import smart_move_event_status as ES  # noqa: E402
import smart_move_live as L  # noqa: E402
import smart_move_outcomes as O  # noqa: E402
import smart_move_tracking as T  # noqa: E402
import smart_move_calendar_guard as G  # noqa: E402
from smvshadow import governor as gv  # noqa: E402

ENV = Path(r"C:\Users\initi\Downloads\US-STOCKS\backend\.env")
UTC = timezone.utc


class NoNetworkFetcher(L.Fetcher):
    """Dry-run fetcher: any attempt to fetch is a programming error."""
    mode = "SIMULATION"

    def fetch(self, kind, symbol, params):
        raise AssertionError("dry run must never call the provider (%s)" % kind)


PILOT_MAX = 50


def build_service(store_dir, *, live: bool, authorized: int, now=None, retries: int = 1, tracking_days: int = 14):
    cfg = L.LiveConfig(mode="LIVE" if live else "SIMULATION", store_dir=Path(store_dir), refresh_enabled=live, authorized_daily=authorized if live else 0, spacing_s=2.0 if live else 0.0,
                       max_retries=retries, tracking_days_ahead=tracking_days)
    if live:
        return L.RefreshService(cfg, L.InfraFetcher(), L.SystemClock())
    return L.RefreshService(cfg, NoNetworkFetcher(), gv.FakeClock(now or datetime.now(UTC)))


def live_gate(args, env=os.environ) -> list:
    """Reasons a live run may NOT start; empty = all explicit authorizations are present."""
    why = []
    if args.mode != "live":
        why.append("mode is not live")
    if not args.arm:
        why.append("--arm missing")
    if args.authorize_requests < 1:
        why.append("--authorize-requests missing")
    if env.get("SMART_MOVE_COLLECTION_LIVE") != "1":
        why.append("SMART_MOVE_COLLECTION_LIVE=1 not set")
    return why


def status_report(svc, now, preds, track_dir) -> dict:
    window = svc.eligible_events(now)
    tracking = svc.eligible_events(now, svc.cfg.tracking_days_ahead)
    events = {(e["symbol"], str(e["event_date"])): e for e in tracking + window}
    ev_list = list(events.values())
    recs = svc.store.read("v2_evidence")
    live_ev = svc.store.read("evidence")
    out_state = O.readiness([{"symbol": e["symbol"], "earnings_date": str(e["event_date"]), "kind": e["kind"]} for e in ev_list], track_dir, now)["by_event"] if track_dir else {}
    out_state = {tuple(k.split("|")): v for k, v in out_state.items()}
    rows = ES.event_rows(ev_list, recs, live_ev, preds, out_state, G.calendar_age_h(svc.store, now), now)
    return {"generated_utc": now.isoformat(), "summary": ES.summarize(rows), "events": rows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store-dir", required=True)
    ap.add_argument("--state-dir", required=True)
    ap.add_argument("--mode", choices=("dry-run", "live"), default="dry-run")
    ap.add_argument("--arm", action="store_true")
    ap.add_argument("--authorize-requests", type=int, default=0)
    ap.add_argument("--ceiling", type=int, default=None, help="provider-attempt ceiling for this run (standard default 700, pilot50 default and maximum 50)")
    ap.add_argument("--batch", type=int, default=J.DEFAULT_BATCH)
    ap.add_argument("--as-of", default=None, help="dry-run only: evaluate the plan at this past instant")
    ap.add_argument("--preds-store", action="append", default=[])
    ap.add_argument("--tracking-dir", default=None)
    ap.add_argument("--profile", choices=("standard", "pilot50"), default="standard", help="pilot50: hard 50-attempt ceiling, no retries, no news paging, 3-day calendar, calendar capped at 14 requests")
    ap.add_argument("--calendar-days", type=int, default=None)
    ap.add_argument("--calendar-requests", type=int, default=None)
    a = ap.parse_args(argv)
    news_pages, retries = None, 1
    if a.profile != "pilot50":
        a.ceiling = J.DEFAULT_CEILING if a.ceiling is None else a.ceiling
    if a.profile == "pilot50":
        if a.authorize_requests > PILOT_MAX or (a.ceiling is not None and a.ceiling > PILOT_MAX):
            print("BLOCKED: the pilot profile never exceeds %d provider attempts" % PILOT_MAX)
            return 6
        a.ceiling = PILOT_MAX if a.ceiling is None else a.ceiling
        a.batch = min(a.batch, 5)
        a.calendar_days = 3 if a.calendar_days is None else a.calendar_days
        a.calendar_requests = 14 if a.calendar_requests is None else a.calendar_requests
        news_pages, retries = 1, 0
    live = a.mode == "live"
    blockers = live_gate(a) if live else []
    if live and blockers:
        print("BLOCKED:", "; ".join(blockers))
        return 6
    if live and a.as_of:
        print("BLOCKED: --as-of is for dry runs only")
        return 6
    now = datetime.fromisoformat(a.as_of) if a.as_of else datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    if live:
        from dotenv import load_dotenv
        load_dotenv(ENV)
        import unusualwhales_service as uw
        if not uw.configured():
            print("BLOCKED: provider credential missing")
            return 4
    svc = build_service(a.store_dir, live=live, authorized=a.authorize_requests, now=now, retries=retries, tracking_days=a.calendar_days or 14)
    kill_was_engaged = svc.store.kill_file.exists()
    if live:
        if not kill_was_engaged:
            print("BLOCKED: the kill-switch file must exist before a live run (it is released only while the run works)")
            return 6
        svc.store.kill_file.unlink()                                      # released for this run only
        svc.gov.authorized_daily, svc.gov.enabled = a.authorize_requests, True
    try:
        job = J.run_cycle(svc, now=now, state_dir=a.state_dir, mode="LIVE" if live else "DRY_RUN", armed=live and not blockers, ceiling=a.ceiling, batch_size=a.batch,
                        news_pages=news_pages, calendar_requests=a.calendar_requests)
    finally:
        if live:
            svc.engage_kill_switch("collection runner: re-engaged after the run")
    preds = T.load_predictions(a.preds_store) if a.preds_store else []
    rep = status_report(svc, now, preds, a.tracking_dir)
    ES.write(Path(a.state_dir) / ES.STATUS_FILE, rep)
    print(json.dumps({"job": job["id"], "mode": job["mode"], "status": job["status"], "provider_requests": job.get("requests", 0), "preflight": job["preflight"], "events": rep["summary"]}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
