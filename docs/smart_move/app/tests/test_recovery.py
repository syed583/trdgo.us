"""Recovery audit: categories, nothing moved to 'available' without a timestamp-valid source, VAL/CE/FANG focus, consensus history below the 7-day band, route."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_recovery as R  # noqa: E402
import smart_move_recovery_run as RR  # noqa: E402

UTC = timezone.utc
AUD = sorted(Path(r"C:\Users\initi\SmartMoveRun\audits_final").glob("interim_v2_audit_*.json"))
V2R = Path(r"C:\Users\initi\SmartMoveRun\v2\v2r_run_20261102_research.json")
V1 = Path(r"C:\Users\initi\SmartMoveRun\out\run_20261101_full_universe\audit_report.json")
STORE = Path(r"C:\Users\initi\SmartMoveRun\v2_runs\v2_20261101\store")
FIRST = Path(r"C:\Users\initi\AppData\Local\Temp\claude\C--Users-initi-Downloads-US-STOCKS\f48be4ee-7f76-4cb9-a237-d4759a147702\scratchpad\live_first_run\view")


def test_each_missing_factor_gets_the_documented_category_and_none_is_recoverable_after_the_cutoff():
    f = lambda cause: {"cause": cause}  # noqa: E731
    cases = [("F3", f("STALE_CALENDAR_AT_CAPTURE"), "NONE_IN_LOOKBACK", "AAA", 6), ("OPTIONS_FLOW", f("API_FAILURE"), "NOT_COLLECTED", "AAA", 7), ("NEWS_GUIDANCE", f("UNVERIFIED_SEMANTICS"), "NOT_COLLECTED", "AAA", 5),
             ("OPTIONS_FLOW", f("FILTERED_BY_RULE"), "NONE_IN_LOOKBACK", "AAA", 3), ("OPTIONS_FLOW", f("GENUINE_ABSENCE"), "NO_ALERTS", "CRD.B", 1), ("ANALYST", f("INSUFFICIENT_HISTORY"), "x", "AAA", 4),
             ("IMPLIED_VS_HIST", f("INSUFFICIENT_HISTORY"), "x", "AAA", 4), ("SHORT_INST", f("INSUFFICIENT_HISTORY"), "x", "AAA", 4)]
    for factor, fd, state, sym, cat in cases:
        c = R.categorize(factor, fd, state, sym, False)
        assert c["category"] == cat and c["recoverable_today"] is False and c["reason"] and c["needs"]
    assert R.categorize("F3", f("STALE_CALENDAR_AT_CAPTURE"), "x", "A", False)["preserves_cutoff"] is False


@pytest.mark.skipif(not (AUD and V2R.exists() and V1.exists() and STORE.exists() and FIRST.exists()), reason="saved collection not on this machine")
def test_the_recovery_pass_on_the_real_saved_data_recovers_nothing_that_was_not_there_and_changes_no_signal(tmp_path):
    ca = [json.loads(ln) for ln in (STORE / "cal_obs.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    roots = [STORE, Path(r"C:\Users\initi\SmartMoveRun\out\run_20261101_full_universe\store"), FIRST]
    rec = R.build(AUD[-1], V2R, V1, RR.make_loader(roots), ca, datetime(2026, 11, 2, 12, tzinfo=UTC))
    assert rec["stocks"] == 95 and rec["recovery"]["recovered_factors"] == 0 and rec["recovery"]["stocks_improved"] == 0 and rec["before_after"]["before"] == rec["before_after"]["after"]
    assert rec["before_after"]["counts_before"] == rec["before_after"]["counts_after"] and rec["before_after"]["counts_after"]["continuation"] == {"DATA_INCOMPLETE": 90, "NO_TRADE": 5}
    assert all(p["cutoff_passed"] for p in rec["per_stock"]) and all(not u["recoverable_today"] for p in rec["per_stock"] for u in p["unresolved"])
    assert all(p["after"]["available_of_100"] == p["before"]["available_of_100"] and p["after"]["directional_of_100"] == p["before"]["directional_of_100"] for p in rec["per_stock"])
    ch = rec["consensus_history"]
    assert ch["max_span_hours"] == 43.4 and ch["usable_for_revision_now"] is False and ch["stocks"] >= 90
    assert rec["focus"]["VAL"]["headroom_by_variant"]["continuation"]["rule_would_fire_if_covered"] == "BUY"
    assert rec["focus"]["CE"]["headroom_by_variant"]["contrarian"]["rule_would_fire_if_covered"] == "SELL" and rec["focus"]["FANG"]["headroom_by_variant"]["contrarian"]["rule_would_fire_if_covered"] == "SELL"
    assert all(rec["focus"][s]["directional"] == 55 for s in ("VAL", "CE", "FANG"))
    assert set(rec["stocks_by_category"]) >= {"STALE_CALENDAR_OR_CAPTURE_TIME_FAILURE", "GENUINE_ABSENCE_OF_QUALIFYING_ACTIVITY", "HISTORICAL_SNAPSHOT_UNAVAILABLE", "UNVERIFIED_NEWS_SEMANTICS"}
    assert "OTHER_IMPLEMENTATION_BUG" not in rec["stocks_by_category"]
    from pypdf import PdfReader

    import smart_move_recovery_pdf as P
    out = P.render(rec, tmp_path / "r.pdf", "tests line")
    assert out["pages"] == 2 and len(PdfReader(out["pdf"]).pages) == 2


def test_the_recovery_route_reports_the_saved_audit_read_only(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import smart_move_local_server as srv
    rec = {"generated_utc": "t", "stocks": 1, "audit_hash": "h", "recovery": {"recovered_factors": 0, "stocks_improved": 0, "stocks_still_blocked": 1}, "before_after": {}, "missing_points_by_category": {}, "stocks_by_category": {}, "consensus_history": {"max_span_hours": 43.4},
           "focus": {}, "per_stock": [{"symbol": "A", "cutoff_passed": True, "unresolved": [{"factor": "ANALYST", "points_missing": 10, "category": 4, "reason": "r", "recoverable_today": False, "needs": "n", "category_name": "x"}]}], "can_be_fixed_now": [], "needs_future_observations": []}
    (tmp_path / "recovery_audit_x.json").write_text(json.dumps(rec))
    monkeypatch.setenv("SMART_MOVE_RECOVERY_DIR", str(tmp_path))
    j = TestClient(srv.app).get("/api/smart-move/recovery").json()
    assert j["available"] and j["recovery"]["recovered_factors"] == 0 and j["per_stock"][0]["unresolved"][0]["recoverable_today"] is False
    monkeypatch.setenv("SMART_MOVE_RECOVERY_DIR", str(tmp_path / "none"))
    assert TestClient(srv.app).get("/api/smart-move/recovery").json()["available"] is False
