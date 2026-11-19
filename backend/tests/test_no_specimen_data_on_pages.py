"""No page may invent a number and show it as this server's own.

The Analysis landing page seeded its panels with a specimen set so they "always rendered populated like
the design": NVDA at $184.72, the S&P at 5682.14, invented headlines and an invented sparkline shape.
Top Movers had no real source at all - it sorted six fixed rows and presented them as the market. A
reader cannot tell a specimen price from a measured one, so every one of those was a false statement
about the product, shipped.

These tests pin the fix. They read the SOURCE, because the defect is the presence of the literals, and
they are deliberately specific: a test that merely greps for "demo" would pass the moment someone
renamed the constant.
"""
from __future__ import annotations

import pathlib
import re

import pytest

FRONT = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"
INSIGHTS = FRONT / "pages" / "InsightsPage.tsx"


def _src() -> str:
    return INSIGHTS.read_text(encoding="utf-8")


def _code_only(text: str) -> str:
    """The source with comments stripped - a comment explaining the old values is not the old values."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return text


@pytest.mark.parametrize("name", ["DEMO_MOVERS", "DEMO_INDICES", "DEMO_NEWS", "DEMO_EARN",
                                  "SPARK_UP", "SPARK_DN"])
def test_the_specimen_constants_are_gone(name):
    assert name not in _code_only(_src()), (
        f"{name} is back: the Analysis page would show invented figures as its own data"
    )


@pytest.mark.parametrize("literal", ["184.72", "5682.14", "17918.32", "42330.45", "2215.83"])
def test_no_hard_coded_prices(literal):
    """The specimen prices themselves, in case they are reintroduced under another name."""
    assert literal not in _code_only(_src()), f"hard-coded price {literal} is back on the Analysis page"


def test_movers_come_from_the_provider():
    """The panel must read the real endpoint, not a literal list."""
    code = _code_only(_src())
    assert "api2.movers(" in code, "Top Movers must be fetched, not invented"
    block = code.split("const movers = useMemo(")[1].split("}, [")[0]
    assert "moversApi.data" in block, "the movers panel must sort the FETCHED rows"


def test_every_panel_can_say_it_has_nothing():
    """A panel with no data states that, rather than rendering blank or falling back to a specimen."""
    code = _code_only(_src())
    assert "function PanelNote(" in code, "the empty-state row must exist"
    # one guard per panel: movers, indices, headlines, earnings
    assert code.count("<PanelNote") >= 4, (
        "each of the four panels needs its own empty state; found %d" % code.count("<PanelNote")
    )


def test_the_data_source_count_is_the_real_one():
    """Four providers: Unusual Whales, Finnhub, Finviz, SEC/EDGAR. The page claimed twelve."""
    code = _code_only(_src())
    assert "<b>12</b><span>Data Sources</span>" not in code, "the false count of 12 is back"
    assert "<b>4</b><span>Data Sources</span>" in code, "the real count must be stated"


def test_the_parameter_count_is_admin_only():
    """B.1: how many parameters a model scores is a model internal, not a landing-page claim."""
    code = _code_only(_src())
    if "Scored Parameters" in code:
        block = code.split("Scored Parameters")[0][-400:]
        assert "ctx.isAdmin" in block, "the parameter count must be gated to an administrator"


def test_a_missing_series_draws_nothing():
    """sparkFor must return nothing without real points - never a stand-in shape."""
    code = _code_only(_src())
    fn = code.split("const sparkFor =")[1].split(";")[0]
    assert "null" in fn, "sparkFor must yield null when there is no real series"
    assert "SPARK_" not in fn, "sparkFor must not fall back to a fixed shape"


# ---------------------------------------------------------------- the hero's four cards
HERO = FRONT / "components" / "HeroStatCards.tsx"


def test_the_hero_cards_exist_as_elements():
    """They were painted into the artwork, where no code change could correct them."""
    assert HERO.exists(), "the hero cards must be real elements, not pixels"
    src = HERO.read_text(encoding="utf-8")
    assert "<HeroStatCards" in _src(), "the Analysis page must render them"


@pytest.mark.parametrize("literal", ["2.4B", "$2.4", "+42%", "Unusual Activity &"])
def test_the_painted_figures_are_not_reintroduced_as_text(literal):
    assert literal not in _code_only(HERO.read_text(encoding="utf-8")), (
        f"the artwork's specimen figure {literal} is back"
    )


def test_every_hero_card_reads_a_real_source():
    """Four cards, four measurements: options premium, the week's earnings, sentiment, the clock."""
    code = _code_only(HERO.read_text(encoding="utf-8"))
    for needle in ("api2.flowMarketSummary", "api2.marketPulse",
                   "earnings_count", "is_open"):
        assert needle in code, f"the hero cards must read {needle}"


def test_a_hero_card_with_no_data_says_so():
    """An em dash and a reason - never a figure that was not measured."""
    code = _code_only(HERO.read_text(encoding="utf-8"))
    assert code.count("'—'") >= 3, "each card needs an explicit no-data value"
    assert "No reading right now" in code
