"""P1 audit fixes. F1: tradability computed from the frozen rule inside the real selection path and verified against the REAL scorer's own output (no hand-built rows). F2: the universe statuses keep 'not tradable',
'deferred by budget', 'served' and 'eligibility unknown' apart. F4: the per-run maximum is measured in provider ATTEMPTS (successful, failed and retried), with the 100-per-New-York-day cap preserved. Stub provider only."""
import json
import sys
from argparse import Namespace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "docs" / "smart_move" / "shadow_impl"))
sys.path.insert(0, str(ROOT / "docs" / "smart_move" / "app"))
import smart_move_calendar_tiers as CT  # noqa: E402
import smart_move_collection_runner as RUNNER  # noqa: E402
import smart_move_engine20 as E20  # noqa: E402
import smart_move_live as L  # noqa: E402
import smart_move_live_publisher as PUB  # noqa: E402
import smart_move_universe as UNI  # noqa: E402
from smvshadow import cutoff as co  # noqa: E402
from smvshadow import governor as gv  # noqa: E402

UTC = timezone.utc
SAT = datetime(2026, 11, 7, 22, 0, tzinfo=UTC)                         # Saturday 17:00 ET: Monday BMO and AMC windows are open


def bars(last="2026-11-06", n=60):
    rows, d = [], date.fromisoformat(last)
    while len(rows) < n:
        if d.weekday() < 5:
            rows.append({"date": d.isoformat(), "open": "100", "high": "102", "low": "99", "close": "101", "volume": 1_000_000, "total_volume": 1_000_000, "market_time": "r"})
        d -= timedelta(days=1)
    return {"data": sorted(rows, key=lambda r: r["date"])}


class Prov(L.Fetcher):
    """Counts every call; optional failures by (kind, symbol) pattern; calendar slots from a dict."""
    mode = "SIMULATION"

    def __init__(self, fail=(), cal=None, fail_kinds=()):
        self.calls, self.fail, self.cal, self.fail_kinds = [], set(fail), cal or {}, set(fail_kinds)

    def fetch(self, kind, symbol, params):
        self.calls.append((kind, symbol))
        if symbol in self.fail or kind in self.fail_kinds:
            raise gv.ProviderError("transient", "boom")
        if kind == "ohlc":
            return json.dumps(bars()).encode()
        if kind == "earnings":
            return json.dumps({"data": [{"report_date": "2026-08-01", "report_time": "premarket", "ending_fiscal_quarter": "2026-06-30", "post_earnings_move_1d": "0.02"}]}).encode()
        if kind == "uw_calendar":
            return json.dumps({"data": [{"symbol": s, "report_date": params["date"], "report_time": params["side"], "source": "company", "marketcap": "5e9", "sector": "Technology"} for s in self.cal.get((params["date"], params["side"]), [])]}).encode()
        if kind == "finviz_calendar":
            return b"Ticker,Earnings\n"
        if kind == "finnhub_calendar":
            return json.dumps({"earningsCalendar": []}).encode()
        raise AssertionError(kind)


def svc_at(tmp, prov, now=SAT, authorized=100, retries=0):
    cfg = L.LiveConfig(mode="SIMULATION", store_dir=tmp / "store", refresh_enabled=True, authorized_daily=authorized, spacing_s=0.0, max_retries=retries)
    return L.RefreshService(cfg, prov, gv.FakeClock(now))


def cal(sym, d, session, at, cap=5e9, uw_label=None, fh_hour=None, fh_date=None):
    uw, fh = uw_label or ("premarket" if session == "BMO" else "afterhours"), fh_hour or session.lower()
    base = {"symbol": sym, "clock": None, "mode": "LIVE", "observed_at": at.isoformat(), "request_id": "r", "company": sym + " INC", "sector": "Technology", "marketcap": str(int(cap))}
    return [dict(base, date=d, source="UW_CALENDAR", label=uw, vendor_flag="company", raw_sha256="u" + sym), dict(base, date=fh_date or d, source="FINNHUB", label=fh, vendor_flag=None, raw_sha256="f" + sym)]


def add(svc, sym, d, session, at=None, **kw):
    for r in cal(sym, d, session, (at or SAT) - timedelta(hours=1), **kw):
        svc.store.append("cal_obs", r)


# =================================================================== F1: tradability on the real scorer path
def test_the_real_scorer_rows_carry_no_tradable_flag_and_selection_still_skips_the_untradable_monday_bmo(tmp_path):
    svc = svc_at(tmp_path, Prov())
    add(svc, "MBMO", "2026-11-09", "BMO")
    add(svc, "MAMC", "2026-11-09", "AMC")
    rows = L.LiveScorer(svc).score_all(SAT)
    assert rows and all("tradable" not in r["plan"] for r in rows)                                   # the root cause of F1: the row has no such key
    caps = {"MBMO": 9e9, "MAMC": 8e9}
    audit = []
    assert [s["symbol"] for s in E20.select_top(rows, caps, SAT, n=10, policy="deadline_priority", audit=audit)] == ["MAMC"]
    skipped = next(a for a in audit if a["symbol"] == "MBMO")
    assert skipped["status"] == "SKIPPED" and "NOT_TRADABLE" in skipped["reason"]
    assert [s["symbol"] for s in E20.select_top(rows, caps, SAT, n=10, policy="deadline")] == ["MBMO", "MAMC"]       # the older policy is untouched


def test_the_rule_agrees_with_the_real_scorers_own_not_tradable_verdict_for_forty_events(tmp_path):
    svc = svc_at(tmp_path, Prov())
    mc, d, n = co._mc(), date(2026, 11, 9), 0
    while d <= date(2026, 12, 4):
        if mc.is_trading_day(d):
            for k in ("BMO", "AMC"):
                n += 1
                add(svc, "E%02d%s" % (n, k[0]), d.isoformat(), k)
        d += timedelta(days=1)
    rows = L.LiveScorer(svc).score_all(SAT)
    assert len(rows) == n >= 38
    untradable = 0
    for r in rows:
        scorer_says = "NOT_TRADABLE" in r["engine"]["continuation"]["reason_codes"]
        assert (E20.tradability(r) is False) == scorer_says, r["symbol"]                              # exactly the events the frozen scorer gates, no more and no fewer
        assert E20.tradability(r) in (True, False)
        untradable += scorer_says
    assert untradable == 5                                                                          # the four Monday BMO events plus Friday Nov 27 BMO (the day after Thanksgiving)


def test_prediction_outcomes_are_unchanged_when_untradable_events_get_no_evidence(tmp_path):
    def collect(policy, sub):
        svc = svc_at(tmp_path / sub, Prov())
        add(svc, "MBMO", "2026-11-09", "BMO")
        add(svc, "MAMC", "2026-11-09", "AMC")
        rows = L.LiveScorer(svc).score_all(SAT)
        sel = E20.select_top(rows, {"MBMO": 9e9, "MAMC": 8e9}, SAT, n=10, policy=policy)
        plan = E20.plan_tasks(svc, sel, SAT, budget=50)
        res = E20.run(svc, plan["funded"], now=SAT, ceiling=50, state_dir=tmp_path / sub / "st")
        return svc, {r["symbol"]: E20.classify(r, SAT) for r in L.LiveScorer(svc).score_all(SAT)}, res
    _, old, res_old = collect("deadline", "old")
    _, new, res_new = collect("deadline_priority", "new")
    assert res_old["requests_attempted"] > res_new["requests_attempted"]                              # the budget saved
    assert old["MBMO"]["result"] == new["MBMO"]["result"] == "NO VALID PREDICTION" and "NOT_TRADABLE" in old["MBMO"]["reason_codes"] and "NOT_TRADABLE" in new["MBMO"]["reason_codes"]
    keys = ("result", "bull", "bear", "coverage_points", "liquidity", "reason_codes", "window")
    assert {k: old["MAMC"][k] for k in keys} == {k: new["MAMC"][k] for k in keys}                    # the tradable event is scored identically
    assert old["MBMO"]["bull"] == new["MBMO"]["bull"] or new["MBMO"]["result"] == old["MBMO"]["result"]


def test_missing_or_uncertain_information_never_causes_a_skip():
    base = {"symbol": "X", "kind": "BMO", "earnings_date": "2026-11-09"}
    assert E20.tradability(base) is False and E20.tradability(dict(base, earnings_date="2026-11-10")) is True
    for bad in (dict(base, kind=None), dict(base, kind="UNKNOWN"), dict(base, kind="DMH"), dict(base, earnings_date=None), dict(base, earnings_date=""), dict(base, earnings_date="garbage"), dict(base, earnings_date="2026-13-45"), {}):
        assert E20.tradability(bad) is None, bad
    real = co.plan_event(date(2026, 11, 9), "BMO")
    agree = dict(base, plan={"window_start_utc": real["window_start_utc"].isoformat(), "cutoff_utc": real["cutoff_utc"].isoformat()})
    assert E20.tradability(agree) is False
    moved = dict(base, plan={"window_start_utc": real["window_start_utc"].isoformat(), "cutoff_utc": (real["cutoff_utc"] + timedelta(hours=3)).isoformat()})     # another input moved the window: unsure, do not skip
    assert E20.tradability(moved) is None
    now = SAT
    row = {"symbol": "MOVED", "company": "M", "earnings_date": "2026-11-09", "kind": "BMO", "blocked_by": [], "plan": {"window_start_utc": (real["window_start_utc"]).isoformat(), "cutoff_utc": (real["cutoff_utc"] + timedelta(hours=3)).isoformat()}}
    audit = []
    assert [s["symbol"] for s in E20.select_top([row], {"MOVED": 1e9}, now, n=5, policy="deadline_priority", audit=audit)] == ["MOVED"]                                       # selected, not skipped
    broken = dict(row, symbol="BROKEN", earnings_date="garbage")
    assert [s["symbol"] for s in E20.select_top([broken], {"BROKEN": 1e9}, now, n=5, policy="deadline_priority")] == ["BROKEN"]


# =================================================================== F2: universe statuses
def test_universe_keeps_not_tradable_deferred_served_and_unknown_apart(tmp_path):
    prov = Prov()
    svc = svc_at(tmp_path, prov)
    add(svc, "MBMO", "2026-11-09", "BMO")                                                           # permanently not tradable
    for s, cap in (("SERV", 9e9), ("DEFR", 1e8)):
        add(svc, s, "2026-11-09", "AMC", cap=cap)                                                   # tradable: one served, one deferred by budget
    add(svc, "CONF", "2026-11-09", "AMC", fh_date="2026-11-12")                                     # the vendors disagree on the date
    add(svc, "NOSS", "2026-11-09", "AMC", uw_label="unknown", fh_hour="unknown")                    # session not confirmed
    rows = L.LiveScorer(svc).score_all(SAT)
    sel = E20.select_top(rows, {"MBMO": 9e9, "SERV": 9e9, "DEFR": 1e8, "CONF": 5e9, "NOSS": 5e9}, SAT, n=1, policy="deadline_priority")
    plan = E20.plan_tasks(svc, sel, SAT, budget=50)
    E20.run(svc, plan["funded"], now=SAT, ceiling=50, state_dir=tmp_path / "st")
    uni = UNI.build(svc, SAT, horizon_days=30, slot_state={"version": 1, "slots": {}, "symbol_index": {}, "refreshes": 0}, funded_symbols=set(), selected_symbols={s["symbol"] for s in sel})
    by = {r["symbol"]: r for r in uni["rows"]}
    assert by["MBMO"]["collection_status"] == "NOT_TRADABLE" and by["MBMO"]["tradable"] is False and by["MBMO"]["deferred"] is False and "No evidence is requested" in by["MBMO"]["status_note"]
    assert by["SERV"]["collection_status"] == "EVIDENCE_COMPLETE" and by["SERV"]["tradable"] is True
    assert by["DEFR"]["collection_status"] == "DEFERRED" and by["DEFR"]["deferred"] is True and "still eligible" in by["DEFR"]["deferred_reason"]
    assert by["CONF"]["collection_status"] == "ELIGIBILITY_UNKNOWN" and by["CONF"]["date_confirmed"] is False and "disagree" in by["CONF"]["status_note"] and by["CONF"]["deferred"] is False
    assert by["NOSS"]["collection_status"] == "ELIGIBILITY_UNKNOWN" and "session not confirmed" in by["NOSS"]["status_note"]
    assert "DEFERRED" not in {by[s]["collection_status"] for s in ("MBMO", "CONF", "NOSS")}              # nothing uncertain or untradable is ever called deferred
    assert uni["summary"]["by_status"] == {"NOT_TRADABLE": 1, "EVIDENCE_COMPLETE": 1, "DEFERRED": 1, "ELIGIBILITY_UNKNOWN": 2} and uni["summary"]["deferred"] == 1 and uni["summary"]["excluded_by_proxy"] == 0


def test_a_ledger_prediction_marks_served_and_beats_every_other_status(tmp_path):
    svc = svc_at(tmp_path, Prov())
    add(svc, "SERV", "2026-11-09", "AMC")
    add(svc, "MBMO", "2026-11-09", "BMO")
    led = tmp_path / "live_predictions.jsonl"
    PUB._append(led, {"schema": PUB.SCHEMA, "record_type": "PREDICTION", "symbol": "SERV", "event_date": "2026-11-09", "session": "AMC", "result": "NO_TRADE"}, "0" * 64, 1)
    uni = UNI.build(svc, SAT, horizon_days=30, slot_state={"version": 1, "slots": {}, "symbol_index": {}, "refreshes": 0}, ledger_path=led)
    by = {r["symbol"]: r["collection_status"] for r in uni["rows"]}
    assert by["SERV"] == "PREDICTED:NO_TRADE" and by["MBMO"] == "NOT_TRADABLE" and uni["ledger_ok"] is True


# =================================================================== F4: attempts, not tasks
def tasks_for(svc, n):
    for i in range(n):
        add(svc, "S%02d" % i, "2026-11-09", "AMC", cap=9e9 - i)
    rows = L.LiveScorer(svc).score_all(SAT)
    sel = E20.select_top(rows, {"S%02d" % i: 9e9 - i for i in range(n)}, SAT, n=n, policy="deadline")
    return E20.plan_tasks(svc, sel, SAT, budget=500)["funded"]


def test_retried_requests_count_against_the_hard_per_run_maximum(tmp_path):
    prov = Prov(fail_kinds=("ohlc", "earnings"))                                                     # every request fails
    svc = svc_at(tmp_path, prov, authorized=100, retries=2)                                          # and each failure is retried twice: 3 attempts per task
    res = E20.run(svc, tasks_for(svc, 30), now=SAT, ceiling=10, state_dir=tmp_path / "st", max_consecutive_failures=999)
    assert len(prov.calls) <= 10 and res["attempts"] <= 10 and E20.attempts_of(svc) <= 10 and res["halted"] == "CEILING_REACHED"
    assert res["requests_attempted"] == 3 and len(prov.calls) == 9                                   # three whole tasks (3 attempts each); a fourth could have needed 3 more, so it was not started


def test_without_retries_the_old_task_arithmetic_is_unchanged_and_attempts_equal_tasks(tmp_path):
    prov = Prov(fail=("S03", "S07"))
    svc = svc_at(tmp_path, prov, retries=0)
    res = E20.run(svc, tasks_for(svc, 30), now=SAT, ceiling=12, state_dir=tmp_path / "st", max_consecutive_failures=999)
    assert res["requests_attempted"] == res["attempts"] == len(prov.calls) == 12 and res["halted"] == "CEILING_REACHED" and res["failed"] >= 1


def test_partial_runs_with_failures_never_pass_the_ceiling_and_failed_calls_count(tmp_path):
    prov = Prov(fail=("S01", "S02", "S05", "S06", "S09"))
    svc = svc_at(tmp_path, prov, retries=1)
    res = E20.run(svc, tasks_for(svc, 40), now=SAT, ceiling=25, state_dir=tmp_path / "st", max_consecutive_failures=999)
    assert len(prov.calls) <= 25 and res["attempts"] == len(prov.calls) and res["failed"] > 0


def test_calendar_and_evidence_together_stay_inside_50_attempts_even_with_failures_and_retries(tmp_path):
    for retries in (0, 1, 2):
        sub = tmp_path / str(retries)
        prov = Prov(fail_kinds=("uw_calendar",) if retries == 2 else (), fail=("S04", "S05") if retries else (), cal={("2026-11-09", "afterhours"): ["S%02d" % i for i in range(60)]})
        svc = svc_at(sub, prov, authorized=100, retries=retries)
        for i in range(60):
            add(svc, "S%02d" % i, "2026-11-09", "AMC", cap=9e9 - i)
        stt = CT.load_state(sub / "cal")
        a = Namespace(ceiling=50, calendar_horizon_days=30)
        att0 = E20.attempts_of(svc)
        plan = CT.plan_refresh(SAT, stt, horizon_days=30, run_ceiling=50, budget_left=50, evidence_demand=48)
        g = RUNNER.tiered_refresh(svc, a, plan, stt, sub / "cal")
        used = max(g["requests"], E20.attempts_of(svc) - att0)
        rows = L.LiveScorer(svc).score_all(SAT)
        sel = E20.select_top(rows, {"S%02d" % i: 9e9 - i for i in range(60)}, SAT, n=60, policy="deadline_priority")
        ev = E20.plan_tasks(svc, sel, SAT, budget=50 - used)
        res = E20.run(svc, ev["funded"], now=SAT, ceiling=50 - used, state_dir=sub / "st", max_consecutive_failures=999)
        assert len(prov.calls) <= 50 and E20.attempts_of(svc) <= 50 and used + res["attempts"] <= 50, (retries, len(prov.calls))


def test_the_100_per_new_york_day_cap_is_preserved_by_the_governor(tmp_path):
    prov = Prov()
    svc = svc_at(tmp_path, prov, authorized=100)
    svc.gov.used_day["2026-11-07"] = 95                                                              # 95 attempts already logged for this New York day
    res = E20.run(svc, tasks_for(svc, 30), now=SAT, ceiling=50, state_dir=tmp_path / "st", max_consecutive_failures=999)
    assert len(prov.calls) <= 5 and sum(svc.gov.used_day.values()) <= 100                            # the day cap, not the run ceiling, is what binds
    nxt = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)                                                   # Sunday 07:00 ET: a new New York day, the Tuesday BMO window is open
    prov2 = Prov()
    svc2 = svc_at(tmp_path / "next", prov2, now=nxt, authorized=100)
    svc2.gov.used_day["2026-11-07"] = 95
    for i in range(30):
        add(svc2, "S%02d" % i, "2026-11-10", "BMO", at=nxt, cap=9e9 - i)
    rows = L.LiveScorer(svc2).score_all(nxt)
    sel = E20.select_top(rows, {"S%02d" % i: 9e9 - i for i in range(30)}, nxt, n=30, policy="deadline")
    res2 = E20.run(svc2, E20.plan_tasks(svc2, sel, nxt, budget=500)["funded"], now=nxt, ceiling=50, state_dir=tmp_path / "next" / "st", max_consecutive_failures=999)
    assert len(prov2.calls) == 50 and res2["attempts"] == 50                                         # the new day starts from zero; the 50-attempt run ceiling binds


def test_the_per_run_ceiling_itself_cannot_exceed_50_without_an_explicit_environment_decision():
    assert RUNNER.PILOT_MAX == 50 and RUNNER.DAILY_LIMIT_MAX == 100
    assert RUNNER.main(["--store-dir", "x", "--state-dir", "y", "--mode", "live", "--arm", "--authorize-requests", "51", "--profile", "engine20", "--calendar-horizon-days", "30"]) == 6
    assert RUNNER.main(["--store-dir", "x", "--state-dir", "y", "--mode", "live", "--arm", "--authorize-requests", "50", "--ceiling", "51", "--profile", "engine20"]) == 6
