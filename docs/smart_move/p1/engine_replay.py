"""Engine-in-the-loop replay of the Nov 9-13 peak week (P1 verification; read-only analysis, stub provider, temp folders, NO network, NO production access).

Unlike smart_move_capacity.simulate (pure arithmetic), this drives the REAL code path for every run: RefreshService + LiveScorer.score_all -> select_top (real tradability rule) -> plan_tasks -> E20.run (real governor, attempt accounting) -> classify.
What is synthetic: the events (from the owner-run calendar probe counts), the market data (flat bars) and ADV20 (assumed by proxy tier: tiers in `adv_pass` get 100M USD, others 10M USD, below the frozen 20M floor), and the calendar refresh cost
(not sent; each run is charged the tiered plan's cost: one request per open or about-to-open slot + 2 confirmations, all assumed stale).
Outputs per universe and policy: events, tradable, SERVED (both F3 and F5 collected inside the event's own window), VALID PREDICTIONS (the frozen engine answered BUY / SELL / genuine NO TRADE), and why the others are not valid.

    python docs/smart_move/p1/engine_replay.py [universe ...]        # default: all four
"""
import json
import math
import sys
import tempfile
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "docs" / "smart_move" / "shadow_impl"))
import smart_move_calendar_tiers as CT  # noqa: E402
import smart_move_capacity as CAP  # noqa: E402
import smart_move_engine20 as E20  # noqa: E402
import smart_move_live as L  # noqa: E402
from smvshadow import cutoff as co  # noqa: E402
from smvshadow import governor as gv  # noqa: E402

UTC = timezone.utc
COUNTS = json.loads((ROOT / "docs" / "smart_move" / "p1" / "cal30_probe_counts_2026-11-05.json").read_text(encoding="utf-8"))["counts"]
SECTORS = list(L.SECTOR_ETF)
PRICE = 100.0


def T(m, d, h):
    return datetime(2026, m, d, h, tzinfo=UTC)


WEEKEND_HEAVY = [T(11, 7, 22), T(11, 8, 4), T(11, 8, 13), T(11, 8, 20), T(11, 9, 13), T(11, 9, 22), T(11, 10, 4), T(11, 10, 13), T(11, 10, 22), T(11, 11, 4), T(11, 11, 13), T(11, 11, 22), T(11, 12, 13), T(11, 12, 22)]


class Provider(L.Fetcher):
    mode = "SIMULATION"

    def __init__(self, clock, volume_by_symbol):
        self.clock, self.vol, self.calls, self.log = clock, volume_by_symbol, 0, []

    def fetch(self, kind, symbol, params):
        self.calls += 1
        self.log.append((kind, symbol))
        if kind == "ohlc":                                                                                    # 3Y of weekday bars with a symbol-specific wave so reactions, trends and relative strength are real numbers
            last = L.expected_session(self.clock.now())
            vol = self.vol.get(symbol, 1_000_000)
            off = sum(map(ord, symbol)) % 17
            rows, d, i = [], last, 0
            while len(rows) < 800:
                if d.weekday() < 5:
                    c = PRICE + 6 * math.sin((i + off) / 8.0) + 0.02 * (800 - i) * (1 if off % 2 else -1)
                    rows.append({"date": d.isoformat(), "open": "%.2f" % c, "high": "%.2f" % (c + 1), "low": "%.2f" % (c - 1), "close": "%.2f" % c, "volume": vol, "total_volume": vol, "market_time": "r"})
                    i += 1
                d -= timedelta(days=1)
            return json.dumps({"data": sorted(rows, key=lambda r: r["date"])}).encode()
        if kind == "earnings":                                                                                # eight earlier quarters, premarket, on weekdays that have bars
            out, d = [], date(2026, 7, 29)
            for q in range(8):
                while d.weekday() >= 4:
                    d -= timedelta(days=1)
                out.append({"report_date": d.isoformat(), "report_time": "premarket", "ending_fiscal_quarter": (d - timedelta(days=30)).isoformat(), "post_earnings_move_1d": "0.02"})
                d -= timedelta(days=91)
            return json.dumps({"data": out}).encode()
        raise AssertionError(kind)


def replay(universe: str, policy: str, runs=WEEKEND_HEAVY, per_run=50, per_day=100, adv_pass=("A", "B", "C"), week=("2026-11-09", "2026-11-13"), window_aware=False) -> dict:
    """window_aware=False is the installed planner (SPY / sector-ETF / own evidence counted fresh by session only). window_aware=True is the F7 fix in the real code (LiveConfig.window_aware_evidence): evidence retrieved before the
    event's prediction window opened is re-requested, as the frozen scorer requires (RETRIEVED_BEFORE_WINDOW). Same synthetic assumptions for both."""
    tmp = Path(tempfile.mkdtemp())
    counts = {d: v for d, v in COUNTS.items() if week[0] <= d <= week[1]}
    events = CAP.events_from_counts(counts, universe)
    sector = {e["symbol"]: SECTORS[i % len(SECTORS)] for i, e in enumerate(events)}
    vol = {e["symbol"]: (1_000_000 if e["tier"] in adv_pass else 100_000) for e in events}              # ADV20 = 100M vs 10M USD against the frozen 20M floor
    clock = gv.FakeClock(runs[0])
    prov = Provider(clock, vol)
    cfg = L.LiveConfig(mode="SIMULATION", store_dir=tmp / "store", refresh_enabled=True, authorized_daily=per_day, spacing_s=0.0, max_retries=0, window_aware_evidence=window_aware)
    svc = L.RefreshService(cfg, prov, clock)
    caps = {e["symbol"]: e["cap"] for e in events}
    last_result, window_of = {}, {}
    for e in events:
        p = co.plan_event(e["event_date"], e["session"])
        window_of[e["symbol"]] = (p["window_start_utc"], p["cutoff_utc"])
    added, run_log = set(), []
    for t in sorted(runs):
        clock.t = t
        et_day = t.astimezone(co.ET).date().isoformat()
        left = min(per_run, per_day - svc.gov.used_day.get(et_day, 0))
        slots = [x for x in CT.horizon_slots(t, CT.HORIZON_MAX) if CT.classify(x, t)["tier"] in ("T0_OPEN", "T0_NEXT")]
        open_ev = [e for e in events if window_of[e["symbol"]][0] <= t <= window_of[e["symbol"]][1]]
        if left <= 0 or not open_ev:
            run_log.append({"at": t.isoformat(), "attempts": 0, "skipped": True})
            continue
        cal_cost = min(left, len(slots) + CT.CONFIRM_RESERVE)                                              # charged, not sent
        svc.gov.used_day[et_day] = svc.gov.used_day.get(et_day, 0) + cal_cost
        left -= cal_cost
        for e in open_ev:                                                                                    # the refreshed calendar: both vendors, observed one hour ago
            for r in ({"source": "UW_CALENDAR", "label": "premarket" if e["session"] == "BMO" else "afterhours", "vendor_flag": "company", "clock": None},
                      {"source": "FINNHUB", "label": e["session"].lower(), "vendor_flag": None, "clock": None}):
                svc.store.append("cal_obs", dict(r, symbol=e["symbol"], date=e["event_date"].isoformat(), mode="LIVE", observed_at=(t - timedelta(hours=1)).isoformat(), request_id="r", company=e["symbol"],
                                                 sector=sector[e["symbol"]], marketcap=str(int(e["cap"])), raw_sha256=r["source"][:1] + e["symbol"]))
        rows = L.LiveScorer(svc).score_all(t)
        kinds = ("ohlc", "earnings")
        pending = {x["symbol"] for x in svc.plan_evidence(t) if x.get("symbol") and x["kind"] in kinds} if policy in ("deadline", "deadline_priority") else None
        sel = E20.select_top(rows, caps, t, n=10_000, policy=policy, pending=pending)
        plan = E20.plan_tasks(svc, sel, t, budget=left)
        res = E20.run(svc, plan["funded"], now=t, ceiling=left, state_dir=tmp / "st", max_consecutive_failures=999)
        for r in L.LiveScorer(svc).score_all(t):
            if r["symbol"] in caps and window_of[r["symbol"]][0] <= t <= window_of[r["symbol"]][1]:
                last_result[r["symbol"]] = E20.classify(r, t)
        run_log.append({"at": t.isoformat(), "attempts": cal_cost + res["attempts"], "calendar_charged": cal_cost, "evidence_attempts": res["attempts"], "provider_calls": prov.calls})
    # ---- tally
    ev_rows = svc.store.read("evidence")
    have = {}
    for r in ev_rows:
        if r["kind"] in ("ohlc", "earnings") and r["symbol"] in caps:
            s, c = window_of[r["symbol"]]
            if s.isoformat() <= r["retrieved_at"] <= c.isoformat():
                have.setdefault(r["symbol"], set()).add(r["kind"])
    served = {s for s, k in have.items() if k == {"ohlc", "earnings"}}
    tradable = [e for e in events if E20.tradability({"kind": e["session"], "earnings_date": e["event_date"].isoformat()}) is not False]
    tset = {e["symbol"] for e in tradable}
    valid, why_not, examples = set(), Counter(), []
    for e in tradable:
        s = e["symbol"]
        res = last_result.get(s, {})
        if s in served and res.get("result") in ("BUY", "SELL", "NO TRADE (genuine)"):
            valid.add(s)
        elif s in served:
            why_not["served but NO VALID PREDICTION (%s)" % ("liquidity below the frozen floor" if res.get("liquidity") == "FAIL" else res.get("reason", "?")[:60])] += 1
            if len(examples) < 3:
                examples.append({"symbol": s, "event": "%s %s" % (e["event_date"], e["session"]), "window": [window_of[s][0].isoformat(), window_of[s][1].isoformat()], "result": res.get("result"), "missing": res.get("missing_factors"), "reason": res.get("reason")})
        else:
            why_not["never funded inside its window (budget)"] += 1
    per_day_used = dict(svc.gov.used_day)
    return {"universe": universe, "policy": policy, "window_aware": window_aware, "events": len(events), "not_tradable": len(events) - len(tradable), "tradable": len(tradable), "served": len(served & tset), "valid_predictions": len(valid),
            "not_served": len(tradable) - len(served & tset), "why_no_valid_prediction": dict(why_not), "requests_by_ny_day": per_day_used, "max_attempts_in_a_run": max(r.get("attempts", 0) for r in run_log),
            "examples": examples, "shared_evidence_attempts": sum(1 for k, s in prov.log if k == "ohlc" and (s == "SPY" or s in set(L.SECTOR_ETF.values()))), "own_evidence_attempts": sum(1 for k, s in prov.log if s in caps), "calendar_attempts_charged": sum(r.get("calendar_charged", 0) for r in run_log),
            "total_attempts": sum(r.get("attempts", 0) for r in run_log), "pending_because_retrieved_before_window": sum(1 for s in served & tset if "RETRIEVED_BEFORE_WINDOW" in str(last_result.get(s, {}).get("reason", ""))),
            "pending_before_window_by_window": dict(Counter("%s %s" % (e["event_date"], e["session"]) for e in tradable if e["symbol"] in served and "RETRIEVED_BEFORE_WINDOW" in str(last_result.get(e["symbol"], {}).get("reason", "")))),
            "provider_calls_sent_to_stub": prov.calls, "skipped_events_that_were_funded": len([e for e in events if e["symbol"] not in tset and e["symbol"] in have])}


if __name__ == "__main__":
    unis = sys.argv[1:] or ["optcap", "options", "price", "broad"]
    for u in unis:
        for wa in (False, True):
            r = replay(u, "deadline_priority", window_aware=wa)
            r.pop("examples", None)
            print(json.dumps(r, default=str), flush=True)
