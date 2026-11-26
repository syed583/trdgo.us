"""F.1a: My Broker must follow the site's theme, not carry its own colours.

The first version of my-broker.css invented token names - --card, --bg-soft, --fg-muted, --ok, --danger,
--warn - none of which exist in styles.css, and gave each a DARK hex fallback. Dark mode looked right by
luck. In light mode every surface kept its dark fallback while the text took the real near-black token, so
the page rendered dark-on-dark: the status reason, the risk notice, the Size help text, the rail labels and
the Set button were all unreadable.

A fallback colour in a themed app is a bug waiting for the other theme. These tests fail on that file.
"""
from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"
CSS = SRC / "pages" / "my-broker.css"


def _declarations() -> str:
    """The stylesheet with comments stripped, so prose about colours is not mistaken for a colour."""
    return re.sub(r"/\*.*?\*/", "", CSS.read_text(encoding="utf-8"), flags=re.S)


def test_no_hard_coded_colours():
    body = _declarations()
    hexes = re.findall(r"#[0-9a-fA-F]{3,8}\b", body)
    assert hexes == [], "hard-coded colours will not follow the theme: %s" % sorted(set(hexes))
    for fn in ("rgb(", "rgba(", "hsl("):
        assert fn not in body, "%s is a literal colour; use a theme token" % fn


def test_no_colour_fallbacks_in_var():
    """var(--token, <colour>) is how the light theme broke: the fallback wins when the token is missing."""
    body = _declarations()
    bad = [m.group(0) for m in re.finditer(r"var\(\s*--[\w-]+\s*,\s*[^)]*\)", body)
           if re.search(r"#[0-9a-fA-F]{3,8}|rgba?\(|hsl\(", m.group(0))]
    assert bad == [], "a colour fallback defeats theming: %s" % bad


def test_only_tokens_that_actually_exist():
    """Every --token used here must be defined in styles.css, or it silently renders as nothing."""
    declared = set(re.findall(r"^\s*(--[\w-]+)\s*:", (SRC / "styles.css").read_text(encoding="utf-8"), re.M))
    # Kit U gave the page the redesigned shell's look. Those rules live under .app-nu and use the shell's own
    # tokens (--nu-*), which new-shell.css defines for both themes - the same rule, a second token file.
    declared |= set(re.findall(r"^\s*(--nu-[\w-]+)\s*:", (SRC / "new-shell.css").read_text(encoding="utf-8"), re.M))
    used = set(re.findall(r"var\(\s*(--[\w-]+)", _declarations()))
    missing = sorted(used - declared)
    assert missing == [], "these tokens are not defined in styles.css or new-shell.css: %s" % missing


def test_surfaces_and_text_come_from_the_same_system():
    """A surface token with text from somewhere else is exactly how dark-on-dark happened."""
    body = _declarations()
    assert "var(--panel" in body, "card surfaces must use the theme's panel tokens"
    assert "var(--text" in body, "text must use the theme's text tokens"


def test_the_light_theme_defines_every_token_we_rely_on():
    styles = (SRC / "styles.css").read_text(encoding="utf-8")
    light = styles.split(':root[data-theme="light"]')[1].split("}")[0]
    used = set(re.findall(r"var\(\s*(--[\w-]+)", _declarations()))
    # tokens the light block does not restate inherit the dark value - fine for shapes, wrong for colour
    colour_tokens = {t for t in used if not t.startswith("--nu-") and any(k in t for k in
                                            ("panel", "text", "border", "green", "red", "amber", "accent", "bg"))}
    missing = sorted(t for t in colour_tokens if t not in light)
    assert missing == [], "light mode never redefines these, so they keep the dark value: %s" % missing
