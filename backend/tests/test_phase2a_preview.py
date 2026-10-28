"""The read-only production preview reproduces the legacy defects and shows the corrected result (synthetic data
shaped like the production findings: premature After Close stamps, a post-release snapshot that wins the old
scoreboard, calendar-sourced timely outcomes)."""
import importlib.util
import pathlib
import sys
from datetime import date, datetime, timezone

UTC = timezone.utc
TOOL = pathlib.Path(__file__).resolve().parent.parent / "tools" / "phase2a_preview.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("phase2a_preview_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def snap(sym, earn, timing, snapshot, predicted, signal, move=None, correct=None, evaluated=None, model="claude_new"):
    return {"model": model, "symbol": sym, "earnings_date": earn, "report_timing": timing, "snapshot_date": snapshot,
            "predicted_at": predicted, "signal": signal, "score": 60.0, "actual_move_pct": move,
            "actual_direction": None, "correct": correct, "evaluated_at": evaluated}


def cal(sym, earn, reaction, move):
    return {"symbol": sym, "earnings_date": earn, "reporting_time": None, "report_time": None,
            "reaction_date": reaction, "post_earnings_move_percent": move, "eps_actual": 1.0}


def test_preview_shows_before_and_after():
    t = load_tool()
    now = datetime(2026, 11, 5, 15, 0, tzinfo=UTC)
    stamp_bmo = datetime(2026, 10, 29, 4, 30, tzinfo=UTC)       # after the BMO reaction close: fine
    stamp_amc = datetime(2026, 10, 29, 4, 30, tzinfo=UTC)       # BEFORE the AMC reaction close: premature
    snaps = [
        # AAA BMO 28 Oct: genuine BUY on the 26th, then a post-release SELL captured 15:58 ET on the day
        snap("AAA", date(2026, 10, 28), "Before Open", date(2026, 10, 26), datetime(2026, 10, 26, 4, 21, tzinfo=UTC), "BUY",
             move=3.0, correct=1, evaluated=stamp_bmo),
        snap("AAA", date(2026, 10, 28), "Before Open", date(2026, 10, 28), datetime(2026, 10, 28, 19, 58, tzinfo=UTC), "SELL",
             move=3.0, correct=0, evaluated=stamp_bmo),
        # BBB AMC 28 Oct: legacy grader stamped it the next morning with no move
        snap("BBB", date(2026, 10, 28), "After Close", date(2026, 10, 27), datetime(2026, 10, 27, 4, 21, tzinfo=UTC), "BUY",
             evaluated=stamp_amc),
        # CCC AMC Friday 30 Oct: reaction is Monday 2 Nov
        snap("CCC", date(2026, 10, 30), "After Close", date(2026, 10, 29), datetime(2026, 10, 29, 4, 21, tzinfo=UTC), "SELL",
             evaluated=datetime(2026, 10, 31, 4, 30, tzinfo=UTC)),
        # DDD AMC: calendar reaction date does not match the AMC rule -> not accepted
        snap("DDD", date(2026, 10, 28), "After Close", date(2026, 10, 27), datetime(2026, 10, 27, 4, 21, tzinfo=UTC), "BUY",
             evaluated=stamp_amc),
        # EEE future
        snap("EEE", date(2026, 11, 20), "Before Open", date(2026, 11, 4), datetime(2026, 11, 4, 4, 21, tzinfo=UTC), "BUY"),
    ]
    calendar = [cal("AAA", date(2026, 10, 28), "2026-10-28", 3.0), cal("BBB", date(2026, 10, 28), "2026-10-29", 5.0),
                cal("CCC", date(2026, 10, 30), "2026-11-02", -4.0), cal("DDD", date(2026, 10, 28), "2026-10-28", 2.0)]
    res = t.preview(snaps, calendar, now=now)

    m = res["models"]["claude_new"]
    before, a, b = m["BEFORE_legacy"], m["AFTER_A_selection_only"], m["AFTER_B_selection_and_timely_outcomes"]
    # BEFORE: the post-release SELL wins AAA and is graded wrong; three events stuck unverifiable; one pending
    assert (before["graded"], before["unverifiable"], before["pending"]) == (1, 3, 1)
    assert before["SELL"] == [1, 0] and before["BUY"] == [0, 0]
    # AFTER-A: selection fixed -> the genuine BUY is graded correct (still only one outcome available)
    assert a["eligible"] == 5 and a["graded"] == 1 and a["BUY"][:2] == [1, 1] and a["SELL"][:2] == [0, 0]
    # AFTER-B: BBB and CCC become gradable once their reaction sessions are accounted for correctly
    assert b["graded"] == 3 and b["BUY"][:2] == [2, 2] and b["SELL"][:2] == [1, 1]
    assert b["missing_outcome"] == 1 and b["pending"] == 1                       # DDD (rejected calendar row) and EEE
    # stamps: all three After Close events were stamped before their reaction session closed; the BMO one was not
    stamps = res["legacy_stamps_before_reaction_close"]
    assert stamps["After Close"] == {"events_stamped": 3, "stamped_before_reaction_close": 3}
    assert stamps["Before Open"] == {"events_stamped": 1, "stamped_before_reaction_close": 0}
    # cross-check: the one event graded by both agrees
    cc = res["outcome_crosscheck"]
    assert cc["events_with_both"] == 1 and cc["same_direction"] == 1 and cc["within_0.1pct"] == 1
    assert res["calendar_outcome_issues"]["reaction_date_mismatch"] == 1 and res["calendar_outcomes_usable"] == 3


def test_tool_is_read_only_by_construction():
    src = TOOL.read_text(encoding="utf-8")
    code = src.split("def load_embedded", 1)[1]
    for word in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ", "CREATE ", "TRUNCATE", "commit("):
        assert word not in code, word
    assert code.count("SET TRANSACTION READ ONLY") == 1
    assert "import swr" not in code and "db_cache" not in code
