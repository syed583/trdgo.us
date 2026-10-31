"""SUPERVISED WINDOW COLLECTION: one run, at most 60 read-only requests in total (retries and the calendar refresh included), at most 10 stocks, only while their capture windows are open.

    python supervised_window_run.py --store-dir <new empty folder> [--execute] [--precheck-store <folder with saved calendar observations>]

Order of events:
  0. PRECHECK, no request: from calendar observations already saved, are any events' capture windows open now? If not, stop (nothing sent).
  1. calendar refresh (Unusual Whales for the next trading days, one Finviz export, one Finnhub window) through the governed service.
  2. SELECTION (outcome-blind): among events that are VENDOR_CONFIRMED, have known timing and an OPEN window now, the 10 with the largest market cap (a sector is required so F8 is complete).
  3. evidence for exactly those names (shared SPY and sector ETF candles first, then per name: daily candles, earnings history, volatility context, iv-rank) until the budget is spent.
  4. scoring with the frozen engine, predictions stored (append-only) with timestamps and evidence hashes; report written.
  5. the kill switch is engaged again (always, also on error).
The governor's authorization for this process is the cap itself (60), so retries count and nothing can exceed it; a 429/401 stops everything.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
ENV = Path(r"C:\Users\initi\Downloads\US-STOCKS\backend\.env")
CAP_TOTAL, MAX_NAMES = 60, 10


def collect(svc, *, max_names=MAX_NAMES, total_cap=CAP_TOTAL):
    """The whole run on an already built RefreshService (live or simulated). Returns the report dict. Never sends more than total_cap requests."""
    import smart_move_live as L
    from smvshadow import calendar_confirm as cc, cutoff as co
    now = svc.clock.now()
    report = {"started_utc": now.isoformat(), "cap_total": total_cap, "max_names": max_names, "mode": svc.cfg.mode}
    # 0. precheck from saved observations: no request
    pre = svc._calobs()
    open_now, upcoming, stale_near = [], [], []
    today = now.astimezone(co.ET).date()
    for sym, pairs in pre.items():
        ev = cc.evaluate(sym, [o for o, _ in pairs], now, today)
        if ev["event_date"] and ev["kind"] in ("BMO", "AMC"):
            plan = co.plan_event(ev["event_date"], ev["kind"])
            if plan["window_start_utc"] <= now <= plan["cutoff_utc"]:
                open_now.append(sym)
            elif plan["window_start_utc"] > now:
                upcoming.append(plan["window_start_utc"])
        elif ev["event_date"] and ev["status"] == cc.STALE and 0 <= (ev["event_date"] - today).days <= 2:
            stale_near.append(sym)           # a saved row too old to count: its timing is unknown until the calendar is refreshed, so it says nothing about the window
    report["precheck_events_with_open_window"] = len(open_now)
    report["precheck_stale_events_within_two_days"] = len(stale_near)
    if not open_now and not stale_near:
        report.update(status="WINDOWS_NOT_OPEN", requests_sent=0, next_window_opens_utc=min(upcoming).isoformat() if upcoming else None)
        return report
    # Saved calendar rows that are too old cannot show an open window; the run's own calendar refresh (inside the same cap) decides. Nothing else is sent unless an open window exists after it.
    svc.cfg.authorized_daily = total_cap
    svc.gov.authorized_daily = total_cap
    svc.cfg.refresh_enabled = True
    svc.gov.enabled = True
    # 1. calendar refresh
    svc.cfg.calendar_days_ahead = 2                  # an open window belongs to an event at most about two days away; later days only waste requests
    n_days = len(svc._trading_days(svc.clock.now()))
    budget = [min(12, total_cap, n_days * 2 + 1 + 2)]   # per trading day premarket + afterhours, one second page, one Finviz export, one Finnhub window
    svc.log("WINDOW_RUN_CALENDAR_START", budget=budget[0], trading_days=n_days)
    svc.refresh_calendar(budget)
    # 2. selection among events whose window is open now
    now = svc.clock.now()
    events = [e for e in svc.eligible_events(now) if e["etf"]]
    events.sort(key=lambda e: (-e["marketcap"], e["symbol"]))
    chosen = events[:max_names]
    report["eligible_open_window_events_after_calendar"] = len(events)
    if not events:                                    # the refreshed calendar shows no vendor-confirmed event with an open window: stop; no evidence request is made
        report.update(status="WINDOWS_NOT_OPEN", selected=[], results=[], requests_sent=len(svc.store.read("requests")), attempts_total=svc.gov.used_day.get(svc.clock.now().astimezone(L.gv.ET).strftime("%Y-%m-%d"), 0),
                      requests_by_provider=svc.feed()["requests_today_by_provider"], failed_attempts=sum(1 for e in svc.store.read("ledger") if e["type"] == "PROVIDER_FAILURE"),
                      finished_utc=svc.clock.now().isoformat())
        return report
    report["selected"] = [{"symbol": e["symbol"], "kind": e["kind"], "event_date": str(e["event_date"]), "marketcap": e["marketcap"], "sector": e["sector"], "window_cutoff_utc": e["plan"]["cutoff_utc"].isoformat()} for e in chosen]
    svc.focus = {e["symbol"] for e in chosen}
    # 3. evidence for exactly these names, within what is left of the cap (every attempt counts)
    left = max(0, total_cap - svc.gov.used_day.get(svc.clock.now().astimezone(L.gv.ET).strftime("%Y-%m-%d"), 0))
    svc.log("WINDOW_RUN_EVIDENCE_START", budget=left, names=sorted(svc.focus))
    svc.refresh_evidence([left])
    # 4. scoring and persistence
    rows = [r for r in L.LiveScorer(svc).score_all(svc.clock.now(), persist=True) if r["symbol"] in svc.focus]
    report["requests_sent"] = len(svc.store.read("requests"))
    report["failed_attempts"] = sum(1 for e in svc.store.read("ledger") if e["type"] == "PROVIDER_FAILURE")
    report["attempts_total"] = svc.gov.used_day.get(svc.clock.now().astimezone(L.gv.ET).strftime("%Y-%m-%d"), 0)
    report["requests_by_provider"] = svc.feed()["requests_today_by_provider"]
    report["denials"] = [e["type"] for e in svc.store.read("ledger") if e["type"].startswith(("DENIED", "STOP"))]
    report["status"] = "OK" if report["attempts_total"] <= total_cap else "OVER_CAP"
    report["results"] = [{"symbol": r["symbol"], "company": r["company"], "earnings_date": r["earnings_date"], "kind": r["kind"], "calendar_status": r["calendar"]["status_now"],
                          "available_points": r["available_points"], "missing_factors": [m["factor"] + ":" + m["reason"] for m in r["missing_factors"]], "blocked_by": r["blocked_by"],
                          "continuation": {k: r["engine"]["continuation"][k] for k in ("signal", "bull", "bear", "available", "reason_codes")},
                          "contrarian": {k: r["engine"]["contrarian"][k] for k in ("signal", "bull", "bear", "available", "reason_codes")}, "evidence": r["evidence"],
                          "capture_ts": r.get("capture_ts"),
                          "hours_to_cutoff": round((datetime.fromisoformat(r["plan"]["cutoff_utc"]) - svc.clock.now()).total_seconds() / 3600, 1) if r.get("plan") else None} for r in rows]
    report["finished_utc"] = svc.clock.now().isoformat()
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store-dir", required=True)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--precheck-store", default="", help="folder holding saved calendar observations to check windows against before any request")
    a = ap.parse_args()
    store = Path(a.store_dir)
    if store.exists() and any(store.iterdir()):
        sys.exit("STOPPED: store folder is not empty (never reused)")
    from dotenv import load_dotenv
    load_dotenv(ENV)
    import smart_move_live as L
    import unusualwhales_service as uw
    import finnhub_service as fh
    import finviz_service as fz
    creds = {"UNUSUAL_WHALES": uw.configured(), "FINNHUB": bool(fh._key()), "FINVIZ": fz.configured()}
    print("credentials present (values never shown):", creds)
    cfg = L.LiveConfig(mode="LIVE", store_dir=store, refresh_enabled=False, authorized_daily=0, spacing_s=2.0)
    svc = L.RefreshService(cfg, L.InfraFetcher() if a.execute else None, L.SystemClock())
    if a.precheck_store:                                   # saved observations from the earlier calendar run, read without any request
        pre = L.RefreshService(L.LiveConfig(mode="LIVE", store_dir=Path(a.precheck_store), refresh_enabled=False), None, L.SystemClock())
        for r in pre.store.read("cal_obs"):
            svc.store.append("cal_obs", r)
    if not a.execute:
        rep = collect(svc)
        print("dry run (no request is sent without --execute):", json.dumps({k: rep[k] for k in rep if k != "results"}, default=str))
        return
    if not all(creds.values()):
        sys.exit("STOPPED: a provider credential is missing")
    try:
        rep = collect(svc)
    finally:
        svc.engage_kill_switch("supervised window run finished")
    (store / "window_run_report.json").write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    print(json.dumps(rep, indent=1, default=str))
    print("kill switch engaged again; requests sent:", len(svc.store.read("requests")))


if __name__ == "__main__":
    main()
