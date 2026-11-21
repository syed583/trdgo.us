"""The redesigned Strategy Trades page: agent names only, and no column we cannot fill.

Two rules meet on this page.

A.26 (owner decision 2026-11-14): a non-administrator is never told which model a strategy is. The SERVER
maps it - `strategy_label` already arrives as "Agent 2" - so the page must render what it is given and
must never carry its own mapping. A lookup table in the frontend is how that decision gets undone by
someone who means well.

"Real data only": the Figma frame has Setup (Intraday/Swing/Position), a second target, a Trend column and
filters for Market, Sector and Signal. The blotter has none of them. They are absent from the page rather
than filled with something plausible.
"""
from __future__ import annotations

import pathlib
import re

import pytest

PAGE = (pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages"
        / "NewStrategyTradesPage.tsx")


def _code() -> str:
    """Source with comments stripped: a comment naming the models is documentation, not a mapping."""
    s = PAGE.read_text(encoding="utf-8")
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"//[^\n]*", "", s)
    return s


def test_the_page_exists_and_reads_the_blotter():
    assert PAGE.exists()
    assert "api2.strategyTrades(" in _code(), "the page must read /api/strategy-trades"


@pytest.mark.parametrize("model", ["grok", "meta", "kimi", "gemini", "claude", "chatgpt", "Grok",
                                   "Meta", "Kimi", "Gemini", "Claude", "ChatGPT"])
def test_no_model_name_anywhere_in_the_page(model):
    """A.26 / B.1: the page may not name a model, in a map, a label or a fallback."""
    assert model not in _code(), f"the page names {model!r}; the server maps strategies to Agent 1-4"


def test_the_label_comes_from_the_server():
    code = _code()
    assert "strategy_label" in code, "the page must render the server's label"
    # and must not build its own from a key
    assert "AGENT_" not in code and "agentFor" not in code, \
        "the page must not derive an agent name itself"


@pytest.mark.parametrize("absent", ["Swing", "Position", "Target 2", "All Sectors", "All Signals",
                                    "Setup"])
def test_columns_we_cannot_fill_are_not_drawn(absent):
    """The frame asks for these; the blotter cannot supply them, so no COLUMN is drawn for them.

    The check is on the table headers, not on the whole file. It used to be "this string appears
    nowhere", which failed the moment the page added a sentence NAMING the missing columns to
    explain their absence - the opposite of drawing them, and exactly what the owner asked for
    (2026-11-20: "leave out what has no data, and say so").
    """
    code = _code()
    headers = re.findall(r"<th[^>]*>(.*?)</th>", code, re.S)
    for h in headers:
        assert absent not in h, f"{absent!r} is drawn as a column but nothing fills it"


def test_reward_to_risk_is_computed_from_the_trade_levels():
    """R:R is the only derived figure and must be arithmetic, not an assumption."""
    code = _code()
    fn = code.split("function rr(")[1].split("\n}")[0]
    assert "entry_price" in fn and "stop" in fn and "target" in fn
    assert "return null" in fn, "a missing level must yield nothing, not a default ratio"


def test_carried_positions_are_stated_and_explained():
    """They belong to the day they were opened, so they are outside the table AND said to be."""
    src = PAGE.read_text(encoding="utf-8")
    assert "carried_open_count" in _code(), "carried positions must be surfaced"
    # the reader is told why they are not in the totals, not left to wonder
    assert "not in" in src and "totals" in src,         "the page must say that carried positions are outside the table and totals"
    # and they must never be concatenated into the rows that feed the table
    code = _code()
    assert "...d.carried_open" not in code and "concat(d.carried_open" not in code,         "carried positions must not be merged into the blotter rows"
