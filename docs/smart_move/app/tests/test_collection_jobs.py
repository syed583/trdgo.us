"""Collection workflow: dry run, arming, passes, page cap, ceiling, restart, provider stops, partial failures, locks, snapshots, event status. A stub fetcher counts every call; no network."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_collection_jobs as J  # noqa: E402
import smart_move_collection_plan as CP  # noqa: E402
import smart_move_collection_runner as RUNNER  # noqa: E402
import smart_move_event_status as ES  # noqa: E402
import smart_move_live as L  # noqa: E402
import smart_move_outcomes as O  # noqa: E402
import smart_move_v2_collect as C  # noqa: E402
from smvshadow import governor as gv  # noqa: E402

UTC = timezone.utc
CUT = datetime(2026, 11, 3, 21, 0, tzinfo=UTC)
WS = CUT - timedelta(hours=24)
REL = CUT + timedelta(hours=24)
T_OPEN = WS + timedelta(hours=1)                       # inside the window, before the final-pass interval


def hl(i, hours_ago):
    return {"created_at": (T_OPEN - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z"), "headline": "Acme item %d" % i, "source": "Business Wire", "tickers": ["X"], "sentiment": "neutral"}


class Stub(L.Fetcher):
    mode = "SIMULATION"

    def __init__(self, fail=(), full_pages=False, auth_after=None):
        self.calls, self.fail, self.full_pages, self.auth_after = [], set(fail), full_pages, auth_after

    def fetch(self, kind, symbol, params):
        self.calls.append((kind, symbol, dict(params)))
        if self.auth_after is not None and len(self.calls) > self.auth_after:
            raise gv.ProviderError("auth", 401)
        if symbol in self.fail:
            raise gv.ProviderError("transient", "boom")
        if kind == "uw_news":
            rows = [hl(i + 100 * int(params.get("page", 0)), 1 + i) for i in range(25)] if self.full_pages else [hl(0, 2)]
            return json.dumps({"data": rows}).encode()
        if kind == "uw_estimates":
            return json.dumps({"data": {"estimates": [{"date": "2026-12-31", "horizon": "fiscal quarter", "eps_estimate_average": 1.5, "revenue_estimate_average": 100.0}]}}).encode()
        raise AssertionError(kind)


def make(tmp_path, fetcher, *, live=True, start=T_OPEN, authorized=700, calendar_age_h=0.5):
    clock = gv.FakeClock(start)
    cfg = L.LiveConfig(mode="SIMULATION", store_dir=tmp_path / "store", refresh_enabled=live, authorized_daily=authorized if live else 0, spacing_s=0.0)
    svc = L.RefreshService(cfg, fetcher, clock)
    svc.store.append("cal_obs", {"observed_at": (start - timedelta(hours=calendar_age_h)).isoformat(), "symbol": "ANY"})
    return svc, clock


def fresh(svc):
    """The calendar is refreshed by the (separately authorized) calendar job; these tests keep it fresh so they exercise the news / snapshot logic only."""
    svc.store.append("cal_obs", {"observed_at": svc.clock.now().isoformat(), "symbol": "ANY"})


def events(symbols, tracking=()):
    mk = lambda s: {"symbol": s, "event_date": "2026-11-04", "kind": "AMC", "plan": {"window_start_utc": WS, "cutoff_utc": CUT, "assumed_release_utc": REL}}  # noqa: E731
    win = [mk(s) for s in symbols]
    trk = [mk(s) for s in tracking]
    return lambda now, horizon=None: (trk if horizon else win)


def cycle(svc, tmp_path, evfn, **kw):
    if kw.pop("refresh_calendar", True):
        fresh(svc)
    kw.setdefault("mode", "LIVE")
    kw.setdefault("armed", True)
    return J.run_cycle(svc, now=svc.clock.now(), state_dir=tmp_path / "state", events_fn=evfn, **kw)


# ------------------------------------------------------------------ dry run and arming
def test_dry_run_is_the_default_and_sends_zero_provider_requests(tmp_path):
    f = Stub()
    svc, _ = make(tmp_path, f, live=False)
    job = J.run_cycle(svc, now=svc.clock.now(), state_dir=tmp_path / "state", events_fn=events(["A", "B"]))
    assert job["mode"] == "DRY_RUN" and job["status"] == "PENDING" and job["provider_requests"] == 0 and f.calls == []
    assert job["preflight"]["tasks"]["news"] == 2 and job["preflight"]["tasks"]["snapshot_b"] == 2 and job["preflight"]["request_estimate"] == {"min": 4, "max": 8}
    assert J._read_state(tmp_path / "state")["jobs"][job["id"]]["status"] == "PENDING"


def test_the_runner_cannot_touch_the_provider_in_dry_run_and_live_needs_every_authorization(tmp_path):
    with pytest.raises(AssertionError):
        RUNNER.NoNetworkFetcher().fetch("uw_news", "A", {})
    class A:  # noqa: E301
        mode, arm, authorize_requests = "live", False, 0
    assert len(RUNNER.live_gate(A(), {})) == 3
    A.arm, A.authorize_requests = True, 700
    assert RUNNER.live_gate(A(), {}) == ["SMART_MOVE_COLLECTION_LIVE=1 not set"]
    assert RUNNER.live_gate(A(), {"SMART_MOVE_COLLECTION_LIVE": "1"}) == []
    rc = RUNNER.main(["--store-dir", str(tmp_path / "s"), "--state-dir", str(tmp_path / "st"), "--mode", "live"])
    assert rc == 6 and not (tmp_path / "st").exists()                                                  # blocked before anything is built
    assert RUNNER.main(["--store-dir", str(tmp_path / "s"), "--state-dir", str(tmp_path / "st"), "--mode", "live", "--arm", "--authorize-requests", "5", "--as-of", "2026-11-01T00:00:00+00:00"]) in (6,)


def test_live_without_arming_or_with_the_kill_switch_engaged_is_blocked_and_sends_nothing(tmp_path):
    f = Stub()
    svc, _ = make(tmp_path, f)
    assert cycle(svc, tmp_path, events(["A"]), armed=False)["status"] == "BLOCKED" and f.calls == []
    svc.engage_kill_switch("test")
    job = cycle(svc, tmp_path, events(["A"]))
    assert job["status"] == "BLOCKED" and "KILL_SWITCH" in job["reason"] and f.calls == []


def test_a_disabled_refresh_flag_blocks_live(tmp_path):
    f = Stub()
    svc, _ = make(tmp_path, f, live=False)
    job = cycle(svc, tmp_path, events(["A"]))
    assert job["status"] == "BLOCKED" and f.calls == []


# ------------------------------------------------------------------ passes, no duplicates, final pass, cutoff
def test_initial_news_and_snapshot_b_run_once_and_a_second_cycle_sends_nothing(tmp_path):
    f = Stub()
    svc, clock = make(tmp_path, f)
    job = cycle(svc, tmp_path, events(["A", "B"]))
    assert job["status"] == "COMPLETED" and job["requests"] == 4 and sorted(c[0] for c in f.calls) == ["uw_estimates", "uw_estimates", "uw_news", "uw_news"]
    clock.sleep(600)
    again = cycle(svc, tmp_path, events(["A", "B"]))
    assert again["tasks_planned"] == 0 and again["requests"] == 0 and len(f.calls) == 4


def test_final_pass_runs_between_six_and_one_hours_before_the_cutoff_and_never_after(tmp_path):
    f = Stub()
    svc, clock = make(tmp_path, f)
    cycle(svc, tmp_path, events(["A"]))
    n0 = len(f.calls)
    clock.sleep(int((CUT - timedelta(hours=7) - clock.now()).total_seconds()))                      # 7 h before the cutoff: nothing due
    assert cycle(svc, tmp_path, events(["A"]))["requests"] == 0
    clock.sleep(int(timedelta(hours=2).total_seconds()))                                              # 5 h before: FINAL news (snapshot B is already satisfied)
    j = cycle(svc, tmp_path, events(["A"]))
    assert j["requests"] == 1 and f.calls[-1][0] == "uw_news" and len(f.calls) == n0 + 1
    clock.sleep(int((CUT - timedelta(minutes=30) - clock.now()).total_seconds()))                    # 30 min before: too close, nothing
    assert cycle(svc, tmp_path, events(["A"]))["requests"] == 0
    clock.sleep(3600)                                                                                  # after the cutoff
    assert cycle(svc, tmp_path, events(["A"]))["requests"] == 0


def test_a_ticker_never_gets_more_than_three_news_pages(tmp_path):
    f = Stub(full_pages=True)
    svc, _ = make(tmp_path, f)
    job = cycle(svc, tmp_path, events(["A"]))
    news = [c for c in f.calls if c[0] == "uw_news"]
    assert [c[2].get("page") for c in news] == [None, 1, 2] and job["requests"] == 4                  # 3 news pages + 1 estimates snapshot


# ------------------------------------------------------------------ budget, restart, provider stops
def test_the_run_ceiling_is_enforced_and_reported_as_partial(tmp_path):
    f = Stub()
    svc, _ = make(tmp_path, f)
    job = cycle(svc, tmp_path, events(["A", "B", "C", "D", "E"]), ceiling=4, batch_size=2)
    assert job["status"] == "PARTIAL" and len(f.calls) <= 4 and job["halted"] == "RUN_CEILING_REACHED"
    assert J._read_state(tmp_path / "state")["requests_by_day"][T_OPEN.date().isoformat()] == len(f.calls)


def test_an_interrupted_run_resumes_without_repeating_a_request(tmp_path):
    f = Stub()
    svc, _ = make(tmp_path, f)
    ev = events(["A", "B", "C"])
    calls_before_stop = {"n": 0}

    def stop_after_first_batch():
        return len(f.calls) >= 2
    first = cycle(svc, tmp_path, ev, batch_size=2, stopped=stop_after_first_batch)
    assert first["status"] == "PARTIAL" and 2 <= len(f.calls) < 6
    done_first = len(f.calls)
    second = cycle(svc, tmp_path, ev, batch_size=2)
    assert second["status"] == "COMPLETED" and len(f.calls) == 6 and len(set((c[0], c[1]) for c in f.calls)) == 6 and done_first < 6     # every (kind, symbol) requested exactly once
    del calls_before_stop


def test_an_authentication_failure_stops_everything_for_the_day(tmp_path):
    f = Stub(auth_after=1)
    svc, _ = make(tmp_path, f)
    job = cycle(svc, tmp_path, events(["A", "B", "C"]), batch_size=1)
    assert job["status"] in ("PARTIAL", "FAILED") and job["halted"] in ("PROVIDER_STOP", "PROVIDER_STOPPED") and len(f.calls) <= 3
    n = len(f.calls)
    job2 = cycle(svc, tmp_path, events(["A", "B", "C"]))
    assert job2["status"] == "BLOCKED" and "PROVIDER_STOPPED_TODAY" in job2["reason"] and len(f.calls) == n


def test_a_partial_provider_failure_is_recorded_and_retried_later_without_touching_finished_work(tmp_path):
    f = Stub(fail={"B"})
    svc, clock = make(tmp_path, f)
    job = cycle(svc, tmp_path, events(["A", "B"]))
    assert job["status"] == "PARTIAL" and job["failures"] and job["tasks_done"] >= 2
    recs = svc.store.read(C.EVIDENCE)
    assert {r["symbol"] for r in recs if r["status"] == "OK"} == {"A"}                                  # B left no OK record: it is a failure, not "empty news"
    f.fail.clear()
    clock.sleep(300)
    n = len(f.calls)
    assert cycle(svc, tmp_path, events(["A", "B"]))["status"] == "COMPLETED" and all(c[1] == "B" for c in f.calls[n:])


def test_overlapping_jobs_are_refused_and_a_stale_lock_is_recovered(tmp_path):
    f = Stub()
    svc, _ = make(tmp_path, f)
    d = tmp_path / "state"
    assert J.acquire_lock(d, T_OPEN)
    assert cycle(svc, tmp_path, events(["A"]))["status"] == "BLOCKED" and f.calls == []
    J.release_lock(d)
    (d / J.LOCK_FILE).write_text(json.dumps({"at": (T_OPEN - timedelta(hours=3)).isoformat()}))
    assert cycle(svc, tmp_path, events(["A"]))["status"] == "COMPLETED" and not (d / J.LOCK_FILE).exists()


def test_a_stale_calendar_blocks_collection_when_it_cannot_be_refreshed(tmp_path):
    class NoCal(Stub):
        def fetch(self, kind, symbol, params):
            if kind in ("uw_calendar", "finviz_calendar", "finnhub_calendar"):
                raise gv.ProviderError("transient", "down")
            return super().fetch(kind, symbol, params)
    f = NoCal()
    svc, _ = make(tmp_path, f, calendar_age_h=9.0)
    job = cycle(svc, tmp_path, events(["A"]), refresh_calendar=False)
    assert job["status"] == "BLOCKED" and job["reason"].startswith("CALENDAR_NOT_FRESH") and not any(c[0] in ("uw_news", "uw_estimates") for c in f.calls)


# ------------------------------------------------------------------ analyst two-snapshot design
def ev1(symbol="A", release=REL):
    return {"symbol": symbol, "event_date": "2026-11-04", "release_utc": release, "window_start_utc": release - timedelta(hours=48), "cutoff_utc": release - timedelta(hours=24)}


def test_snapshot_a_is_due_about_ten_days_before_and_b_inside_the_window():
    e = ev1()
    assert CP.snapshot_stage(e["release_utc"], e["window_start_utc"], e["cutoff_utc"], REL - timedelta(days=13)) is None
    assert CP.snapshot_stage(e["release_utc"], e["window_start_utc"], e["cutoff_utc"], REL - timedelta(days=11)) == "A"
    assert CP.snapshot_stage(e["release_utc"], e["window_start_utc"], e["cutoff_utc"], REL - timedelta(days=8)) is None
    assert CP.snapshot_stage(e["release_utc"], e["window_start_utc"], e["cutoff_utc"], WS + timedelta(hours=1)) == "B"
    assert CP.snapshot_stage(e["release_utc"], e["window_start_utc"], e["cutoff_utc"], CUT) is None            # within the last hour of the cutoff: no new snapshot
    assert WS - (REL - timedelta(days=CP.SNAP_A_TO_DAYS)) >= timedelta(days=7)                                           # the latest A is at least 7 days before the earliest B


def est(sym, when, status="OK"):
    return {"symbol": sym, "kind": "uw_estimates", "status": status, "retrieved_at": when.isoformat(), "rows": 41}


def test_snapshot_dedup_reschedule_reuse_and_failed_records_do_not_count():
    now = REL - timedelta(days=10)
    assert [t["stage"] for t in CP.snapshot_stage_tasks_due([ev1()], [], now)] == ["A"]
    assert CP.snapshot_stage_tasks_due([ev1()], [est("A", now - timedelta(hours=3))], now) == []                  # A already taken
    assert [t["stage"] for t in CP.snapshot_stage_tasks_due([ev1()], [est("A", now - timedelta(hours=3), "INVALID")], now)] == ["A"]
    moved = ev1(release=REL + timedelta(days=2))                                                                     # rescheduled 2 days later: the earlier record still counts as A
    assert CP.snapshot_stage_tasks_due([moved], [est("A", REL - timedelta(days=10))], REL - timedelta(days=9, hours=12)) == []
    ids = {t["id"] for t in CP.snapshot_stage_tasks_due([ev1(), ev1("B")], [], now)}
    assert ids == {"uw_estimates-A-2026-11-04-A", "uw_estimates-B-2026-11-04-A"}


def snap(when, rows):
    return {"retrieved_at": when.isoformat(), "period_rows": rows}


def test_revision_needs_the_same_fiscal_quarter_seven_to_sixty_days_apart_and_nothing_after_the_cutoff():
    a = snap(REL - timedelta(days=10), {"2026-09-30": {"eps": 1.00, "revenue": 100.0}, "2026-12-31": {"eps": 2.0, "revenue": 200.0}})
    b = snap(CUT - timedelta(hours=5), {"2026-09-30": {"eps": 1.10, "revenue": 101.0}})
    r = CP.revision_state([a, b], CUT)
    assert r["state"] == "REVISION_MEASURABLE" and r["quarter"] == "2026-09-30" and r["gap_days"] >= 8 and r["eps_revision_pct"] == 10.0 and r["revenue_revision_pct"] == 1.0
    assert CP.revision_state([a], CUT) == {"state": "INSUFFICIENT_HISTORY", "reason": "FEWER_THAN_TWO_SNAPSHOTS_BEFORE_CUTOFF", "snapshots": 1}
    late = snap(CUT + timedelta(hours=1), {"2026-09-30": {"eps": 9.0, "revenue": 1.0}})
    assert CP.revision_state([a, late], CUT)["state"] == "INSUFFICIENT_HISTORY"                                        # retrieved after the cutoff: invisible
    other = snap(CUT - timedelta(hours=5), {"2027-03-31": {"eps": 1.0, "revenue": 1.0}})
    assert CP.revision_state([a, other], CUT)["reason"] == "NO_COMMON_FISCAL_QUARTER_IN_THE_TWO_SNAPSHOTS"
    close = snap(CUT - timedelta(days=3), {"2026-09-30": {"eps": 1.0, "revenue": 1.0}})
    assert CP.revision_state([close, b], CUT)["reason"] == "SNAPSHOTS_NOT_7_TO_60_DAYS_APART"


# ------------------------------------------------------------------ tracking horizon and event status
def test_the_tracking_horizon_defaults_to_fourteen_days_and_scoring_freshness_is_unchanged():
    c = L.LiveConfig()
    assert c.tracking_days_ahead == 14 and c.calendar_days_ahead == 3 and c.calendar_interval_h == 6.0


def test_event_status_keeps_missing_news_snapshots_and_flow_in_distinct_states():
    e = {"symbol": "A", "event_date": "2026-11-04", "kind": "AMC", "plan": {"window_start_utc": WS, "cutoff_utc": CUT, "assumed_release_utc": REL}}
    recs = [{"kind": "uw_news", "symbol": "A", "status": "OK", "rows": 3, "retrieved_at": (WS + timedelta(hours=2)).isoformat()},
            {"kind": "uw_estimates", "symbol": "A", "status": "INVALID", "rows": 0, "retrieved_at": (WS + timedelta(hours=2)).isoformat()},
            {"kind": "uw_flow_alerts", "symbol": "A", "status": "OK", "rows": 0, "retrieved_at": (WS + timedelta(hours=1)).isoformat()}]
    rows = ES.event_rows([e, dict(e, symbol="Z", kind=None)], recs, [{"kind": "ohlc", "symbol": "A"}], [], {("A", "2026-11-04"): {"state": "WAITING"}}, 2.0, WS + timedelta(hours=3))
    a, z = rows
    assert (a["news_initial"], a["news_final"], a["analyst_snapshot_a"], a["analyst_snapshot_b"], a["options_flow"]) == ("OK", "NOT_ATTEMPTED", "NOT_ATTEMPTED", "FAILED", "EMPTY")
    assert a["calendar"]["state"] == "FRESH" and a["price_sector_evidence"] == "OK" and a["outcome_status"] == "WAITING" and a["prediction_status"] == "NO_PREDICTION_YET"
    assert z["kind"] == "UNKNOWN" and "timing:UNKNOWN" in z["missing_reasons"] and z["price_sector_evidence"] == "NOT_COLLECTED" and z["outcome_status"] == "NOT_DUE"
    assert ES.summarize(rows)["events"] == 2


def test_event_status_reports_coverage_prediction_status_and_stale_calendar():
    e = {"symbol": "A", "event_date": "2026-11-04", "kind": "BMO", "plan": {"window_start_utc": WS, "cutoff_utc": CUT, "assumed_release_utc": REL}}
    pred = {"symbol": "A", "earnings_date": "2026-11-04", "version": 1, "engine": {"continuation": {"signal": "NO_TRADE", "available": 20, "max": 40, "reason_codes": ["INSUFFICIENT_COVERAGE"]}}, "blocked_by": ["STALE_CALENDAR"]}
    r = ES.event_rows([e], [], [], [pred], {}, 9.0, WS + timedelta(hours=1))[0]
    assert r["prediction_status"] == "DATA_INCOMPLETE" and r["directional_coverage"]["fraction"] == 0.5 and "calendar:STALE" in r["missing_reasons"] and "STALE_CALENDAR" in r["missing_reasons"]


def test_outcome_readiness_separates_due_waiting_graded_and_quarantined(tmp_path):
    now = datetime(2026, 11, 5, 22, tzinfo=UTC)
    O.record(tmp_path, {"symbol": "G", "earnings_date": "2026-11-02", "mode": "LIVE", "status": "GRADED", "move_pct": 1.0})
    O.record(tmp_path, {"symbol": "Q", "earnings_date": "2026-11-02", "mode": "LIVE", "status": "UNVERIFIED_CORPORATE_ACTION"})
    evs = [{"symbol": s, "earnings_date": d, "kind": k} for s, d, k in (("G", "2026-11-02", "AMC"), ("Q", "2026-11-02", "AMC"), ("D", "2026-11-03", "AMC"), ("W", "2026-11-05", "AMC"), ("U", "2026-11-03", None))]
    c = O.readiness(evs, tmp_path, now)["counts"]
    assert c == {"GRADED": 1, "UNVERIFIED_CORPORATE_ACTION": 1, "DUE": 1, "WAITING": 1, "UNKNOWN_TIMING": 1}


def test_no_news_record_is_ever_read_as_neutral_sentiment():
    assert ES._state([], WS, CUT) == "NOT_ATTEMPTED" and ES._state([{"status": "OK", "rows": 0, "retrieved_at": WS.isoformat()}], WS, CUT) == "EMPTY"


# ------------------------------------------------------------------ runner end to end (dry run) and the reporting route
def test_runner_dry_run_writes_the_status_report_with_zero_requests_and_the_route_serves_it(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import smart_move_local_server as srv
    store = tmp_path / "store"
    store.mkdir()
    (store / "cal_obs.jsonl").write_text("")                                                              # no calendar observation at all: the plan must say so, not guess
    rc = RUNNER.main(["--store-dir", str(store), "--state-dir", str(tmp_path / "state"), "--as-of", "2026-11-01T10:30:00+00:00"])
    assert rc == 0
    rep = json.loads((tmp_path / "state" / ES.STATUS_FILE).read_text(encoding="utf-8"))
    assert rep["summary"]["events"] == 0
    pre = [j["preflight"] for j in J._read_state(tmp_path / "state")["jobs"].values()][0]
    assert pre["calendar_state"] == "UNKNOWN" and pre["calendar_refresh_needed"] is True and pre["eligible_window_events"] == 0
    st = J._read_state(tmp_path / "state")
    assert [j["mode"] for j in st["jobs"].values()] == ["DRY_RUN"] and all(j.get("provider_requests", 0) == 0 for j in st["jobs"].values())
    monkeypatch.setenv("SMART_MOVE_COLLECTION_STATE_DIR", str(tmp_path / "state"))
    j = TestClient(srv.app).get("/api/smart-move/tracking/collection-status").json()
    assert j["available"] is True and j["sends_requests"] is False and j["summary"]["events"] == 0
    monkeypatch.setenv("SMART_MOVE_COLLECTION_STATE_DIR", str(tmp_path / "none"))
    assert TestClient(srv.app).get("/api/smart-move/tracking/collection-status").json()["available"] is False


def test_preflight_counts_the_requests_the_governor_already_logged_in_a_copied_store(tmp_path):
    """Found live: a store copied from an earlier run carries that run's request log; the governor counts it against today's cap, so the preflight must say so instead of reporting a full budget."""
    f = Stub()
    svc, _ = make(tmp_path, f)
    for i in range(50):
        svc.store.append("requests", {"at": svc.clock.now().isoformat(), "kind": "uw_news", "symbol": "S%d" % i, "request_id": "old%d" % i, "attempts": 1, "provider": "UNUSUAL_WHALES"})
    svc2 = L.RefreshService(svc.cfg, f, svc.clock, store=svc.store)                      # a service built on the copied store seeds its counters from that log
    fresh(svc2)
    job = J.run_cycle(svc2, now=svc2.clock.now(), state_dir=tmp_path / "state", mode="DRY_RUN", events_fn=events(["A"]), ceiling=50)
    pf = job["preflight"]
    assert pf["governor_used_today"] == 50 and pf["daily_left"] == 0 and pf["allowed"] is False
    assert any(b.startswith("GOVERNOR_DAILY_CAP_ALREADY_USED_IN_THIS_STORE") for b in pf["blockers"])
    svc3, _ = make(tmp_path / "clean", Stub())
    fresh(svc3)
    clean = J.run_cycle(svc3, now=svc3.clock.now(), state_dir=tmp_path / "state2", mode="DRY_RUN", events_fn=events(["A"]), ceiling=50)["preflight"]
    assert clean["governor_used_today"] == 0 and clean["daily_left"] == 50 and clean["allowed"] is True


def test_owner_daily_limit_100_keeps_the_logged_pilot_attempts_and_leaves_50_additional(tmp_path):
    f = Stub()
    svc, _ = make(tmp_path, f)
    for i in range(50):
        svc.store.append("requests", {"at": svc.clock.now().isoformat(), "kind": "uw_news", "symbol": "S%d" % i, "request_id": "old%d" % i, "attempts": 1, "provider": "UNUSUAL_WHALES"})
    svc2 = L.RefreshService(svc.cfg, f, svc.clock, store=svc.store)
    fresh(svc2)
    pf = J.run_cycle(svc2, now=svc2.clock.now(), state_dir=tmp_path / "s", mode="DRY_RUN", events_fn=events(["A"]), ceiling=50, daily_limit=100)["preflight"]
    assert pf["governor_used_today"] == 50 and pf["governor_daily_limit"] == 100 and pf["daily_left"] == 50
    pf = J.run_cycle(svc2, now=svc2.clock.now(), state_dir=tmp_path / "s2", mode="DRY_RUN", events_fn=events(["A"]), ceiling=50, daily_limit=60)["preflight"]
    assert pf["daily_left"] == 10                                               # a lower owner limit still caps the day
    assert len(svc2.store.read("requests")) == 50                               # the ledger is untouched

