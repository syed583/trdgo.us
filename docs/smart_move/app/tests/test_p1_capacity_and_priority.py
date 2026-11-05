"""P1: the Nov 9-13 peak week (real window times, weekend behaviour, cutoff ordering, budget exhaustion, no duplicate work) and the prioritisation-only policy. Pure arithmetic and selection: nothing is sent."""
import json
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "docs" / "smart_move" / "shadow_impl"))
import smart_move_capacity as CAP  # noqa: E402
import smart_move_engine20 as E20  # noqa: E402
from smvshadow import cutoff as co  # noqa: E402

UTC = timezone.utc
COUNTS = json.loads((ROOT / "docs" / "smart_move" / "p1" / "cal30_probe_counts_2026-11-05.json").read_text(encoding="utf-8"))["counts"]
WEEK = {d: v for d, v in COUNTS.items() if "2026-11-09" <= d <= "2026-11-13"}


def T(m, d, h, mi=0):
    return datetime(2026, m, d, h, mi, tzinfo=UTC)


def hhmm(t):
    return t.strftime("%a %m-%d %H:%MZ")


# ---------------------------------------------------------------- the verified windows
def test_the_november_9_to_13_windows_by_the_frozen_rules_including_the_weekend_and_the_session_rules():
    """EST applies (daylight time ended 1 Nov): window = release - 48 h .. release - 24 h, BMO release 06:00 ET, AMC 16:00 ET."""
    exp = {
        (9, "BMO"): ("Sat 11-07 11:00Z", "Sun 11-08 11:00Z", False, date(2026, 11, 6)),   # Monday BMO: the entry would be a Friday close order whose deadline (Fri 15:50 ET) passed before the cutoff: NOT TRADABLE
        (9, "AMC"): ("Sat 11-07 21:00Z", "Sun 11-08 21:00Z", True, date(2026, 11, 6)),
        (10, "BMO"): ("Sun 11-08 11:00Z", "Mon 11-09 11:00Z", True, date(2026, 11, 6)),
        (10, "AMC"): ("Sun 11-08 21:00Z", "Mon 11-09 21:00Z", True, date(2026, 11, 6)),     # Monday's close (16:45 ET) comes after this cutoff (16:00 ET): evidence is still Friday's bar
        (11, "BMO"): ("Mon 11-09 11:00Z", "Tue 11-10 11:00Z", True, date(2026, 11, 9)),
        (11, "AMC"): ("Mon 11-09 21:00Z", "Tue 11-10 21:00Z", True, date(2026, 11, 9)),
        (12, "BMO"): ("Tue 11-10 11:00Z", "Wed 11-11 11:00Z", True, date(2026, 11, 10)),
        (12, "AMC"): ("Tue 11-10 21:00Z", "Wed 11-11 21:00Z", True, date(2026, 11, 10)),
        (13, "BMO"): ("Wed 11-11 11:00Z", "Thu 11-12 11:00Z", True, date(2026, 11, 11)),
        (13, "AMC"): ("Wed 11-11 21:00Z", "Thu 11-12 21:00Z", True, date(2026, 11, 11)),
    }
    for (day, kind), (opens, closes, tradable, last_session) in exp.items():
        p = co.plan_event(date(2026, 11, day), kind)
        assert (hhmm(p["window_start_utc"]), hhmm(p["cutoff_utc"]), p["tradable"], p["last_usable_session"]) == (opens, closes, tradable, last_session), (day, kind)
        assert p["cutoff_utc"] - p["window_start_utc"] == timedelta(hours=24)                      # every window is exactly 24 h wide, weekends included
    p = co.plan_event(date(2026, 11, 9), "AMC")
    assert p["window_start_utc"].astimezone(co.ET).strftime("%a %H:%M") == "Sat 16:00" and p["cutoff_utc"].astimezone(co.ET).strftime("%a %H:%M") == "Sun 16:00"     # New York
    assert co._mc().is_trading_day(date(2026, 11, 7)) is False and co._mc().is_trading_day(date(2026, 11, 8)) is False                                          # weekends are not sessions, yet the windows run through them


# ---------------------------------------------------------------- prioritisation only
def row(sym, d, kind, cap_blocked=None):
    p = co.plan_event(d, kind)
    return {"symbol": sym, "earnings_date": d.isoformat(), "kind": kind, "blocked_by": cap_blocked or [], "plan": p, "company": sym, "sector": "Technology"}


def test_cap_tier_buckets_and_unknown_is_the_lowest_tier_never_an_exclusion():
    assert [E20.cap_tier(x) for x in (50e9, 10e9, 5e9, 2e9, 1e9, 3e8, 1e8, None, "x")] == ["A", "A", "B", "B", "C", "C", "D", "D", "D"]


def test_deadline_priority_skips_untradable_serves_urgent_cutoffs_first_then_proxy_tier_then_deadline_and_records_every_event():
    now = T(11, 8, 17, 30)                                                                         # Sunday 12:30 ET
    rows = [row("MONBMO", date(2026, 11, 9), "BMO"),                                               # window closed already (Sun 06:00 ET)
            row("URG_D", date(2026, 11, 9), "AMC"),                                                # cutoff Sun 21:00Z = 3.5 h: urgent, lowest tier
            row("TUE_A", date(2026, 11, 10), "BMO"), row("TUE_D", date(2026, 11, 10), "BMO"), row("TUE_B", date(2026, 11, 10), "BMO")]
    caps = {"URG_D": 5e7, "TUE_A": 50e9, "TUE_B": 4e9, "TUE_D": 5e7}
    audit = []
    sel = E20.select_top(rows, caps, now, n=3, policy="deadline_priority", audit=audit)
    assert [s["symbol"] for s in sel] == ["URG_D", "TUE_A", "TUE_B"]                                # urgent first even though its tier is D; then A, then B
    by = {a["symbol"]: a for a in audit}
    assert by["TUE_D"]["status"] == "NOT_SELECTED" and "DEFERRED by priority" in by["TUE_D"]["reason"] and "still eligible" in by["TUE_D"]["reason"]      # deferred, transparent, not excluded
    assert E20.select_top(rows, caps, now, n=10, policy="deadline_priority")[-1]["symbol"] == "TUE_D"                                                # with room, the lowest tier is still selected
    # the same rows under the older policies are untouched
    assert [s["symbol"] for s in E20.select_top(rows, caps, now, n=4, policy="deadline")] == ["URG_D", "TUE_A", "TUE_B", "TUE_D"]
    assert [s["symbol"] for s in E20.select_top(rows, caps, now, n=4, policy="cap")] == ["TUE_A", "TUE_B", "TUE_D", "URG_D"]


def test_untradable_events_get_no_evidence_under_priority_and_the_audit_says_why():
    now = T(11, 7, 14, 0)                                                                          # Saturday 09:00 ET: the Monday BMO window is open but the event is not tradable
    rows = [row("MON_BMO", date(2026, 11, 9), "BMO"), row("MON_AMC", date(2026, 11, 9), "AMC")]
    rows = [r for r in rows if r["plan"]["window_start_utc"] <= now <= r["plan"]["cutoff_utc"]]
    audit = []
    sel = E20.select_top(rows, {"MON_BMO": 9e9}, now, n=5, policy="deadline_priority", audit=audit)
    assert [s["symbol"] for s in sel] == [] and audit[0]["symbol"] == "MON_BMO" and audit[0]["status"] == "SKIPPED" and "NOT_TRADABLE" in audit[0]["reason"]
    assert [s["symbol"] for s in E20.select_top(rows, {"MON_BMO": 9e9}, now, n=5, policy="deadline")] == ["MON_BMO"]          # the older policy would have spent requests on it


# ---------------------------------------------------------------- the peak week under the approved budget
def run_week(universe, hours_by_day, **kw):
    runs = [T(m, d, h) for (m, d), hs in hours_by_day.items() for h in hs]
    return CAP.simulate(CAP.events_from_counts(WEEK, universe), runs, **kw)


TWO_A_DAY = {(11, 7): [13, 22], (11, 8): [13, 22], (11, 9): [13, 22], (11, 10): [13, 22], (11, 11): [13, 22], (11, 12): [13, 22]}
WEEKEND_HEAVY = {(11, 7): [22], (11, 8): [4, 13, 20], (11, 9): [13, 22], (11, 10): [4, 13, 22], (11, 11): [4, 13, 22], (11, 12): [13, 22]}


def test_the_budget_is_never_exceeded_and_nothing_is_served_outside_its_window_twice_or_when_untradable():
    for universe in ("broad", "price", "options"):
        r = run_week(universe, WEEKEND_HEAVY)
        assert all(run["calendar"] + run["shared"] + run["evidence"] <= 50 for run in r["runs"])                  # per run
        assert max(r["requests_by_et_day"].values()) <= 100                                                       # per New York day
        for e in r["events_detail"]:
            if e["served_at"]:
                t = datetime.fromisoformat(e["served_at"])
                assert e["_start"] <= t <= e["_cutoff"] and e["_tradable"]                                         # inside its own window, and tradable
        assert len({e["symbol"] for e in r["events_detail"]}) == len(r["events_detail"])                          # no event appears (or is funded) twice
        assert r["served"] + r["missed"] + r["not_tradable"] == r["events"]


def test_monday_bmo_is_never_funded_and_the_weekend_runs_serve_monday_amc_only_before_its_sunday_cutoff():
    r = run_week("options", WEEKEND_HEAVY)
    mon = [e for e in r["events_detail"] if e["event_date"] == date(2026, 11, 9)]
    mon_bmo = [e for e in mon if e["session"] == "BMO"]
    assert mon_bmo and all(e["served_at"] is None and not e["_tradable"] for e in mon_bmo) and len(mon_bmo) == r["not_tradable"]        # Monday BMO is the only untradable group of the week, and it is never funded
    served_amc = [datetime.fromisoformat(e["served_at"]) for e in mon if e["session"] == "AMC" and e["served_at"]]
    assert served_amc and all(T(11, 7, 21) <= t <= T(11, 8, 21) for t in served_amc)                              # Sat 16:00 ET .. Sun 16:00 ET, and nothing on Monday for them
    assert not any(run["served"] for run in r["runs"] if run["at"] < T(11, 7, 21).isoformat())                    # nothing is funded before the first window opens


def test_a_heavier_weekend_serves_more_than_one_run_a_day_and_the_extra_capacity_comes_from_the_new_york_day_boundary():
    base, heavy = run_week("options", TWO_A_DAY), run_week("options", WEEKEND_HEAVY)
    assert heavy["served"] >= base["served"] >= 100 and heavy["served"] > base["served"]                          # Saturday night and early Sunday UTC belong to different New York days: 100 requests each
    assert base["requests_by_et_day"]["2026-11-07"] == 50 and heavy["requests_by_et_day"]["2026-11-07"] > 90


def test_daily_budget_exhaustion_stops_funding_and_leaves_the_rest_missed_not_invented():
    r = run_week("broad", TWO_A_DAY, per_day=20, per_run=50)
    assert max(r["requests_by_et_day"].values()) <= 20 and r["served"] < run_week("broad", TWO_A_DAY)["served"]
    zero = run_week("broad", TWO_A_DAY, per_day=0)
    assert zero["served"] == 0 and zero["missed"] == zero["events"] - zero["not_tradable"] and all(run.get("skipped") for run in zero["runs"])


def test_with_the_approved_limits_the_broad_universe_cannot_be_covered_but_the_likely_liquid_names_nearly_can():
    broad, price, options, optcap = (run_week(u, WEEKEND_HEAVY) for u in ("broad", "price", "options", "optcap"))
    tradable = lambda r: r["events"] - r["not_tradable"]  # noqa: E731
    assert broad["served"] < 0.5 * tradable(broad)                                                                # 50 / 100 limits: less than half of the broad universe fits
    assert options["served"] >= 0.9 * tradable(options) and optcap["served"] == tradable(optcap)                  # the proxy-likely-liquid subset fits
    assert broad["served"] > options["served"] > 0 and tradable(options) < tradable(broad)


def test_priority_protects_the_likely_liquid_names_in_a_junk_heavy_universe_but_plain_deadline_order_is_better_at_raw_count_when_the_universe_is_already_filtered():
    """Measured honestly (probe counts, same runs): in the broad universe plain deadline order serves 75 of the 82 proxy-tier-A events and priority serves all 82, for about the same total; in the already-filtered options universe
    plain deadline order serves slightly MORE events in total (117 vs 113) because it never delays an early cutoff for a higher tier. The policy is a choice about WHO is served when the budget cannot cover everyone."""
    pri, dl = run_week("broad", WEEKEND_HEAVY, policy="deadline_priority"), run_week("broad", WEEKEND_HEAVY, policy="deadline")
    assert pri["served_by_tier"]["A"] == pri["total_by_tier"]["A"] == 82 and dl["served_by_tier"]["A"] == 75
    assert abs(pri["served"] - dl["served"]) <= 3 and pri["served_by_tier"].get("D", 0) < dl["served_by_tier"].get("D", 0)
    f_pri, f_dl = run_week("options", WEEKEND_HEAVY, policy="deadline_priority"), run_week("options", WEEKEND_HEAVY, policy="deadline")
    assert f_dl["served"] >= f_pri["served"] and f_pri["served"] >= f_dl["served"] - 6


def test_within_one_run_the_earliest_cutoff_is_served_first_under_the_deadline_policy():
    evs = [{"symbol": "LATE", "event_date": date(2026, 11, 10), "session": "AMC", "tier": "A", "cap": 9e9},
           {"symbol": "EARLY", "event_date": date(2026, 11, 10), "session": "BMO", "tier": "D", "cap": 1.0}]
    r = CAP.simulate(evs, [T(11, 9, 10)], per_run=20, policy="deadline")                                          # Mon 10:00Z: 4 slots (6 calendar) + SPY and 11 ETFs (12) leave room for exactly one stock (2)
    assert r["runs"][0]["calendar"] == 6 and r["runs"][0]["shared"] == 12 and r["runs"][0]["served"] == 1
    assert {e["symbol"] for e in r["events_detail"] if e["served_at"]} == {"EARLY"}                                 # Tue BMO cutoff (Mon 11:00Z) is before Tue AMC cutoff (Mon 21:00Z), whatever the tier
