"""Automatic earnings monitor: gate, store, calendar observation, predictions, change detection, consensus, alerts, scheduling, recovery, budgets, API and a full offline simulation.
Every test uses sample data and a scripted adapter; nothing here can reach a provider."""
import ast
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from earnings_monitor import alerts as A  # noqa: E402
from earnings_monitor import config as C  # noqa: E402
from earnings_monitor import logic as L  # noqa: E402
from earnings_monitor.gate import CACHE_ONLY, OFF, REFRESH, ProviderBlocked, evaluate  # noqa: E402
from earnings_monitor.locks import ScanLock  # noqa: E402
from earnings_monitor.service import outcome_ready_at  # noqa: E402
from earnings_monitor.sim import Clock, SimAdapters, build_sim, write_approval  # noqa: E402
from earnings_monitor.store import Store  # noqa: E402

UTC = timezone.utc
BASE = datetime(2026, 11, 9, 17, 0, tzinfo=UTC)          # Monday 12:00 ET


def mk(tmp_path, mode="refresh", budget=700, events=8, approval=True, cfg=None, **over):
    clock = Clock(BASE)
    d = tmp_path / "em"
    d.mkdir(parents=True, exist_ok=True)
    if approval:
        write_approval(d, BASE, mode=mode, budget=budget, events=events, **over)
    store, cfg, ad, mon, sched, access = build_sim(d, clock, cfg=cfg)
    return d, clock, store, cfg, ad, mon, sched, access


def cycle(sched, clock):
    t = sched.tick(clock())
    return t, sched.run_pending(clock()) + sched.run_pending(clock(), lane="light")


def ev(store, sym):
    return store.one("SELECT * FROM events WHERE symbol=? ORDER BY id DESC", (sym,))


def kinds(store, **where):
    sql = "SELECT kind FROM changes" + (" WHERE " + " AND ".join("%s=?" % k for k in where) if where else "")
    return [r["kind"] for r in store.q(sql, tuple(where.values()))]


# ------------------------------------------------------------------ store
def test_history_tables_are_append_only(tmp_path):
    store = Store(tmp_path / "s.sqlite")
    store.audit(BASE, "t", "x", {"a": 1})
    with store.tx() as c:
        c.execute("INSERT INTO predictions(event_id,model,signal,as_of,recorded_at,input_hash) VALUES(1,'m','BUY','2026-11-09T00:00:00+00:00','2026-11-09T00:00:00+00:00','h')")
    for sql in ("UPDATE predictions SET signal='SELL'", "DELETE FROM predictions", "UPDATE audit_log SET action='y'", "DELETE FROM audit_log"):
        with pytest.raises(sqlite3.DatabaseError):
            with store.tx() as c:
                c.execute(sql)
    assert store.q("SELECT signal FROM predictions")[0]["signal"] == "BUY"


# ------------------------------------------------------------------ gate
def test_the_monitor_is_off_by_default_and_every_approval_requirement_is_enforced(tmp_path):
    cfg, d = C.Config(), tmp_path
    assert evaluate(cfg, d, BASE, {}).mode == OFF
    env = {"EARNINGS_MONITOR_ENABLED": "1"}
    s = evaluate(cfg, d, BASE, env)
    assert s.mode == OFF and "NO_OWNER_APPROVAL_FILE" in s.reasons
    write_approval(d, BASE, mode="cache_only")
    assert evaluate(cfg, d, BASE, env).mode == CACHE_ONLY
    assert evaluate(cfg, d, BASE, {}).mode == OFF                                    # the environment switch is separate from the file
    write_approval(d, BASE, mode="refresh", budget=700)
    assert evaluate(cfg, d, BASE, env).mode == REFRESH
    for k, v, reason in (("approved_by", "", "APPROVAL_NOT_SIGNED"), ("expires_at", (BASE - timedelta(days=1)).isoformat(), "APPROVAL_EXPIRED"), ("scheduling_approved", False, "SCHEDULING_NOT_APPROVED"),
                         ("provider_authorization", False, "PROVIDER_AUTHORIZATION_MISSING"), ("kill_switch_acknowledged", False, "KILL_SWITCH_NOT_ACKNOWLEDGED"), ("daily_request_budget", 10, "BUDGET_BELOW_WORST_CASE"),
                         ("daily_request_budget", 5000, "DAILY_BUDGET_INVALID"), ("mode", "bogus", "APPROVAL_MODE_INVALID"), ("max_inflight", 3, "APPROVED_CONCURRENCY_ABOVE_CEILING")):
        write_approval(d, BASE, **{**{"mode": "refresh", "budget": 700}, k: v})
        s = evaluate(cfg, d, BASE, env)
        assert s.mode == OFF and any(r.startswith(reason) for r in s.reasons), (k, s.reasons)
    write_approval(d, BASE, mode="refresh", budget=700, expires_at=(BASE + timedelta(days=90)).isoformat())
    assert "APPROVAL_LONGER_THAN_30_DAYS" in evaluate(cfg, d, BASE, env).reasons
    write_approval(d, BASE, mode="refresh", budget=700)
    (d / "KILL").write_text("x")
    s = evaluate(cfg, d, BASE, env)
    assert s.mode == OFF and "KILL_SWITCH_ENGAGED" in s.reasons                       # a kill switch beats a valid approval


def test_provider_access_blocks_never_bypasses(tmp_path):
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path, mode="cache_only")
    with pytest.raises(ProviderBlocked, match="NOT_APPROVED"):
        access.call("p", 1, lambda: 1)
    d2, clock2, store2, cfg2, ad2, mon2, sched2, access2 = mk(tmp_path / "b", mode="refresh", budget=700)
    sched2.tick(clock2())
    assert access2.call("p", 5, lambda: "ok") == "ok" and store2.usage("2026-11-09") == 5
    (d2 / "KILL").write_text("x")
    with pytest.raises(ProviderBlocked, match="KILL"):
        access2.call("p", 1, lambda: 1)
    (d2 / "KILL").unlink()
    with pytest.raises(ProviderBlocked, match="BUDGET"):
        access2.call("p", 700, lambda: 1)
    for _ in range(3):                                                                # failed attempts count and trip the breaker
        with pytest.raises(RuntimeError):
            access2.call("q", 1, lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert store2.usage("2026-11-09", "q") == 3
    with pytest.raises(ProviderBlocked, match="CIRCUIT_BREAKER_OPEN"):
        access2.call("q", 1, lambda: 1)
    clock2.advance(minutes=31)
    assert access2.call("q", 1, lambda: "back") == "back"


def test_an_adapter_that_declines_neither_counts_a_request_nor_trips_the_breaker(tmp_path):
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path)
    sched.tick(clock())
    for _ in range(5):
        with pytest.raises(ProviderBlocked, match="NO_EVENT_REFRESH_ADAPTER"):
            access.call("event", 3, lambda: ad.__class__.__mro__[1].refresh_event(ad, "AAA"))
    assert store.usage("2026-11-09") == 0 and not access.breaker_open("event")


# ------------------------------------------------------------------ calendar
def test_calendar_new_duplicate_rescheduled_confirmed_and_canceled_events(tmp_path):
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path)
    now = clock()
    st = mon.observe_calendar([{"symbol": "AAA", "company": "A", "date": "2026-11-11", "session": "BMO"}, {"symbol": "BBB", "date": "2026-11-12", "session": "AMC"},
                               {"symbol": "AAA", "date": "2026-11-11", "session": "BMO"}, {"symbol": "OLD", "date": "2026-11-01", "session": "BMO"}], now, complete=True)
    assert st["new"] == 2 and st["ignored"] == 2                                      # duplicate row and a past date are not events
    assert store.one("SELECT COUNT(*) n FROM events")["n"] == 2
    mon.observe_calendar([{"symbol": "AAA", "date": "2026-11-13", "session": "BMO"}, {"symbol": "BBB", "date": "2026-11-12", "session": "AMC"}], now + timedelta(hours=6), complete=True)
    a = ev(store, "AAA")
    assert a["earnings_date"] == "2026-11-13" and store.one("SELECT COUNT(*) n FROM events WHERE symbol='AAA'")["n"] == 1
    assert [v["reason"] for v in store.q("SELECT reason FROM event_versions WHERE event_id=? ORDER BY id", (a["id"],))] == ["NEW_EVENT", "RESCHEDULED"]
    mon.observe_calendar([{"symbol": "CCC", "date": "2026-11-12", "session": "UNKNOWN"}], now + timedelta(hours=7), complete=False)
    mon.observe_calendar([{"symbol": "CCC", "date": "2026-11-12", "session": "AMC"}], now + timedelta(hours=8), complete=False)
    assert ev(store, "CCC")["session"] == "AMC" and "SESSION_CHANGED" in kinds(store, symbol="CCC")
    assert store.one("SELECT COUNT(*) n FROM alerts WHERE symbol='CCC' AND rule='DATE_CHANGE'")["n"] == 0      # UNKNOWN -> confirmed is not worth an alert
    assert store.one("SELECT COUNT(*) n FROM alerts WHERE symbol='AAA' AND rule='DATE_CHANGE'")["n"] == 1
    for i in range(3):                                                                # cache-derived scans can be partial: they never cancel
        mon.observe_calendar([{"symbol": "AAA", "date": "2026-11-13", "session": "BMO"}], now + timedelta(hours=9 + i), complete=False)
    assert ev(store, "BBB")["status"] == "ACTIVE"
    mon.observe_calendar([{"symbol": "AAA", "date": "2026-11-13", "session": "BMO"}], now + timedelta(hours=12), complete=True)
    assert ev(store, "BBB")["status"] == "ACTIVE" and ev(store, "BBB")["missing_scans"] == 1
    mon.observe_calendar([{"symbol": "AAA", "date": "2026-11-13", "session": "BMO"}], now + timedelta(hours=18), complete=True)
    b = ev(store, "BBB")
    assert b["status"] == "CANCELED" and "EVENT_CANCELED" in kinds(store, symbol="BBB") and store.one("SELECT COUNT(*) n FROM alerts WHERE symbol='BBB' AND rule='DATE_CHANGE'")["n"] == 1
    assert not [e for e in store.q("SELECT symbol FROM events WHERE status='ACTIVE'") if e["symbol"] == "BBB"]      # off the active list, still in the table
    mon.observe_calendar([{"symbol": "BBB", "date": "2026-11-12", "session": "AMC"}], now + timedelta(hours=20), complete=True)
    assert ev(store, "BBB")["status"] == "ACTIVE" and store.one("SELECT COUNT(*) n FROM events WHERE symbol='BBB'")["n"] == 1      # reinstated, not duplicated


def test_a_new_quarter_after_the_old_event_is_a_new_event_and_the_old_one_stays_as_history(tmp_path):
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path)
    mon.observe_calendar([{"symbol": "AAA", "date": "2026-11-10", "session": "BMO"}], clock(), complete=True)
    later = BASE + timedelta(days=100)
    mon.observe_calendar([{"symbol": "AAA", "date": "2027-02-18", "session": "BMO"}], later, complete=True)
    rows = store.q("SELECT earnings_date, status FROM events WHERE symbol='AAA' ORDER BY id")
    assert rows == [{"earnings_date": "2026-11-10", "status": "REPORTED"}, {"earnings_date": "2027-02-18", "status": "ACTIVE"}]


# ------------------------------------------------------------------ predictions and changes
def seed(mon, ad, clock, sym="AAA", date="2026-11-11", sess="BMO"):
    ad.cal = [{"symbol": sym, "company": sym, "date": date, "session": sess}]
    mon.observe_calendar(ad.cal, clock(), complete=True)


def test_predictions_are_recorded_only_when_real_and_changed_and_never_overwritten(tmp_path):
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path)
    seed(mon, ad, clock)
    ad.set_model("claude", "AAA", "BUY", BASE, score=70, coverage="9/10")
    ad.set_model("grok", "AAA", "PENDING", BASE)
    ad.set_model("meta", "AAA", "LOW_DATA", BASE)
    ad.set_model("smart_move", "AAA", "NO_VALID_PREDICTION", BASE)
    r = mon.recompute(clock())
    assert r["new_predictions"] == 1                                                  # PENDING / LOW_DATA / NO_VALID_PREDICTION are not predictions
    st = {m["model"]: m["status"] for m in store.q("SELECT model,status FROM model_state")}
    assert st["claude"] == "VALID" and st["grok"] == "PENDING" and st["meta"] == "INSUFFICIENT_DATA" and st["smart_move"] == "INSUFFICIENT_DATA" and st["ped"] == "ABSENT"
    assert mon.recompute(clock())["new_predictions"] == 0                             # identical input: no new row
    first = store.one("SELECT * FROM predictions ORDER BY id LIMIT 1")
    clock.advance(hours=2)
    ad.set_model("claude", "AAA", "BUY", clock(), score=75, coverage="9/10")           # same signal, new score: recorded, not a signal change
    mon.recompute(clock())
    assert kinds(store, model="claude") == ["NEW_PREDICTION"]
    ad.set_model("claude", "AAA", "SELL", clock(), score=30, coverage="9/10")
    mon.recompute(clock())
    assert "SIGNAL_CHANGED" in kinds(store, model="claude")
    ch = store.one("SELECT * FROM changes WHERE kind='SIGNAL_CHANGED'")
    assert json.loads(ch["detail"])["subtype"] == "REVERSAL" and (ch["old_value"], ch["new_value"]) == ("BUY", "SELL")
    ad.set_model("claude", "AAA", "NEUTRAL", clock(), score=50, coverage="9/10")
    mon.recompute(clock())
    assert json.loads(store.q("SELECT detail FROM changes WHERE kind='SIGNAL_CHANGED' ORDER BY id DESC LIMIT 1")[0]["detail"])["subtype"] == "DIRECTION_LOST"
    assert store.one("SELECT COUNT(*) n FROM predictions WHERE model='claude'")["n"] == 4
    assert store.one("SELECT recorded_at, signal FROM predictions ORDER BY id LIMIT 1") == {"recorded_at": first["recorded_at"], "signal": "BUY"}      # the original is untouched
    alerts = {r["rule"] for r in store.q("SELECT rule FROM alerts")}
    assert {"NEW_BUY_SELL", "DIRECTION_CHANGE"} <= alerts


def test_coverage_staleness_and_missing_evidence_are_detected_once(tmp_path):
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path)
    seed(mon, ad, clock)
    ad.set_model("grok", "AAA", "BUY", BASE, score=60, coverage="9/10")
    mon.recompute(clock())
    clock.advance(hours=1)
    ad.set_model("grok", "AAA", "BUY", clock(), score=60, coverage="4/10")
    mon.recompute(clock())
    clock.advance(hours=1)
    ad.set_model("grok", "AAA", "BUY", clock(), score=60, coverage="9/10")
    mon.recompute(clock())
    assert "COVERAGE_DETERIORATED" in kinds(store) and "COVERAGE_IMPROVED" in kinds(store)
    clock.advance(hours=40)                                                           # the cached call is now older than the freshness limit
    mon.recompute(clock())
    mon.recompute(clock())
    assert kinds(store).count("PREDICTION_STALE") == 1
    assert store.one("SELECT COUNT(*) n FROM alerts WHERE rule='STALE_OR_MISSING_EVIDENCE'")["n"] == 1
    assert store.one("SELECT stale FROM model_state WHERE model='grok'")["stale"] == 1


def test_coverage_fraction_understands_the_shapes_and_never_guesses():
    f = L.coverage_fraction
    assert f("9/10") == 0.9 and f(0.5) == 0.5 and f(80) == 0.8 and f({"available": 3, "max": 4}) == 0.75 and f("30/40") == 0.75
    assert f(None) is None and f("n/a") is None and f(1000) is None and f(True) is None


def test_consensus_counts_only_genuinely_comparable_models():
    cfg = C.Config()
    t = BASE.isoformat()

    def s(model, status, signal, as_of=t, stale=False):
        return {"model": model, "status": status, "signal": signal, "as_of": as_of, "stale": stale}
    strong = L.consensus([s("a", "VALID", "BUY"), s("b", "VALID", "BUY"), s("c", "VALID", "BUY"), s("d", "VALID", "NEUTRAL"), s("e", "PENDING", None, None)], cfg)
    assert strong["level"] == "STRONG" and strong["majority"] == "BUY" and strong["comparable"] == 4 and strong["excluded"] == ["e"]
    assert L.consensus([s("a", "VALID", "BUY"), s("b", "VALID", "SELL"), s("c", "VALID", "BUY")], cfg)["level"] == "CONFLICT"
    assert L.consensus([s("a", "VALID", "BUY"), s("b", "VALID", "BUY")], cfg)["level"] == "INSUFFICIENT"
    assert L.consensus([s("a", "VALID", "BUY"), s("b", "VALID", "BUY"), s("c", "VALID", "BUY", stale=True)], cfg)["level"] == "INSUFFICIENT"       # a stale call does not count
    old = (BASE - timedelta(hours=30)).isoformat()
    assert L.consensus([s("a", "VALID", "BUY"), s("b", "VALID", "BUY"), s("c", "VALID", "BUY", as_of=old)], cfg)["level"] == "INSUFFICIENT"      # built too far apart: not comparable
    assert L.consensus([s("a", "VALID", "BUY"), s("b", "VALID", "BUY"), s("c", "VALID", "NO_TRADE"), s("d", "VALID", "NEUTRAL")], cfg)["level"] == "WEAK"


# ------------------------------------------------------------------ alerts
def test_alerts_are_deduplicated_configurable_and_say_who_what_when_and_why(tmp_path):
    cfg = C.Config()
    cfg.alert_rules["DIRECTION_CHANGE"] = {"enabled": False}
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path, cfg=cfg)
    seed(mon, ad, clock)
    ad.set_model("claude", "AAA", "BUY", BASE, score=70)
    mon.recompute(clock())
    clock.advance(hours=1)
    ad.set_model("claude", "AAA", "SELL", clock(), score=30)
    mon.recompute(clock())
    rules = [r["rule"] for r in store.q("SELECT rule FROM alerts")]
    assert rules == ["NEW_BUY_SELL"]                                                  # the direction-change rule is switched off
    p = json.loads(store.one("SELECT payload FROM alerts")["payload"])
    assert p["model"] == "claude" and p["symbol"] == "AAA" and p["earnings_date"] == "2026-11-11" and p["session"] == "BMO" and p["prediction_ts"] == BASE.isoformat() and "new BUY" in p["reason"]
    cand = A.build({"kind": "NEW_PREDICTION", "model": "claude", "old": None, "new": "BUY", "detail": {}}, ev(store, "AAA"), BASE.isoformat(), cfg)
    assert A.enqueue(store, clock(), cand) == 0 and A.enqueue(store, clock(), cand) == 0       # the same alert again is dropped
    out = tmp_path / "out.jsonl"
    res = A.dispatch(store, clock(), [A.FileSink(out)], cfg)
    assert res["delivered"] == 1 and len(out.read_text().splitlines()) == 1
    assert A.dispatch(store, clock(), [A.FileSink(out)], cfg)["delivered"] == 0       # delivered once only


# ------------------------------------------------------------------ scheduler
def test_nothing_is_scheduled_without_approval_and_cache_only_never_calls_a_provider(tmp_path):
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path, approval=False)
    ad.cal = [{"symbol": "AAA", "date": "2026-11-11", "session": "BMO"}]
    t, ran = cycle(sched, clock)
    assert t["scheduling"] == OFF and t["enqueued"] == 0 and ran == [] and store.one("SELECT COUNT(*) n FROM events")["n"] == 0
    assert "NO_OWNER_APPROVAL_FILE" in store.kv_get("gate")["reasons"]
    write_approval(d, BASE, mode="cache_only")
    t, ran = cycle(sched, clock)
    assert t["scheduling"] == CACHE_ONLY and t["enqueued"] >= 3 and all(r["ran"] for r in ran)
    assert ad.calls == {"refresh_calendar": 0, "refresh_event": 0, "verify_outcome": 0} and store.usage("2026-11-09") == 0
    assert store.one("SELECT COUNT(*) n FROM events")["n"] == 1


def test_recurring_jobs_are_idempotent_and_follow_their_intervals(tmp_path):
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path, cfg=C.Config(plan_driven_refresh=False))
    ad.cal = [{"symbol": "AAA", "date": "2026-11-11", "session": "BMO"}]
    assert sched.tick(clock())["enqueued"] == 7                                       # plan-driven refresh is off by default
    assert sched.tick(clock())["enqueued"] == 0                                       # same buckets: nothing new
    sched.run_pending(clock())
    assert ad.calls["refresh_calendar"] == 1 and store.usage("2026-11-09") == C.COST_CALENDAR_REFRESH
    clock.advance(minutes=31)
    assert sched.tick(clock())["enqueued"] >= 1                                       # the 30-minute eligibility bucket moved on
    n = sched.tick(clock())["enqueued"]
    sched.run_pending(clock())
    assert ad.calls["refresh_calendar"] == 1 and n == 0                               # calendar still inside its 6-hour bucket
    clock.advance(hours=6)
    sched.tick(clock())
    sched.run_pending(clock())
    assert ad.calls["refresh_calendar"] == 2


def test_restart_recovers_a_job_left_running_and_an_overlapping_scan_is_refused(tmp_path):
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path)
    sched.tick(clock())
    j = store.claim(clock(), "dead-worker", 60)                                       # a worker claimed it, then the process died
    assert j and store.job_counts().get("RUNNING") == 1
    clock.advance(minutes=5)
    # a fresh scheduler on the same database (what a restart does)
    store_b, cfg_b, ad_b, mon_b, sched_b, access_b = build_sim(d, clock)
    held = ScanLock(d / "scan.lock")
    assert held.acquire()
    assert sched_b.run_once(clock()) == {"ran": False, "reason": "SCAN_LOCK_HELD"}            # another process is scanning
    held.release()
    r = sched_b.run_once(clock())
    assert r["ran"] and store_b.q("SELECT attempts FROM jobs WHERE id=?", (j["id"],))[0]["attempts"] == 2      # the dead worker's job ran again
    assert any(a["action"] == "RECOVERED_JOBS" for a in store_b.q("SELECT action FROM audit_log"))


def test_failures_back_off_open_the_breaker_and_end_in_a_dead_letter_with_an_alert(tmp_path):
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path)
    ad.fail_calendar = 10
    sched.tick(clock())
    delays = []
    for _ in range(3):
        r = [x for x in sched.run_pending(clock()) if x.get("kind") == "CALENDAR_REFRESH"][0]
        delays.append(r["retry_in_s"])
        clock.advance(seconds=r["retry_in_s"] + 1)
    assert delays == [60, 120, 240] and ad.calls["refresh_calendar"] == 3
    r = [x for x in sched.run_pending(clock()) if x.get("kind") == "CALENDAR_REFRESH"][0]
    assert r["blocked"].startswith("CIRCUIT_BREAKER_OPEN") and ad.calls["refresh_calendar"] == 3          # the open breaker stops further provider calls
    assert store.one("SELECT COUNT(*) n FROM alerts WHERE rule='SCANNER_FAILURE'")["n"] >= 1
    # dead letter: a non-provider job that keeps failing
    cfg2 = C.Config(max_attempts=2)
    d2, clock2, store2, cfg2, ad2, mon2, sched2, access2 = mk(tmp_path / "x", cfg=cfg2)
    mon2.eligibility_check = lambda now: (_ for _ in ()).throw(ValueError("bad data"))
    sched2.tick(clock2())
    out = []
    for _ in range(3):
        out += sched2.run_pending(clock2())
        clock2.advance(seconds=200)
    dead = [x for x in out if x.get("dead")]
    assert dead and store2.one("SELECT status FROM jobs WHERE kind='ELIGIBILITY_CHECK'")["status"] == "DEAD"
    assert "bad data" in json.loads(store2.one("SELECT payload FROM alerts WHERE rule='SCANNER_FAILURE'")["payload"])["reason"]


def test_the_kill_switch_and_budget_stop_work_immediately(tmp_path):
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path, budget=200, events=1, purposes=("calendar",))
    sched.tick(clock())
    (d / "KILL").write_text("x")
    assert sched.run_once(clock()) == {"ran": False, "reason": "GATE_OFF", "gate": ["KILL_SWITCH_ENGAGED"]}
    assert ad.calls["refresh_calendar"] == 0
    (d / "KILL").unlink()
    sched.run_pending(clock())
    assert store.usage("2026-11-09") == C.COST_CALENDAR_REFRESH
    ok = 0
    for _ in range(8):
        try:
            mon.calendar_refresh(clock())
            ok += 1
        except ProviderBlocked as b:
            assert b.reason.startswith("DAILY_BUDGET_EXHAUSTED")
            break
    assert store.usage("2026-11-09") <= 200 and ok == 3


def test_request_estimate_matches_the_defaults_and_the_budget_check_uses_it():
    e = C.estimate(C.Config(), 20)
    assert e["calendar_requests"] == 184 and e["earnings_day_requests"] == 1220 and e["outcome_requests"] == 40 and e["total_requests_per_day"] == 1444 and e["cache_only_requests_per_day"] == 0
    assert C.estimate(C.Config(), 10)["total_requests_per_day"] == 814 and C.estimate(C.Config(), 40)["total_requests_per_day"] == 2704 and C.estimate(C.Config(), 0)["total_requests_per_day"] == 184
    assert C.estimate(C.Config(), 20, purposes=("calendar",))["total_requests_per_day"] == 184 and C.estimate(C.Config(), 20, purposes=())["total_requests_per_day"] == 0
    assert C.estimate(C.Config(calendar_every_h=12), 20)["calendar_requests"] == 92


# ------------------------------------------------------------------ end-to-end offline simulation
def test_full_simulation_new_changed_canceled_stale_reported_and_graded_events(tmp_path):
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path, budget=700, events=8, cfg=C.Config(plan_driven_refresh=False))      # this test is about the earnings-day refresh only
    t0 = BASE
    ad.cal = [{"symbol": "AAA", "company": "Alpha", "date": "2026-11-11", "session": "BMO"}, {"symbol": "BBB", "company": "Beta", "date": "2026-11-12", "session": "AMC"},
              {"symbol": "CCC", "company": "Gamma", "date": "2026-11-12", "session": "UNKNOWN"}]
    for m, sig, sc, cov in (("claude", "BUY", 70, "9/10"), ("grok", "BUY", 65, "8/10"), ("meta", "BUY", 60, "7/10"), ("ped", "NEUTRAL", 50, "6/10"), ("smart_move", "BUY", None, "30/40")):
        ad.set_model(m, "AAA", sig, t0, score=sc, coverage=cov)
    for m in ("claude", "grok", "meta"):
        ad.set_model(m, "BBB", "SELL", t0, score=30, coverage="8/10")
    ad.set_model("ped", "BBB", "LOW_DATA", t0)
    cycle(sched, clock)
    assert store.one("SELECT COUNT(*) n FROM events")["n"] == 3
    assert store.one("SELECT COUNT(*) n FROM predictions")["n"] == 8
    rule_counts = {r["rule"]: r["n"] for r in store.q("SELECT rule, COUNT(*) n FROM alerts GROUP BY rule")}
    assert rule_counts == {"NEW_BUY_SELL": 7, "STRONG_AGREEMENT": 2}
    delivered = (d / "alerts_outbox.jsonl").read_text().splitlines()
    assert len(delivered) == 9 and {json.loads(x)["model"] for x in delivered if json.loads(x)["rule"] == "NEW_BUY_SELL"} == {"claude", "grok", "meta", "smart_move"}
    before = (store.one("SELECT COUNT(*) n FROM predictions")["n"], store.one("SELECT COUNT(*) n FROM changes")["n"], store.one("SELECT COUNT(*) n FROM alerts")["n"])
    cycle(sched, clock)
    assert (store.one("SELECT COUNT(*) n FROM predictions")["n"], store.one("SELECT COUNT(*) n FROM changes")["n"], store.one("SELECT COUNT(*) n FROM alerts")["n"]) == before     # a repeat changes nothing

    clock.advance(hours=6)                                                            # t1: AAA moves a day, CCC session confirmed, DDD announced, BBB disappears; Claude flips, Grok loses coverage
    t1 = clock()
    ad.cal = [{"symbol": "AAA", "date": "2026-11-12", "session": "BMO"}, {"symbol": "CCC", "date": "2026-11-12", "session": "AMC"}, {"symbol": "DDD", "date": "2026-11-13", "session": "BMO"}]
    ad.set_model("claude", "AAA", "SELL", t1, score=35, coverage="9/10")
    ad.set_model("grok", "AAA", "BUY", t1, score=65, coverage="4/10")
    cycle(sched, clock)
    assert ev(store, "AAA")["earnings_date"] == "2026-11-12" and ev(store, "DDD")["status"] == "ACTIVE" and ev(store, "CCC")["session"] == "AMC" and ev(store, "BBB")["missing_scans"] == 1
    assert {"EVENT_RESCHEDULED", "SESSION_CHANGED", "SIGNAL_CHANGED", "COVERAGE_DETERIORATED", "AGREEMENT_CHANGED"} <= set(kinds(store))

    clock.advance(hours=6)                                                            # t2: BBB is still missing -> canceled
    ad.cal = [r for r in ad.cal]
    cycle(sched, clock)
    assert ev(store, "BBB")["status"] == "CANCELED" and "EVENT_CANCELED" in kinds(store)

    clock.advance(hours=1)
    ad.set_model("grok", "AAA", "BUY", clock(), score=65, coverage="9/10")            # coverage recovers
    clock.t = datetime(2026, 11, 12, 11, 0, tzinfo=UTC)                               # t3: AAA's report day, 06:00 ET, inside the Before-Open window (meta's call is now 42 h old)
    ad.set_model("claude", "AAA", "SELL", clock(), score=35, coverage="9/10")
    ad.set_model("grok", "AAA", "BUY", clock(), score=65, coverage="9/10")
    ad.set_model("smart_move", "AAA", "BUY", clock(), coverage="30/40")
    ad.set_model("ped", "AAA", "NEUTRAL", clock(), score=50, coverage="6/10")
    cycle(sched, clock)
    refreshed_once = ad.calls["refresh_event"]
    assert refreshed_once == 1 and "PREDICTION_STALE" in kinds(store, model="meta") and "COVERAGE_IMPROVED" in kinds(store)
    clock.advance(minutes=15)
    cycle(sched, clock)
    assert ad.calls["refresh_event"] == refreshed_once                                # the 15-minute check ran but the event's evidence is not re-requested inside 2 hours

    clock.t = datetime(2026, 11, 12, 23, 0, tzinfo=UTC)                               # t4: result out and the reaction known
    ad.res[("AAA", "2026-11-12")] = {"published_at": "2026-11-12T11:30:00+00:00"}
    ad.out[("AAA", "2026-11-12")] = {"move_pct": -4.2, "direction": "SELL", "available_at": clock().isoformat()}
    cycle(sched, clock)
    assert {"RESULT_PUBLISHED", "OUTCOME_AVAILABLE"} <= set(kinds(store)) and ad.calls["verify_outcome"] <= 1
    assert ev(store, "AAA")["status"] == "REPORTED" and json.loads(ev(store, "AAA")["outcome_json"])["move_pct"] == -4.2
    expected = {"NEW_EVENT", "EVENT_RESCHEDULED", "SESSION_CHANGED", "EVENT_CANCELED", "NEW_PREDICTION", "SIGNAL_CHANGED", "AGREEMENT_CHANGED", "COVERAGE_IMPROVED", "COVERAGE_DETERIORATED",
                "PREDICTION_STALE", "RESULT_PUBLISHED", "OUTCOME_AVAILABLE"}
    assert expected == set(kinds(store))                                              # every requested change type was detected
    assert {r["rule"] for r in store.q("SELECT rule FROM alerts")} >= {"NEW_BUY_SELL", "DIRECTION_CHANGE", "STRONG_AGREEMENT", "DATE_CHANGE", "STALE_OR_MISSING_EVIDENCE"}
    assert len(set(r["dedupe_key"] for r in store.q("SELECT dedupe_key FROM alerts"))) == store.one("SELECT COUNT(*) n FROM alerts")["n"]
    assert store.one("SELECT COUNT(*) n FROM alerts WHERE status='PENDING'")["n"] == 0
    days = {r["day"]: r["requests"] for r in store.q("SELECT day, SUM(requests) requests FROM usage GROUP BY day")}
    assert days and all(v <= 300 for v in days.values()), days                        # never above the approved daily budget
    first_pred = store.one("SELECT recorded_at FROM predictions WHERE model='claude' ORDER BY id LIMIT 1")["recorded_at"]
    assert first_pred.startswith("2026-11-09T17:00")                                  # the original prediction keeps its timestamp


# ------------------------------------------------------------------ API
def test_api_is_read_only_and_reports_status_models_changes_consensus_and_accuracy(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from earnings_monitor import routes
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path, budget=700, events=8)
    ad.cal = [{"symbol": "AAA", "company": "Alpha", "date": "2026-11-12", "session": "BMO"}]
    for m, sig in (("claude", "SELL"), ("grok", "BUY"), ("meta", "BUY"), ("smart_move", "BUY")):
        ad.set_model(m, "AAA", sig, BASE, score=50, coverage="9/10")
    ad.set_model("ped", "AAA", "NEUTRAL", BASE)
    cycle(sched, clock)
    monkeypatch.setenv("EARNINGS_MONITOR_DIR", str(d))
    monkeypatch.setattr(routes, "now", lambda: clock())
    app = FastAPI()
    app.include_router(routes.router)
    c = TestClient(app)
    m = c.get("/api/earnings-monitor/models").json()
    cell = m["rows"][0]["cells"]["grok"]
    assert cell["signal"] == "BUY" and cell["validation"] == "VALID" and cell["as_of"] and cell["coverage_fraction"] == 0.9 and cell["predictions_recorded"] == 1
    assert m["rows"][0]["cells"]["ped"]["signal"] == "NEUTRAL" and {x["id"] for x in m["models"]} >= {"claude", "grok", "smart_move"}
    cons = c.get("/api/earnings-monitor/consensus").json()["rows"][0]
    assert cons["comparable"] == 5 and cons["level"] == "CONFLICT"      # Claude says SELL while three others say BUY
    clock.t = datetime(2026, 11, 12, 23, 0, tzinfo=UTC)
    ad.out[("AAA", "2026-11-12")] = {"move_pct": -4.2, "direction": "SELL"}
    cycle(sched, clock)
    s = c.get("/api/earnings-monitor/status").json()
    assert s["status"] == "OK" and s["scheduling"] in (REFRESH, CACHE_ONLY, OFF) and s["last_any_success"] and s["estimate_20_events_per_day"]["total_requests_per_day"] == 1444
    up = c.get("/api/earnings-monitor/upcoming?include_history=1").json()["rows"]
    assert up[0]["symbol"] == "AAA" and up[0]["status"] == "REPORTED" and up[0]["outcome"]["move_pct"] == -4.2 and up[0]["latest_change"]["kind"] == "OUTCOME_AVAILABLE"
    ch = c.get("/api/earnings-monitor/changes?limit=50").json()
    assert ch["latest_change_at"] and "NEW_EVENT" in {r["kind"] for r in ch["rows"]}
    acc = {r["model"]: r for r in c.get("/api/earnings-monitor/accuracy").json()["rows"]}
    assert acc["claude"]["correct"] == 1 and acc["grok"]["incorrect"] == 1 and acc["ped"]["abstained"] == 1 and acc["claude"]["accuracy_pct"] is None and "TOO_SMALL" in acc["claude"]["sample_status"]      # one scored call proves nothing: no percentage
    for m_ in ("post", "put", "patch", "delete"):
        assert getattr(c, m_)("/api/earnings-monitor/status").status_code == 405
    monkeypatch.setenv("EARNINGS_MONITOR_DIR", str(tmp_path / "nothing"))
    assert c.get("/api/earnings-monitor/status").json()["status"] == "NOT_STARTED"
    assert c.get("/api/earnings-monitor/upcoming").json()["status"] == "NO_DATA"


# ------------------------------------------------------------------ safety of the package itself
def test_the_package_loads_no_provider_module_and_adapters_import_lazily(tmp_path):
    forbidden = ("unusualwhales", "finnhub", "finviz", "uw_", "anthropic", "openai", "requests", "httpx", "urllib3", "market_data")
    for f in (BACKEND / "earnings_monitor").glob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        top = {n.names[0].name.split(".")[0] for n in tree.body if isinstance(n, ast.Import)} | {n.module.split(".")[0] for n in tree.body if isinstance(n, ast.ImportFrom) and n.module and n.level == 0}
        assert not [m for m in top if m.lower().startswith(forbidden)], (f.name, top)
    code = ("import sys; sys.path.insert(0, %r)\n"
            "import earnings_monitor.routes, earnings_monitor.runtime, earnings_monitor.sim, earnings_monitor.adapters\n"
            "bad=[m for m in sys.modules if m.lower().startswith(('unusualwhales','uw_','finnhub','finviz','swr','all_models','model_prediction','database'))]\n"
            "print('LOADED', bad)\n") % str(BACKEND)
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-500:]
    assert out.stdout.strip().splitlines()[-1] == "LOADED []"


def test_runtime_starts_nothing_unless_enabled(monkeypatch, tmp_path):
    from earnings_monitor import runtime
    monkeypatch.delenv("EARNINGS_MONITOR_ENABLED", raising=False)
    monkeypatch.setenv("EARNINGS_MONITOR_DIR", str(tmp_path))
    assert runtime.start_if_enabled() is None and not (tmp_path / "monitor.sqlite").exists()
