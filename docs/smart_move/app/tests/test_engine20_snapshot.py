"""Engine20 prediction snapshot: immutable historical record, honest labels, outcome comparison only from independently verified GRADED outcomes."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_engine20_snapshot as S  # noqa: E402
import smart_move_outcomes as O  # noqa: E402

UTC = timezone.utc
GEN = "2026-11-03T08:29:08+00:00"
CUT = "2026-11-03T11:00:00+00:00"


def row(sym, result, bull, bear, cov=30):
    return {"symbol": sym, "company": sym + " INC", "event_date": "2026-11-04", "session": "BMO", "result": result, "reason": "r", "reason_codes": [], "coverage_points": cov, "coverage_max": 40, "bull": bull, "bear": bear,
            "liquidity": "PASS", "adv20_usd": 5e8, "missing_factors": [["F7", "NOT_CAPTURED_LIVE_IN_WINDOW"]], "window": "OPEN", "window_cutoff_utc": CUT, "evidence_retrieved": ["2026-11-03T08:29:00+00:00"], "model_version": "score40-v1"}


def report(tmp_path):
    rows = [row("AAA", "BUY", 20, 0), row("BBB", "SELL", 0, 20), row("CCC", "NO TRADE (genuine)", 10, 0), row("DDD", "NO VALID PREDICTION", 3, 7, cov=15)]
    p = tmp_path / "engine20_report.json"
    p.write_text(json.dumps({"as_of": GEN, "counts": {}, "rows": rows, "model_version": "score40-v1"}), encoding="utf-8")
    return p


PASS = {"verdict": "PASS", "problems": []}


def test_snapshot_keeps_no_trade_and_no_valid_prediction_apart_and_is_immutable(tmp_path):
    f = S.build_snapshot(report(tmp_path), PASS, tmp_path / "snaps", source="t")
    snap = json.loads(f.read_text(encoding="utf-8"))
    cats = {r["symbol"]: r["category"] for r in snap["rows"]}
    assert cats == {"AAA": "BUY", "BBB": "SELL", "CCC": "NO_TRADE", "DDD": "NO_VALID_PREDICTION"}
    assert snap["counts"]["NO_TRADE"] == 1 and snap["counts"]["NO_VALID_PREDICTION"] == 1 and snap["kind"] == "HISTORICAL_SNAPSHOT"
    assert "never a neutral view" in snap["rows"][3]["category_text"] or "not a neutral view" in snap["rows"][3]["category_text"]
    with pytest.raises(FileExistsError):
        S.build_snapshot(report(tmp_path), PASS, tmp_path / "snaps", source="t")                      # same generation: never overwritten
    with pytest.raises(ValueError):
        S.build_snapshot(report(tmp_path), {"verdict": "FAIL", "problems": ["X"]}, tmp_path / "other", source="t")


def test_unknown_result_text_is_refused_not_guessed(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"as_of": GEN, "counts": {}, "rows": [row("AAA", "MAYBE", 1, 1)], "model_version": "score40-v1"}), encoding="utf-8")
    with pytest.raises(ValueError):
        S.build_snapshot(p, PASS, tmp_path / "s", source="t")


def test_read_status_is_never_a_live_signal_and_reports_window_and_age(tmp_path):
    S.build_snapshot(report(tmp_path), PASS, tmp_path / "s", source="t")
    snap = S.load_latest(tmp_path / "s")
    early = S.read_status(snap, datetime(2026, 11, 3, 9, 29, tzinfo=UTC))
    assert early["is_live_signal"] is False and early["window_open_now"] is True and early["snapshot_age_h"] == 1.0 and early["rows"][0]["evidence_age_h"] == 1.0
    assert "Historical snapshot generated 2026-11-03 08:29 UTC" in early["rows"][0]["snapshot_label"]
    late = S.read_status(snap, datetime(2026, 11, 4, 20, 0, tzinfo=UTC))
    assert late["is_live_signal"] is False and late["window_open_now"] is False


def test_an_altered_snapshot_file_is_detected(tmp_path):
    f = S.build_snapshot(report(tmp_path), PASS, tmp_path / "s", source="t")
    f.write_text(f.read_text(encoding="utf-8").replace("BUY", "SELL", 1), encoding="utf-8")
    assert S.load_latest(tmp_path / "s")["error"] == "SNAPSHOT_FILE_MISSING_OR_ALTERED"


def test_outcomes_compare_only_graded_buy_sell_and_never_invent_one(tmp_path):
    S.build_snapshot(report(tmp_path), PASS, tmp_path / "s", source="t")
    snap = S.load_latest(tmp_path / "s")
    now = datetime(2026, 11, 6, 22, 0, tzinfo=UTC)
    assert S.outcomes(snap, None, now)["available"] is False                                               # no outcome store configured: nothing is compared
    track = tmp_path / "track"
    track.mkdir()
    by = lambda o: {r["symbol"]: r for r in o["rows"]}                                                    # noqa: E731
    none_yet = by(S.outcomes(snap, track, datetime(2026, 11, 3, 12, 0, tzinfo=UTC)))
    assert all(r["comparison"] == "NOT_AVAILABLE" and r["outcome_state"] == "WAITING" for r in none_yet.values())
    O.record(track, {"symbol": "AAA", "earnings_date": "2026-11-04", "mode": "LIVE", "status": "GRADED", "move_pct": -3.0})          # BUY, stock fell: incorrect
    O.record(track, {"symbol": "BBB", "earnings_date": "2026-11-04", "mode": "LIVE", "status": "GRADED", "move_pct": -2.0})          # SELL, stock fell: correct
    O.record(track, {"symbol": "CCC", "earnings_date": "2026-11-04", "mode": "LIVE", "status": "GRADED", "move_pct": 5.0})           # NO TRADE: never scored
    res = S.outcomes(snap, track, now)
    r = by(res)
    assert (r["AAA"]["comparison"], r["BBB"]["comparison"], r["CCC"]["comparison"], r["DDD"]["comparison"]) == ("INCORRECT", "CORRECT", "NOT_APPLICABLE", "NOT_AVAILABLE")
    assert res["tally"] == {"CORRECT": 1, "INCORRECT": 1, "NOT_APPLICABLE": 1, "NOT_AVAILABLE": 1}


def test_routes_serve_the_snapshot_read_only(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import smart_move_local_server as srv
    monkeypatch.delenv("SMART_MOVE_ENGINE20_DIR", raising=False)
    c = TestClient(srv.app)
    assert c.get("/api/smart-move/engine20/latest").json()["status"] == "NO_SNAPSHOT"
    S.build_snapshot(report(tmp_path), PASS, tmp_path / "s", source="t")
    monkeypatch.setenv("SMART_MOVE_ENGINE20_DIR", str(tmp_path / "s"))
    monkeypatch.delenv("SMART_MOVE_OUTCOMES_DIR", raising=False)
    j = c.get("/api/smart-move/engine20/latest").json()
    assert j["status"] == "OK" and j["is_live_signal"] is False and j["read_only"] is True and len(j["rows"]) == 4
    assert c.get("/api/smart-move/engine20/outcomes").json()["available"] is False
    assert c.post("/api/smart-move/engine20/latest").status_code == 405
