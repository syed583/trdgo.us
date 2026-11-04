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
import smart_move_engine20 as E20  # noqa: E402
import smart_move_event_records as ER  # noqa: E402
import smart_move_event_registry as REG  # noqa: E402
import smart_move_event_status as ES  # noqa: E402
import smart_move_snapshot_pairs as SPR  # noqa: E402
import smart_move_live as L  # noqa: E402
import smart_move_outcomes as O  # noqa: E402
import smart_move_tracking as T  # noqa: E402
import smart_move_calendar_guard as G  # noqa: E402
import smart_move_secret_check as SC  # noqa: E402
import smart_move_shared_key_gate as SK  # noqa: E402
from smvshadow import governor as gv  # noqa: E402

ENV = Path(r"C:\Users\initi\Downloads\US-STOCKS\backend\.env")
UTC = timezone.utc


class NoNetworkFetcher(L.Fetcher):
    """Dry-run fetcher: any attempt to fetch is a programming error."""
    mode = "SIMULATION"

    def fetch(self, kind, symbol, params):
        raise AssertionError("dry run must never call the provider (%s)" % kind)


PILOT_MAX = int(os.environ.get("SMART_MOVE_RUN_MAX") or 50)             # internal governor ceilings: the defaults are the approved production limits; raising them is an explicit owner decision (environment variable)
DAILY_LIMIT_MAX = int(os.environ.get("SMART_MOVE_DAILY_MAX") or 100)


def build_service(store_dir, *, live: bool, authorized: int, now=None, retries: int = 1, tracking_days: int = 14, attempts_path=None):
    cfg = L.LiveConfig(mode="LIVE" if live else "SIMULATION", store_dir=Path(store_dir), refresh_enabled=live, authorized_daily=authorized if live else 0, spacing_s=2.0 if live else 0.0,
                       max_retries=retries, tracking_days_ahead=tracking_days)
    if live:
        clock = L.SystemClock()
        fetcher = L.InfraFetcher()
        if attempts_path:
            fetcher = J.AttemptRecorder(fetcher, attempts_path, clock)                # one line per provider attempt, failures and 429s included
        return L.RefreshService(cfg, fetcher, clock)
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


def execute(svc, *, now, state_dir, live: bool, authorized: int, ceiling: int, batch: int, news_pages=None, calendar_requests=None, stage_caps=None, calendar_scope="horizon", max_consecutive_failures=None, outcome_events_fn=None, daily_limit=None) -> dict:
    """One collection cycle. A live cycle releases the kill-switch file only for its own duration and ALWAYS re-engages it afterwards, whatever happens."""
    if live:
        svc.store.kill_file.unlink()
        svc.gov.authorized_daily, svc.gov.enabled = authorized, True
    try:
        return J.run_cycle(svc, now=now, state_dir=state_dir, mode="LIVE" if live else "DRY_RUN", armed=live, ceiling=ceiling, batch_size=batch,
                           news_pages=news_pages, calendar_requests=calendar_requests, stage_caps=stage_caps, calendar_scope=calendar_scope, max_consecutive_failures=max_consecutive_failures, outcome_events_fn=outcome_events_fn, daily_limit=daily_limit)
    finally:
        if live:
            svc.engage_kill_switch("collection runner: re-engaged after the run")


def outcome_events(svc, now) -> list:
    """Events with a BMO/AMC session recorded in the saved calendar (the event record, not a vendor label alone) whose date has passed: the candidates for daily-bar collection once their reaction session has closed."""
    recs = ER.records_from_saved(svc.store.read("cal_obs"), now, {})
    return [{"symbol": r["symbol"], "event_date": r["event_date"], "session": r["session"]} for r in recs if r["event_date"] and r["session"] in ("BMO", "AMC")
            and r["event_date"] < now.date().isoformat()]


def engine20_profile(a, svc, now, live: bool) -> int:
    """Collect the frozen engine's evidence for a selected set of stocks. Dry run: select, plan, print, write the plan; zero requests. Live (separately gated like every live run): the kill file is released only for the run
    and always re-engaged; a calendar refresh happens only if the calendar is older than 5 h and is counted in the same ceiling."""
    d = Path(a.state_dir)
    d.mkdir(parents=True, exist_ok=True)
    caps = {}
    for o in svc.store.read("cal_obs"):
        try:
            caps[o["symbol"]] = float(o["marketcap"])
        except (KeyError, TypeError, ValueError):
            pass
    audit = []
    kinds = ("ohlc", "earnings") + (("vol_context", "iv_rank") if a.with_f7 else ())             # only the kinds this run would actually fund decide whether a stock still needs work

    def choose(t):
        """Selection at time t. Called again after a calendar refresh: the refresh changes which events are vendor-confirmed, so a selection made before it would be empty or stale."""
        audit.clear()
        rows = L.LiveScorer(svc).score_all(t)
        pending = {x["symbol"] for x in svc.plan_evidence(t) if x.get("symbol") and x["kind"] in kinds} if a.select_policy == "deadline" else None
        selected = E20.select_top(rows, caps, t, policy=a.select_policy, pending=pending, audit=audit, n=a.select_top, event_date=a.select_date, session=a.select_session, only=[x.strip().upper() for x in a.select_symbols.split(",") if x.strip()] if a.select_symbols else None)
        return selected

    selected = choose(now)
    cal_needed = G.needs_refresh(svc.store, now)
    cal_slots = J.horizon_calendar_slots(now, a.calendar_days_ahead) if a.calendar_days_ahead is not None else J.useful_calendar_slots(now)
    cal_est = (len(cal_slots) if a.calendar_days_ahead is not None else 4) if cal_needed else 0          # unchanged default: 4 requests for the open windows only
    plan = E20.plan_tasks(svc, selected, now, budget=a.ceiling - cal_est, with_f7=a.with_f7)
    summary = {"mode": "LIVE" if live else "DRY_RUN", "profile": "engine20", "as_of": now.isoformat(), "selected": selected, "calendar_refresh_needed": cal_needed, "calendar_attempts_reserved": cal_est,
               "attempts_planned_for_evidence": plan["attempts_planned"], "total_attempts_planned": plan["attempts_planned"] + cal_est, "ceiling": a.ceiling, "spare": plan["spare"],
               "stocks_fully_funded": plan["stocks_fully_funded"], "stocks_already_fresh": plan["already_fresh_stocks"], "unfunded": [(t["kind"], t["symbol"]) for t in plan["unfunded"]],
               "tasks": [(t["kind"], t["symbol"], t["why"]) for t in plan["funded"]], "provider_requests": 0}
    (d / "engine20_plan.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    (d / "engine20_selection.json").write_text(json.dumps({"policy": a.select_policy, "slots": a.select_top, "as_of": now.isoformat(), "decisions": audit}, indent=1, default=str), encoding="utf-8")
    limits = {"run_limit": a.ceiling, "daily_limit": a.daily_limit or a.authorize_requests}
    if not live:
        if a.publish_dir:
            import smart_move_live_publisher as PUB                                # a dry run shows itself on the dashboard (status only) and publishes NO prediction
            PUB.publish(svc, now, a.publish_dir, scan_id=d.name, mode="DRY_RUN", scan_ok=True, run={"started_at": now.isoformat(), "requests_attempted": 0, "planned": summary["total_attempts_planned"]},
                        limits=limits, unfunded=len(summary["unfunded"]))
        print(json.dumps({k: v for k, v in summary.items() if k != "tasks"}, indent=1, default=str))
        return 0
    svc.store.kill_file.unlink()
    svc.gov.authorized_daily, svc.gov.enabled = (a.daily_limit or a.authorize_requests), True
    try:
        used = 0
        if cal_needed:
            g = G.ensure_fresh(svc, budget_left=a.ceiling, stopped=lambda: bool(svc.gov.stopped_until and svc.clock.now() < svc.gov.stopped_until), max_requests=cal_est, dates=(J.horizon_calendar_slots(svc.clock.now(), a.calendar_days_ahead) if a.calendar_days_ahead is not None else J.useful_calendar_slots(svc.clock.now())))
            used = g.get("requests", 0)
            summary["calendar_refresh"] = g
            if not g["ok"]:
                (d / "engine20_plan.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
                print("BLOCKED: calendar not fresh:", g.get("reason"))
                return 6
        if cal_needed:
            selected = choose(svc.clock.now())                                  # the refreshed calendar decides who is eligible: re-select (this was missing: a run that refreshed the calendar used its empty pre-refresh selection)
            summary["selected"] = selected
            (d / "engine20_selection.json").write_text(json.dumps({"policy": a.select_policy, "slots": a.select_top, "as_of": svc.clock.now().isoformat(), "decisions": audit, "after_calendar_refresh": True}, indent=1, default=str), encoding="utf-8")
        plan = E20.plan_tasks(svc, selected, svc.clock.now(), budget=a.ceiling - used, with_f7=a.with_f7)
        summary.update(attempts_planned_for_evidence=plan["attempts_planned"], spare=plan["spare"], stocks_fully_funded=plan["stocks_fully_funded"], stocks_already_fresh=plan["already_fresh_stocks"],
                       unfunded=[(t["kind"], t["symbol"]) for t in plan["unfunded"]], tasks=[(t["kind"], t["symbol"], t["why"]) for t in plan["funded"]], total_attempts_planned=plan["attempts_planned"] + used)
        (d / "engine20_plan.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")                  # the plan file describes what was actually run, after the refresh
        res = E20.run(svc, plan["funded"], now=svc.clock.now(), ceiling=a.ceiling - used, state_dir=d, stopped=lambda: bool(svc.gov.stopped_until and svc.clock.now() < svc.gov.stopped_until), max_consecutive_failures=3)
        rep = E20.report(svc, selected, svc.clock.now())
        (d / "engine20_report.json").write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
        print(json.dumps({"requests_attempted": res["requests_attempted"] + used, "ok": res["ok"], "failed": res["failed"], "halted": res["halted"], "counts": rep["counts"]}, indent=1))
        if a.publish_dir:
            import smart_move_live_publisher as PUB
            try:
                st = PUB.publish(svc, svc.clock.now(), a.publish_dir, scan_id=d.name, mode="LIVE", scan_ok=(res["halted"] is None and res["failed"] == 0),
                                 run={"started_at": now.isoformat(), "finished_at": svc.clock.now().isoformat(), "requests_attempted": res["requests_attempted"] + used, "ok": res["ok"], "failed": res["failed"], "halted": res["halted"]},
                                 limits=limits, unfunded=len(summary["unfunded"]))
                print(json.dumps({"published": st["events"]}, default=str))
            except PUB.LedgerError as e:
                print("PUBLISH BLOCKED: the prediction ledger failed its integrity check:", e)
                return 7
        return 0
    finally:
        svc.engage_kill_switch("engine20 run finished: re-engaged")


def record_events(svc, now, state_dir) -> dict:
    """Offline planning step: turn the saved calendar observations (and any saved earnings payloads) into versioned event records in <state>/event_records.jsonl and refresh the symbol-level EventRegistry view
    in <state>/event_registry.jsonl. Reads saved files only, sends nothing, writes only inside the state folder."""
    def load(sha):
        try:
            return json.loads(svc.store.get(sha))
        except Exception:  # noqa: BLE001
            return None
    # Found after Batch #2: the collector saves its earnings payloads as kind `uw_earnings` in v2_evidence, while the engine-kind `earnings` rows live in `evidence`. Both feed the quarter lookup; the files themselves are never touched.
    ev_rows, v2_rows = svc.store.read("evidence"), svc.store.read("v2_evidence")
    payloads = ER.earnings_payloads(list(ev_rows) + list(v2_rows), load, now)
    recs = ER.records_from_saved(svc.store.read("cal_obs"), now, payloads)
    store = ER.EventRecords(Path(state_dir) / "event_records.jsonl")
    res = store.observe_snapshot(recs, observed=now)
    reg = REG.EventRegistry(Path(state_dir) / "event_registry.jsonl")
    ER.sync_registry(reg, store, now)
    def est_rows(sha):
        body = load(sha)
        d = body.get("data") if isinstance(body, dict) else None
        return (d.get("estimates") if isinstance(d, dict) else d) if d is not None else None
    pairs = SPR.pair_all(store, [r for r in svc.store.read("v2_evidence") if r.get("kind") == "uw_estimates"], est_rows, now, SPR.PairStore(Path(state_dir) / "snapshot_pairs.jsonl"))
    return dict(ER.summarize(store), changes={k: len(v) for k, v in res.items()}, earnings_payloads_used=len(payloads), as_of_utc=now.isoformat(), evidence_rows_read={"evidence": len(ev_rows), "v2_evidence": len(v2_rows)}, snapshot_pairs=dict(pairs["counts"], reasons=pairs["reasons"]))


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
    ap.add_argument("--daily-limit", type=int, default=None, help="owner-authorized internal daily attempt limit for the governor (US/Eastern date, counts attempts already logged in the store); max %d. The per-run ceiling stays at most %d additional attempts" % (DAILY_LIMIT_MAX, PILOT_MAX))
    ap.add_argument("--ceiling", type=int, default=None, help="provider-attempt ceiling for this run (standard default 700, pilot50 default and maximum 50)")
    ap.add_argument("--batch", type=int, default=J.DEFAULT_BATCH)
    ap.add_argument("--as-of", default=None, help="dry-run only: evaluate the plan at this past instant")
    ap.add_argument("--preds-store", action="append", default=[])
    ap.add_argument("--tracking-dir", default=None)
    ap.add_argument("--profile", choices=("standard", "pilot50", "batch2", "engine20"), default="standard", help="pilot50: hard 50-attempt ceiling, no retries, no news paging, 3-day calendar, calendar capped at 14 requests")
    ap.add_argument("--calendar-days", type=int, default=None)
    ap.add_argument("--calendar-requests", type=int, default=None)
    ap.add_argument("--select-top", type=int, default=20, help="engine20 profile: how many stocks (vendor-confirmed, window open, ranked by market cap)")
    ap.add_argument("--select-symbols", default=None, help="engine20 profile: pin the owner-approved list (comma separated); each symbol must still pass the eligibility checks, none is substituted")
    ap.add_argument("--select-date", default=None, help="engine20 profile: only events on this date, e.g. 2026-11-04")
    ap.add_argument("--select-session", default=None, choices=("BMO", "AMC"), help="engine20 profile: only this announcement session")
    ap.add_argument("--select-policy", choices=("cap", "deadline"), default="cap", help="engine20 profile: cap = the n largest market caps (default, unchanged); deadline = stocks that still NEED evidence, closest prediction cutoff first (already-collected stocks never take a slot)")
    ap.add_argument("--publish-dir", default=None, help="engine20 profile: folder for the live prediction ledger and status file read by the dashboard (append-only; a dry run writes status only)")
    ap.add_argument("--calendar-days-ahead", type=int, default=None, help="engine20 profile: keep the calendar this many days forward (both sessions of every trading day, 2 requests per day) instead of only the open windows")
    ap.add_argument("--with-f7", action="store_true", help="engine20 profile: also request volatility context and IV rank (2 more requests per stock)")
    ap.add_argument("--require-rotated-from", default=None, help="8-hex fingerprint of the OLD token; a live run is BLOCKED while the active token still has this fingerprint")
    ap.add_argument("--shared-key-attestation", default=None, help="owner acknowledgement file (see smart_move_shared_key_gate); without a valid one a live run stays BLOCKED")
    a = ap.parse_args(argv)
    news_pages, retries, stage_caps, scope, max_fail = None, 1, None, "horizon", None
    if a.profile not in ("pilot50", "batch2", "engine20"):
        a.ceiling = J.DEFAULT_CEILING if a.ceiling is None else a.ceiling
    if a.daily_limit is not None and (a.profile not in ("pilot50", "batch2", "engine20") or a.daily_limit > DAILY_LIMIT_MAX or a.daily_limit < a.authorize_requests):
        print("BLOCKED: --daily-limit is only for the pilot-family profiles, at most %d, and not below --authorize-requests" % DAILY_LIMIT_MAX)
        return 6
    if a.profile in ("pilot50", "batch2", "engine20"):
        if a.authorize_requests > PILOT_MAX or (a.ceiling is not None and a.ceiling > PILOT_MAX):
            print("BLOCKED: the pilot profile never exceeds %d provider attempts" % PILOT_MAX)
            return 6
        a.ceiling = PILOT_MAX if a.ceiling is None else a.ceiling
        a.batch = min(a.batch, 5)
        a.calendar_days = 3 if a.calendar_days is None else a.calendar_days
        a.calendar_requests = 14 if a.calendar_requests is None else a.calendar_requests
        news_pages, retries, scope, max_fail = 1, 0, "useful", 3                # pilot: three failed requests in a row (e.g. HTTP 5xx) end the run
                                   # pilot: refresh only the event dates whose 48-to-24 h window can be open now
        stage_caps = {"snapshot_a": 0, "snapshot_b": 18}                      # 3-day pilot: no Snapshot A; Snapshot B at most 18; news gets what the 50-attempt ceiling leaves
        if a.profile == "batch2":                                             # Phase 14 batch: the same hard 50-attempt ceiling, no retries, no paging; earnings payloads first, then Snapshot B, news, price bars
            stage_caps = {"snapshot_a": 0, "snapshot_b": 14, "earnings": 12, "ohlc": 3, "news": 17}
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
    if a.shared_key_attestation:
        os.environ[SK.ATTESTATION_ENV] = a.shared_key_attestation          # the same attestation is what the InfraFetcher choke point reads
    sk = SK.evaluate(now, a.shared_key_attestation)
    if live and sk["blocks_live"]:
        print("BLOCKED: %s - %s" % (sk["status"], sk["reason"]))
        return 7
    if live:
        tk = SC.inspect(ENV, old_fingerprint=a.require_rotated_from)
        if tk["source"] == "MISSING" or tk["shape_problems"] or tk["rotated_from_old"] is False or any("DIFFERENT" in n for n in tk["notes"]):
            print(SC.redact("BLOCKED: token configuration - source=%s problems=%s rotated=%s notes=%s" % (tk["source"], tk["shape_problems"], tk["rotated_from_old"], tk["notes"]), ENV))
            return 4
        os.environ["UW_MAX_INFLIGHT"] = "1"                                   # one request on the wire at a time (the provider allows 3 per token across every application)
        from dotenv import load_dotenv
        load_dotenv(ENV)
        import unusualwhales_service as uw
        if not uw.configured():
            print("BLOCKED: provider credential missing")
            return 4
    svc = build_service(a.store_dir, live=live, authorized=a.daily_limit or a.authorize_requests, now=now, retries=retries, tracking_days=a.calendar_days or 14, attempts_path=Path(a.state_dir) / "attempts.jsonl")
    kill_was_engaged = svc.store.kill_file.exists()
    if live and not kill_was_engaged:
        print("BLOCKED: the kill-switch file must exist before a live run (it is released only while the run works)")
        return 6
    if a.profile == "engine20":
        return engine20_profile(a, svc, now, live)
    job = execute(svc, now=now, state_dir=a.state_dir, live=live, authorized=a.daily_limit or a.authorize_requests, ceiling=a.ceiling, batch=a.batch, news_pages=news_pages, calendar_requests=a.calendar_requests, stage_caps=stage_caps, calendar_scope=scope, max_consecutive_failures=max_fail, outcome_events_fn=(lambda n: outcome_events(svc, n)) if a.profile == "batch2" else None, daily_limit=a.daily_limit)
    after = svc.clock.now() if live else now                                     # a live run reports AS OF ITS END: the start-of-run instant predates the refreshed calendar and everything the run saved (Batch #2 showed 0 events)
    ev_summary = record_events(svc, after, a.state_dir)
    preds = T.load_predictions(a.preds_store) if a.preds_store else []
    rep = status_report(svc, after, preds, a.tracking_dir)
    ES.write(Path(a.state_dir) / ES.STATUS_FILE, rep)
    print(SC.redact(json.dumps({"job": job["id"], "mode": job["mode"], "status": job["status"], "provider_requests": job.get("requests", 0), "preflight": job["preflight"], "shared_key": {"status": sk["status"], "blocks_live": sk["blocks_live"], "reason": sk["reason"]}, "events": rep["summary"], "event_records": ev_summary}, indent=1, default=str), ENV))
    return 0


if __name__ == "__main__":
    sys.exit(main())
