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
