"""The redesigned Pre-Earnings Direction page must not publish the model.

The Figma frame does two things this page deliberately does not copy:

1. Its "How to read this" states the THRESHOLDS - "BUY >= 65, NEUTRAL 50-64, SELL <= 35". Where a score
   turns into a verdict is a model internal; B.1/A.29 keep points, weights and parameters to an
   administrator. Printing the cut-offs on a landing panel hands the model's calibration to anyone.

2. Its table has a column per contributing factor (Options, Price Action, Insider, Peers, EPS Revisions,
   Historical Reaction, Guidance, Dark Pool). Those are the model's parameters. The server strips them
   for a non-administrator, so the page must not render them at all - a column that is always blank for
   a user still tells them what the model looks at.

PENDING is also pinned here: a name with no score yet is not evidence of a neutral view, so it must be
excluded from the shares and the ring.
"""
from __future__ import annotations

import pathlib
import re

import pytest

PAGE = (pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages"
        / "NewPreEarningsPage.tsx")


def _code() -> str:
    s = PAGE.read_text(encoding="utf-8")
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"//[^\n]*", "", s)
    return s


def test_the_page_exists_and_reads_the_scan():
    assert PAGE.exists()
    assert "api2.preEarningsDirectionScan(" in _code()


@pytest.mark.parametrize("threshold", ["65", "50-64", "50–64", ">= 65", "<= 35"])
def test_the_score_thresholds_are_not_published(threshold):
    """Where a score becomes BUY or SELL is the model's calibration, not page copy."""
    code = _code()
    # the window options (7/10/15/21/30) and the 0-100 range are fine; a cut-off is not
    assert f"BUY {threshold}" not in code and f"SELL {threshold}" not in code, \
        f"a scoring threshold ({threshold}) is printed on the page"


def test_the_how_to_read_panel_names_no_cut_off():
    code = _code()
    if "How to read this" in code:
        panel = code.split("How to read this")[1][:1200]
        assert "≥" not in panel and ">=" not in panel and "<=" not in panel, \
            "the explanation must not state a threshold"


@pytest.mark.parametrize("param", ["Price Act", "Insider", "Peers", "EPS Rev", "Hist React",
                                   "Rev Rev", "Guidance", "Dark Pool"])
def test_no_parameter_column_is_drawn(param):
    """These are the model's inputs; the server strips them for users, so the page never asks."""
    assert param not in _code(), f"parameter column {param!r} is drawn"


def test_params_are_never_read_from_a_row():
    """The row carries a `params` list for an administrator; this page must not surface it."""
    code = _code()
    assert ".params" not in code, "the page must not read a row's parameter list"


def test_pending_is_excluded_from_the_shares():
    """A name without a score is not a neutral view on the stock."""
    code = _code()
    assert "const scored = counts.BUY + counts.SELL + counts.NEUTRAL" in code, \
        "the denominator must be the scored names only"
    assert "counts.PENDING" not in code.split("const scored =")[1].split("\n")[0], \
        "PENDING must not be counted as scored"


def test_an_unscored_name_sorts_last_not_as_zero():
    code = _code()
    blk = code.split("const shown =")[1].split("}, [rows")[0]
    assert "score == null ? -1" in blk, "a missing score must sort last, not as a zero score"
