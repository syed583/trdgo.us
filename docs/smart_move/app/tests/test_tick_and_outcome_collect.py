"""Tick CLI (plan only by default, refuses to execute unless fully enabled) and the governor-bound outcome collector (mock call, no provider)."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "docs" / "smart_move" / "app"))
import scheduler_tick as ST  # noqa: E402
import smart_move_outcome_collect as OC  # noqa: E402
import smart_move_outcomes as O  # noqa: E402

UTC = timezone.utc
ROWS = [{"date": "2026-11-02", "market_time": "r", "open": 99, "high": 101, "low": 98, "close": 100, "volume": 1}, {"date": "2026-11-03", "market_time": "r", "open": 104, "high": 109, "low": 103, "close": 108, "volume": 1}]
EV = [{"symbol": s, "earnings_date": "2026-11-02", "kind": "AMC"} for s in ("A", "B", "C")]
NOW = datetime(2026, 11, 3, 22, tzinfo=UTC)


def good(kind, sym, params):
    assert kind == "ohlc" and params == {"timeframe": "1M"}
    return json.dumps({"data": ROWS}).encode(), "sha-" + sym, NOW


def test_collects_only_due_events_within_budget_and_records_append_only(tmp_path):
    calls = []

    def call(k, s, p):
        calls.append(s)
        return good(k, s, p)
    r = OC.collect(EV, tmp_path, NOW, call, max_requests=2)
    assert calls == ["A", "B"] and r["deferred_by_budget"] == ["C"] and len(r["recorded"]) == 2
    assert {o["symbol"] for o in O.latest(tmp_path)} == {"A", "B"} and O.latest(tmp_path)[0]["source"].startswith("uw.ohlc.1d raw_sha256=sha-")
    again = OC.collect(EV, tmp_path, NOW, call, max_requests=5)
    assert calls == ["A", "B", "C"] and len(O.latest(tmp_path)) == 3            # A and B are never requested twice


def test_not_due_yet_sends_nothing_and_a_failed_call_records_nothing_and_a_stop_ends_the_batch(tmp_path):
    early = datetime(2026, 11, 3, 21, 5, tzinfo=UTC)
    assert OC.collect(EV, tmp_path, early, lambda *a: (_ for _ in ()).throw(AssertionError("must not call")), max_requests=9)["requests"] == 0
    seen = []

    def failing(k, s, p):
        seen.append(s)
        return None, None, NOW
    r = OC.collect(EV, tmp_path, NOW, failing, max_requests=9, stopped=lambda: len(seen) >= 1)
    assert r["stopped"] and seen == ["A"] and O.latest(tmp_path) == []


def test_tick_plans_only_by_default_and_refuses_execute_without_the_enable_switches(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SMART_MOVE_LIVE_COLLECTION", raising=False)
    rc = ST.main(["--run-root", str(tmp_path), "--now", "2026-11-01T12:00:00+00:00"])
    assert rc == 0 and json.loads((tmp_path / "tick_log.jsonl").read_text().splitlines()[0])["mode"] == "PLAN_ONLY"
    rc = ST.main(["--run-root", str(tmp_path), "--now", "2026-11-01T12:00:00+00:00", "--execute"])
    assert rc == 2 and "refused" in json.loads((tmp_path / "tick_log.jsonl").read_text().splitlines()[-1])
    monkeypatch.setenv("SMART_MOVE_LIVE_COLLECTION", "1")
    assert ST.live_enabled(tmp_path) is False                                   # env alone is not enough
    (tmp_path / "LIVE_COLLECTION_ENABLED").write_text("x")
    assert ST.live_enabled(tmp_path) is True
    monkeypatch.delenv("SMART_MOVE_LIVE_COLLECTION")
    assert ST.live_enabled(tmp_path) is False                                   # flag file alone is not enough


def test_deploy_files_are_disabled():
    d = ROOT / "docs" / "smart_move" / "deploy"
    x = (d / "SmartMove_Tick.task.xml").read_text()
    assert x.count("<Enabled>false</Enabled>") == 2 and "--execute" not in x.split("<Arguments>")[1].split("</Arguments>")[0]
    assert "WantedBy" in (d / "smartmove-tick.timer").read_text() and "enable --now" in (d / "smartmove-tick.timer").read_text() and "--execute" not in (d / "smartmove-tick.service").read_text().split("ExecStart")[1]


def _seeded_root(tmp_path):
    d = tmp_path / "run_old"
    (d / "store" / "payloads").mkdir(parents=True)
    for n in ("cal_obs.jsonl", "evidence.jsonl"):
        (d / "store" / n).write_text("")
    (d / "audit_report.json").write_text(json.dumps({"started_utc": "2026-10-31T21:00:00+00:00", "results": [{"symbol": "A", "earnings_date": "2026-11-02", "capture_ts": "x"}]}))
    return tmp_path


def test_execute_path_builds_real_argument_paths_budget_and_runs_one_collection_per_tick(tmp_path):
    root = _seeded_root(tmp_path)
    now = datetime(2026, 11, 2, 12, tzinfo=UTC)
    res = {"actions": [{"action": "COLLECT", "symbols": ["A"], "requests": 60}, {"action": "COLLECT", "symbols": ["B"], "requests": 60}]}
    seen = []

    def runner(cmd):
        seen.append(cmd)
        return 0
    assert ST.run_actions(res, root, now, runner=runner) == 0 and len(seen) == 1
    c = seen[0]
    assert c[c.index("--seed-store") + 1] == str(root / "run_old" / "store") and c[c.index("--seed-report") + 1] == str(root / "run_old" / "audit_report.json")
    assert c[c.index("--out-dir") + 1] == str(root / "run_20261102_1200_auto") and int(c[c.index("--cap") + 1]) == 85 and "--resume" not in c


def test_execute_refuses_without_a_seed_resumes_an_interrupted_folder_and_ignores_a_finished_one(tmp_path):
    now = datetime(2026, 11, 2, 12, tzinfo=UTC)
    res = {"actions": [{"action": "COLLECT", "symbols": ["A"], "requests": 60}]}
    called = []
    assert ST.run_actions(res, tmp_path, now, runner=lambda c: called.append(c) or 0) == 2 and called == []          # nothing to seed from: nothing is sent
    root = _seeded_root(tmp_path / "r")
    out = root / "run_20261102_1200_auto"
    out.mkdir()
    (out / "checkpoint.json").write_text("{}")
    cmds = []
    ST.run_actions(res, root, now, runner=lambda c: cmds.append(c) or 0)
    assert cmds[0][-1] == "--resume"                                                                               # interrupted: continue, never restart
    (out / "audit_report.json").write_text("{}")
    cmds.clear()
    assert ST.run_actions(res, root, now, runner=lambda c: cmds.append(c) or 0) == 0 and cmds == []              # finished: a repeated tick is a no-op


def test_a_provider_stop_in_the_collector_returns_exit_5_and_the_cli_never_executes_by_default(tmp_path, monkeypatch):
    root = _seeded_root(tmp_path)
    res = {"actions": [{"action": "COLLECT", "symbols": ["A"], "requests": 10}]}
    assert ST.run_actions(res, root, datetime(2026, 11, 2, 13, tzinfo=UTC), runner=lambda c: 5) == 5
    monkeypatch.delenv("SMART_MOVE_LIVE_COLLECTION", raising=False)
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    assert ST.main(["--run-root", str(root), "--now", "2026-11-02T12:00:00+00:00", "--execute"]) == 2


def test_graded_outcomes_flow_into_tracking_and_the_accuracy_analytics_without_touching_predictions(tmp_path):
    import smart_move_analytics as A
    import smart_move_tracking as T
    pred = {"symbol": "A", "earnings_date": "2026-11-02", "digest": "d", "version": 1, "captured_at": "2026-10-31T21:30:00+00:00", "mode": "LIVE", "blocked_by": [], "evidence": [],
            "engine": {"continuation": {"signal": "BUY", "bull": 18, "bear": 1, "available": 40, "reason_codes": ["RULE"]}, "contrarian": {"signal": "SELL", "bull": 1, "bear": 18, "available": 40, "reason_codes": ["RULE"]}}}
    store = tmp_path / "s"
    store.mkdir()
    (store / "predictions.jsonl").write_text(json.dumps(pred) + "\n")
    before = (store / "predictions.jsonl").read_bytes()
    OC.collect([{"symbol": "A", "earnings_date": "2026-11-02", "kind": "AMC"}], tmp_path / "t", NOW, good, max_requests=5)
    outs = T.load_outcomes(tmp_path / "t")
    assert len(outs) == 1 and outs[0]["move_pct"] == 8.0 and outs[0]["mode"] == "LIVE"
    rows = A.rows_from_records(T.load_predictions([store]), outs)
    assert {(r["variant"], r["signal"], r["move_pct"]) for r in rows} == {("continuation", "BUY", 8.0), ("contrarian", "SELL", 8.0)}
    ana = A.breakdown(rows)
    assert ana["variants"]["continuation"]["overall"]["hits"] == 1 and ana["variants"]["contrarian"]["overall"]["hits"] == 0 and ana["claim_allowed"] is False
    assert (store / "predictions.jsonl").read_bytes() == before                      # the issued prediction is never altered by an outcome
    OC.collect([{"symbol": "A", "earnings_date": "2026-11-02", "kind": "AMC"}], tmp_path / "t", NOW, good, max_requests=5)
    assert len(T.load_outcomes(tmp_path / "t")) == 1                                  # a second pass adds nothing


# ------------------------------------------------------------------ V2 evidence, outcomes and audit actions through the service path (mock service, no provider)
class FakeLive:
    def __init__(self, tmp, now, stop_after=None):
        import smart_move_live as L
        self.store = L.LiveStore(tmp)
        self.calls = []
        self.now, self.stop_after = now, stop_after

        class G:
            stopped_until = None
        self.gov = G()

    class clock:
        pass

    def _call(self, kind, symbol, params, rid, priority="core"):
        self.calls.append((kind, symbol))
        if self.stop_after is not None and len(self.calls) > self.stop_after:
            self.gov.stopped_until = self.now + timedelta(days=1)
            return None, None, self.now
        body = {"uw_analysts": {"data": []}, "uw_estimates": {"data": {"estimates": [{"date": "2026-12-31", "eps_estimate_average": 1}]}}, "uw_flow_alerts": {"data": [{"created_at": "2026-11-01T00:00:00+00:00", "type": "call"}]},
                "uw_short_float": {"data": {"market_date": "2026-10-20"}}, "uw_inst_own": {"data": [{"filing_date": "2026-09-30"}]}, "ohlc": {"data": ROWS}}[kind]
        raw = json.dumps(body).encode()
        return raw, self.store.put(raw), self.now


def _res(actions):
    return {"actions": actions}


def test_v2_collection_and_outcomes_run_through_the_service_and_the_kill_switch_is_re_engaged(tmp_path):
    import smart_move_v2_collect as V2C
    run_root = tmp_path / "out"
    run_root.mkdir()
    now = NOW
    fake = FakeLive(tmp_path / "v2_store", now)
    fake.clock = type("C", (), {"now": staticmethod(lambda: now)})()
    tasks = V2C.plan(["A"], fake.store, now)
    audits = []
    rc = ST.run_service_actions(_res([{"action": "COLLECT_V2", "tasks": tasks, "requests": len(tasks), "symbols": ["A"]},
                                      {"action": "FETCH_OUTCOMES", "requests": 2, "event_list": [{"symbol": "A", "earnings_date": "2026-11-02", "kind": "AMC"}]}, {"action": "GENERATE_AUDIT"}]),
                                run_root, now, factory=lambda *a: fake, audit_fn=lambda: audits.append(1))
    assert rc == 0 and len([c for c in fake.calls if c[0] != "ohlc"]) == 5 and ("ohlc", "A") in fake.calls and audits == [1]
    assert (tmp_path / "v2_store" / "KILL").exists()                                        # engaged again after the execution
    import smart_move_outcomes as O
    assert O.latest(tmp_path / "tracking")[0]["status"] == "GRADED" and (tmp_path / "v2_store" / "checkpoint.json").exists()


def test_a_provider_stop_during_v2_collection_returns_exit_5_and_sends_nothing_more(tmp_path):
    import smart_move_v2_collect as V2C
    run_root = tmp_path / "out"
    run_root.mkdir()
    fake = FakeLive(tmp_path / "v2_store", NOW, stop_after=2)
    fake.clock = type("C", (), {"now": staticmethod(lambda: NOW)})()
    tasks = V2C.plan(["A", "B"], fake.store, NOW)
    rc = ST.run_service_actions(_res([{"action": "COLLECT_V2", "tasks": tasks, "requests": len(tasks), "symbols": ["A", "B"]}]), run_root, NOW, factory=lambda *a: fake)
    assert rc == 5 and len(fake.calls) == 3 and (tmp_path / "v2_store" / "KILL").exists()


def test_the_tick_plans_v2_evidence_after_v1_and_an_audit_after_a_finished_run():
    import smart_move_scheduler as S
    from smvshadow import cutoff as co
    from datetime import date
    s, _ = co.plan_event(date(2026, 11, 3), "AMC")["window_start_utc"], None
    now = s + timedelta(hours=3)
    tasks = [{"kind": "uw_flow_alerts", "symbol": "A", "id": "x"}, {"kind": "uw_short_float", "symbol": "A", "id": "y"}]
    t = S.tick(now, [{"symbol": "A", "earnings_date": "2026-11-03", "kind": "AMC"}], set(), set(), S.Budget(), calendar_age_h=1.0, collection_enabled=False, v2_tasks=tasks, audit_due=True)
    kinds = [a["action"] for a in t["actions"]]
    assert kinds == ["COLLECT", "COLLECT_V2", "GENERATE_AUDIT"] and t["execute"] is False and all("blocked" in a for a in t["actions"])
    stopped = S.tick(now, [{"symbol": "A", "earnings_date": "2026-11-03", "kind": "AMC"}], set(), set(), S.Budget(stopped=True), calendar_age_h=1.0, collection_enabled=True, v2_tasks=tasks)
    assert not any(a["action"] in ("COLLECT", "COLLECT_V2") for a in stopped["actions"])
