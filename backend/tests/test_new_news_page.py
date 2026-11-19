"""The redesigned News & Sentiment page does not depend on a leaked vendor name.

A THIRD UNWRAPPED ROUTE. /api/sentiment/{symbol} returns `source: "Claude · headline read"` straight from
uw_news_adapter, with no model_privacy wrap. `source` IS one of model_privacy's ATTRIBUTION_KEYS and
`claude` IS in its EXTRA_MODELS, so scrub_text would remove it - the machinery exists and simply is not
applied on this route.

The page this replaces branched on `sent.source.includes('Claude')`, which both relied on the leak and
kept it alive: fixing the server would have silently removed a hint from the UI. This page describes the
method in its own words, so the server can be fixed without touching the frontend.

SEPARATELY: the frame counts "positive terms" and "negative terms". The payload counts HEADLINES by
tone. Labelling a headline count as a term count would misdescribe what was measured.
"""
from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend" / "src" / "pages" / "NewNewsPage.tsx"
ROUTES = ROOT / "backend" / "api_routes.py"


def _code() -> str:
    s = PAGE.read_text(encoding="utf-8")
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"//[^\n]*", "", s)
    return s


def test_the_page_exists_and_reads_the_three_feeds():
    assert PAGE.exists()
    code = _code()
    for call in ("api2.news(", "api2.sentiment(", "api2.earningsAlerts("):
        assert call in code, f"the page must read {call}"


@pytest.mark.parametrize("vendor", ["claude", "chatgpt", "grok", "gemini", "kimi"])
def test_no_vendor_name_in_the_page(vendor):
    assert vendor not in _code().lower(), f"{vendor!r} appears in the page"


def test_the_page_never_reads_the_attribution_string():
    """Branching on `source` is what kept the leak alive in the page it replaces."""
    code = _code()
    assert "s.source" not in code and "sent.source" not in code, \
        "the page must not depend on the sentiment payload's attribution field"


def test_the_method_is_described_in_our_own_words():
    src = PAGE.read_text(encoding="utf-8")
    assert "headline text alone" in src, \
        "the page should say how the score is produced without quoting the payload"


def test_tone_counts_are_called_headlines_not_terms():
    """We count headlines by tone; the frame counts terms. They are different measurements."""
    src = PAGE.read_text(encoding="utf-8")
    assert "Positive headlines" in src and "Negative headlines" in src
    assert "Positive terms" not in src and "Negative terms" not in src, \
        "a headline count must not be labelled a term count"


def test_an_unscored_headline_is_its_own_bucket():
    """'No score' is not 'neutral' - the page must keep them apart."""
    code = _code()
    assert "'none'" in code, "an unscored headline needs its own tone"
    fn = code.split("const TONE_OF")[1].split("};")[0]
    assert "return 'none'" in fn, "an unrecognised tone must not fall back to neutral"


def test_a_headline_without_a_link_is_not_a_dead_link():
    code = _code()
    assert "e.preventDefault()" in code, \
        "a headline with no url must not look clickable and do nothing"


def test_the_route_is_wrapped(self_check=None):
    """Kit L closed this. The page's workaround can now be relaxed whenever we choose to.

    This replaces a reminder that failed on purpose while the route was open; keeping it as a real
    assertion means the leak cannot quietly come back.
    """
    src = ROUTES.read_text(encoding="utf-8")
    route = src.split('@router.get("/sentiment/{symbol}")')[1].split("@router.")[0]
    assert "_privacy.public_payload" in route, "the route must not return the payload unwrapped"
    assert "_is_admin(request)" in route, "the decision must depend on the caller"
