"""
The AI Trade board: ranking, and what it refuses to rank.

The board is the one screen where a label sits next to nine others, which is
exactly where a weak reading is most likely to be read as a strong one. These
tests pin the two rules that stop that: a withheld call never enters a column,
and the columns are sorted by conviction rather than by decision text.
"""

import ai_trade_service as board
import directional_model as dm
from live_market_service import cache


def _row(symbol, decision, score, *, actionable=True, reasons=None):
    return {
        "symbol": symbol,
        "status": "OK",
        "decision": decision,
        "direction_score": score,
        "confidence": 70.0,
        "agreement_pct": 70.0,
        "coverage_pct": 90.0,
        "actionable": actionable,
        "blocked_reasons": reasons or [],
        "reasons": [],
    }


def _fake(monkeypatch, table):
    cache.clear()
    board._ROWS.clear()
    board._CURSOR = 0
    monkeypatch.setattr(board, "UNIVERSE", list(table))
    monkeypatch.setattr(
        board.ds, "get_directional_score",
        lambda symbol, progress=None: table[symbol])


def test_columns_sort_by_distance_from_neutral(monkeypatch):
    _fake(monkeypatch, {
        "AAA": _row("AAA", "BUY", 61.0),
        "BBB": _row("BBB", "STRONG BUY", 78.0),
        "CCC": _row("CCC", "SELL", 39.0),
        "DDD": _row("DDD", "STRONG SELL", 22.0),
    })
    d = board.get_board(refresh=True)

    assert [r["symbol"] for r in d["buyers"]] == ["BBB", "AAA"]
    # Sellers lead with the most bearish, so both columns read strongest-first.
    assert [r["symbol"] for r in d["sellers"]] == ["DDD", "CCC"]
    assert d["buyers"][0]["lean"] == 28.0
    assert d["sellers"][0]["lean"] == -28.0


def test_withheld_call_never_enters_a_column(monkeypatch):
    """
    A name the model declines to call is held out of both columns entirely.
    Ranking it low would put a reading the model does not trust on the same
    scale as ones it does.
    """
    _fake(monkeypatch, {
        "AAA": _row("AAA", "BUY", 70.0),
        "BAD": _row("BAD", dm.NO_TRADE, 88.0,
                    actionable=False,
                    reasons=["the parameters that arrived contradict "
                             "each other"]),
    })
    d = board.get_board(refresh=True)

    assert [r["symbol"] for r in d["buyers"]] == ["AAA"]
    assert [r["symbol"] for r in d["sellers"]] == []
    assert [r["symbol"] for r in d["held"]] == ["BAD"]
    assert d["held"][0]["blocked_reasons"]


def test_unactionable_buy_is_held_even_without_the_no_trade_label(monkeypatch):
    """The gate is `actionable`, not the wording of the decision."""
    _fake(monkeypatch, {
        "AAA": _row("AAA", "STRONG BUY", 80.0, actionable=False),
    })
    d = board.get_board(refresh=True)

    assert d["buyers"] == []
    assert [r["symbol"] for r in d["held"]] == ["AAA"]


def test_board_reports_what_it_could_not_score(monkeypatch):
    cache.clear()
    monkeypatch.setattr(board, "UNIVERSE", ["AAA", "GONE"])

    def score(symbol, progress=None):
        if symbol == "GONE":
            return {"status": "NO_DATA"}
        return _row("AAA", "BUY", 65.0)

    monkeypatch.setattr(board.ds, "get_directional_score", score)
    d = board.get_board(refresh=True)

    # Scored counts the names that answered, not the names attempted -- a
    # board that reported 2 of 2 here would be claiming to have read a market
    # it only half saw.
    assert d["universe"] == 2
    assert d["scored"] == 1
    assert [r["symbol"] for r in d["buyers"]] == ["AAA"]


def test_asking_for_the_board_never_rescans(monkeypatch):
    """
    Composing the ranking must not touch a provider. The page polls this every
    minute; if a poll could start a market-wide scan, polling would be the
    most expensive thing the app does.
    """
    calls = {"n": 0}

    def score(symbol, progress=None):
        calls["n"] += 1
        return _row(symbol, "BUY", 62.0)

    cache.clear()
    monkeypatch.setattr(board, "UNIVERSE", ["AAA"])
    monkeypatch.setattr(board.ds, "get_directional_score", score)

    board.get_board(refresh=True)
    first = calls["n"]
    board.get_board()
    board.get_board()
    assert calls["n"] == first, "a plain read must not rescore anything"


def test_coverage_accumulates_across_passes(monkeypatch):
    """
    A pass cannot reach the whole universe inside its budget. The next one
    must start where it stopped, and scores already taken must survive, or the
    tail of the universe is never scored at all.
    """
    cache.clear()
    board._ROWS.clear()
    board._CURSOR = 0
    monkeypatch.setattr(board, "UNIVERSE", ["AAA", "BBB", "CCC", "DDD"])
    monkeypatch.setattr(
        board.ds, "get_directional_score",
        lambda symbol, progress=None: _row(symbol, "BUY", 60.0))

    # A budget that only ever allows two names per pass.
    real = board._score_one
    seen: list[str] = []

    def counted(symbol):
        seen.append(symbol)
        return real(symbol)

    monkeypatch.setattr(board, "_score_one", counted)
    monkeypatch.setattr(board, "WORKERS", 1)
    monkeypatch.setattr(board, "BOARD_BUDGET", 0.0)

    first = board.get_board(refresh=True)
    second = board.get_board(refresh=True)

    # Whatever each pass managed, the second must not have thrown away the
    # first pass's work.
    assert second["scored"] >= first["scored"]
    assert board._CURSOR != 0 or second["scored"] == len(board.UNIVERSE)


def test_stale_scores_expire(monkeypatch):
    cache.clear()
    board._ROWS.clear()
    monkeypatch.setattr(board, "ROW_TTL", -1.0)
    board._remember(_row("AAA", "BUY", 60.0))
    assert board._fresh_rows() == []


def test_second_caller_is_told_a_build_is_running(monkeypatch):
    """A request arriving mid-scan must not start a second scan."""
    cache.clear()
    board._ROWS.clear()
    board._BUILDING.acquire()
    try:
        d = board.get_board()
        assert d["status"] in ("BUILDING", "STALE")
        assert d["building"] is True
    finally:
        board._BUILDING.release()


def test_board_reports_how_old_its_scores_are(monkeypatch):
    """
    A page polling every minute re-fetches every minute; that does not make
    the scores a minute old. The payload has to say which it is.
    """
    _fake(monkeypatch, {"AAA": _row("AAA", "BUY", 61.0)})
    board.get_board(refresh=True)
    d = board.get_board()

    assert d["youngest_seconds"] is not None
    assert d["oldest_seconds"] is not None
    assert d["oldest_seconds"] >= d["youngest_seconds"]
    assert d["buyers"][0]["age_seconds"] is not None


def test_a_read_during_a_scan_still_ranks_what_is_known(monkeypatch):
    """
    Mid-scan the board must show the scores it already has, flagged as still
    building -- not an empty page, and not a second scan.
    """
    _fake(monkeypatch, {"AAA": _row("AAA", "BUY", 61.0)})
    board.get_board(refresh=True)

    board._BUILDING.acquire()
    try:
        d = board.get_board()
        assert d["status"] == "OK"
        assert d["building"] is True
        assert [r["symbol"] for r in d["buyers"]] == ["AAA"]
    finally:
        board._BUILDING.release()


# --- background work must not compete with the screen ----------------------

def test_scan_stands_aside_while_a_request_is_in_flight(monkeypatch):
    """
    One TWS connection serves both the scan and every page. If the scan does
    not give way, opening a screen means queueing behind a market-wide scan.
    """
    import foreground

    _fake(monkeypatch, {s: _row(s, "BUY", 60.0) for s in ("AAA", "BBB")})

    waited: list[float] = []
    real_yield = foreground.yield_to_foreground
    monkeypatch.setattr(
        board.foreground, "yield_to_foreground",
        lambda *a, **k: waited.append(real_yield(0.3)) or 0.0)

    board.get_board(refresh=True)
    assert waited, "the scan must check whether anyone is waiting"


def test_a_request_makes_the_scanner_wait():
    import foreground

    assert not foreground.busy()
    with foreground.request():
        assert foreground.busy()
        assert foreground.inflight() == 1
        # Bounded: a hung request must not stop background work for ever.
        assert foreground.yield_to_foreground(max_wait=0.2) >= 0.2


def test_board_polls_do_not_count_as_someone_waiting():
    """
    Polling the board is the page asking what the scan found. Treating that as
    foreground would pause the very scan the poll is asking about.
    """
    import foreground

    assert "/api/ai-trade/board".startswith(foreground.IGNORE_PREFIXES)


def test_scanner_slows_down_when_the_market_is_shut(monkeypatch):
    monkeypatch.setattr(board, "market_clock", lambda: {"session": "CLOSED"})
    assert board.next_pause() == board.CLOSED_PAUSE

    monkeypatch.setattr(board, "market_clock", lambda: {"session": "OPEN"})
    assert board.next_pause() == board.SCAN_PAUSE


# --- the board survives a restart ------------------------------------------

def test_scores_are_saved_and_restored(monkeypatch, tmp_path):
    """
    The scores used to live only in memory, so restarting the server emptied
    the board and the page showed nothing for the ten minutes the rolling
    scan took to refill it.
    """
    from pathlib import Path

    monkeypatch.setattr(board, "_STATE", Path(tmp_path) / "rows.json")
    # The universe has to be declared: a saved row for a name the board does
    # not score is not restored, which is what keeps a stale file from putting
    # an unknown ticker on the live board.
    monkeypatch.setattr(board, "UNIVERSE", ["AAA", "BBB"])
    board._ROWS.clear()
    board._remember(_row("AAA", "BUY", 63.0))
    board._remember(_row("BBB", "SELL", 38.0))
    board._save_rows()

    board._ROWS.clear()
    assert board._fresh_rows() == []

    board._load_rows()
    got = {r["symbol"] for r in board._fresh_rows()}
    assert got == {"AAA", "BBB"}

    d = board._compose()
    assert [r["symbol"] for r in d["buyers"]] == ["AAA"]
    assert [r["symbol"] for r in d["sellers"]] == ["BBB"]


def test_a_restart_does_not_resurrect_a_stale_score(monkeypatch, tmp_path):
    """A restart is not a reason to show a reading the running app would
    already have dropped."""
    from pathlib import Path

    monkeypatch.setattr(board, "_STATE", Path(tmp_path) / "rows.json")
    monkeypatch.setattr(board, "UNIVERSE", ["AAA"])
    board._ROWS.clear()
    board._remember(_row("AAA", "BUY", 63.0))
    board._save_rows()
    board._ROWS.clear()

    monkeypatch.setattr(board, "ROW_TTL", -1.0)
    board._load_rows()
    assert board._fresh_rows() == []


def test_a_corrupt_state_file_does_not_stop_the_board(monkeypatch, tmp_path):
    from pathlib import Path

    state = Path(tmp_path) / "rows.json"
    state.write_text("{not json at all", encoding="utf-8")
    monkeypatch.setattr(board, "_STATE", state)
    board._ROWS.clear()

    board._load_rows()                      # must not raise
    assert board._fresh_rows() == []


# --- the two screens must not disagree about the same name ------------------

def test_a_finished_analysis_updates_the_board(monkeypatch, tmp_path):
    """
    Running an analysis is the freshest read there is. If the board keeps its
    older number, the row and the screen the reader just watched show two
    different confidences for one stock -- both honest, and indistinguishable
    from a bug.
    """
    from pathlib import Path

    monkeypatch.setattr(board, "_STATE", Path(tmp_path) / "rows.json")
    monkeypatch.setattr(board, "UNIVERSE", ["AAPL"])
    board._ROWS.clear()

    board._remember(_row("AAPL", "BUY", 61.0))
    assert board._compose()["buyers"][0]["confidence"] == 70.0

    fresh = _row("AAPL", "STRONG BUY", 78.0)
    fresh["confidence"] = 84.0
    board.record_score("AAPL", fresh)

    row = board._compose()["buyers"][0]
    assert row["confidence"] == 84.0
    assert row["direction_score"] == 78.0
    assert row["decision"] == "STRONG BUY"


def test_a_run_of_something_off_the_board_is_ignored(monkeypatch, tmp_path):
    """The board scores a fixed universe. Analysing anything else must not
    add a row to it."""
    from pathlib import Path

    monkeypatch.setattr(board, "_STATE", Path(tmp_path) / "rows.json")
    monkeypatch.setattr(board, "UNIVERSE", ["AAPL"])
    board._ROWS.clear()

    board.record_score("VRT", _row("VRT", "BUY", 66.0))
    assert board._fresh_rows() == []


def test_tests_never_write_the_real_state_file():
    """
    A scan with a fixture universe wrote "AAA" and "BBB" into the file the
    running app reloads, and they appeared on the live board as buy-rated
    names. Invented tickers shown exactly like real ones.
    """
    import os

    assert os.environ.get("PYTEST_CURRENT_TEST"), "this runs under pytest"
    assert board._STATE == board._DEFAULT_STATE

    before = board._DEFAULT_STATE.read_bytes() if board._DEFAULT_STATE.is_file() else None
    board._ROWS.clear()
    board._remember(_row("AAA", "BUY", 60.0))
    board._save_rows()
    after = board._DEFAULT_STATE.read_bytes() if board._DEFAULT_STATE.is_file() else None

    assert before == after, "the live board must be untouched by a test"


def test_a_saved_row_outside_the_universe_is_not_restored(monkeypatch, tmp_path):
    """A file written by an older build, or by anything else, must not put a
    ticker on the board that this app does not actually score."""
    from pathlib import Path

    state = Path(tmp_path) / "rows.json"
    monkeypatch.setattr(board, "_STATE", state)
    monkeypatch.setattr(board, "UNIVERSE", ["AAA", "BBB"])
    board._ROWS.clear()
    board._remember(_row("AAA", "BUY", 63.0))
    board._remember(_row("ZZZ", "BUY", 63.0))
    board._save_rows()

    board._ROWS.clear()
    monkeypatch.setattr(board, "UNIVERSE", ["AAA"])
    board._load_rows()
    assert {r["symbol"] for r in board._fresh_rows()} == {"AAA"}
