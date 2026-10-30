"""Offline readiness check of the saved live calendar store: capture windows, who is ready or blocked and why, what evidence is valid when. NO request, nothing is opened early.

    python readiness_today.py --store <saved live store with calendar observations> [--now ISO] [--out report.json]
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_live as L  # noqa: E402
from smart_move_routes import SECTOR_ETF  # noqa: E402
from smvshadow import calendar_confirm as cc, cutoff as co  # noqa: E402

UTC = timezone.utc


def et(t):
    return t.astimezone(co.ET).strftime("%a %d %b %H:%M %Z")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--now", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    now = datetime.fromisoformat(a.now) if a.now else datetime.now(UTC)
    svc = L.RefreshService(L.LiveConfig(mode="LIVE", store_dir=Path(a.store), refresh_enabled=False), None, L.SystemClock())
    pre = svc._calobs()
    rep = {"now_utc": now.isoformat(), "now_et": et(now), "symbols": len(pre)}
    last_obs = max(o.observed_at for ps in pre.values() for o, _ in ps)
    rep["calendar_observed_utc"] = last_obs.isoformat()
    rep["calendar_age_hours_now"] = round((now - last_obs).total_seconds() / 3600, 1)
    groups = defaultdict(lambda: {"events": 0, "vendor_confirmed": 0, "single_vendor": 0, "other_blocked": 0})
    blocked = Counter()
    ready, rows = [], []
    for sym, pairs in pre.items():
        obs = [o for o, _ in pairs]
        raw = [r for _, r in pairs]
        ev_obs = cc.evaluate(sym, obs, last_obs + timedelta(seconds=1), last_obs.astimezone(co.ET).date())     # judged at the moment it was observed
        if ev_obs["event_date"] is None:
            blocked["no usable event date (" + ",".join(ev_obs["no_trade_reasons"]) + ")"] += 1
            continue
        kind = ev_obs["kind"]
        d = ev_obs["event_date"]
        if kind not in ("BMO", "AMC"):
            plan = None
        else:
            plan = co.plan_event(d, kind)
        uw = next((r for r in raw if r["source"] == "UW_CALENDAR"), {})
        cap = 0.0
        try:
            cap = max(float(r["marketcap"]) for r in raw if r.get("marketcap"))
        except ValueError:
            pass
        key = "%s %s" % (d, kind)
        g = groups[key]
        g["events"] += 1
        if plan:
            g["window_open_et"], g["window_close_et"] = et(plan["window_start_utc"]), et(plan["cutoff_utc"])
            g["state_now"] = "CLOSED (cutoff passed)" if now > plan["cutoff_utc"] else "OPEN" if now >= plan["window_start_utc"] else "opens in %.1f h" % ((plan["window_start_utc"] - now).total_seconds() / 3600)
        status = ev_obs["status"]
        sector = uw.get("sector")
        etf = SECTOR_ETF.get(sector)
        if status in cc.ELIGIBLE:
            g["vendor_confirmed"] += 1
            if plan and now <= plan["cutoff_utc"]:
                ready.append({"symbol": sym, "date": str(d), "kind": kind, "marketcap": cap, "sector": sector, "etf": etf, "window_open_et": et(plan["window_start_utc"]), "window_close_et": et(plan["cutoff_utc"])})
            elif plan:
                blocked["window already closed (event today or earlier)"] += 1
        else:
            why = ev_obs["no_trade_reasons"] or ["ESTIMATED"]
            for w in why:
                blocked[w] += 1
            g["single_vendor" if "SINGLE_VENDOR" in why else "other_blocked"] += 1
    rep["event_groups"] = dict(sorted(groups.items()))
    rep["blocked_reason_counts"] = dict(blocked.most_common())
    ready.sort(key=lambda r: -r["marketcap"])
    rep["ready_events_total"] = len(ready)
    rep["ready_by_window"] = dict(Counter("%s %s opens %s" % (r["date"], r["kind"], r["window_open_et"]) for r in ready))
    rep["ready_without_sector"] = sum(1 for r in ready if not r["etf"])
    rep["top_ready_by_marketcap"] = [{k: r[k] for k in ("symbol", "date", "kind", "sector", "window_open_et", "window_close_et")} | {"marketcap_usd": round(r["marketcap"])} for r in ready[:15]]
    # which of those names would be the 10 chosen the moment the first AMC window opens (Saturday 17:00 ET) if the calendar were unchanged
    t_sat = datetime(2026, 10, 31, 21, 5, tzinfo=UTC)
    open_sat = [r for r in ready if co.plan_event(datetime.fromisoformat(r["date"]).date(), r["kind"])["window_start_utc"] <= t_sat <= co.plan_event(datetime.fromisoformat(r["date"]).date(), r["kind"])["cutoff_utc"] and r["etf"]]
    rep["preview_selection_sat_17_05_et"] = [{"symbol": r["symbol"], "kind": r["kind"], "date": r["date"], "sector": r["sector"], "marketcap_usd": round(r["marketcap"])} for r in open_sat[:10]]
    rep["open_at_that_moment"] = len(open_sat)
    # validity of the evidence already stored (the first live run) at different moments
    ev = svc.store.read("evidence")
    checks = []
    plan_aapl = co.plan_event(datetime(2026, 11, 2).date(), "AMC")
    for label, t in (("now", now), ("Fri 17:00 ET (after the 16:45 usable time)", datetime(2026, 10, 30, 21, 0, tzinfo=UTC)), ("Sat 17:05 ET (first AMC window open)", t_sat)):
        row = {"moment": label, "utc": t.isoformat(), "expected_session": str(L.expected_session(t)), "window_AAPL_open": plan_aapl["window_start_utc"] <= t <= plan_aapl["cutoff_utc"], "records": []}
        for r in ev:
            d = svc._derive(r)
            row["records"].append({"kind": r["kind"], "symbol": r["symbol"], "retrieved_et": et(datetime.fromisoformat(r["retrieved_at"])), "fresh_for_session": L.rec_fresh(d, r["kind"], t, svc.cfg),
                                   "retrieved_inside_window": plan_aapl["window_start_utc"] <= datetime.fromisoformat(r["retrieved_at"]) <= plan_aapl["cutoff_utc"]})
        checks.append(row)
    rep["stored_evidence_validity"] = checks
    # earliest moment candles can be valid for the weekend: after Friday's close + 45 minutes
    fri = co._mc().session_close(datetime(2026, 10, 30).date()) if hasattr(co._mc(), "session_close") else None
    rep["friday_session_usable_from_et"] = "16:45 ET (close 16:00 + 45 minutes)"
    out = json.dumps(rep, indent=1, default=str)
    if a.out:
        Path(a.out).write_text(out, encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
