"""The redesigned Unusual Activity page draws nothing it cannot fill.

The Figma frame has two elements with no data behind them, and one with data the server withholds:

1. A "Call/Put Premium Ratio (Universe)" line chart over time. The scan returns each name's CURRENT
   ratio against its own 20-day normal - one point per symbol, no universe series. A chart drawn from
   that would be invented.
2. A "NORMAL 66" counter. Part B returns ONLY names that lean (call-heavy or put-heavy); the rest are
   filtered server-side and never arrive, so their count cannot be known.
3. The exact scan thresholds (3x the 20-day average, volume >= open interest, premium >= $100K, 60% at
   the ask). Those are tab_settings values, administrator-only since A.25, and the payload does not
   carry them - so hard-coding them on the page would both leak a setting and go stale the moment it
   changed.
"""
from __future__ import annotations

import pathlib
import re

import pytest

PAGE = (pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages"
        / "NewUnusualActivityPage.tsx")


def _code() -> str:
    s = PAGE.read_text(encoding="utf-8")
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"//[^\n]*", "", s)
    return s


def test_the_page_exists_and_reads_the_scan():
    assert PAGE.exists()
    assert "api2.unusualActivityScan(" in _code()


@pytest.mark.parametrize("threshold", ["100K", "$100,000", "100000", "3x the", "60% at the ask",
                                       "UA_MIN_PREMIUM", "UA_VOL_AVG_MULT", "UA_CALL_HEAVY"])
def test_no_scan_threshold_is_hard_coded(threshold):
    assert threshold not in _code(), f"the scan threshold {threshold!r} is printed on the page"


def test_there_is_no_universe_ratio_chart():
    """One cached point per symbol is not a time series."""
    code = _code()
    assert "Premium Ratio (Universe)" not in code
    # no chart component is imported for this page
    assert "Sparkline" not in code and "PriceChart" not in code, \
        "nothing on this page has a series to plot"


def test_there_is_no_normal_count():
    """Part B never returns the names that do not lean, so their number cannot be shown."""
    code = _code()
    assert ">Normal<" not in code and "'NORMAL'" not in code, \
        "a count of 'normal' names would be invented"
    # and the page says why
    assert "no “normal” count" in PAGE.read_text(encoding="utf-8") \
        or 'no "normal" count' in PAGE.read_text(encoding="utf-8"), \
        "the page should explain the absence rather than leave a gap"


def test_counters_are_computed_from_the_rows_that_arrived():
    code = _code()
    for needle in ("a.filter(", "a.reduce(", "b.filter("):
        assert needle in code, f"counters must be derived from the payload ({needle})"


def test_every_counter_has_a_no_data_value():
    """With an empty scan each counter shows an em dash, not a zero that looks measured."""
    code = _code()
    assert code.count("'—'") >= 6, "each counter needs an explicit no-data value"


def test_the_provider_budget_is_administrator_only():
    """refresh{} names our hourly cap and in-flight limit - operator detail, not a user's (B.1)."""
    code = _code()
    assert "d.refresh" in code
    block = code.split("d.refresh")[0]
    assert "isAdmin" in block[-400:], "the scan state must be behind the admin flag"
