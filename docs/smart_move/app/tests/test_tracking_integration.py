"""Tracking over real-shaped saved data: dedupe across stores, universe scope, old records without metadata, outcomes, ops view. Read only."""
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_local_server as srv  # noqa: E402
import smart_move_tracking as T  # noqa: E402


def pred(sym, d="2026-11-02", sig="BUY", meta=True, ver=1, digest="d1", cap="2026-10-31T21:30:00+00:00"):
    e = {"signal": sig, "bull": 16, "bear": 1, "available": 40, "reason_codes": ["RULE"] if sig != "NO_TRADE" else ["BELOW_MARGIN"]}
    p = {"symbol": sym, "earnings_date": d, "digest": digest, "version": ver, "captured_at": cap, "mode": "LIVE", "engine": {"continuation": e, "contrarian": dict(e, signal="NO_TRADE", reason_codes=["BELOW_MARGIN"])}, "blocked_by": [], "evidence": []}
    if meta:
        p["meta"] = {"sector": "Technology", "kind": "AMC", "adv20": 5e8, "coverage": 40}
    return p


def write(store, rows):
    Path(store).mkdir(parents=True, exist_ok=True)
    Path(store, "predictions.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")


@pytest.fixture
def env(tmp_path, monkeypatch):
    a, b = tmp_path / "out" / "run_a" / "store", tmp_path / "out" / "run_b" / "store"
    write(a, [pred("AAA"), pred("OLD", meta=False), pred("ZZZ")])
    write(b, [pred("AAA"), pred("OLD", meta=False)])                     # the same saved records reachable through a second store
    rep = {"results": [{"symbol": s, "earnings_date": "2026-11-02", "kind": "AMC", "capture_ts": "2026-10-31T21:30:00+00:00"} for s in ("AAA", "OLD")], "started_utc": "2026-10-31T21:00:00+00:00"}
    (tmp_path / "out" / "run_b").mkdir(exist_ok=True)
    (tmp_path / "out" / "run_b" / "audit_report.json").write_text(json.dumps(rep))
    monkeypatch.setenv("SMART_MOVE_RUN_ROOT", str(tmp_path / "out"))
    monkeypatch.setenv("SMART_MOVE_TRACKING_DIR", str(tmp_path / "track"))
    monkeypatch.delenv("SMART_MOVE_PRED_STORES", raising=False)
    monkeypatch.delenv("SMART_MOVE_TRACKING_DEMO", raising=False)
    return TestClient(srv.app), tmp_path


def test_duplicates_across_stores_collapse_and_scope_is_the_evaluated_universe(env):
    c, _ = env
    assert len(T.load_predictions([str(env[1] / "out" / "run_a" / "store"), str(env[1] / "out" / "run_b" / "store")])) == 3
    h = c.get("/api/smart-move/tracking/history").json()
    assert h["total"] == 2 and h["all_saved_events"] == 3 and {r["symbol"] for r in h["rows"]} == {"AAA", "OLD"}
    assert c.get("/api/smart-move/tracking/history?scope=all").json()["total"] == 3


def test_old_records_without_metadata_show_unknown_never_guessed_and_new_ones_keep_theirs(env):
    c, tmp = env
    a = c.get("/api/smart-move/tracking/analytics").json()
    assert a["metadata_known"]["rows"] == 4 and a["metadata_known"]["with_liquidity"] == 2        # AAA (2 variants) has adv20, OLD has none
    liq = a["variants"]["continuation"]["by"]["liquidity"]
    assert set(liq) == {">=200M", "unknown"} and liq[">=200M"]["events"] == 1 and liq["unknown"]["events"] == 1
    assert a["variants"]["continuation"]["signal_counts"] == {"BUY": 2, "SELL": 0, "NO_TRADE": 0}
    assert a["claim_allowed"] is False and a["graded_rows"] == 0


def test_graded_outcome_reaches_analytics_and_the_outcomes_view(env):
    c, tmp = env
    T.import_outcomes(tmp / "track", [{"symbol": "AAA", "earnings_date": "2026-11-02", "move_pct": 6.0, "source": "t"}], mode="LIVE", recorded_at=__import__("datetime").datetime(2026, 11, 5, tzinfo=__import__("datetime").timezone.utc))
    a = c.get("/api/smart-move/tracking/analytics").json()
    assert a["variants"]["continuation"]["overall"]["hits"] == 1 and a["variants"]["continuation"]["overall"]["ci95"]
    assert c.get("/api/smart-move/tracking/outcomes").json()["read_only"] is True


def test_simulation_analytics_is_labelled_and_never_claims(env):
    c, _ = env
    s = c.get("/api/smart-move/tracking/analytics?mode=SIMULATION").json()
    assert s["simulated"] is True and s["claim_allowed"] is False


def test_ops_view_is_read_only_and_reports_plan_only_scheduler(env):
    c, _ = env
    j = c.get("/api/smart-move/ops").json()
    assert j["read_only"] is True and j["sends_requests"] is False and j["scheduler"]["recurring_enabled"] is False and "PLAN ONLY" in j["scheduler"]["mode"]
    assert j["kill_switch"]["run"] == "run_b" and j["kill_switch"]["engaged"] is False       # this synthetic run has no KILL file: reported honestly


def test_restart_is_idempotent_registry_history_and_outcomes_do_not_duplicate(env):
    import smart_move_board as B
    import smart_move_outcomes as O
    from datetime import datetime, timezone
    c, tmp = env
    root = str(tmp / "out")
    reps = B.load_run_reports(root)
    r1 = B.derive_registry([], reps).current()
    r2 = B.derive_registry([], B.load_run_reports(root)).current()
    assert r1 == r2 and set(r1) == {"AAA", "OLD"}
    h1, h2 = c.get("/api/smart-move/tracking/history").json(), c.get("/api/smart-move/tracking/history").json()
    assert h1 == h2 and h1["total"] == 2 and all(r["versions"] == 1 for r in h1["rows"])
    now = datetime(2026, 11, 5, 22, tzinfo=timezone.utc)
    rows = [{"date": "2026-11-02", "market_time": "r", "open": 99, "high": 101, "low": 98, "close": 100, "volume": 1}, {"date": "2026-11-03", "market_time": "r", "open": 104, "high": 109, "low": 103, "close": 108, "volume": 1}]
    o = O.compute("AAA", "2026-11-02", "AMC", rows, now)
    assert O.record(tmp / "track", o)["result"] == "recorded" and O.record(tmp / "track", o)["result"] == "duplicate_same"
    assert len(O.latest(tmp / "track")) == 1
