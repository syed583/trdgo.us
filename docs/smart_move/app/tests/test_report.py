"""PASS / FAIL / FLAT reporting, contrarian_partial_factor comparison, accuracy status and calendar freshness. Saved or SIMULATED data only."""
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_local_server as srv  # noqa: E402
import smart_move_report as RP  # noqa: E402


def pred(sym, cs, ks, ed="2026-11-03", codes=()):
    eng = {"continuation": {"signal": cs, "bull": 16, "bear": 2, "available": 30, "reason_codes": list(codes)}, "contrarian": {"signal": ks, "bull": 2, "bear": 16, "available": 30, "reason_codes": list(codes)}}
    return {"symbol": sym, "earnings_date": ed, "digest": "d" + sym, "version": 1, "captured_at": "2026-11-01T18:00:00+00:00", "mode": "SIMULATION", "engine": eng, "blocked_by": [], "evidence": [], "kind": "AMC"}


def out(sym, mv, ed="2026-11-03", **kw):
    return dict({"symbol": sym, "earnings_date": ed, "mode": "SIMULATION", "status": "GRADED", "move_pct": mv, "source": "fixture"}, **kw)


@pytest.mark.parametrize("sig,mv,want", [("BUY", 0.51, "PASS"), ("BUY", -0.51, "FAIL"), ("BUY", 0.5, "FLAT"), ("BUY", -0.5, "FLAT"), ("BUY", 0.0, "FLAT"),
                                         ("SELL", -0.51, "PASS"), ("SELL", 0.51, "FAIL"), ("SELL", 0.5, "FLAT"), ("SELL", -0.5, "FLAT"),
                                         ("NO_TRADE", 5.0, None), ("DATA_INCOMPLETE", -5.0, None), ("BUY", None, None), (None, 1.0, None)])
def test_pass_fail_flat_follows_the_frozen_band_with_inclusive_boundaries(sig, mv, want):
    assert RP.pass_fail(sig, mv) == want


def test_rows_never_grade_no_trade_or_incomplete_and_never_manufacture_missing_outcomes():
    preds = [pred("A", "BUY", "SELL"), pred("B", "NO_TRADE", "NO_TRADE"), pred("C", "NO_TRADE", "NO_TRADE", codes=("INSUFFICIENT_COVERAGE",)), pred("D", "SELL", "BUY")]
    rows = RP.event_rows(preds, [out("A", 3.0, base_close=100.0, reaction_close=103.0), out("B", 4.0), out("C", -4.0)], mode="SIMULATION")
    by = {(r["symbol"], r["strategy"]): r for r in rows}
    assert by[("A", "continuation")]["result"] == "PASS" and by[("A", "contrarian_partial_factor")]["result"] == "FAIL"
    assert by[("A", "continuation")]["reference_close"] == 100.0 and by[("A", "continuation")]["reaction_close"] == 103.0 and by[("A", "continuation")]["actual_direction"] == "UP"
    assert by[("B", "continuation")]["result"] is None and by[("B", "continuation")]["decision"] == "NO_TRADE"
    assert by[("C", "continuation")]["result"] is None and by[("C", "continuation")]["decision"] == "DATA_INCOMPLETE" and by[("C", "continuation")]["missing_reason"]
    assert by[("D", "continuation")]["result"] is None and by[("D", "continuation")]["move_pct"] is None and by[("D", "continuation")]["actual_direction"] is None
    assert by[("A", "continuation")]["coverage"] == 0.75


def test_late_capture_is_not_graded():
    p = pred("L", "BUY", "SELL")
    p["captured_at"] = "2026-11-03T15:00:00+00:00"
    r = RP.event_rows([p], [out("L", 3.0)], mode="SIMULATION")[0]
    assert r["late_capture"] and r["result"] is None and r["decision"] == "LATE_CAPTURE_NOT_GRADED"


def test_comparison_separates_agree_opposite_and_one_abstains_and_labels_the_definition():
    c = RP.comparison([pred("A", "BUY", "BUY"), pred("B", "BUY", "SELL"), pred("C", "BUY", "NO_TRADE")], mode="SIMULATION")
    assert c["relations"] == {"AGREE": 1, "OPPOSITE": 1, "ONE_ABSTAINS": 1} and "contrarian_full_mirror" in c["note"] and "contrarian_full_mirror" in c["definitions"]["contrarian"]


def test_accuracy_status_is_not_verified_without_outcomes_and_without_the_claim_gate():
    rows = RP.event_rows([pred("A", "BUY", "SELL")], [], mode="SIMULATION")
    assert RP.accuracy_status(rows)["headline"] == RP.NOT_VERIFIED
    rows = RP.event_rows([pred("A", "BUY", "SELL")], [out("A", 3.0)], mode="SIMULATION")
    a = RP.accuracy_status(rows, {"claim_allowed": False})
    assert a["headline"] == RP.NOT_VERIFIED and a["strategies"]["continuation"]["pass"] == 1 and a["claim_allowed"] is False


def test_calendar_status_uses_the_frozen_six_hour_limit():
    assert RP.calendar_status(None)["status"] == "UNKNOWN" and RP.calendar_status(1)["status"] == "FRESH"
    assert RP.calendar_status(5.5)["status"] == "AGING" and RP.calendar_status(6.5)["status"] == "STALE"


def test_events_route_is_read_only_and_does_not_change_the_stores(tmp_path, monkeypatch):
    store = tmp_path / "s"
    store.mkdir()
    (store / "predictions.jsonl").write_text(json.dumps(pred("A", "BUY", "SELL")) + "\n")
    (store / "cal_obs.jsonl").write_text(json.dumps({"observed_at": "2026-11-01T10:00:00+00:00"}) + "\n")
    before = {f.name: f.read_bytes() for f in store.iterdir()}
    monkeypatch.setenv("SMART_MOVE_PRED_STORES", str(store))
    monkeypatch.setenv("SMART_MOVE_TRACKING_DIR", str(tmp_path / "t"))
    j = TestClient(srv.app).get("/api/smart-move/tracking/events?mode=SIMULATION").json()
    assert j["accuracy"]["headline"] == RP.NOT_VERIFIED and j["rows"][0]["strategy"] == "continuation" and j["calendar"]["status"] == "STALE"
    assert before == {f.name: f.read_bytes() for f in store.iterdir()}
