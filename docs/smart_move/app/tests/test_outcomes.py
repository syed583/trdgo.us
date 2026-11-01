"""Outcome pipeline: premature refusal, BMO/AMC session matching, bars, append-only corrections, fetch planning, tracking/grader hand-off. No provider is contacted."""
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_outcomes as O  # noqa: E402
import smart_move_tracking as T  # noqa: E402

UTC = timezone.utc


def bar(d, o, c):
    return {"date": d, "market_time": "r", "open": o, "high": max(o, c), "low": min(o, c), "close": c, "volume": 1000}


ROWS = [bar("2026-11-02", 99, 100), bar("2026-11-03", 104, 108), bar("2026-11-04", 108, 90)]


def test_amc_uses_next_session_and_refuses_before_close_plus_delay():
    early = datetime(2026, 11, 3, 21, 10, tzinfo=UTC)            # 16:10 ET: closed, but the safety delay has not passed
    assert O.compute("AAPL", "2026-11-02", "AMC", ROWS, early)["status"] == "PENDING_SESSION_NOT_CLOSED"
    ok = O.compute("AAPL", "2026-11-02", "AMC", ROWS, datetime(2026, 11, 3, 21, 31, tzinfo=UTC))
    assert ok["status"] == "GRADED" and ok["reaction_session"] == "2026-11-03" and ok["base_session"] == "2026-11-02"
    assert abs(ok["move_pct"] - 8.0) < 1e-6 and ok["reaction_open"] == 104 and ok["reaction_close"] == 108 and abs(ok["gap_pct"] - 4.0) < 1e-6


def test_bmo_uses_event_day_and_prior_close():
    r = O.compute("ON", "2026-11-03", "BMO", ROWS, datetime(2026, 11, 3, 22, 0, tzinfo=UTC))
    assert r["status"] == "GRADED" and r["base_session"] == "2026-11-02" and abs(r["move_pct"] - 8.0) < 1e-6


def test_missing_bars_and_extended_hours_rows_never_grade():
    now = datetime(2026, 11, 5, 22, tzinfo=UTC)
    assert O.compute("X", "2026-11-02", "AMC", ROWS[:1], now)["status"] == "MISSING_BARS"
    pre = [dict(r, market_time="pr") for r in ROWS]
    assert O.compute("X", "2026-11-02", "AMC", pre, now)["status"] == "MISSING_BARS"


def test_record_is_append_only_with_correction_versions(tmp_path):
    now = datetime(2026, 11, 5, 22, tzinfo=UTC)
    a = O.compute("AAPL", "2026-11-02", "AMC", ROWS, now)
    assert O.record(tmp_path, a)["result"] == "recorded" and O.record(tmp_path, a)["result"] == "duplicate_same"
    fixed = O.compute("AAPL", "2026-11-02", "AMC", [bar("2026-11-02", 99, 100), bar("2026-11-03", 104, 110)], now)
    assert O.record(tmp_path, fixed) == {"result": "correction", "version": 2}
    assert len((tmp_path / O.FILE).read_text().splitlines()) == 2 and O.latest(tmp_path)[0]["version"] == 2
    assert O.record(tmp_path, O.compute("Z", "2026-11-02", "AMC", ROWS, datetime(2026, 11, 3, 21, 10, tzinfo=UTC)))["result"] == "not_graded"


def test_fetch_plan_is_budget_capped_skips_done_and_unknown_timing(tmp_path):
    now = datetime(2026, 11, 3, 22, tzinfo=UTC)
    O.record(tmp_path, O.compute("DONE", "2026-11-02", "AMC", ROWS, now))
    ev = [{"symbol": s, "earnings_date": "2026-11-02", "kind": "AMC"} for s in ("DONE", "A", "B", "C")] + [{"symbol": "LATER", "earnings_date": "2026-11-04", "kind": "AMC"}, {"symbol": "UNK", "earnings_date": "2026-11-02", "kind": None}]
    p = O.plan_fetches(ev, tmp_path, now, max_requests=2)
    assert p["fetch"] == ["A", "B"] and p["deferred_by_budget"] == ["C"] and p["not_due_yet"] == ["LATER"]


def test_graded_outcome_feeds_tracking_and_simulation_stays_separate(tmp_path):
    now = datetime(2026, 11, 5, 22, tzinfo=UTC)
    o = O.compute("AAPL", "2026-11-02", "AMC", ROWS, now, mode="SIMULATION")
    res = T.import_outcomes(tmp_path, [{"symbol": o["symbol"], "earnings_date": o["earnings_date"], "move_pct": o["move_pct"], "source": "test"}], mode="SIMULATION", recorded_at=now)
    assert res == {"imported": 1} and T.load_outcomes(tmp_path)[0]["mode"] == "SIMULATION"
