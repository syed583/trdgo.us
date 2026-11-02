"""Pilot preflight with a deterministic mocked provider: 14-day calendar refresh (pages, truncation, reserve), eligibility rules, priority order, a hard 50-attempt ceiling, kill switch, restart, partial failure.
Every call goes to a counting stub; nothing reaches a real provider."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_collection_jobs as J  # noqa: E402
import smart_move_collection_runner as RUNNER  # noqa: E402
import smart_move_live as L  # noqa: E402
import smart_move_pilot_preflight as P  # noqa: E402
from smvshadow import governor as gv  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 11, 2, 14, 0, tzinfo=UTC)          # Monday 09:00 ET


def uw(sym, date, side="postmarket", flag="company", mc=1e11):
    return {"symbol": sym, "report_date": date, "report_time": side, "source": flag, "full_name": sym + " Inc", "sector": "Technology", "marketcap": str(mc)}


class Provider(L.Fetcher):
    """Serves UW calendar pages of 100 rows, a Finviz CSV, a Finnhub window, and news / estimates. Counts every call by kind."""
    mode = "SIMULATION"

    def __init__(self, uw_rows=(), finviz=(), finnhub=(), fail_symbols=(), fail_kinds=()):
        self.rows, self.finviz, self.finnhub = list(uw_rows), list(finviz), list(finnhub)
        self.calls, self.fail_symbols, self.fail_kinds = [], set(fail_symbols), set(fail_kinds)

    def count(self, kind=None):
        return sum(1 for c in self.calls if kind is None or c[0] == kind)

    def fetch(self, kind, symbol, params):
        self.calls.append((kind, symbol, dict(params)))
        if kind in self.fail_kinds or symbol in self.fail_symbols:
            raise gv.ProviderError("transient", "down")
        if kind == "uw_calendar":
            side = "premarket" if params["side"] == "premarket" else "postmarket"
            rows = [r for r in self.rows if r["report_date"] == params["date"] and r["report_time"] == side]
            pg = int(params.get("page", 0))
            return json.dumps({"data": rows[pg * 100:(pg + 1) * 100]}).encode()
        if kind == "finviz_calendar":
            return ("Ticker,Earnings Date\n" + "\n".join("%s,%s" % (s, raw) for s, raw in self.finviz)).encode()
        if kind == "finnhub_calendar":
            return json.dumps({"earningsCalendar": [{"symbol": s, "date": d, "hour": h} for s, d, h in self.finnhub]}).encode()
        if kind == "uw_news":
            return json.dumps({"data": [{"created_at": (NOW - timedelta(hours=3)).isoformat().replace("+00:00", "Z"), "headline": "Item %s" % symbol, "source": "Business Wire", "tickers": [symbol], "sentiment": "neutral"}]}).encode()
        if kind == "uw_estimates":
            return json.dumps({"data": {"estimates": [{"date": "2026-12-31", "horizon": "fiscal quarter", "eps_estimate_average": 1.0, "revenue_estimate_average": 10.0}]}}).encode()
        raise AssertionError(kind)


def sym(prefix, i):
    return prefix + chr(65 + i // 26) + chr(65 + i % 26)


def vendor_set(events):
    """events: [(symbol, 'YYYY-MM-DD', 'BMO'|'AMC')] -> consistent UW (company flag) + Finviz + Finnhub rows, i.e. VENDOR_CONFIRMED events."""
    u, fz, fh = [], [], []
    for s, d, k in events:
        y, m, dd = d.split("-")
        u.append(uw(s, d, "premarket" if k == "BMO" else "postmarket"))
        fz.append((s, "%d/%d/%d %s" % (int(m), int(dd), int(y), "7:00:00 AM" if k == "BMO" else "4:30:00 PM")))
        fh.append((s, d, "bmo" if k == "BMO" else "amc"))
    return u, fz, fh


def service(tmp_path, prov, *, retries=0, authorized=50, tracking_days=14, start=NOW, live=True):
    clock = gv.FakeClock(start)
    cfg = L.LiveConfig(mode="SIMULATION", store_dir=tmp_path / "store", refresh_enabled=live, authorized_daily=authorized if live else 0, spacing_s=0.0, max_retries=retries, tracking_days_ahead=tracking_days)
    return L.RefreshService(cfg, prov, clock), clock


# the events used by the pilot scenarios: 2 inside an open window (3 Nov AMC, 4 Nov BMO), 3 whose Snapshot A is due (release 9-12 days ahead), others far away
WINDOW = [("WAA", "2026-11-03", "AMC"), ("WAB", "2026-11-03", "AMC"), ("WBA", "2026-11-04", "BMO"), ("WBB", "2026-11-04", "BMO")]
SNAPA = [("SAA", "2026-11-12", "AMC"), ("SAB", "2026-11-12", "AMC"), ("SAC", "2026-11-13", "BMO")]
FAR = [("FRA", "2026-11-16", "AMC")]


def provider_for(events, **kw):
    u, fz, fh = vendor_set(events)
    return Provider(u, fz, fh, **kw)


# ------------------------------------------------------------------ 14-day calendar
def test_a_fourteen_day_refresh_costs_two_requests_per_trading_day_plus_two_vendor_calls_when_no_page_fills(tmp_path):
    prov = provider_for(WINDOW + SNAPA + FAR)
    svc, _ = service(tmp_path, prov)
    days = svc._trading_days(NOW, 14)
    assert len(days) == 11 and str(days[0]) == "2026-11-02" and str(days[-1]) == "2026-11-16"            # weekends 7-8 and 14-15 Nov skipped; no US market holiday in the range
    done = svc.refresh_calendar([100], 14)
    assert done == prov.count() == 2 * 11 + 2 and prov.count("finviz_calendar") == 1 and prov.count("finnhub_calendar") == 1
    cold, _ = service(tmp_path / "cold", Provider())                                                    # no calendar yet: stale, so the preflight prices the refresh
    pf = J.preflight(cold, {"tasks": [], "news": [], "snapshot_a": [], "snapshot_b": []}, now=NOW, ceiling=700, state=J._read_state(tmp_path / "x"), window_events=[], tracking_events=[])
    assert pf["calendar_refresh_estimate_requests"] == 24 and pf["calendar_trading_days"] == 11


def test_full_pages_are_followed_up_to_the_page_cap_and_a_still_full_side_is_reported_truncated(tmp_path):
    filler = [uw("F%03d" % i, "2026-11-04", "postmarket", flag="estimation") for i in range(230)]       # 230 rows: 3 pages
    prov = Provider(filler)
    svc, _ = service(tmp_path, prov)
    svc.refresh_calendar([100], 3)
    pages = [c[2]["page"] for c in prov.calls if c[0] == "uw_calendar" and c[2]["date"] == "2026-11-04" and c[2]["side"] == "afterhours"]
    assert pages == [0, 1, 2] and svc.store.state()["calendar_truncated"] == []
    big = Provider([uw("G%03d" % i, "2026-11-04", "postmarket", flag="estimation") for i in range(620)])      # more rows than 5 pages hold
    svc2, _ = service(tmp_path / "b", big)
    svc2.refresh_calendar([100], 3)
    assert [c[2]["page"] for c in big.calls if c[0] == "uw_calendar" and c[2]["side"] == "afterhours" and c[2]["date"] == "2026-11-04"] == [0, 1, 2, 3, 4]
    assert svc2.store.state()["calendar_truncated"] == [{"date": "2026-11-04", "side": "afterhours", "pages": 5}]


def test_a_small_budget_keeps_the_two_vendor_confirmation_requests(tmp_path):
    prov = provider_for(WINDOW)
    svc, _ = service(tmp_path, prov)
    done = svc.refresh_calendar([14], 14)
    assert done == 14 and prov.count("finviz_calendar") == 1 and prov.count("finnhub_calendar") == 1 and prov.count("uw_calendar") == 12


def test_eligibility_handles_bmo_amc_unknown_duplicates_reschedules_and_weekends(tmp_path):
    u, fz, fh = vendor_set(WINDOW)
    u.append(uw("DUP", "2026-11-03", "postmarket")); u.append(uw("DUP", "2026-11-03", "postmarket"))          # the same event listed twice
    fz.append(("DUP", "11/3/2026 4:30:00 PM")); fh.append(("DUP", "2026-11-03", "amc"))
    u.append(uw("RSC", "2026-11-03", "postmarket")); fz.append(("RSC", "11/6/2026 4:30:00 PM")); fh.append(("RSC", "2026-11-06", "amc"))   # vendors disagree by 3 days (rescheduled)
    u.append(uw("UNK", "2026-11-03", None)); fz.append(("UNK", "11/3/2026 4:30:00 PM")); fh.append(("UNK", "2026-11-03", ""))         # no timing from the first vendor
    u.append(uw("EST", "2026-11-03", "postmarket", flag="estimation")); fz.append(("EST", "11/3/2026 4:30:00 PM")); fh.append(("EST", "2026-11-03", "amc"))   # vendor says estimated
    prov = Provider(u, fz, fh)
    svc, _ = service(tmp_path, prov)
    svc.refresh_calendar([100], 14)
    win = {e["symbol"]: e for e in svc.eligible_events(NOW)}
    assert set(win) >= {"WAA", "WAB", "WBA", "WBB", "DUP"} and "RSC" not in win and "EST" not in win
    assert win["WAA"]["kind"] == "AMC" and win["WBA"]["kind"] == "BMO" and not any(e["kind"] not in ("BMO", "AMC") for e in win.values())
    assert sum(1 for e in svc.eligible_events(NOW) if e["symbol"] == "DUP") == 1                          # duplicates collapse to one event


def test_stale_calendar_means_zero_eligible_events_and_blocks_scoring_gates(tmp_path):
    prov = provider_for(WINDOW)
    svc, clock = service(tmp_path, prov)
    svc.refresh_calendar([100], 3)
    assert len(svc.eligible_events(NOW)) == 4
    clock.sleep(7 * 3600)                                                                                # 7 h later the strict 6 h rule has expired
    assert svc.eligible_events(clock.now()) == []                                                         # nothing eligible, so nothing can be scored BUY or SELL from it
    assert svc.cfg.calendar_interval_h == 6.0


# ------------------------------------------------------------------ the 50-attempt pilot
def run_pilot(tmp_path, prov, **kw):
    svc, clock = service(tmp_path, prov, **kw.pop("svc_kw", {}))
    job = J.run_cycle(svc, now=NOW, state_dir=tmp_path / "state", mode="LIVE", armed=True, ceiling=50, batch_size=5, news_pages=1, calendar_requests=kw.pop("calendar_requests", 30), **kw)
    return svc, job


def test_pilot_order_is_calendar_then_snapshot_a_then_b_then_news_and_stays_within_fifty(tmp_path):
    prov = provider_for(WINDOW + SNAPA + FAR)
    svc, job = run_pilot(tmp_path, prov)
    kinds = [c[0] for c in prov.calls]
    first_task = next(i for i, k in enumerate(kinds) if k in ("uw_estimates", "uw_news"))
    assert all(k in ("uw_calendar", "finviz_calendar", "finnhub_calendar") for k in kinds[:first_task])
    order = [(c[0], c[1]) for c in prov.calls[first_task:]]
    est = [s for k, s in order if k == "uw_estimates"]
    assert est[:3] == ["SAA", "SAB", "SAC"] and set(est[3:]) == {"WAA", "WAB", "WBA", "WBB"}              # Snapshot A first, then B
    assert max(i for i, (k, s) in enumerate(order) if k == "uw_estimates") < min(i for i, (k, s) in enumerate(order) if k == "uw_news")   # news only after both snapshot stages
    assert len(prov.calls) <= 50 and job["requests"] == len(prov.calls) and job["status"] in ("COMPLETED", "PARTIAL")
    assert prov.count("uw_news") == 4                                                                      # window events only, page 0 only


def test_the_fifty_attempt_ceiling_is_hard_even_when_more_work_is_due(tmp_path):
    many = [(sym("W", i), "2026-11-03", "AMC") for i in range(40)] + [(sym("V", i), "2026-11-04", "BMO") for i in range(40)]
    prov = provider_for(many)
    svc, job = run_pilot(tmp_path, prov, calendar_requests=14)
    assert len(prov.calls) <= 50 and job["status"] == "PARTIAL" and job["halted"] in ("RUN_CEILING_REACHED", "BUDGET_EXHAUSTED")
    assert sum(svc.gov.used_day.values()) <= 50 and J._read_state(tmp_path / "state")["requests_by_day"]["2026-11-02"] <= 50
    assert prov.count("uw_calendar") + prov.count("finviz_calendar") + prov.count("finnhub_calendar") <= 14       # the calendar never eats the evidence budget


def test_retries_are_counted_as_attempts_and_the_pilot_uses_none(tmp_path):
    prov = provider_for(WINDOW, fail_symbols={"WAA"})
    svc, job = run_pilot(tmp_path, prov, svc_kw={"retries": 0})
    assert sum(1 for c in prov.calls if c[1] == "WAA") == 2 and job["failures"]                              # one attempt per task kind, no retry
    prov2 = provider_for(WINDOW, fail_symbols={"WAA"})
    svc2, job2 = run_pilot(tmp_path / "r", prov2, svc_kw={"retries": 1})
    assert sum(1 for c in prov2.calls if c[1] == "WAA") == 4                                                  # with a retry the same task costs two attempts ...
    assert job2["requests"] == len(prov2.calls) and sum(svc2.gov.used_day.values()) == len(prov2.calls)      # ... and both are counted against the ceiling


def test_the_governors_own_daily_authorization_is_a_second_hard_stop(tmp_path):
    many = [(sym("W", i), "2026-11-03", "AMC") for i in range(30)]
    prov = provider_for(many)
    svc, job = run_pilot(tmp_path, prov, svc_kw={"authorized": 20})
    assert len(prov.calls) <= 20                                                                              # authorization 20 < ceiling 50: the stricter limit wins


def test_kill_switch_engaged_during_the_pilot_halts_it_and_restart_does_not_repeat_requests(tmp_path):
    prov = provider_for(WINDOW + SNAPA)
    svc, clock = service(tmp_path, prov)
    orig = prov.fetch
    state = {"n": 0}

    def fetch(kind, symbol, params):
        out = orig(kind, symbol, params)
        if kind == "uw_estimates":
            state["n"] += 1
            if state["n"] == 2:
                svc.engage_kill_switch("operator")                                                            # kill switch pulled mid-run
        return out
    prov.fetch = fetch
    job = J.run_cycle(svc, now=NOW, state_dir=tmp_path / "state", mode="LIVE", armed=True, ceiling=50, batch_size=2, news_pages=1, calendar_requests=30)
    assert job["status"] == "PARTIAL" and job["halted"] == "KILL_SWITCH"
    n = len(prov.calls)
    assert J.run_cycle(svc, now=NOW, state_dir=tmp_path / "state", mode="LIVE", armed=True, ceiling=50, batch_size=2)["status"] == "BLOCKED" and len(prov.calls) == n    # still engaged: nothing is sent
    svc.store.kill_file.unlink()
    svc.cfg.refresh_enabled = True
    job2 = J.run_cycle(svc, now=NOW, state_dir=tmp_path / "state", mode="LIVE", armed=True, ceiling=50, batch_size=2, news_pages=1)
    est_calls = [(c[1]) for c in prov.calls if c[0] == "uw_estimates"]
    assert len(est_calls) == len(set(est_calls)) and job2["status"] in ("COMPLETED", "PARTIAL")             # every estimate request is made exactly once across both runs


def test_a_dry_run_of_the_pilot_selects_the_same_tasks_with_zero_requests(tmp_path):
    prov = provider_for(WINDOW + SNAPA)
    live_svc, _ = service(tmp_path / "l", prov)
    live_svc.refresh_calendar([30], 14)                                                                        # a fresh calendar in this (mock) store
    n = len(prov.calls)
    dry = J.run_cycle(live_svc, now=NOW, state_dir=tmp_path / "ds", mode="DRY_RUN", ceiling=50)
    assert dry["provider_requests"] == 0 and len(prov.calls) == n and dry["preflight"]["tasks"]["snapshot_a"] == 3 and dry["preflight"]["tasks"]["snapshot_b"] == 4 and dry["preflight"]["tasks"]["news"] == 4


def test_allocation_never_exceeds_the_ceiling_and_reports_the_remainder_as_deferred():
    a = P.pilot_allocation(50, calendar_requests=14, snapshot_a=10, snapshot_b=30, news=30)
    assert a["allocated"] == 50 and a["unallocated"] == 0 and [l["allocated"] for l in a["lines"]] == [14, 10, 26, 0] and a["lines"][3]["deferred"] == 30
    small = P.pilot_allocation(50, 14, 0, 5, 5)
    assert small["allocated"] == 24 and small["unallocated"] == 26


def test_pilot_profile_never_allows_more_than_fifty_attempts(tmp_path):
    rc = RUNNER.main(["--store-dir", str(tmp_path / "s"), "--state-dir", str(tmp_path / "st"), "--mode", "live", "--arm", "--authorize-requests", "51", "--profile", "pilot50"])
    assert rc == 6 and not (tmp_path / "st").exists()


def test_pilot_profile_defaults_to_a_fifty_ceiling_and_rejects_a_larger_explicit_one(tmp_path):
    store = tmp_path / "s"
    store.mkdir()
    (store / "cal_obs.jsonl").write_text("")
    assert RUNNER.main(["--store-dir", str(store), "--state-dir", str(tmp_path / "ok"), "--profile", "pilot50"]) == 0
    st = J._read_state(tmp_path / "ok")
    assert list(st["jobs"].values())[0]["preflight"]["ceiling"] == 50
    assert RUNNER.main(["--store-dir", str(store), "--state-dir", str(tmp_path / "no"), "--profile", "pilot50", "--ceiling", "51"]) == 6


# ------------------------------------------------------------------ pilot calendar scope: only dates whose window can be open now
def test_useful_calendar_dates_are_exactly_the_event_dates_with_an_open_window():
    after_close = datetime(2026, 11, 2, 22, 0, tzinfo=UTC)          # Mon 17:00 ET, after the close
    assert [str(d) for d in J.useful_calendar_dates(after_close)] == ["2026-11-04"]                  # 3 Nov windows closed (cutoffs 2 Nov), 5 Nov windows open only from 3 Nov 06:00 ET
    morning = datetime(2026, 11, 2, 14, 0, tzinfo=UTC)               # Mon 09:00 ET
    assert [str(d) for d in J.useful_calendar_dates(morning)] == ["2026-11-03", "2026-11-04"]        # 3 Nov AMC and 4 Nov BMO/AMC windows are open
    friday_night = datetime(2026, 11, 6, 23, 0, tzinfo=UTC)          # Fri 18:00 ET: Monday's windows open only on Saturday
    assert J.useful_calendar_dates(friday_night) == []
    saturday_night = datetime(2026, 11, 7, 23, 0, tzinfo=UTC)
    assert [str(d) for d in J.useful_calendar_dates(saturday_night)] == ["2026-11-09"]               # Saturday: Monday's BMO and AMC windows are open


def test_the_pilot_calendar_refresh_requests_only_the_useful_dates_and_keeps_the_vendor_calls(tmp_path):
    after_close = datetime(2026, 11, 2, 22, 0, tzinfo=UTC)
    prov = provider_for([("PAA", "2026-11-04", "AMC"), ("PAB", "2026-11-04", "BMO"), ("OLD", "2026-11-03", "AMC")])
    svc, _ = service(tmp_path, prov, start=after_close)
    svc.refresh_calendar([14], dates=J.useful_calendar_dates(after_close))
    uw_dates = {c[2]["date"] for c in prov.calls if c[0] == "uw_calendar"}
    assert uw_dates == {"2026-11-04"} and prov.count("uw_calendar") == 2 and prov.count("finviz_calendar") == 1 and prov.count("finnhub_calendar") == 1
    fh = [c for c in prov.calls if c[0] == "finnhub_calendar"][0][2]
    assert fh == {"from": "2026-11-04", "to": "2026-11-04"}


def test_a_busy_target_day_fits_the_fourteen_request_calendar_cap_with_the_pilot_scope(tmp_path):
    after_close = datetime(2026, 11, 2, 22, 0, tzinfo=UTC)
    rows = [uw("B%03d" % i, "2026-11-04", "premarket", flag="estimation") for i in range(256)] + [uw("A%03d" % i, "2026-11-04", "postmarket", flag="estimation") for i in range(400)]
    prov = Provider(rows)
    svc, _ = service(tmp_path, prov, start=after_close)
    done = svc.refresh_calendar([14], dates=J.useful_calendar_dates(after_close))
    assert done <= 14 and prov.count("uw_calendar") == 3 + 5 and svc.store.state()["calendar_truncated"] == []      # 256 rows = 3 pages, 400 rows = 5 pages (the last one empty)
    busy_earlier = [uw("C%03d" % i, "2026-11-02", "postmarket", flag="estimation") for i in range(206)] + [uw("D%03d" % i, "2026-11-03", "premarket", flag="estimation") for i in range(200)]         + [uw("F%03d" % i, "2026-11-03", "postmarket", flag="estimation") for i in range(274)]
    old_style = Provider(rows + busy_earlier)                                                                  # the real calendar is busy on the days before the target day
    svc2, _ = service(tmp_path / "old", old_style, start=after_close)
    svc2.refresh_calendar([14], 3)                                                                              # the unscoped 3-day refresh starts at today and runs out before reaching 4 Nov after-close
    assert not any(c[2]["date"] == "2026-11-04" and c[2]["side"] == "afterhours" for c in old_style.calls if c[0] == "uw_calendar")


def test_pilot_run_with_scope_collects_the_open_window_events_within_fifty(tmp_path):
    after_close = datetime(2026, 11, 2, 22, 0, tzinfo=UTC)
    ev = [(sym("E", i), "2026-11-04", "BMO" if i % 2 else "AMC") for i in range(60)]
    prov = provider_for(ev)
    svc, _ = service(tmp_path, prov, start=after_close)
    job = J.run_cycle(svc, now=after_close, state_dir=tmp_path / "st", mode="LIVE", armed=True, ceiling=50, batch_size=5, news_pages=1, calendar_requests=14, stage_caps={"snapshot_a": 0, "snapshot_b": 18}, calendar_scope="useful")
    cal = prov.count("uw_calendar") + prov.count("finviz_calendar") + prov.count("finnhub_calendar")
    assert {c[2]["date"] for c in prov.calls if c[0] == "uw_calendar"} == {"2026-11-04"}
    assert cal <= 14 and prov.count("uw_estimates") == 18 and len(prov.calls) == 50 and prov.count("uw_news") == 50 - cal - 18
    est = [c[1] for c in prov.calls if c[0] == "uw_estimates"]
    assert all(s.startswith("E") for s in est) and job["preflight"]["calendar_dates"] == ["2026-11-04 premarket", "2026-11-04 afterhours"]


def test_useful_calendar_slots_are_per_side_and_halve_the_cost_during_the_day():
    morning = datetime(2026, 11, 2, 14, 20, tzinfo=UTC)               # Mon 09:20 ET (market hours)
    slots = J.useful_calendar_slots(morning)
    assert [(str(d), side) for d, side in slots] == [("2026-11-03", "afterhours"), ("2026-11-04", "premarket")]   # 3 Nov AMC (closes 16:00 ET today) and 4 Nov BMO (closes 06:00 ET tomorrow)
    evening = datetime(2026, 11, 2, 22, 0, tzinfo=UTC)               # Mon 17:00 ET
    assert [(str(d), side) for d, side in J.useful_calendar_slots(evening)] == [("2026-11-04", "premarket"), ("2026-11-04", "afterhours")]


def test_a_slot_refresh_requests_only_those_sides_and_stays_inside_the_cap(tmp_path):
    morning = datetime(2026, 11, 2, 14, 20, tzinfo=UTC)
    rows = [uw("G%03d" % i, "2026-11-03", "postmarket", flag="estimation") for i in range(274)] + [uw("H%03d" % i, "2026-11-04", "premarket", flag="estimation") for i in range(256)]         + [uw("X%03d" % i, "2026-11-03", "premarket", flag="estimation") for i in range(200)] + [uw("Y%03d" % i, "2026-11-04", "postmarket", flag="estimation") for i in range(400)]
    prov = Provider(rows)
    svc, _ = service(tmp_path, prov, start=morning)
    done = svc.refresh_calendar([14], dates=J.useful_calendar_slots(morning))
    sides = sorted({(c[2]["date"], c[2]["side"]) for c in prov.calls if c[0] == "uw_calendar"})
    assert sides == [("2026-11-03", "afterhours"), ("2026-11-04", "premarket")] and prov.count("uw_calendar") == 6 and prov.count("finnhub_calendar") == 1 and done == 6 + prov.count("finviz_calendar") + 1 and done <= 8   # filler tickers contain digits, so no Finviz request here
