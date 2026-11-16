"""B.2a: the chunk every visitor downloads must not name a vendor, a model, or the model's shape.

A.29 and B.1 stop the SERVER sending the model's working. Neither can reach a string compiled into the
client: before B.2a the main bundle carried "Unusual Whales" (from demo fixture data, of all things),
"Grok", "Kimi", "Gemini", "ChatGPT", page titles reading "11 parameters · 100 pts", and one subtitle that
printed the entire weight table - "options flow 18, unusual activity 14, volume/PCR 12, ...".

This test reads the BUILT bundle, so it fails on what is actually shipped rather than on what the source
looks like. Run `npm run build` first; it skips rather than lying if there is no build to read.
"""
import re
from pathlib import Path

import pytest

DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist" / "assets"

# Words that must not reach the chunk a visitor downloads before signing in.
BANNED = (
    "Unusual Whales", "unusualwhales", "Finnhub", "Finviz", "Benzinga", "Marketaux", "mtapi",
    "Grok", "Kimi", "Gemini", "ChatGPT",
    "100 pts", "options flow 18",
)

# Known and accepted, with the reason. Shrinking this list is B.2b's job.
ALLOWED = {
    "points_label": "an API field name; renaming it is a contract change, scheduled as B.2b",
    "options_flow": "an API field key in a label map; same contract change, B.2b",
    "Claude": "appears only in a lazily-loaded chunk's filename, not in the main chunk's content",
}


def main_chunk() -> Path:
    if not DIST.is_dir():
        pytest.skip("no built bundle: run `npm run build` in frontend/ first")
    chunks = sorted(DIST.glob("index-*.js"))
    if not chunks:
        pytest.skip("no index-*.js in the built bundle")
    return max(chunks, key=lambda p: p.stat().st_size)


@pytest.mark.parametrize("word", BANNED)
def test_the_main_bundle_does_not_contain(word):
    text = main_chunk().read_text(encoding="utf-8", errors="replace")
    assert word not in text, (
        "%r is in the chunk every visitor downloads. No server-side rule can take it back: "
        "remove it from the frontend source." % word
    )


def test_no_page_title_counts_the_models_parameters():
    """'11 parameters · 100 pts' gives the model's shape away even without naming it."""
    text = main_chunk().read_text(encoding="utf-8", errors="replace")
    hits = re.findall(r"\d+\s*parameters?\s*[·|]\s*\d+\s*pts", text)
    assert not hits, "a page title still counts the model's parameters: %s" % hits[:3]


def test_the_allowed_list_is_documented_not_just_long():
    """Every exception must carry its reason, so nobody quietly widens it."""
    for word, why in ALLOWED.items():
        assert why and len(why) > 20, "%s needs a real reason, not a placeholder" % word
