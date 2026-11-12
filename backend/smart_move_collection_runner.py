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
import smart_move_calendar_tiers as CT  # noqa: E402
import smart_move_calendar_changes as CC  # noqa: E402
import smart_move_universe as UNI  # noqa: E402
import smart_move_secret_check as SC  # noqa: E402
import smart_move_shared_key_gate as SK  # noqa: E402
from smvshadow import governor as gv  # noqa: E402

ENV = Path(os.environ.get("SMART_MOVE_ENV_FILE") or r"C:\Users\initi\Downloads\US-STOCKS\backend\.env")         # on the server the wrapper points this at the application's own backend\.env
UTC = timezone.utc


class NoNetworkFetcher(L.Fetcher):
    """Dry-run fetcher: any attempt to fetch is a programming error."""
    mode = "SIMULATION"

    def fetch(self, kind, symbol, params):
        raise AssertionError("dry run must never call the provider (%s)" % kind)


PILOT_MAX = int(os.environ.get("SMART_MOVE_RUN_MAX") or 50)             # internal governor ceilings: the defaults are the approved production limits; raising them is an explicit owner decision (environment variable)
DAILY_LIMIT_MAX = int(os.environ.get("SMART_MOVE_DAILY_MAX") or 100)


def build_service(store_dir, *, live: bool, authorized: int, now=None, retries: int = 1, tracking_days: int = 14, attempts_path=None, window_aware: bool = False):
    cfg = L.LiveConfig(mode="LIVE" if live else "SIMULATION", store_dir=Path(store_dir), refresh_enabled=live, authorized_daily=authorized if live else 0, spacing_s=2.0 if live else 0.0,
                       max_retries=retries, tracking_days_ahead=tracking_days, window_aware_evidence=window_aware)
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
    if persistent_live_flag():
        why.append("SMART_MOVE_COLLECTION_LIVE is set at User or Machine scope (it must exist only inside the one authorised process)")
    return why


def persistent_live_flag() -> bool:
    """True when the live flag is stored in the Windows User or Machine environment (registry), which would arm every
    process on the box. The scheduled scripts refuse that too; this makes a direct runner invocation refuse as well."""
    try:
        import winreg
    except ImportError:                                                      # not Windows: nothing persistent to inspect
        return False
    for hive, path in ((winreg.HKEY_CURRENT_USER, r"Environment"),
                       (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
        try:
            with winreg.OpenKey(hive, path) as k:
                v, _ = winreg.QueryValueEx(k, "SMART_MOVE_COLLECTION_LIVE")
                if str(v).strip():
                    return True
        except OSError:
            continue
    return False


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
    recs = ER.records_from_saved(svc.cal_obs(), now, {})
    return [{"symbol": r["symbol"], "event_date": r["event_date"], "session": r["session"]} for r in recs if r["event_date"] and r["session"] in ("BMO", "AMC")
            and r["event_date"] < now.date().isoformat()]


def tiered_refresh(svc, a, plan, st, cal_dir) -> dict:
    """P1 live step: refresh exactly the planned slots through the governed path (same request cap, spacing, 401/403/429 stop and kill switch as every calendar refresh), record each slot's result in calendar_slots.json,
    append the changes it reveals (new / rescheduled / missing / removed) to event_changes.jsonl, and report. ok=False only when nothing could be refreshed or the provider stopped; slots the cap left stale are reported
    as `deferred` and `t0_complete`, never treated as fresh."""
    now = svc.clock.now()
    out = {"checked_at": now.isoformat(), "tiered": True, "requests": 0, "ok": True, "reason": None, "slots": [c["key"] for c in plan["refresh"]], "deferred": [c["key"] for c in plan["deferred"]]}
    stopped = lambda: bool(svc.gov.stopped_until and svc.clock.now() < svc.gov.stopped_until)  # noqa: E731
    if not plan["refresh"]:
        return out
    if stopped():
        return dict(out, ok=False, reason="PROVIDER_STOP")
    per_request = 1 + int(getattr(svc.cfg, "max_retries", 0) or 0)                       # one calendar request can cost this many attempts when retries are on
    att0 = E20.attempts_of(svc)
    budget = [min(plan["est_requests"], a.ceiling) // per_request]                          # the hard per-run maximum is on ATTEMPTS: size the request budget so that even the worst case stays inside it
    done = svc.refresh_calendar(budget, dates=[(datetime.fromisoformat(c["date"]).date(), c["side"]) for c in plan["refresh"]])
    t = svc.clock.now()
    CT.record_refresh(st, svc.last_slot_log, t)
    changes = CC.track(st, svc.last_slot_log, t)
    CC.append_changes(cal_dir, changes)
    CT.save_state(cal_dir, st)
    comp = CT.completeness(st, t, a.calendar_horizon_days)
    out.update(requests=done, attempts=E20.attempts_of(svc) - att0, changes={k: sum(1 for c in changes if c["change"] == k) for k in ("NEW", "RESCHEDULED", "MISSING", "REMOVED", "REAPPEARED")}, t0_complete=comp["t0_complete"], t0_stale=comp["t0_open_stale"] + comp["t0_next_stale"], slots_fresh=comp["slots_fresh"], slots_total=comp["slots_total"])
    if stopped():
        out.update(ok=False, reason="PROVIDER_STOP_DURING_REFRESH")
    elif done == 0:
        out.update(ok=False, reason="REFRESH_SENT_NOTHING_USEFUL")
    return out


def engine20_profile(a, svc, now, live: bool) -> int:
    """Collect the frozen engine's evidence for a selected set of stocks. Dry run: select, plan, print, write the plan; zero requests. Live (separately gated like every live run): the kill file is released only for the run
    and always re-engaged; a calendar refresh happens only if the calendar is older than 5 h and is counted in the same ceiling."""
    for k, v in (("calendar_horizon_days", 0), ("calendar_state_dir", None), ("abort_on_stale_bars", False)):       # callers that build their own argument namespace (older tests, tools) keep working unchanged
        if not hasattr(a, k):
            setattr(a, k, v)
    d = Path(a.state_dir)
    d.mkdir(parents=True, exist_ok=True)
    caps = {}
    for o in svc.cal_obs():
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
        pending = {x["symbol"] for x in svc.plan_evidence(t) if x.get("symbol") and x["kind"] in kinds} if a.select_policy in ("deadline", "deadline_priority") else None
        selected = E20.select_top(rows, caps, t, policy=a.select_policy, pending=pending, audit=audit, n=a.select_top, event_date=a.select_date, session=a.select_session, only=[x.strip().upper() for x in a.select_symbols.split(",") if x.strip()] if a.select_symbols else None)
        return selected

    selected = choose(now)
    tiered = bool(a.calendar_horizon_days)
    cal_dir = Path(a.calendar_state_dir or a.publish_dir or a.state_dir)
    slot_state = CT.load_state(cal_dir) if tiered else None
    cal_plan = None
    if tiered:                                                              # P1: per-slot, tiered, budget-aware (the evidence this run needs is estimated first so far days only get what is left)
        prelim = E20.plan_tasks(svc, selected, now, budget=a.ceiling, with_f7=a.with_f7)
        cal_plan = CT.plan_refresh(now, slot_state, horizon_days=a.calendar_horizon_days, run_ceiling=a.ceiling, budget_left=a.ceiling, evidence_demand=prelim["attempts_planned"])
        cal_needed, cal_est = cal_plan["needed"], cal_plan["est_requests"]
    else:
        cal_needed = G.needs_refresh(svc.store, now)
        cal_slots = J.horizon_calendar_slots(now, a.calendar_days_ahead) if a.calendar_days_ahead is not None else J.useful_calendar_slots(now)
        cal_est = (len(cal_slots) if a.calendar_days_ahead is not None else 4) if cal_needed else 0          # unchanged default: 4 requests for the open windows only
    plan = E20.plan_tasks(svc, selected, now, budget=a.ceiling - cal_est, with_f7=a.with_f7)
    summary = {"mode": "LIVE" if live else "DRY_RUN", "profile": "engine20", "as_of": now.isoformat(), "selected": selected, "calendar_refresh_needed": cal_needed, "calendar_attempts_reserved": cal_est,
               "attempts_planned_for_evidence": plan["attempts_planned"], "total_attempts_planned": plan["attempts_planned"] + cal_est, "ceiling": a.ceiling, "spare": plan["spare"],
               "stocks_fully_funded": plan["stocks_fully_funded"], "stocks_already_fresh": plan["already_fresh_stocks"], "unfunded": [(t["kind"], t["symbol"]) for t in plan["unfunded"]],
               "tasks": [(t["kind"], t["symbol"], t["why"]) for t in plan["funded"]], "provider_requests": 0}
    if tiered:
        summary["calendar_tiered"] = {"horizon_days": a.calendar_horizon_days, "state_dir": str(cal_dir), "refresh_slots": [(c["key"], c["tier"], c["why"], c["est_requests"]) for c in cal_plan["refresh"]],
                                      "deferred_slots": [(c["key"], c["tier"], c["deferred_because"]) for c in cal_plan["deferred"]], "estimated_requests": cal_plan["est_requests"], "cap": cal_plan["cap"],
                                      "completeness_before": CT.completeness(slot_state, now, a.calendar_horizon_days)}
    (d / "engine20_plan.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    (d / "engine20_selection.json").write_text(json.dumps({"policy": a.select_policy, "slots": a.select_top, "as_of": now.isoformat(), "decisions": audit}, indent=1, default=str), encoding="utf-8")
    limits = {"run_limit": a.ceiling, "daily_limit": a.daily_limit or a.authorize_requests or DAILY_LIMIT_MAX}
    if not live:
        if a.publish_dir:
            import smart_move_live_publisher as PUB                                # a dry run shows itself on the dashboard (status only) and publishes NO prediction
            PUB.publish(svc, now, a.publish_dir, scan_id=d.name, mode="DRY_RUN", scan_ok=True, run={"started_at": now.isoformat(), "requests_attempted": 0, "planned": summary["total_attempts_planned"]},
                        limits=limits, unfunded=len(summary["unfunded"]))
        if tiered and a.publish_dir:
            UNI.write(a.publish_dir, UNI.build(svc, now, horizon_days=a.calendar_horizon_days, slot_state=slot_state, funded_symbols={t["symbol"] for t in plan["funded"]}, selected_symbols={x["symbol"] for x in selected},
                                              ledger_path=Path(a.publish_dir) / UNI.PUB.LEDGER))
        print(json.dumps({k: v for k, v in summary.items() if k != "tasks"}, indent=1, default=str))
        return 0
    svc.gov.authorized_daily, svc.gov.enabled = (a.daily_limit or a.authorize_requests), True
    try:
        svc.store.kill_file.unlink()                                          # released INSIDE the guarded block: whatever happens next, `finally` re-engages it
        used = 0
        att_start = E20.attempts_of(svc)
        if cal_needed:
            g = tiered_refresh(svc, a, cal_plan, slot_state, cal_dir) if tiered else G.ensure_fresh(svc, budget_left=a.ceiling, stopped=lambda: bool(svc.gov.stopped_until and svc.clock.now() < svc.gov.stopped_until), max_requests=cal_est, dates=(J.horizon_calendar_slots(svc.clock.now(), a.calendar_days_ahead) if a.calendar_days_ahead is not None else J.useful_calendar_slots(svc.clock.now())))
            used = max(g.get("requests", 0), E20.attempts_of(svc) - att_start)                 # attempts (successful, failed, retried), not only the successful ones: the evidence budget is what the hard maximum leaves
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
        res = E20.run(svc, plan["funded"], now=svc.clock.now(), ceiling=a.ceiling - used, state_dir=d, stopped=lambda: bool(svc.gov.stopped_until and svc.clock.now() < svc.gov.stopped_until), max_consecutive_failures=3, abort_on_stale_bars=bool(a.abort_on_stale_bars or a.calendar_horizon_days))
        rep = E20.report(svc, selected, svc.clock.now())
        (d / "engine20_report.json").write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
        print(json.dumps({"requests_attempted": res["attempts"] + used, "ok": res["ok"], "failed": res["failed"], "halted": res["halted"], "counts": rep["counts"]}, indent=1))
        if a.publish_dir:
            import smart_move_live_publisher as PUB
            try:
                st = PUB.publish(svc, svc.clock.now(), a.publish_dir, scan_id=d.name, mode="LIVE", scan_ok=E20.scan_ok_of(res),
                                 run={"started_at": now.isoformat(), "finished_at": svc.clock.now().isoformat(), "requests_attempted": res["attempts"] + used, "ok": res["ok"], "failed": res["failed"], "halted": res["halted"]},
                                 limits=limits, unfunded=len(summary["unfunded"]))
                print(json.dumps({"published": st["events"]}, default=str))
                if tiered:
                    UNI.write(a.publish_dir, UNI.build(svc, svc.clock.now(), horizon_days=a.calendar_horizon_days, slot_state=slot_state, funded_symbols={t["symbol"] for t in plan["funded"]}, selected_symbols={x["symbol"] for x in selected},
                                                      ledger_path=Path(a.publish_dir) / UNI.PUB.LEDGER))
            except PUB.LedgerError as e:
                print("PUBLISH BLOCKED: the prediction ledger failed its integrity check:", e)
                return 8                                                       # 8 = ledger; 7 is the shared-key gate, 6 the live gates, 4 the token configuration
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
    recs = ER.records_from_saved(svc.cal_obs(), now, payloads)
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
    ap.add_argument("--profile", choices=("standard", "pilot50", "batch2", "engine20", "monday_bmo_friday"), default="standard", help="pilot50: hard 50-attempt ceiling, no retries, no news paging, 3-day calendar, calendar capped at 14 requests; monday_bmo_friday: the SEPARATE Friday-window variant for Monday-BMO events (own ledger folder under --publish-dir; frozen model untouched)")
    ap.add_argument("--calendar-days", type=int, default=None)
    ap.add_argument("--calendar-requests", type=int, default=None)
    ap.add_argument("--select-top", type=int, default=20, help="engine20 profile: how many stocks (vendor-confirmed, window open, ranked by market cap)")
    ap.add_argument("--select-symbols", default=None, help="engine20 profile: pin the owner-approved list (comma separated); each symbol must still pass the eligibility checks, none is substituted")
    ap.add_argument("--select-date", default=None, help="engine20 profile: only events on this date, e.g. 2026-11-04")
    ap.add_argument("--select-session", default=None, choices=("BMO", "AMC"), help="engine20 profile: only this announcement session")
    ap.add_argument("--select-policy", choices=("cap", "deadline", "deadline_priority"), default="cap", help="engine20 profile: cap = the n largest market caps (default, unchanged); deadline = stocks that still NEED evidence, closest prediction cutoff first (already-collected stocks never take a slot); deadline_priority = like deadline, but when the budget cannot cover every open event, cutoffs closer than 4 h go strictly by deadline and the rest by market-cap proxy tier then deadline (prioritisation only: deferred events are recorded and stay eligible)")
    ap.add_argument("--publish-dir", default=None, help="engine20 profile: folder for the live prediction ledger and status file read by the dashboard (append-only; a dry run writes status only)")
    ap.add_argument("--calendar-days-ahead", type=int, default=None, help="engine20 profile: keep the calendar this many days forward (both sessions of every trading day, 2 requests per day) instead of only the open windows")
    ap.add_argument("--calendar-horizon-days", type=int, default=0, help="engine20 profile (P1): rolling horizon of 3..30 days with a TIERED calendar refresh (open and next-opening windows every 5 h, far days daily with leftover budget, per slot) instead of --calendar-days-ahead; 0 = off (unchanged behaviour). Also turns on the stale-bar early abort")
    ap.add_argument("--calendar-state-dir", default=None, help="engine20 profile (P1): where calendar_slots.json and event_changes.jsonl persist between runs (default: --publish-dir, else --state-dir)")
    ap.add_argument("--abort-on-stale-bars", action="store_true", help="engine20 profile (P1): stop the run when the first (SPY) price payload is older than the last completed session instead of spending the budget on stale evidence")
    ap.add_argument("--window-aware-evidence", action="store_true", help="engine20 profile (F7 fix): also re-request evidence that was retrieved before the event's prediction window opened (the frozen scorer rejects it). Implied by --calendar-horizon-days; off by default = the installed behaviour")
    ap.add_argument("--with-f7", action="store_true", help="engine20 profile: also request volatility context and IV rank (2 more requests per stock)")
    ap.add_argument("--require-rotated-from", default=None, help="8-hex fingerprint of the OLD token; a live run is BLOCKED while the active token still has this fingerprint")
    ap.add_argument("--shared-key-attestation", default=None, help="owner acknowledgement file (see smart_move_shared_key_gate); without a valid one a live run stays BLOCKED")
    a = ap.parse_args(argv)
    if a.calendar_horizon_days and (a.profile not in ("engine20", "monday_bmo_friday") or not 3 <= a.calendar_horizon_days <= CT.HORIZON_MAX or a.calendar_days_ahead is not None):
        print("BLOCKED: --calendar-horizon-days is for the engine20 and monday_bmo_friday profiles only, 3..%d, and cannot be combined with --calendar-days-ahead" % CT.HORIZON_MAX)
        return 6                                                      # the variant accepts the flag because the wrapper passes the shared settings file to every profile; it only turns on window-aware evidence there
    news_pages, retries, stage_caps, scope, max_fail = None, 1, None, "horizon", None
    PILOT_FAMILY = ("pilot50", "batch2", "engine20", "monday_bmo_friday")
    if a.profile not in PILOT_FAMILY:
        a.ceiling = J.DEFAULT_CEILING if a.ceiling is None else a.ceiling
    if a.daily_limit is not None and (a.profile not in PILOT_FAMILY or a.daily_limit > DAILY_LIMIT_MAX or a.daily_limit < a.authorize_requests):
        print("BLOCKED: --daily-limit is only for the pilot-family profiles, at most %d, and not below --authorize-requests" % DAILY_LIMIT_MAX)
        return 6
    if a.profile in PILOT_FAMILY:
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
    svc = build_service(a.store_dir, live=live, authorized=a.daily_limit or a.authorize_requests, now=now, retries=retries, tracking_days=a.calendar_days or 14, attempts_path=Path(a.state_dir) / "attempts.jsonl", window_aware=bool(getattr(a, "window_aware_evidence", False) or a.calendar_horizon_days))
    kill_was_engaged = svc.store.kill_file.exists()
    if live and not kill_was_engaged:
        print("BLOCKED: the kill-switch file must exist before a live run (it is released only while the run works)")
        return 6
    if a.profile == "engine20":
        return engine20_profile(a, svc, now, live)
    if a.profile == "monday_bmo_friday":
        import smart_move_monday_bmo as MB                                    # the separate Friday-window variant: own ledger folder, frozen model untouched
        return MB.profile(a, svc, now, live)
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
