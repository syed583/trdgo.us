"""P1 (rolling 30-day horizon): tiered, per-slot calendar refresh. Pure planning tests plus one integration test through the real RefreshService with a stub provider (no network, no credentials)."""
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "docs" / "smart_move" / "app"))
import smart_move_calendar_changes as CC  # noqa: E402
import smart_move_calendar_tiers as CT  # noqa: E402
import smart_move_live as L  # noqa: E402

from test_live import make  # noqa: E402

UTC = timezone.utc
SAT = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)          # Saturday 07:00 ET (EST: daylight time ended 1 Nov)


def fresh_state():
    return {"version": 1, "slots": {}, "symbol_index": {}, "refreshes": 0}


def log(d, side, syms=(), pages=1, complete=True, reason=None):
    return {"date": d, "side": side, "pages_fetched": pages, "symbols": list(syms), "complete": complete, "reason": reason}


# ---------------------------------------------------------------- horizon and tiers
def test_a_30_day_horizon_has_trading_days_only_and_starts_on_monday_when_today_is_a_saturday():
    slots = CT.horizon_slots(SAT, 30)
    days = sorted({d for d, _ in slots})
    assert days[0] == date(2026, 11, 9) and days[-1] == date(2026, 12, 7)
    assert all(d.weekday() < 5 for d in days) and date(2026, 11, 26) not in days                  # no weekend, no Thanksgiving
    assert len(slots) == 40 and {s for _, s in slots} == {"premarket", "afterhours"}
    assert len(CT.horizon_slots(SAT, 99)) == 40 and len(CT.horizon_slots(SAT, 3)) == 4              # the horizon is clamped to 1..30 days (Saturday + 3 days = Tuesday: Monday and Tuesday, two sessions each)


def test_tiers_follow_the_frozen_prediction_windows_on_the_weekend_before_the_peak_week():
    c = {(x["date"], x["side"]): x for x in (CT.classify(s, SAT) for s in CT.horizon_slots(SAT, 30))}
    mon_bmo, mon_amc, tue_bmo = c[("2026-11-09", "premarket")], c[("2026-11-09", "afterhours")], c[("2026-11-10", "premarket")]
    assert (mon_bmo["tier"], mon_bmo["window_start_utc"], mon_bmo["cutoff_utc"]) == ("T0_OPEN", "2026-11-07T11:00:00+00:00", "2026-11-08T11:00:00+00:00")       # Sat 06:00 ET .. Sun 06:00 ET
    assert (mon_amc["tier"], mon_amc["window_start_utc"], mon_amc["cutoff_utc"]) == ("T0_NEXT", "2026-11-07T21:00:00+00:00", "2026-11-08T21:00:00+00:00")     # opens in 9 h
    assert tue_bmo["tier"] == "T1" and tue_bmo["window_start_utc"] == "2026-11-08T11:00:00+00:00"                                                           # opens in 23 h: far
    assert CT.classify((date(2026, 11, 9), "premarket"), datetime(2026, 11, 8, 12, 0, tzinfo=UTC))["tier"] == "CLOSED"                                      # Sunday 07:00 ET: the Monday BMO window has closed
    assert CT.required_age_h("T0_OPEN") == 5.0 and CT.required_age_h("T0_NEXT") == 5.0 and CT.required_age_h("T1") == 24.0 and CT.required_age_h("CLOSED") is None


# ---------------------------------------------------------------- the refresh plan
def test_first_plan_serves_open_windows_first_then_a_few_far_days_inside_the_calendar_share():
    p = CT.plan_refresh(SAT, fresh_state(), horizon_days=30, run_ceiling=50, budget_left=50, evidence_demand=0)
    keys = [c["key"] for c in p["refresh"]]
    assert keys[:2] == ["2026-11-09|premarket", "2026-11-09|afterhours"]                          # open window first, then the one opening next
    assert [c["tier"] for c in p["refresh"]] == ["T0_OPEN", "T0_NEXT"] + ["T1"] * CT.T1_RUN_MAX
    assert p["cap"] == 13 and p["est_requests"] == 8 + CT.CONFIRM_RESERVE and p["est_requests"] <= int(0.30 * 50)      # never more than 30 % of the run, confirmations included
    assert len(p["deferred"]) == 40 - 8 and all("T1_DEFERRED" in d["deferred_because"] for d in p["deferred"])
    assert len(set(keys)) == len(keys)                                                          # no slot twice


def test_far_days_get_only_what_is_left_after_the_evidence_the_run_needs():
    heavy = CT.plan_refresh(SAT, fresh_state(), horizon_days=30, run_ceiling=50, budget_left=50, evidence_demand=48)
    assert [c["tier"] for c in heavy["refresh"]] == ["T0_OPEN", "T0_NEXT"]                       # open windows are never starved; far days wait
    assert any(d["tier"] == "T1" for d in heavy["deferred"])


def test_daily_budget_exhaustion_defers_open_slots_and_says_the_calendar_is_incomplete():
    p = CT.plan_refresh(SAT, fresh_state(), horizon_days=30, run_ceiling=50, budget_left=0, evidence_demand=0)
    assert p["refresh"] == [] and p["needed"] is False and p["cap"] == 0
    assert {d["key"] for d in p["deferred"] if d["tier"] != "T1"} == {"2026-11-09|premarket", "2026-11-09|afterhours"} and all("CALENDAR_CAP" in d["deferred_because"] for d in p["deferred"] if d["tier"] != "T1")
    comp = CT.completeness(fresh_state(), SAT, 30)
    assert comp["t0_complete"] is False and comp["t0_open_stale"] == ["2026-11-09|premarket"] and comp["t0_next_stale"] == ["2026-11-09|afterhours"]


def test_a_small_cap_defers_the_later_open_slot_not_the_earlier_one():
    p = CT.plan_refresh(SAT, fresh_state(), horizon_days=30, run_ceiling=10, budget_left=50, evidence_demand=0)        # cap = int(0.3*10) - 2 = 1
    assert [c["key"] for c in p["refresh"]] == ["2026-11-09|premarket"] and [d["key"] for d in p["deferred"] if d["tier"] == "T0_NEXT"] == ["2026-11-09|afterhours"]


# ---------------------------------------------------------------- per-slot freshness
def test_refreshing_far_days_never_makes_the_near_days_look_fresh():
    st = CT.record_refresh(fresh_state(), [log("2026-11-12", "premarket", ["AAA"])], SAT)
    near = CT.classify((date(2026, 11, 9), "premarket"), SAT)
    far = CT.classify((date(2026, 11, 12), "premarket"), SAT)
    assert CT.slot_is_fresh(st, near, SAT) is False and CT.slot_is_fresh(st, far, SAT) is True      # the old 'newest observation anywhere' rule would have called both fresh


def test_a_refresh_that_was_cut_short_stays_stale_and_is_planned_again_with_its_reason():
    st = CT.record_refresh(fresh_state(), [log("2026-11-09", "premarket", ["AAA"], pages=4, complete=False, reason="BUDGET_EXHAUSTED_AFTER_FULL_PAGE")], SAT)
    p = CT.plan_refresh(SAT + timedelta(hours=1), st, horizon_days=30, run_ceiling=50, budget_left=50)
    first = p["refresh"][0]
    assert first["key"] == "2026-11-09|premarket" and "incomplete" in first["why"] and first["est_requests"] == 4          # a slot that took 4 pages is budgeted at 4


def test_no_slot_is_requested_twice_inside_its_age_limit_and_t0_comes_back_after_5_hours():
    p1 = CT.plan_refresh(SAT, fresh_state(), horizon_days=30, run_ceiling=50, budget_left=50)
    st = CT.record_refresh(fresh_state(), [log(c["date"], c["side"], ["S" + c["date"][-2:]]) for c in p1["refresh"]], SAT)
    p2 = CT.plan_refresh(SAT + timedelta(hours=1), st, horizon_days=30, run_ceiling=50, budget_left=50)
    assert not {c["key"] for c in p1["refresh"]} & {c["key"] for c in p2["refresh"]}                  # duplicate prevention
    p3 = CT.plan_refresh(SAT + timedelta(hours=5, minutes=1), st, horizon_days=30, run_ceiling=50, budget_left=50)
    assert {"2026-11-09|premarket", "2026-11-09|afterhours"} <= {c["key"] for c in p3["refresh"]}      # T0 is stale again after 5 h
    assert not {c["key"] for c in p1["refresh"] if c["tier"] == "T1"} & {c["key"] for c in p3["refresh"]}      # T1 refreshed 5 h ago waits for 24 h


def test_state_round_trips_atomically_and_a_damaged_file_means_empty_state_not_a_crash(tmp_path):
    st = CT.record_refresh(fresh_state(), [log("2026-11-09", "premarket", ["AAA", "BBB"])], SAT)
    CT.save_state(tmp_path, st)
    assert CT.load_state(tmp_path)["slots"]["2026-11-09|premarket"]["symbols"] == ["AAA", "BBB"] and not list(tmp_path.glob("*.tmp"))
    (tmp_path / CT.STATE_FILE).write_text("{not json", encoding="utf-8")
    assert CT.load_state(tmp_path)["slots"] == {}


# ---------------------------------------------------------------- integration with the real RefreshService (stub provider)
class Cal(L.Fetcher):
    mode = "SIMULATION"

    def __init__(self, by_slot, full_pages=None):
        self.by_slot, self.uw, self.full_pages = by_slot, [], full_pages or set()

    def fetch(self, kind, symbol, params):
        if kind == "uw_calendar":
            self.uw.append((params["date"], params["side"], params.get("page", 0)))
            syms = self.by_slot.get((params["date"], params["side"]), [])
            if (params["date"], params["side"]) in self.full_pages:
                syms = ["F%s%d" % (params["date"][-2:], i) for i in range(100)]
            return json.dumps({"data": [{"symbol": s, "report_date": params["date"], "report_time": params["side"], "source": "company", "marketcap": "1"} for s in syms]}).encode()
        return b"Ticker,Earnings\n" if kind == "finviz_calendar" else json.dumps({"earningsCalendar": []}).encode()


def test_refresh_calendar_records_what_each_slot_returned_and_the_tiered_cycle_updates_state_and_changes(tmp_path):
    prov = Cal({("2026-11-09", "premarket"): ["AAA"], ("2026-11-09", "afterhours"): ["BBB", "CCC"], ("2026-11-10", "premarket"): ["DDD"]})
    svc, _, _ = make(tmp_path, start=SAT, fetcher=prov)
    st = fresh_state()
    plan = CT.plan_refresh(SAT, st, horizon_days=30, run_ceiling=50, budget_left=50)
    dates = [(date.fromisoformat(c["date"]), c["side"]) for c in plan["refresh"]]
    done = svc.refresh_calendar([plan["est_requests"]], dates=dates)
    sent = {(d, s) for d, s, _ in prov.uw}
    assert sent == {(c["date"], c["side"]) for c in plan["refresh"]} and len(prov.uw) == len(plan["refresh"]) and done == len(prov.uw) + 2         # exactly the planned slots, once each, plus Finviz and Finnhub
    got = {(e["date"], e["side"]): e for e in svc.last_slot_log}
    assert got[("2026-11-09", "afterhours")]["symbols"] == ["BBB", "CCC"] and all(e["complete"] for e in svc.last_slot_log)
    CT.record_refresh(st, svc.last_slot_log, SAT)
    ch = CC.track(st, svc.last_slot_log, SAT)
    assert sorted(c["symbol"] for c in ch if c["change"] == "NEW") == ["AAA", "BBB", "CCC", "DDD"]
    again = CT.plan_refresh(SAT + timedelta(minutes=30), st, horizon_days=30, run_ceiling=50, budget_left=50)
    assert not {c["key"] for c in plan["refresh"]} & {c["key"] for c in again["refresh"]}


def test_a_slot_that_fills_every_page_is_recorded_incomplete_not_silently_complete(tmp_path):
    prov = Cal({}, full_pages={("2026-11-09", "premarket")})
    svc, _, _ = make(tmp_path, start=SAT, fetcher=prov, calendar_max_pages=2)
    svc.refresh_calendar([30], dates=[(date(2026, 11, 9), "premarket")])
    e = svc.last_slot_log[0]
    assert e["complete"] is False and e["reason"] == "PAGE_CAP_REACHED" and e["pages_fetched"] == 2
    st = CT.record_refresh(fresh_state(), svc.last_slot_log, SAT)
    assert CT.slot_is_fresh(st, CT.classify((date(2026, 11, 9), "premarket"), SAT), SAT) is False
