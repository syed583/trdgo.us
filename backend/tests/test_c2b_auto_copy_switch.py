"""C.2b: "Switch Auto-copy ON" looked broken because a second switch was silently holding it.

The press always persisted. `copying` needs auto_copy AND enabled AND NOT admin_paused AND consent, and C.1
creates every account with enabled = FALSE, so the row read "off" with no reason and the owner pressed again
- turning it back off. These tests pin both halves of the fix: ON clears the pause, and the status always
carries the reason it is not copying.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import user_copy_service as CP                                   # noqa: E402
import user_mt5_store as store                                   # noqa: E402


# --------------------------------------------------------------------------- ON means on
def test_switching_auto_copy_on_also_clears_the_pause():
    """The SQL itself: asking to copy while paused is the contradiction the user could not see."""
    src = (BACKEND / "user_mt5_store.py").read_text(encoding="utf-8")
    block = src.split('if "auto_copy" in patch:')[1].split("if not fields")[0]
    assert 'params["auto_copy"]' in block
    assert 'fields.append("enabled = TRUE")' in block, \
        "switching Auto-copy ON must clear the pause, or the button appears to do nothing"


def test_switching_auto_copy_off_does_not_touch_the_pause():
    """Off must not resume anything, and must not forget the pause state."""
    src = (BACKEND / "user_mt5_store.py").read_text(encoding="utf-8")
    block = src.split('if "auto_copy" in patch:')[1].split("if not fields")[0]
    guarded = block.split('if params["auto_copy"]:')[1]
    assert 'fields.append("enabled = TRUE")' in guarded, \
        "the resume must sit INSIDE the 'switching on' branch"


def test_the_copying_rule_is_unchanged():
    """C.2b changes which switches a USER sets, never which ones an order requires."""
    src = (BACKEND / "user_mt5_store.py").read_text(encoding="utf-8")
    where = src.split("def copying_users")[1].split("def ")[0]
    for clause in ("auto_copy = TRUE", "enabled = TRUE", "admin_paused = FALSE", "consent_at IS NOT NULL"):
        assert clause in where, "copying_users no longer requires %s" % clause


# --------------------------------------------------------------------------- the status says why
def _status(monkeypatch, **settings):
    base = {"shares": 10, "max_positions": 20, "daily_loss_pct": 1.0, "auto_copy": False, "enabled": False,
            "admin_paused": False, "has_account": True, "consent_at": "2026-11-16T11:49:48Z"}
    base.update(settings)
    monkeypatch.setattr(CP.store, "settings_of", lambda u: base)
    monkeypatch.setattr(CP.store, "open_tickets", lambda u: [])
    monkeypatch.setattr(CP.store, "realized_today", lambda u: 0.0)
    return CP.user_status("ann")


def test_the_exact_case_the_owner_hit(monkeypatch):
    """auto_copy on, account still paused: the old page just said 'off'."""
    out = _status(monkeypatch, auto_copy=True, enabled=False)
    assert out["copying"] is False
    assert "paused" in out["blocked_reason"].lower()
    assert "Resume" in out["blocked_reason"], "the reason must name the control that fixes it"


def test_auto_copy_off_says_so(monkeypatch):
    out = _status(monkeypatch, auto_copy=False, enabled=True)
    assert "Auto-copy is off" in out["blocked_reason"]


def test_no_consent_says_so(monkeypatch):
    out = _status(monkeypatch, auto_copy=True, enabled=True, consent_at=None)
    assert "risk notice" in out["blocked_reason"]


def test_no_account_says_so(monkeypatch):
    out = _status(monkeypatch, has_account=False)
    assert "Connect an account" in out["blocked_reason"]


def test_an_operator_pause_is_named(monkeypatch):
    out = _status(monkeypatch, auto_copy=True, enabled=True, admin_paused=True)
    assert "administrator" in out["blocked_reason"].lower()


def test_a_copying_account_has_no_blocked_reason(monkeypatch):
    out = _status(monkeypatch, auto_copy=True, enabled=True)
    assert out["copying"] is True and out["blocked_reason"] is None
    assert "being placed" in out["note"]


def test_the_note_carries_the_reason(monkeypatch):
    out = _status(monkeypatch, auto_copy=True, enabled=False)
    assert out["blocked_reason"] in out["note"]


@pytest.mark.parametrize("settings", [
    {"auto_copy": False, "enabled": False},
    {"auto_copy": True, "enabled": False},
    {"auto_copy": False, "enabled": True},
    {"auto_copy": True, "enabled": True, "admin_paused": True},
])
def test_not_copying_always_has_a_reason(monkeypatch, settings):
    out = _status(monkeypatch, **settings)
    assert out["copying"] is False
    assert out["blocked_reason"], "a status of 'not copying' with no reason is what broke this for the owner"


# --------------------------------------------------------------------------- the page
def _page() -> str:
    return (BACKEND.parent / "frontend" / "src" / "pages" / "MyBrokerPage.tsx").read_text(encoding="utf-8")


def test_the_page_shows_the_switch_state_and_the_reason():
    src = _page()
    assert "blocked_reason" in src, "the page must show why it is not copying"
    assert "{set.auto_copy ? 'on' : 'off'}" in src, "the page must show the SWITCH state, not only `copying`"


def test_the_page_no_longer_prints_a_bare_off():
    src = _page()
    assert ": <b>off</b>}" not in src, "a bare 'off' with no reason is the thing that was fixed"


def test_the_pause_button_says_what_it_pauses():
    src = _page()
    assert "Pause copying" in src and "Resume copying" in src, \
        "'Pause everything' / 'Resume' gave no hint that it gated copying"
