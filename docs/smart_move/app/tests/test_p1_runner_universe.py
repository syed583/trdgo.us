"""P1: the runner's tiered mode (dry run sends nothing and persists nothing; the live refresh path through a stub provider requests only planned slots, once), the stale-bar early abort, and the per-event universe table."""
import json
import sys
from argparse import Namespace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "docs" / "smart_move" / "app"))
import smart_move_calendar_changes as CC  # noqa: E402
import smart_move_calendar_tiers as CT  # noqa: E402
import smart_move_collection_runner as RUNNER  # noqa: E402
import smart_move_engine20 as E20  # noqa: E402
import smart_move_live as L  # noqa: E402
import smart_move_universe as UNI  # noqa: E402

import test_engine20 as T20  # noqa: E402
from test_p1_calendar_tiers import Cal  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 11, 2, 20, 30, tzinfo=UTC)
SAT = datetime(2026, 11, 7, 22, 0, tzinfo=UTC)                         # Saturday 17:00 ET: the Monday AMC window is open


def amc_rows(sym, at, date_="2026-11-09"):
    base = {"symbol": sym, "date": date_, "clock": None, "mode": "LIVE", "observed_at": at.isoformat(), "request_id": "r", "company": sym + " INC", "sector": T20.SECTORS[sym], "marketcap": str(int(T20.CAPS[sym]))}
    return [dict(base, source="UW_CALENDAR", label="afterhours", vendor_flag="company", raw_sha256="u" + sym), dict(base, source="FINNHUB", label="amc", vendor_flag=None, raw_sha256="f" + sym)]


def write_store(tmp_path, syms, at, date_="2026-11-09"):
    store = tmp_path / "store"
    store.mkdir()
    (store / "cal_obs.jsonl").write_text("\n".join(json.dumps(r) for s in syms for r in amc_rows(s, at, date_)) + "\n", encoding="utf-8")
    return store


# ---------------------------------------------------------------- runner arguments and the dry run
def test_the_horizon_flag_is_limited_to_the_engine20_profile_3_to_30_days_and_cannot_be_mixed_with_the_old_horizon(tmp_path):
    store = write_store(tmp_path, ["AAA"], SAT - timedelta(hours=1))
    base = ["--store-dir", str(store), "--state-dir", str(tmp_path / "s"), "--as-of", SAT.isoformat()]
    assert RUNNER.main(base + ["--profile", "engine20", "--calendar-horizon-days", "2"]) == 6
    assert RUNNER.main(base + ["--profile", "engine20", "--calendar-horizon-days", "31"]) == 6
    assert RUNNER.main(base + ["--profile", "engine20", "--calendar-horizon-days", "30", "--calendar-days-ahead", "3"]) == 6
    assert RUNNER.main(base + ["--profile", "standard", "--calendar-horizon-days", "30"]) == 6
    assert RUNNER.main(base + ["--mode", "live", "--arm", "--authorize-requests", "51", "--profile", "engine20", "--calendar-horizon-days", "30"]) == 6          # the 50-per-run ceiling is untouched


def test_tiered_dry_run_plans_the_calendar_by_slot_sends_nothing_persists_no_state_and_writes_the_universe(tmp_path):
    store = write_store(tmp_path, ["AAA", "BBB", "CCC"], SAT - timedelta(hours=1))
    pub = tmp_path / "pub"
    rc = RUNNER.main(["--store-dir", str(store), "--state-dir", str(tmp_path / "st"), "--profile", "engine20", "--select-policy", "deadline_priority", "--calendar-horizon-days", "30", "--publish-dir", str(pub),
                      "--select-top", "2", "--as-of", SAT.isoformat()])
    assert rc == 0
    plan = json.loads((tmp_path / "st" / "engine20_plan.json").read_text(encoding="utf-8"))
    cal = plan["calendar_tiered"]
    assert plan["mode"] == "DRY_RUN" and plan["provider_requests"] == 0 and plan["ceiling"] == 50 and not (tmp_path / "st" / "attempts.jsonl").exists()
    assert cal["horizon_days"] == 30 and [x[:2] for x in cal["refresh_slots"][:2]] == [["2026-11-09|premarket", "T0_OPEN"], ["2026-11-09|afterhours", "T0_OPEN"]] and cal["estimated_requests"] <= 15 and plan["calendar_attempts_reserved"] == cal["estimated_requests"]
    assert not (pub / CT.STATE_FILE).exists() and not (pub / CC.CHANGES_FILE).exists()                                  # a dry run never writes calendar state
    uni = json.loads((pub / UNI.UNIVERSE_FILE).read_text(encoding="utf-8"))
    st = {r["symbol"]: r["collection_status"] for r in uni["rows"]}
    assert st == {"AAA": "QUEUED_THIS_RUN", "BBB": "QUEUED_THIS_RUN", "CCC": "DEFERRED"}                                  # --select-top 2: the third is deferred, not dropped
    ccc = next(r for r in uni["rows"] if r["symbol"] == "CCC")
    assert ccc["deferred"] and "still eligible while its window is open" in ccc["deferred_reason"] and uni["summary"]["excluded_by_proxy"] == 0 and all(r["excluded_by_proxy"] is False for r in uni["rows"])
    assert ccc["window_start"]["new_york"].startswith("2026-11-07 16:00") and ccc["cutoff"]["new_york"].startswith("2026-11-08 16:00") and ccc["cutoff"]["dubai"].startswith("2026-11-09 01:00")      # verified Monday AMC window


# ---------------------------------------------------------------- the live refresh step (stub provider)
def args(**kw):
    return Namespace(**dict(dict(ceiling=50, calendar_horizon_days=30), **kw))


def svc_with_cal(tmp_path, prov, at=SAT):
    from test_live import make
    svc, _, _ = make(tmp_path, start=at, fetcher=prov, max_retries=0)                      # the engine20 profile never retries
    return svc


def test_tiered_refresh_requests_only_the_planned_slots_once_records_state_and_changes_and_a_second_pass_sends_nothing(tmp_path):
    prov = Cal({("2026-11-09", "afterhours"): ["AAA", "BBB"], ("2026-11-09", "premarket"): ["CCC"]})
    svc = svc_with_cal(tmp_path / "svc", prov)
    st, cal_dir = CT.load_state(tmp_path / "cal"), tmp_path / "cal"
    plan = CT.plan_refresh(SAT, st, horizon_days=30, run_ceiling=50, budget_left=50)
    out = RUNNER.tiered_refresh(svc, args(), plan, st, cal_dir)
    assert out["ok"] and out["requests"] == len(prov.uw) + 2 and len(prov.uw) == len(plan["refresh"]) and out["changes"]["NEW"] == 3
    assert len({(d, s) for d, s, _ in prov.uw}) == len(prov.uw)                                                         # no slot requested twice
    on_disk = CT.load_state(cal_dir)
    assert on_disk["slots"]["2026-11-09|afterhours"]["symbols"] == ["AAA", "BBB"] and [c["change"] for c in CC.read_changes(cal_dir)].count("NEW") == 3
    again = CT.plan_refresh(SAT + timedelta(minutes=20), on_disk, horizon_days=30, run_ceiling=50, budget_left=50)
    n0 = len(prov.uw)
    done = RUNNER.tiered_refresh(svc, args(), dict(again, refresh=[c for c in again["refresh"] if c["tier"] != "T1"]), on_disk, cal_dir)
    assert done["requests"] == 0 and len(prov.uw) == n0                                                                  # T0 is fresh: nothing to send


def test_tiered_refresh_stops_on_a_provider_stop_and_never_spends_the_run_ceiling_on_the_calendar(tmp_path):
    prov = Cal({})
    svc = svc_with_cal(tmp_path / "svc", prov)
    svc.gov.stopped_until = SAT + timedelta(hours=1)
    st = CT.load_state(tmp_path / "cal")
    plan = CT.plan_refresh(SAT, st, horizon_days=30, run_ceiling=50, budget_left=50)
    out = RUNNER.tiered_refresh(svc, args(), plan, st, tmp_path / "cal")
    assert out["ok"] is False and out["reason"] == "PROVIDER_STOP" and prov.uw == []
    assert plan["est_requests"] <= 0.30 * 50                                                                              # the calendar never takes more than 30 % of a 50-request run


# ---------------------------------------------------------------- stale-bar early abort
class StaleSpy(T20.Stub):
    def fetch(self, kind, symbol, params):
        if kind == "ohlc" and symbol == "SPY":
            self.calls.append((kind, symbol, dict(params)))
            return json.dumps(T20.bars_body(last="2026-10-28")).encode()                   # older than the last completed session (2026-10-30)
        return super().fetch(kind, symbol, params)


def test_a_stale_first_price_payload_stops_the_run_after_one_request_when_the_guard_is_on(tmp_path):
    f = StaleSpy()
    svc = T20.make(tmp_path, f)
    plan = E20.plan_tasks(svc, T20.select(svc, n=3), T20.NOW, budget=50)
    res = E20.run(svc, plan["funded"], now=T20.NOW, ceiling=50, state_dir=tmp_path / "st", abort_on_stale_bars=True)
    assert res["halted"] == "STALE_BARS" and res["requests_attempted"] == 1 and len(f.calls) == 1 and f.calls[0][1] == "SPY"


def test_without_the_guard_behaviour_is_unchanged_and_fresh_bars_never_trigger_it(tmp_path):
    f = StaleSpy()
    svc = T20.make(tmp_path / "a", f)
    plan = E20.plan_tasks(svc, T20.select(svc, n=2), T20.NOW, budget=50)
    res = E20.run(svc, plan["funded"], now=T20.NOW, ceiling=50, state_dir=tmp_path / "st")
    assert res["halted"] is None and res["requests_attempted"] == len(plan["funded"])
    good = T20.make(tmp_path / "b", T20.Stub())
    plan2 = E20.plan_tasks(good, T20.select(good, n=2), T20.NOW, budget=50)
    res2 = E20.run(good, plan2["funded"], now=T20.NOW, ceiling=50, state_dir=tmp_path / "st2", abort_on_stale_bars=True)
    assert res2["halted"] is None and res2["ok"] == len(plan2["funded"])


# ---------------------------------------------------------------- the universe table
def test_universe_says_for_every_event_what_is_known_and_what_is_being_done(tmp_path):
    f = T20.Stub()
    svc = T20.make(tmp_path, f, symbols=("AAA", "BBB", "CCC", "DDD"))
    sel = T20.select(svc, n=2)
    plan = E20.plan_tasks(svc, sel, T20.NOW, budget=50)
    E20.run(svc, plan["funded"], now=T20.NOW, ceiling=50, state_dir=tmp_path / "st")
    st = CT.record_refresh({"version": 1, "slots": {}, "symbol_index": {}, "refreshes": 0}, [{"date": "2026-11-04", "side": "premarket", "pages_fetched": 1, "symbols": ["AAA", "BBB", "CCC", "DDD"], "complete": True, "reason": None}], T20.NOW)
    CC.track(st, [{"date": "2026-11-04", "side": "premarket", "pages_fetched": 1, "symbols": ["AAA", "BBB", "CCC", "DDD"], "complete": True}], T20.NOW)
    uni = UNI.build(svc, T20.NOW, horizon_days=30, slot_state=st, funded_symbols=set(), selected_symbols={"AAA", "BBB", "CCC"})
    by = {r["symbol"]: r for r in uni["rows"]}
    assert by["AAA"]["collection_status"] == "EVIDENCE_COMPLETE" and by["AAA"]["evidence"] == {"F5_price": "FRESH", "F3_earnings_history": "FRESH", "F8_market": "FRESH", "F8_sector_etf": "FRESH"}
    assert by["CCC"]["collection_status"] == "DEFERRED" and "selected but not funded" in by["CCC"]["deferred_reason"] and by["DDD"]["deferred_reason"].startswith("ranked below")
    assert by["AAA"]["session"] == "BMO" and by["AAA"]["window"] == "OPEN" and by["AAA"]["calendar_tier"] == "T0_OPEN" and by["AAA"]["calendar_slot_state"] == "FRESH" and by["AAA"]["calendar_change"] == "LISTED"
    assert by["AAA"]["priority_proxy_tier"] == "A" and by["AAA"]["market_cap"] == str(int(T20.CAPS["AAA"])) and all(r["excluded_by_proxy"] is False for r in uni["rows"])
    assert uni["summary"]["by_status"] == {"EVIDENCE_COMPLETE": 2, "DEFERRED": 2} and uni["summary"]["deferred"] == 2
    assert uni["calendar_completeness"]["t0_complete"] is False and "2026-11-03|afterhours" in uni["calendar_completeness"]["t0_open_stale"]                  # only the Nov 4 premarket slot was refreshed: another open window is still stale and the table says so
    path = UNI.write(tmp_path / "pub", uni)
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == "smart-move-universe-1" and not list((tmp_path / "pub").glob("*.tmp"))


def test_universe_marks_removed_and_missing_events_and_reads_the_ledger_without_changing_it(tmp_path):
    svc = T20.make(tmp_path, T20.Stub(), symbols=("AAA", "BBB"))
    st = {"version": 1, "slots": {}, "symbol_index": {"AAA": {"slot": "2026-11-04|premarket", "state": "REMOVED"}, "BBB": {"slot": "2026-11-04|premarket", "state": "MISSING"}}, "refreshes": 1}
    uni = UNI.build(svc, T20.NOW, horizon_days=30, slot_state=st, ledger_path=tmp_path / "none.jsonl")
    assert {r["symbol"]: r["collection_status"] for r in uni["rows"]} == {"AAA": "REMOVED", "BBB": "CALENDAR_MISSING_UNCONFIRMED"} and uni["ledger_ok"] is True
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"seq": 1, "prev_sha256": "x"}\n', encoding="utf-8")
    before = bad.read_bytes()
    assert UNI.build(svc, T20.NOW, horizon_days=30, slot_state=st, ledger_path=bad)["ledger_ok"] is False and bad.read_bytes() == before        # damage is reported, the file is never touched
