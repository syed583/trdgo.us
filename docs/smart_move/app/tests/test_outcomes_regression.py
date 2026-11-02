"""Outcome collector regression: holiday, early close, exact percentages and FLAT boundaries, corporate-action behaviour (documented gap), duplicates. Fixtures only, no provider."""
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_outcomes as O  # noqa: E402
import smart_move_report as RP  # noqa: E402

UTC = timezone.utc


def bar(d, o, c):
    return {"date": d, "market_time": "r", "open": o, "high": max(o, c), "low": min(o, c), "close": c, "volume": 1000}


def test_amc_before_a_holiday_grades_against_the_session_after_it():
    rows = [bar("2026-11-25", 100, 100), bar("2026-11-27", 101, 104)]
    r = O.compute("H", "2026-11-25", "AMC", rows, datetime(2026, 11, 27, 23, 0, tzinfo=UTC))
    assert r["status"] == "GRADED" and r["base_session"] == "2026-11-25" and r["reaction_session"] == "2026-11-27" and abs(r["move_pct"] - 4.0) < 1e-6


def test_early_close_session_is_gradable_only_after_13_00_et_plus_the_safety_delay():
    rows = [bar("2026-11-25", 100, 100), bar("2026-11-27", 101, 104)]
    assert O.compute("E", "2026-11-25", "AMC", rows, datetime(2026, 11, 27, 18, 10, tzinfo=UTC))["status"] == "PENDING_SESSION_NOT_CLOSED"
    assert O.compute("E", "2026-11-25", "AMC", rows, datetime(2026, 11, 27, 18, 40, tzinfo=UTC))["status"] == "GRADED"


def test_percentage_move_and_flat_boundaries_are_exact():
    now = datetime(2026, 11, 5, 22, tzinfo=UTC)
    for close, want in ((100.5, "FLAT"), (99.5, "FLAT"), (100.51, "PASS"), (99.49, "FAIL")):
        r = O.compute("F", "2026-11-02", "AMC", [bar("2026-11-02", 100, 100), bar("2026-11-03", 100, close)], now)
        assert RP.pass_fail("BUY", r["move_pct"]) == want, (close, r["move_pct"])


def test_a_missing_close_never_grades_and_never_defaults_to_flat():
    now = datetime(2026, 11, 5, 22, tzinfo=UTC)
    r = O.compute("M", "2026-11-02", "AMC", [bar("2026-11-02", 100, 100)], now)
    assert r["status"] == "MISSING_BARS" and r["move_pct"] is None and RP.pass_fail("BUY", r["move_pct"]) is None


def test_known_gap_unadjusted_split_is_graded_as_a_real_move_and_only_over_100_pct_is_rejected():
    """DOCUMENTED GAP, no adjustment rule introduced: a 2-for-1 split in raw closes grades as -50 % (counted as DOWN). Only moves beyond 100 % are marked IMPLAUSIBLE."""
    now = datetime(2026, 11, 5, 22, tzinfo=UTC)
    split = O.compute("S", "2026-11-02", "AMC", [bar("2026-11-02", 100, 100), bar("2026-11-03", 50, 50)], now)
    assert split["status"] == "GRADED" and abs(split["move_pct"] + 50.0) < 1e-6
    reverse = O.compute("R", "2026-11-02", "AMC", [bar("2026-11-02", 10, 10), bar("2026-11-03", 25, 25)], now)
    assert reverse["status"] == "IMPLAUSIBLE" and reverse["move_pct"] is None


def test_duplicate_outcome_is_not_rewritten(tmp_path):
    now = datetime(2026, 11, 5, 22, tzinfo=UTC)
    o = O.compute("D", "2026-11-02", "AMC", [bar("2026-11-02", 100, 100), bar("2026-11-03", 100, 103)], now, mode="SIMULATION")
    assert O.record(tmp_path, o)["result"] == "recorded" and O.record(tmp_path, o)["result"] == "duplicate_same"
    assert len((tmp_path / O.FILE).read_text().splitlines()) == 1
