"""The redesigned Signal History page shows outcomes, and does not pretend they are a track record.

The endpoint returns, per symbol, its real PAST EARNINGS EVENTS - each cell is one previous report and
the move that followed - plus the current call. Two consequences the Figma frame does not respect:

1. The frame's columns are D-5..D-1, the five daily moves BEFORE a report. Ours are past reports. Using
   the frame's headings would mislabel the data outright, so each column is headed by how many reports
   ago it was.

2. The frame's right rail is win rate, signal performance, average return per signal, a win-rate trend
   and top performers. Every one of those needs a past SIGNAL paired with its OUTCOME. These cells are
   outcomes only - what the stock did, not what was called beforehand - so pairing them would mean
   inventing the historical call. That comparison belongs to the Forward Scoreboard, and per-model
   accuracy is administrator-only (B.1a).
"""
from __future__ import annotations

import pathlib
import re

import pytest

PAGE = (pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages"
        / "NewSignalHistoryPage.tsx")


def _code() -> str:
    s = PAGE.read_text(encoding="utf-8")
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"//[^\n]*", "", s)
    return s


def test_the_page_exists_and_reads_the_history():
    assert PAGE.exists()
    assert "api2.earningsSignalHistory(" in _code()


@pytest.mark.parametrize("claim", ["Win Rate", "win rate trend", "Top Performing",
                                   "Signal Performance", "Avg. Return"])
def test_no_track_record_is_claimed(claim):
    """These need past calls paired with outcomes. The payload has outcomes only."""
    code = _code()
    assert claim not in code, f"{claim!r} would need a past call this endpoint never returns"


@pytest.mark.parametrize("header", ["D-5", "D-4", "D-3", "D-2", "D-1"])
def test_the_frames_daily_headings_are_not_used(header):
    """A cell is a past REPORT, not a day before one. Borrowing the heading would mislabel it."""
    assert header not in _code(), f"{header!r} mislabels a past-earnings cell as a daily move"


def test_a_cell_is_described_as_an_outcome():
    src = PAGE.read_text(encoding="utf-8")
    assert "what the stock did" in src, "the page must say the cells are outcomes, not predictions"


def test_a_missing_reaction_is_not_counted_as_flat():
    """An absent move is unknown, not a zero move."""
    code = _code()
    blk = code.split("const allMoves")[1].split("[rows]")[0]
    assert "Number.isFinite" in blk, "non-numeric cells must be filtered out, not coerced"


def test_names_without_a_current_call_are_not_bucketed():
    """`now` can be absent; such a name belongs in no BUY/SELL/NEUTRAL bucket."""
    code = _code()
    blk = code.split("const calls =")[1].split("[rows]")[0]
    assert "if (k in c)" in blk, "only a recognised call may increment a bucket"


def test_the_average_move_is_described_as_size_not_direction():
    """It averages absolute moves; calling it an average return would be wrong."""
    code = _code()
    blk = code.split("const avgAbs")[1].split(";")[0]
    assert "Math.abs" in blk
    assert "size, not direction" in code, "the label must say it is a size"


def test_the_win_rate_box_is_real_and_says_where_it_comes_from():
    """The owner asked (2026-11-19) for the frame's Win Rate box. This endpoint cannot fill it.

    A win rate needs a call recorded BEFORE an event; these cells are outcomes only - what a stock
    did, with nothing predicted beforehand to be right or wrong about. Deriving one from them would
    mean inventing the historical call. So the box is fed from the Forward Scoreboard, which
    measures exactly that, and says so on its face. This fails if anyone derives it from the cells.
    """
    code = _code()
    assert "Win rate" in code, "the owner asked for the frame's Win Rate box; it is missing"
    assert "forwardScoreboard" in code, "the win rate must come from the Forward Scoreboard"
    assert "overall?.cost" in code, "the win rate must read the scoreboard's measured figure"
    assert "recorded calls" in code, "the box must say the figure is over recorded calls"
    # Pin the tile's VALUE expression, not a nearby window: the stats array holds other tiles that
    # legitimately use allMoves, so proximity proved nothing either way.
    assert "value: winRate != null" in code, (
        "the Win Rate tile must render the scoreboard's winRate, not a figure derived here"
    )
