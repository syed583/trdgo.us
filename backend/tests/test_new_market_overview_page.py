"""Market Overview in the redesigned shell, and the two theme faults this page's build uncovered.

THE FRAME IS A RESTYLE, NOT A NEW PAGE. Figma 24:327 is panel-for-panel what /market already renders -
header with stock search and Refresh, five index cards, the explainer, Market Tide, Sector Performance,
Market Heatmap, Market Breadth, Key Market Indicators, Top Gainers, Top Losers, Global Markets, Market
Sentiment, Options Put / Call - down to the footnote wording. So the page renders the LIVE component
and restyles it; it must not grow a copy of thirteen panels of market data.

TEXT ON A HERO PHOTOGRAPH. The photograph is dark in BOTH themes, so text over it cannot use a token
that inverts with the theme. --nu-rail-ink-dim is light in dark mode and dark in light mode, and using
it on the hero rendered the subtitle, the feature captions, the Strategy rulebar and the Analysis basis
line dark-on-dark the moment anyone switched to light. The --nu-on-photo* tokens are declared once,
outside both theme blocks, so a later theme cannot invert them.

THE SIDEBAR BREAKPOINT. Three frames are drawn on a 1024-wide canvas and all three show the full
sidebar; collapsing it at max-width 1024px hid the rail at exactly the width the design shows it.
"""
from __future__ import annotations

import pathlib
import re

SRC = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"
PAGE = SRC / "pages" / "NewMarketOverviewPage.tsx"
SHELL = (SRC / "new-shell.css").read_text(encoding="utf-8")
DASH = (SRC / "new-dashboard.css").read_text(encoding="utf-8")
STRAT = (SRC / "new-strategy.css").read_text(encoding="utf-8")
MARKET_CSS = (SRC / "new-market.css").read_text(encoding="utf-8")


def _code(p: pathlib.Path) -> str:
    s = p.read_text(encoding="utf-8")
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    return re.sub(r"//[^\n]*", "", s)


def test_market_overview_renders_the_live_page_and_reads_nothing_itself():
    code = _code(PAGE)
    assert "<MarketPulsePage ctx={ctx} />" in code
    for banned in ("useApi", "api2.", "fetch(", "<table", ".map("):
        assert banned not in code, f"the redesigned Market Overview builds its own data ({banned})"


def test_market_overview_is_wired_behind_the_flag_and_keeps_the_old_page():
    app = _code(SRC / "App.tsx")
    route = [l for l in app.splitlines() if 'path="/market"' in l]
    assert len(route) == 1, route
    assert "newUi ?" in route[0] and "NewMarketOverviewPage" in route[0] and "MarketPulsePage" in route[0]


def test_market_stylesheet_names_no_literal_colour():
    """Light, dark and mobile must follow the theme, so the restyle uses tokens, not the frame's hex."""
    body = re.sub(r"/\*.*?\*/", "", MARKET_CSS, flags=re.S)
    literals = set(re.findall(r"#[0-9a-fA-F]{3,8}\b", body))
    assert literals <= {"#fff"}, f"hard-coded colours in the Market Overview stylesheet: {sorted(literals)}"


# ----------------------------------------------------------------- on-photo ink
def test_on_photo_tokens_are_declared_once_outside_both_themes():
    assert "--nu-on-photo:" in SHELL and "--nu-on-photo-dim:" in SHELL
    # one declaration block, not one per theme - a per-theme pair is exactly the fault being prevented
    assert len(re.findall(r"--nu-on-photo:\s*#", SHELL)) == 1, "the on-photo ink is declared per theme"


def test_no_hero_text_uses_an_inverting_token():
    """Every rule that paints text over a hero photograph must use the fixed on-photo ink."""
    offenders = []
    for name, css in (("new-dashboard.css", DASH), ("new-strategy.css", STRAT)):
        for rule in re.findall(r"\.app-nu [^{}]*\{[^}]*\}|\.nal [^{}]*\{[^}]*\}", css):
            on_photo_sel = any(k in rule.split("{")[0] for k in
                               ("nd-hero", "nst-hero", "nsm-hero", "nd-eyebrow", "nst-rulebar", "anh-basis", "anh-try"))
            if on_photo_sel and "--nu-rail-ink" in rule:
                offenders.append((name, rule.split("{")[0].strip()))
            if on_photo_sel and "--nu-page-ink" in rule:
                offenders.append((name, rule.split("{")[0].strip()))
    assert not offenders, f"hero text using a token that inverts with the theme: {offenders}"


# --------------------------------------------------------------- the breakpoint
def test_the_sidebar_survives_a_1024_wide_canvas():
    m = re.search(r"@media \(max-width: (\d+)px\) \{\s*\.nu-rail\b", SHELL)
    assert m, "the rail's collapse breakpoint was not found"
    assert int(m.group(1)) < 1024, (
        f"the rail collapses at {m.group(1)}px, so it is hidden on the 1024-wide canvas "
        "three of the design's frames draw it on"
    )
