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
    # grading split by report timing: AAA is Before Open; BBB, CCC, DDD are After Close; EEE is not reported yet
    tb = res["grading_by_timing"]
    assert tb["Before Open"]["reported"] == 1 and tb["Before Open"]["graded_with_available_prices"] == 1
    assert tb["After Close"]["reported"] == 3 and tb["After Close"]["graded_with_available_prices"] == 2
    assert tb["After Close"]["missing_price"] == 1 and tb["After Close"]["legacy_stamped_unverifiable"] == 3
    assert res["events_not_reported_yet"] == 1
    # integrity: one prediction per event and model; nothing accepted before its reaction session closed
    ic = res["integrity_checks"]
    assert ic["one_prediction_per_event_model"] is True and ic["no_outcome_accepted_before_reaction_close"] is True
    assert ic["rows_vs_events_after_B"]["claude_new"] == {"before_release_rows": 5, "eligible_events": 5, "post_release_rows": 1,
                                                          "ambiguous_rows": 0, "retrospective_rows": 0}


def test_an_outcome_before_the_reaction_close_is_never_accepted():
    t = load_tool()
    now = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)          # the morning after an After Close report: session not closed
    snaps = [snap("BBB", date(2026, 10, 28), "After Close", date(2026, 10, 27), datetime(2026, 10, 27, 4, 21, tzinfo=UTC), "BUY")]
    res = t.preview(snaps, [cal("BBB", date(2026, 10, 28), "2026-10-29", 5.0)], now=now)
    assert res["calendar_outcomes_usable"] == 0 and res["calendar_outcome_issues"]["not_ready"] == 1
    assert res["grading_by_timing"]["After Close"]["pending_reaction_window_open"] == 1
    assert res["models"]["claude_new"]["AFTER_B_selection_and_timely_outcomes"]["graded"] == 0


def test_tool_is_read_only_by_construction():
    src = TOOL.read_text(encoding="utf-8")
    code = src.split("def load_embedded", 1)[1]
    for word in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ", "CREATE ", "TRUNCATE", "commit("):
        assert word not in code, word
    assert code.count("SET TRANSACTION READ ONLY") == 1
    assert "import swr" not in code and "db_cache" not in code


def test_main_prints_the_full_report_without_touching_a_database(tmp_path, monkeypatch, capsys):
    """main() end to end with a stub database module and synthetic rows (the VPS print path)."""
    import types
    t = load_tool()
    monkeypatch.setitem(sys.modules, "database", types.ModuleType("database"))
    monkeypatch.setenv("APP_ENV", "production")
    now_snaps = [snap("AAA", date(2026, 10, 20), "Before Open", date(2026, 10, 18), datetime(2026, 10, 18, 4, 21, tzinfo=UTC), "BUY",
                      move=3.0, correct=1, evaluated=datetime(2026, 10, 21, 4, 30, tzinfo=UTC)),
                 snap("BBB", date(2026, 10, 20), "After Close", date(2026, 10, 19), datetime(2026, 10, 19, 4, 21, tzinfo=UTC), "SELL",
                      evaluated=datetime(2026, 10, 21, 4, 30, tzinfo=UTC))]
    calendar = [cal("AAA", date(2026, 10, 20), "2026-10-20", 3.0), cal("BBB", date(2026, 10, 20), "2026-10-21", -2.0)]
    calls = []

    def fake_run(sql):
        calls.append(sql.split()[0])
        return now_snaps if "model_predictions" in sql else calendar

    monkeypatch.setattr(t, "_run_ro", fake_run)
    out = tmp_path / "p.json"
    monkeypatch.setattr(sys, "argv", ["phase2a_preview.py", "--out", str(out)])
    t.main()
    text = capsys.readouterr().out
    for needle in ("calendar outcomes usable", "BEFORE (legacy)", "GRADING BY REPORT TIMING", "INTEGRITY:", "Per model (AFTER-B)"):
        assert needle in text, needle
    assert out.exists() and calls == ["SELECT", "SELECT"]            # exactly two SELECTs, nothing else


def test_embedded_database_and_provider_paths_are_switched_off():
    import pytest
    t = load_tool()
    mc, eo, sb = t.load_embedded()
    for fn in ("run", "ensure_table", "load_outcomes", "_save", "sync_legacy_columns", "events_to_review", "default_bars_fetcher"):
        with pytest.raises(RuntimeError, match="disabled in the read-only preview"):
            getattr(eo, fn)()
    for fn in ("scoreboard", "load_rows"):
        with pytest.raises(RuntimeError, match="disabled in the read-only preview"):
            getattr(sb, fn)()
    # the pure functions the preview relies on still work
    assert eo.timing_kind("After Close") == "AMC" and eo.reaction_window(date(2026, 10, 30), "AMC")[1] == date(2026, 11, 2)


def test_preview_computation_runs_with_the_network_blocked():
    import socket
    import pytest
    t = load_tool()
    with t.no_network():
        with pytest.raises(RuntimeError, match="network blocked"):
            socket.create_connection(("127.0.0.1", 9), timeout=1)
        res = t.preview([snap("AAA", date(2026, 10, 20), "Before Open", date(2026, 10, 18),
                              datetime(2026, 10, 18, 4, 21, tzinfo=UTC), "BUY")], [], now=datetime(2026, 11, 5, tzinfo=UTC))
    assert res["models"]["claude_new"]["BEFORE_legacy"]["events"] == 1
