"""Prediction history, outcome tracking, accuracy and reports: saved or SIMULATED data only, no request, no provider."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_local_server as srv  # noqa: E402
import smart_move_tracking as T  # noqa: E402
import smart_move_tracking_sim as S  # noqa: E402

UTC = timezone.utc


def pred(sym="AAA", ed="2026-11-03", ver=1, cap="2026-11-01T18:00:00+00:00", sig="BUY", mode="SIMULATION", reasons=(), blocked=()):
    eng = {v: {"signal": sig, "bull": 16, "bear": 2, "available": 40, "max": 40, "reason_codes": list(reasons)} for v in T.VARIANTS}
    return {"symbol": sym, "earnings_date": ed, "digest": "d%d" % ver, "version": ver, "captured_at": cap, "mode": mode, "engine": eng, "blocked_by": list(blocked), "evidence": []}


def write_store(path, rows):
    path.mkdir(parents=True, exist_ok=True)
    (path / "predictions.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


def test_history_keeps_the_whole_version_chain_and_never_edits(tmp_path):
    store = write_store(tmp_path / "s", [pred(ver=1, sig="NO_TRADE"), pred(ver=2, sig="BUY", cap="2026-11-02T01:00:00+00:00")])
    before = (store / "predictions.jsonl").read_bytes()
    h = T.history(T.load_predictions([store]))
    assert len(h) == 1 and h[0]["versions"] == 2 and [c["signals"]["continuation"] for c in h[0]["chain"]] == ["NO_TRADE", "BUY"] and h[0]["latest"]["continuation"]["signal"] == "BUY"
    assert (store / "predictions.jsonl").read_bytes() == before


def test_late_capture_is_flagged_and_excluded_from_grading(tmp_path):
    late = pred(sym="LATE", cap="2026-11-03T15:00:00+00:00")            # 10:00 ET on the earnings day
    ok = pred(sym="OK")
    assert T.is_late(late) and not T.is_late(ok)
    outs = [{"symbol": s, "earnings_date": "2026-11-03", "mode": "SIMULATION", "status": "GRADED", "move_pct": 4.0, "source": "x"} for s in ("LATE", "OK")]
    acc = T.accuracy([late, ok], outs, mode="SIMULATION")
    assert acc["extra_audit"]["late_capture_excluded"] == 1 and acc["variants"]["continuation"]["graded_events"] == 1


def test_modes_never_mix_and_simulation_never_allows_a_claim(tmp_path):
    preds = [pred(sym="S1", mode="SIMULATION"), pred(sym="L1", mode="LIVE")]
    outs = [{"symbol": "S1", "earnings_date": "2026-11-03", "mode": "SIMULATION", "status": "GRADED", "move_pct": 3.0, "source": "x"},
            {"symbol": "L1", "earnings_date": "2026-11-03", "mode": "LIVE", "status": "GRADED", "move_pct": -3.0, "source": "x"}]
    sim, live = T.accuracy(preds, outs, mode="SIMULATION"), T.accuracy(preds, outs, mode="LIVE")
    assert sim["variants"]["continuation"]["graded_events"] == 1 and live["variants"]["continuation"]["graded_events"] == 1
    assert sim["variants"]["continuation"]["strict_hit_rate"] == 1.0 and live["variants"]["continuation"]["strict_hit_rate"] == 0.0
    assert sim["claim_allowed"] is False and "SIMULATED" in sim["claim_text"] and live["claim_allowed"] is False


def test_outcomes_are_append_only_and_refuse_hindsight_and_overwrites(tmp_path):
    t = tmp_path / "track"
    now = datetime(2026, 11, 10, tzinfo=UTC)
    r = T.import_outcomes(t, [{"symbol": "aaa", "earnings_date": "2026-11-03", "move_pct": "4.2", "source": "csv"}], mode="LIVE", recorded_at=now)
    assert r == {"imported": 1}
    assert T.import_outcomes(t, [{"symbol": "AAA", "earnings_date": "2026-11-03", "move_pct": 4.2}], mode="LIVE", recorded_at=now) == {"duplicate_same": 1}
    assert T.import_outcomes(t, [{"symbol": "AAA", "earnings_date": "2026-11-03", "move_pct": -9}], mode="LIVE", recorded_at=now) == {"refused_conflicting_overwrite": 1}
    assert len(T.load_outcomes(t)) == 1 and T.load_outcomes(t)[0]["move_pct"] == 4.2
    early = T.import_outcomes(t, [{"symbol": "BBB", "earnings_date": "2026-11-12", "move_pct": 1}], mode="LIVE", recorded_at=now)
    assert early == {"refused_recorded_before_the_event_ended": 1}
    assert T.import_outcomes(t, [{"symbol": "CCC"}, {"symbol": "DDD", "earnings_date": "2026-11-03", "move_pct": 500}], mode="LIVE", recorded_at=now) == {"refused_malformed": 1, "refused_implausible_move": 1}


def test_simulated_run_shows_no_skill_no_claim_and_a_full_report(tmp_path):
    info = S.simulate(tmp_path / "store", tmp_path / "track", weeks=10, per_week=14)
    preds, outs = T.load_predictions([tmp_path / "store"]), T.load_outcomes(tmp_path / "track")
    assert info["predictions"] > 140 and len(outs) > 100 and all(p["mode"] == "SIMULATION" for p in preds)
    rep = T.write_report(tmp_path / "reports", preds, outs, generated_at=datetime(2026, 11, 20, tzinfo=UTC))
    blk = rep["by_mode"]["SIMULATION"]
    assert blk["accuracy"]["claim_allowed"] is False and rep["late_captures"] > 0
    md = Path(rep["paths"]["md"]).read_text()
    assert "SIMULATED data" in md and "says nothing about the model" in md
    assert "| Variant | BUY | SELL | NO TRADE |" in md and "Outcome tracking" in md
    hr = [v["strict_hit_rate"] for v in blk["accuracy"]["variants"].values() if v["strict_hit_rate"] is not None]
    assert hr and all(0.2 <= x <= 0.8 for x in hr)                      # pure noise: nowhere near a performance claim
    with pytest.raises(FileExistsError):
        T.write_report(tmp_path / "reports", preds, outs, generated_at=datetime(2026, 11, 20, tzinfo=UTC))      # a report is never overwritten


def test_empty_state_is_explained_not_faked(tmp_path, monkeypatch):
    monkeypatch.delenv("SMART_MOVE_TRACKING_DEMO", raising=False)
    monkeypatch.delenv("SMART_MOVE_PRED_STORES", raising=False)
    monkeypatch.delenv("SMART_MOVE_TRACKING_DIR", raising=False)
    c = TestClient(srv.app)
    s = c.get("/api/smart-move/tracking/status").json()
    assert s["prediction_versions"] == 0 and s["sends_requests"] is False and "No stored predictions" in s["message"]
    assert c.get("/api/smart-move/tracking/history").json()["rows"] == []
    rep = c.get("/api/smart-move/tracking/report").json()
    assert rep["events"] == 0 and "No stored predictions yet." in rep["markdown"]


def test_api_serves_saved_predictions_with_outcomes_and_is_read_only(tmp_path, monkeypatch):
    store = write_store(tmp_path / "s", [pred(sym="AAA", mode="LIVE", sig="BUY"), pred(sym="BBB", mode="LIVE", sig="NO_TRADE")])
    T.import_outcomes(tmp_path / "t", [{"symbol": "AAA", "earnings_date": "2026-11-03", "move_pct": 5.5, "source": "csv"}], mode="LIVE", recorded_at=datetime(2026, 11, 10, tzinfo=UTC))
    monkeypatch.setenv("SMART_MOVE_PRED_STORES", str(store))
    monkeypatch.setenv("SMART_MOVE_TRACKING_DIR", str(tmp_path / "t"))
    monkeypatch.delenv("SMART_MOVE_TRACKING_DEMO", raising=False)
    c = TestClient(srv.app)
    h = c.get("/api/smart-move/tracking/history").json()
    assert h["total"] == 2 and {r["symbol"]: (r["outcome"] or {}).get("class") for r in h["rows"]} == {"AAA": "UP", "BBB": None}
    assert c.get("/api/smart-move/tracking/history?signal=BUY").json()["total"] == 1
    acc = c.get("/api/smart-move/tracking/accuracy?mode=LIVE").json()
    assert acc["claim_allowed"] is False and acc["variants"]["continuation"]["graded_events"] == 1 and acc["variants"]["continuation"]["minimum_sample_met"] is False
    for method in ("post", "put", "delete"):
        assert getattr(c, method)("/api/smart-move/tracking/history").status_code in (404, 405)
    assert c.get("/api/smart-move/tracking/history?mode=bogus").status_code == 422


def test_demo_mode_is_labelled_simulation(monkeypatch):
    monkeypatch.setenv("SMART_MOVE_TRACKING_DEMO", "1")
    c = TestClient(srv.app)
    s = c.get("/api/smart-move/tracking/status").json()
    assert s["demo"] is True and s["modes"] == ["SIMULATION"] and s["outcomes"] > 0
    assert c.get("/api/smart-move/tracking/accuracy?mode=SIMULATION").json()["claim_allowed"] is False


def test_this_layer_cannot_reach_a_provider_or_the_scoring_engine():
    src = "".join((ROOT / "backend" / f).read_text() for f in ("smart_move_tracking.py", "smart_move_tracking_routes.py", "smart_move_tracking_sim.py"))
    for forbidden in ("unusualwhales_service", "finnhub_service", "finviz_service", "InfraFetcher", "requests.get", "httpx", "urlopen", "scorer", "RefreshService", "engage_kill_switch"):
        assert forbidden not in src, forbidden
