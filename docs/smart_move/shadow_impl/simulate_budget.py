"""Offline simulation of the shared request governor next to the warmer: fake clock, fake provider, no request. Reports whether the shared hourly caps, the daily
authorization and the core-first priority hold, and what happens to events when capacity is short.

    python simulate_budget.py [--out results]
"""
import argparse
import json
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE / "tests")]
from fixtures import FakeProvider                                                # noqa: E402
from smvshadow import capture as cap, calendar_validation as cv, cutoff as co, governor as gv, scorer as sc, selection as sel   # noqa: E402
from smvshadow.runner import ShadowRunner                                       # noqa: E402
from smvshadow.store import PayloadStore                                         # noqa: E402
from test_e2e_rehearsal import EVENT, START, world                              # noqa: E402

UTC = timezone.utc


def simulate(tmp, *, warmer_live, warmer_off, authorized, n_events=250, core_cap=220, full_cap=30):
    clock = gv.FakeClock(START)

    def existing(ts):
        n = ts.astimezone(gv.ET).time()
        return warmer_live if gv.LIVE_START <= n < gv.LIVE_END else warmer_off
    g = gv.Governor(clock, authorized_daily=authorized, existing_usage_fn=existing)
    worlds = {"__default__": world("DEF", [4] * 6), "SPY": world("SPY", [0.5] * 6), "XLK": world("XLK", [0.5] * 6)}
    fetch = FakeProvider(worlds, clock.now)
    cands, infos = [], {}
    for i in range(n_events):
        kind = "BMO" if i % 2 == 0 else "AMC"
        sym = "E%03d" % i
        p = co.plan_event(EVENT, kind)
        cands.append(sel.Candidate(sym, EVENT, 1, kind, cv.CONFIRMED, cv.T_CONFIRMED, p["window_start_utc"], p["deadline_utc"]))
        infos[(sym, str(EVENT))] = {"kind": kind, "timing_status": cv.T_CONFIRMED, "version": 1, "plan": p, "sector_etf": "XLK"}
    plan = sel.schedule(cands, seed="sim", core_cap=core_cap, full_cap=full_cap)
    pre, sha = sc.load_prereg()
    r = ShadowRunner(g, PayloadStore(tmp), fetch, pre, sha)
    r.run(plan["assignments"], infos)
    live_hours = {h: n for h, n in g.used_hour.items() if gv.LIVE_START <= datetime.strptime(h, "%Y-%m-%d %H").time() < gv.LIVE_END}
    off_hours = {h: n for h, n in g.used_hour.items() if h not in live_hours}
    full_done = sum(1 for a in r.processed if a["tier"] == "FULL")
    return {"warmer_live_per_hour": warmer_live, "warmer_off_per_hour": warmer_off, "authorized_daily": authorized, "events_scheduled": len(plan["assignments"]),
            "events_not_scheduled_capacity": sum(1 for e in plan["ledger"] if e["type"] == "MISSED_CAPACITY"), "processed": len(r.processed), "missed_deadline": len(r.missed),
            "full_tier_scheduled": sum(1 for a in plan["assignments"] if a["tier"] == "FULL"), "full_tier_processed": full_done,
            "requests_total": sum(g.used_day.values()), "requests_per_et_day": dict(g.used_day), "max_day_requests": max(g.used_day.values(), default=0),
            "core_endpoint_calls": sum(1 for e, _ in fetch.calls if e in cap.CORE_ENDPOINTS), "full_extra_calls": sum(1 for e, _ in fetch.calls if e in cap.FULL_EXTRA_ENDPOINTS), "max_live_hour_requests": max(live_hours.values(), default=0), "max_off_hour_requests": max(off_hours.values(), default=0),
            "hours_used": sorted(g.used_hour.items()), "ledger_counts": dict(Counter(e["type"] for e in g.ledger)), "finished_at_utc": clock.now().isoformat(),
            "shared_hourly_limits_respected": all(n <= max(0, 400 - max(warmer_live, 300)) for n in live_hours.values()) and all(n <= max(0, 6000 - max(warmer_off, 1000)) for n in off_hours.values()),
            "daily_authorization_respected": max(g.used_day.values(), default=0) <= min(authorized, 1700)}


def main():
    import tempfile
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE / "results"))
    a = ap.parse_args()
    runs = {}
    for name, kw in (("warmer_idle_300_live", dict(warmer_live=300, warmer_off=1000, authorized=1700)),
                     ("warmer_at_cap_406_live", dict(warmer_live=406, warmer_off=1000, authorized=1700)),
                     ("warmer_at_cap_authorized_1000", dict(warmer_live=406, warmer_off=1000, authorized=1000)),
                     ("zero_authorization", dict(warmer_live=406, warmer_off=1000, authorized=0))):
        with tempfile.TemporaryDirectory() as t:
            runs[name] = simulate(t, **kw)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "budget_simulation.json").write_text(json.dumps({"note": "fake clock and fake provider; planning evidence only", "runs": runs}, indent=1, sort_keys=True), encoding="ascii")
    for k, v in runs.items():
        print(k, {x: v[x] for x in ("events_scheduled", "processed", "missed_deadline", "full_tier_processed", "core_endpoint_calls", "full_extra_calls", "requests_per_et_day", "max_live_hour_requests", "max_off_hour_requests",
                                    "shared_hourly_limits_respected", "daily_authorization_respected", "finished_at_utc")})


if __name__ == "__main__":
    main()
