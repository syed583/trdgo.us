"""Stage 1 of the redesign: the per-account flag, and the menu both shells read.

The flag must FAIL CLOSED. The existing interface is the known-good one, so every failure - an unset
variable, an unreadable one, an anonymous caller - has to land there. A redesign that shows itself to
everyone when a setting cannot be read is worse than one that never ships.

The menu tests exist because of a specific risk: A.25 and A.26 removed pages from the user's menu by owner
decision, and a redesigned sidebar is the natural place for them to come back unnoticed - the two shells
are never on screen together, so nobody would see the difference.
"""
from __future__ import annotations

import os
import pathlib
import re

import pytest

FRONT = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"
COMPONENTS = FRONT / "components"


# ---------------------------------------------------------------- the flag, fail closed
@pytest.fixture()
def flags(monkeypatch):
    import ui_flags
    return ui_flags


def test_nobody_sees_the_new_shell_by_default(flags, monkeypatch):
    monkeypatch.delenv(flags.ENV_VAR, raising=False)
    assert flags.new_ui_for("owner") is False
    assert flags.new_ui_for("anyone") is False


def test_an_empty_setting_means_nobody(flags, monkeypatch):
    monkeypatch.setenv(flags.ENV_VAR, "")
    assert flags.new_ui_for("owner") is False
    monkeypatch.setenv(flags.ENV_VAR, "   ,  , ")
    assert flags.new_ui_for("owner") is False


def test_only_the_named_account(flags, monkeypatch):
    monkeypatch.setenv(flags.ENV_VAR, "owner")
    assert flags.new_ui_for("owner") is True
    assert flags.new_ui_for("someone_else") is False


def test_the_name_is_matched_without_case_or_spaces(flags, monkeypatch):
    monkeypatch.setenv(flags.ENV_VAR, " Owner , Second ")
    assert flags.new_ui_for("owner") is True
    assert flags.new_ui_for("SECOND") is True
    assert flags.new_ui_for("third") is False


def test_an_anonymous_caller_is_never_allowed(flags, monkeypatch):
    monkeypatch.setenv(flags.ENV_VAR, "*")
    assert flags.new_ui_for(None) is False
    assert flags.new_ui_for("") is False


def test_a_star_means_everyone_signed_in(flags, monkeypatch):
    monkeypatch.setenv(flags.ENV_VAR, "*")
    assert flags.new_ui_for("anyone") is True


def test_me_reports_the_flag_and_cannot_raise():
    """/auth/me must carry new_ui, and a broken flag module must not break sign-in."""
    src = (pathlib.Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    assert '"new_ui": _new_ui(user["username"])' in src, "the me payload must carry new_ui"
    helper = src.split("def _new_ui(")[1].split("\ndef ")[0]
    assert "except Exception" in helper and "return False" in helper, \
        "a failure in the flag must serve the EXISTING shell, never the new one"


# ---------------------------------------------------------------- one menu, both shells
def _nav_model() -> str:
    return (COMPONENTS / "navModel.tsx").read_text(encoding="utf-8")


def _routes(text: str) -> set[str]:
    return set(re.findall(r"to:\s*'([^']+)'", text))


def test_both_shells_read_the_same_menu_module():
    """Neither sidebar may declare its own list - that is how the two drift apart."""
    for name in ("Sidebar.tsx", "NewSidebar.tsx"):
        src = (COMPONENTS / name).read_text(encoding="utf-8")
        assert "from './navModel'" in src, f"{name} must import the shared menu"
        assert "const NAV_GROUPS" not in src, \
            f"{name} declares its own menu; there must be exactly one"


@pytest.mark.parametrize("route", [
    # A.26 (2026-11-14): the four per-model intraday boards and All Intraday left the user's menu.
    "/grok-intraday", "/meta-intraday", "/kimi-intraday", "/gemini-intraday", "/all-intraday-models",
    # A.25 (2026-11-13): the seven per-model earnings boards.
    "/claude-last", "/grok", "/meta", "/chatgpt", "/kimi", "/gemini", "/claude-pro",
    # A.25: four more intraday pages.
    "/tradgo-call", "/trade-plan", "/signal-history", "/peers",
    # B.1a (2026-11-15): the model's own scoreboard is for an administrator only.
    "/all-models", "/model-accuracy",
])
def test_a_removed_page_does_not_come_back(route):
    """Every page an owner decision took out of the user's menu stays out of BOTH shells."""
    model = _nav_model()
    user_part = model.split("const NAV_GROUPS")[1].split("export const ADMIN_GROUP")[0]
    assert route not in _routes(user_part), (
        f"{route} is back in the user's menu - A.25/A.26/B.1a removed it by owner decision"
    )


def test_the_new_sidebar_shows_the_live_menu_not_the_figma_list():
    """The frame draws twelve flat links; the live menu is the five groups. Style copied, content kept."""
    src = (COMPONENTS / "NewSidebar.tsx").read_text(encoding="utf-8")
    assert "USER_GROUPS(mt5Operator)" in src and "ADMIN_GROUP(mt5Operator)" in src
    # the groups' headings must still be rendered, or twenty-three links become a flat wall again
    assert "nu-nav-head" in src


def test_the_new_shell_styles_cannot_reach_the_old_one():
    """Every rule is scoped under .app-nu; a bare selector would restyle the existing interface."""
    css = (FRONT / "new-shell.css").read_text(encoding="utf-8")
    body = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for block in re.findall(r"([^{}]+)\{", body):
        for sel in block.split(","):
            sel = sel.strip()
            if not sel or sel.startswith("@") or sel.startswith("to") or sel.startswith("from"):
                continue
            # `.app.app-nu` is scoped too: both classes sit on the shell root, and the second
            # exists to out-weigh styles.css's own `.app` rule at equal specificity.
            ok = (sel.startswith(".app-nu") or sel.startswith(".app.app-nu")
                  or sel.startswith(".nu-") or sel.startswith(":root"))
            assert ok, f"unscoped selector would leak into the existing shell: {sel!r}"


def test_the_new_shell_uses_tokens_not_hard_coded_colours():
    """F.1a: hard-coded colours are how My Broker became unreadable in light mode."""
    css = (FRONT / "new-shell.css").read_text(encoding="utf-8")
    body = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    # the token blocks are where hex belongs; every rule after them must use var()
    after = body.split(":root[data-theme='light'] .app-nu")[1]
    after = after.split("}", 1)[1]
    leaked = [h for h in re.findall(r"#[0-9a-fA-F]{3,8}", after)]
    assert not leaked, f"hex outside the token block: {leaked}"
