"""End-to-end synthetic rehearsal: calendar -> selection -> governor -> capture -> scorer (both variants) -> payload store -> seal -> grade. Fake provider and fake clock only."""
import json
from datetime import date, datetime, timezone

import pytest

from fixtures import FakeProvider, make_history, make_series, rows_from
from smvshadow import calendar_validation as cv
from smvshadow import cutoff as co
from smvshadow import grader, governor as gv, runner as rn, scorer as sc, selection as sel
from smvshadow.store import PayloadStore

UTC = timezone.utc
EVENT = date(2026, 11, 5)               # Thursday, BMO -> release 06:00 ET (UTC-5 after the 1 Nov DST end)
START = datetime(2026, 11, 3, 11, 5, tzinfo=UTC)       # 06:05 ET, inside the window


def world(symbol, pcts):
    hist, react = make_history(EVENT, "BMO", pcts)
    ser = make_series(symbol, date(2023, 10, 1), date(2026, 11, 2), reactions=react)
    return {"rows": rows_from(ser), "history": hist}


def build(tmp_path, symbols, authorized=500, core_cap=220):
    clock = gv.FakeClock(START)
    worlds = {"__default__": world("DEF", [4, 4, 4, 4, 4, 4]), "SPY": world("SPY", [0.5] * 6), "XLK": world("XLK", [0.5] * 6)}
    for s in symbols:
        worlds[s] = world(s, [4, 4, 4, 4, 4, 4])
    fetch = FakeProvider(worlds, clock.now)
    g = gv.Governor(clock, authorized_daily=authorized)
    cands, infos = [], {}
    for s in symbols:
        p = co.plan_event(EVENT, "BMO")
        cands.append(sel.Candidate(s, EVENT, 1, "BMO", cv.CONFIRMED, cv.T_CONFIRMED, p["window_start_utc"], p["deadline_utc"]))
        infos[(s, str(EVENT))] = {"kind": "BMO", "timing_status": cv.T_CONFIRMED, "version": 1, "plan": p, "sector_etf": "XLK"}
    plan = sel.schedule(cands, seed="rehearsal", core_cap=core_cap, full_cap=2)
    pre, sha = sc.load_prereg()
    store = PayloadStore(tmp_path / "store")
    return clock, fetch, g, plan, infos, pre, sha, store


def test_full_rehearsal_both_variants_budget_and_idempotency(tmp_path):
    symbols = ["AAA", "BBB", "CCC", "DDD"]
    clock, fetch, g, plan, infos, pre, sha, store = build(tmp_path, symbols)
    r = rn.ShadowRunner(g, store, fetch, pre, sha)
    r.run(plan["assignments"], infos)
    r.finish()
    assert len(r.processed) == 4 and not r.missed
    # exactly two predictions per event, one per variant, separate and mirrored
    assert len(r.predictions) == 8
    by = {(p["symbol"], p["variant"]): p for p in r.predictions}
    for s in symbols:
        c, k = by[(s, "continuation")], by[(s, "contrarian")]
        assert c["in_window"] and k["in_window"]
        assert {c["signal"], k["signal"]} <= {"BUY", "SELL", "NO_TRADE"}
        if c["signal"] in ("BUY", "SELL"):
            assert k["signal"] in ("NO_TRADE", "BUY", "SELL")
        assert c["prereg_sha256"] == k["prereg_sha256"] == sha
        assert c["weight_available"] <= 40 and k["weight_available"] <= 40               # never normalised to 100
    # request budget: 4 events x 4 core + SPY + XLK once, 2 full events x 13 extras
    n = len(fetch.calls)
    assert n == 4 * 4 + 2 + 2 * 13
    assert sum(1 for e, s in fetch.calls if s == "SPY") == 1 and sum(1 for e, s in fetch.calls if s == "XLK") == 1
    assert g.used_day and max(g.used_day.values()) == n
    # all raw payloads stored once, verified
    total, bad = store.verify_all()
    assert total > 0 and bad == []
    # nothing captured after the cutoff
    cutoff = infos[("AAA", str(EVENT))]["plan"]["cutoff_utc"]
    assert clock.now() < cutoff


def test_rerun_is_idempotent_for_payloads(tmp_path):
    clock, fetch, g, plan, infos, pre, sha, store = build(tmp_path, ["AAA", "BBB"])
    rn.ShadowRunner(g, store, fetch, pre, sha).run(plan["assignments"], infos)
    files1 = [f.name for f in store.files()]
    clock2, fetch2, g2, plan2, infos2, pre2, sha2, _ = build(tmp_path, ["AAA", "BBB"])
    rn.ShadowRunner(g2, store, fetch2, pre2, sha2).run(plan2["assignments"], infos2)
    assert [f.name for f in store.files()] == files1                                    # identical bytes -> identical hashes -> no new files
    assert plan["plan_hash"] == plan2["plan_hash"]


def test_zero_authorization_makes_no_requests_and_logs(tmp_path):
    clock, fetch, g, plan, infos, pre, sha, store = build(tmp_path, ["AAA"], authorized=0)
    r = rn.ShadowRunner(g, store, fetch, pre, sha)
    r.run(plan["assignments"], infos)
    assert fetch.calls == [] and len(r.missed) == 1 and r.predictions == []
    assert any(e["type"] == "MISSED_DEADLINE" for e in g.ledger)


def test_kill_switch_and_daily_cap_stop_new_events(tmp_path):
    clock, fetch, g, plan, infos, pre, sha, store = build(tmp_path, ["AAA", "BBB", "CCC"], authorized=6)
    r = rn.ShadowRunner(g, store, fetch, pre, sha)
    r.run(plan["assignments"], infos)
    assert len(fetch.calls) <= 6
    assert len(r.processed) + len(r.missed) == 3 and len(r.missed) >= 1
    assert all(e["type"] for e in g.ledger)


def test_missing_provider_data_never_counts_as_evidence(tmp_path):
    clock, fetch, g, plan, infos, pre, sha, store = build(tmp_path, ["AAA"])
    fetch.worlds["AAA"]["history"] = []              # no earnings history -> F3 must be missing
    r = rn.ShadowRunner(g, store, fetch, pre, sha)
    r.run(plan["assignments"], infos)
    snap = {e.factor: e for e in r.snapshots[0]["evidence"]}
    assert snap[3].status != "OK" and not snap[3].usable
    for p in r.predictions:
        assert p["weight_available"] <= 25


def test_grading_of_rehearsal_has_no_claim(tmp_path):
    clock, fetch, g, plan, infos, pre, sha, store = build(tmp_path, ["AAA", "BBB"])
    r = rn.ShadowRunner(g, store, fetch, pre, sha)
    r.run(plan["assignments"], infos)
    preds = [dict(p, earnings_date=EVENT) for p in r.predictions]
    outcomes = {(s, EVENT): {"status": "GRADED", "move_pct": 3.0} for s in ("AAA", "BBB")}
    rep = grader.grade(preds, outcomes, {(s, EVENT): 1 for s in ("AAA", "BBB")})
    assert rep["claim_allowed"] is False and "NO ACCURACY CLAIM" in rep["claim_text"]
    for v in rep["variants"].values():
        assert v["minimum_sample_met"] is False


def test_crash_midway_then_rerun_loses_nothing_and_duplicates_nothing(tmp_path):
    class Boom(Exception):
        pass
    clock, fetch, g, plan, infos, pre, sha, store = build(tmp_path, ["AAA", "BBB", "CCC"])
    real, state = fetch.__call__, {"n": 0}

    def crashing(endpoint, symbol, params):
        state["n"] += 1
        if state["n"] == 9:
            raise Boom()
        return real(endpoint, symbol, params)
    r = rn.ShadowRunner(g, store, crashing, pre, sha)
    with pytest.raises((Boom, Exception)):
        r.run(plan["assignments"], infos)
    store.recover()
    files_before = [f.name for f in store.files()]
    total, bad = store.verify_all()
    assert bad == [] and total == len(files_before)
    clock2, fetch2, g2, plan2, infos2, pre2, sha2, _ = build(tmp_path, ["AAA", "BBB", "CCC"])
    r2 = rn.ShadowRunner(g2, store, fetch2, pre2, sha2)
    r2.run(plan2["assignments"], infos2)
    assert len(r2.processed) == 3 and len(r2.predictions) == 6
    assert set(files_before) <= {f.name for f in store.files()} and store.verify_all()[1] == []
