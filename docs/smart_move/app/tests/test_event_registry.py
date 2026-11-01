"""Versioned event registry: added, removed, rescheduled, duplicate symbols, date revisions, prediction reconciliation."""
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
from smart_move_event_registry import CURRENT, REMOVED, RESCHEDULED, EventRegistry  # noqa: E402

T1 = datetime(2026, 10, 30, 15, tzinfo=timezone.utc)
T2 = datetime(2026, 11, 1, 20, tzinfo=timezone.utc)


def ev(s, d="2026-11-02", k="AMC", c=True):
    return {"symbol": s, "earnings_date": d, "kind": k, "confirmed": c}


def test_added_removed_and_unchanged(tmp_path):
    r = EventRegistry(tmp_path / "ev.jsonl")
    assert r.apply_snapshot([ev("APPS"), ev("AAPL")], T1)["added"] == ["APPS", "AAPL"]
    out = r.apply_snapshot([ev("AAPL"), ev("ROKU")], T2)
    assert out["removed"] == ["APPS"] and out["added"] == ["ROKU"] and out["rescheduled"] == []
    assert set(r.current()) == {"AAPL", "ROKU"} and r.history("APPS")[-1]["status"] == REMOVED
    assert len(r.history("AAPL")) == 1                           # unchanged event gets no new version


def test_date_revision_keeps_old_version_and_original_date(tmp_path):
    r = EventRegistry(tmp_path / "ev.jsonl")
    r.apply_snapshot([ev("ON", "2026-11-02")], T1)
    assert r.apply_snapshot([ev("ON", "2026-11-04", "BMO")], T2)["rescheduled"] == ["ON"]
    h = r.history("ON")
    assert [v["status"] for v in h] == [CURRENT, RESCHEDULED, CURRENT] and h[-1]["version"] == 2 and h[-1]["original_date"] == "2026-11-02"
    assert r.current()["ON"]["earnings_date"] == "2026-11-04"


def test_duplicate_symbols_in_one_snapshot_are_reported_first_wins(tmp_path):
    r = EventRegistry(tmp_path / "ev.jsonl")
    out = r.apply_snapshot([ev("CC", "2026-11-02"), ev("CC", "2026-11-09")], T1)
    assert out["duplicates"] == ["CC"] and r.current()["CC"]["earnings_date"] == "2026-11-02"


def test_partial_snapshot_never_removes(tmp_path):
    r = EventRegistry(tmp_path / "ev.jsonl")
    r.apply_snapshot([ev("A"), ev("B")], T1)
    assert r.apply_snapshot([ev("A")], T2, complete=False)["removed"] == [] and set(r.current()) == {"A", "B"}


def test_reappearing_symbol_becomes_current_again_and_survives_reload(tmp_path):
    p = tmp_path / "ev.jsonl"
    r = EventRegistry(p)
    r.apply_snapshot([ev("A")], T1)
    r.apply_snapshot([], T2)
    r.apply_snapshot([ev("A")], T2)
    r2 = EventRegistry(p)
    assert "A" in r2.current() and [v["status"] for v in r2.history("A")] == [CURRENT, REMOVED, CURRENT]


def test_reconcile_never_drops_a_prediction_and_counts_match_the_universe(tmp_path):
    r = EventRegistry(tmp_path / "ev.jsonl")
    r.apply_snapshot([ev("APPS"), ev("AAPL")], T1)
    r.apply_snapshot([ev("AAPL"), ev("ROKU")], T2)
    preds = [{"symbol": "AAPL", "earnings_date": "2026-11-02"}, {"symbol": "APPS", "earnings_date": "2026-11-02"},
             {"symbol": "ZZZ", "earnings_date": "2026-11-02"}]
    rec = r.reconcile(preds)
    assert len(rec["live"]) + len(rec["historical"]) + len(rec["orphan"]) == 3
    assert [p["symbol"] for p in rec["historical"]] == ["APPS"] and [p["symbol"] for p in rec["orphan"]] == ["ZZZ"]
    assert rec["universe"] == 2 and rec["current_without_prediction"] == ["ROKU"]
