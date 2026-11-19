"""The redesigned IV Crush Check page shows the two readings it has, and nothing it does not.

A scan row holds the ATM call and put as priced TEN TRADING SESSIONS AGO and as priced NOW - each just
{rate, iv} - at ONE locked strike, plus the report date and the ATM's liquidity. The Figma frame asks
for considerably more, and each of these is therefore absent rather than approximated:

  * a 30-day implied-volatility trend line (we hold two points, ten sessions apart);
  * an IV-crush history over the past four reports, and a post-earnings IV figure (the scan looks only
    forward to the NEXT report, so no post-event reading exists);
  * a five-strike chain comparison with bid/ask/last (one strike is locked on purpose, so the two
    readings are comparable, and a leg carries no quote beyond rate and IV).

The locked strike is the point of the page: comparing today's ATM with the ATM of ten sessions ago would
compare two different contracts and call the difference "IV change".
"""
from __future__ import annotations

import pathlib
import re

import pytest

PAGE = (pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages"
        / "NewIvCrushPage.tsx")


def _code() -> str:
    s = PAGE.read_text(encoding="utf-8")
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"//[^\n]*", "", s)
    return s


def test_the_page_exists_and_reads_the_scan():
    assert PAGE.exists()
    assert "api2.tabBScan(" in _code()


@pytest.mark.parametrize("absent", ["Implied Volatility Trend", "IV Crush History",
                                    "Past 4 Earnings", "IV Crush (Post)", "Strike IV Table"])
def test_nothing_is_drawn_that_needs_data_we_do_not_have(absent):
    assert absent not in _code(), f"{absent!r} is drawn but nothing can fill it"


@pytest.mark.parametrize("field", ["bid", "ask", "last"])
def test_no_quote_field_a_leg_does_not_carry(field):
    """A leg is {rate, iv}. Reading a bid or an ask would render undefined forever."""
    code = _code()
    assert f"call_now?.{field}" not in code and f"put_now?.{field}" not in code


def test_iv_change_is_the_difference_of_two_measured_readings():
    code = _code()
    fn = code.split("function ivChange(")[1].split("\n}")[0]
    assert "call_now" in fn and "call_then" in fn, "the change must come from both readings"
    assert "null" in fn, "a missing reading must yield nothing, not a zero change"


def test_an_unpriced_row_never_sorts_as_a_zero_change():
    """A row still pricing is not a row with no IV movement."""
    code = _code()
    blk = code.split("if (order === 'ivchange')")[1].split("} else")[0]
    assert "return 1" in blk and "return -1" in blk, "unpriced rows must sort last explicitly"


def test_the_default_order_is_still_the_report_date():
    """The page this replaces had no ranking by design; ranking is offered, not imposed."""
    code = _code()
    assert "useState<'date' | 'ivchange'>('date')" in code, \
        "the default order must remain the report date"


def test_the_locked_strike_is_explained():
    """Without saying the strike is locked, a reader cannot know the two prices are comparable."""
    src = PAGE.read_text(encoding="utf-8")
    assert "locked" in src.lower(), "the page must say the strike is held fixed"
