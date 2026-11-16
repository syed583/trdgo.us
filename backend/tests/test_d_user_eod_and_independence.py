"""D: users trade independently of the admin account, and close on their own clock at 15:45 ET.

The owner asked for "no hierarchy". Most of it was already true and is pinned here so it stays true: the
admin mirror and the user copier are SIBLINGS on the Strategy Trades signal, and the copy path never reads
the admin's connection, pause, halt or auto-trade switch.

What was missing was the end of day. A user's position closed only when the PAPER trade closed; on
2026-11-12 that ran 19-27 minutes late and the broker then refused every close, leaving seven admin positions
open overnight. The admin has had its own sweep since A.22; users now have the same.

Owner decisions, 2026-11-16: the exclusion list stays shared, and the user sweep runs on its own clock.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import mt5_autotrade as AT                                       # noqa: E402
import user_copy_service as CP                                   # noqa: E402

ET = ZoneInfo("America/New_York")


# --------------------------------------------------------------------------- no hierarchy
def test_the_user_copier_never_reads_the_admin_connection():
    """A disconnected, paused, halted or switched-off admin account must not stop a user trading."""
    src = (BACKEND / "user_copy_service.py").read_text(encoding="utf-8")
    for forbidden in ("is_connected", "auto_trade_enabled", "AT.paused", "AT.halted", "_conn",
                      "daily_loss_reached", "dry_run"):
        assert forbidden not in src, "the user path reads the admin engine state: %s" % forbidden


def test_both_listeners_are_siblings_on_the_same_signal():
    """Not a chain: the admin mirror does not feed the user copier."""
    src = (BACKEND / "main.py").read_text(encoding="utf-8")
    assert "dts.HOOKS.append(AT.on_paper_event)" in src
    assert "dts.HOOKS.append(CP.on_paper_event)" in src
    admin_src = (BACKEND / "mt5_autotrade.py").read_text(encoding="utf-8")
    assert "user_copy_service" not in admin_src, "the admin engine must not call the user copier"


def test_the_shared_exclusion_list_still_applies(monkeypatch):
    """Owner decision 2026-11-16: excluded symbols stay blocked for everyone, after the A.18 wrong fills."""
    src = (BACKEND / "user_copy_service.py").read_text(encoding="utf-8")
    assert "AT.is_excluded" in src, "the shared safety list must still apply to users"


# --------------------------------------------------------------------------- the sweep window
def test_the_user_sweep_uses_the_same_window_as_the_admin():
    """Imported, not copied, so the two cannot drift apart."""
    src = (BACKEND / "user_copy_service.py").read_text(encoding="utf-8")
    assert "AT.EOD_SWEEP_START" in src and "AT.EOD_SWEEP_END" in src
    assert "(15, 45)" not in src, "the window must not be re-typed here"
    assert CP.sweep_state()["window_et"] == "15:45-15:58"


def _at(monkeypatch, hh, mm, *, weekday_offset=0):
    """Pin the ET clock to a Monday at hh:mm (2026-11-16 is a Monday)."""
    when = datetime(2026, 11, 16 + weekday_offset, hh, mm, tzinfo=ET)
    monkeypatch.setattr(CP, "_et_now", lambda: when)
    return when


@pytest.fixture
def swept(monkeypatch):
    calls = []
    monkeypatch.setattr(CP, "_sweep_state", {"last_sweep_date": None, "closed": 0, "failed": 0,
                                             "last_run": None, "last_retry_date": None, "retried": 0})
    monkeypatch.setattr(CP, "close_row",
                        lambda name, row, reason, **k: (calls.append((name, row["admin_trade_id"], reason)),
                                                        {"ok": True})[1])
    monkeypatch.setattr(CP.store, "all_accounts", lambda: [{"username": "ann"}, {"username": "bob"}])
    monkeypatch.setattr(CP.store, "open_tickets",
                        lambda u: [{"admin_trade_id": "T-%s" % u, "ticket": 1, "symbol": "AAPL",
                                    "broker_symbol": "AAPL", "side": "BUY", "shares": 10, "volume": 0.1,
                                    "price": 100.0, "strategy": "gemini"}])
    return calls


@pytest.mark.parametrize("hh,mm", [(15, 45), (15, 50), (15, 58)])
def test_the_sweep_closes_inside_the_window(swept, monkeypatch, hh, mm):
    _at(monkeypatch, hh, mm)
    out = CP.sweep_once()
    assert out["action"] == "EOD_SWEEP" and out["closed"] == 2
    assert {c[0] for c in swept} == {"ann", "bob"}
    assert all(c[2] == "EOD_SWEEP" for c in swept)


@pytest.mark.parametrize("hh,mm", [(15, 44), (15, 59), (12, 0)])
def test_the_sweep_does_nothing_outside_the_window(swept, monkeypatch, hh, mm):
    _at(monkeypatch, hh, mm)
    out = CP.sweep_once()
    assert out["action"] == "NONE"
    assert swept == []


def test_nothing_happens_at_the_weekend(swept, monkeypatch):
    _at(monkeypatch, 15, 50, weekday_offset=5)          # Saturday
    assert CP.sweep_once()["reason"] == "WEEKEND"
    assert swept == []


def test_a_refused_close_is_retried_next_session(swept, monkeypatch):
    _at(monkeypatch, 9, 31)
    out = CP.sweep_once()
    assert out["action"] == "PENDING_RETRY" and out["closed"] == 2
    assert all(c[2] == "PENDING_RETRY" for c in swept)


def test_the_morning_retry_runs_once_a_day(swept, monkeypatch):
    _at(monkeypatch, 9, 31)
    CP.sweep_once()
    first = len(swept)
    CP.sweep_once()
    assert len(swept) == first, "the retry must not repeat all morning"


def test_the_morning_retry_does_nothing_when_nothing_is_open(swept, monkeypatch):
    monkeypatch.setattr(CP.store, "open_tickets", lambda u: [])
    _at(monkeypatch, 9, 31)
    assert CP.sweep_once()["action"] == "NONE"
    assert swept == []


# --------------------------------------------------------------------------- it closes for everyone
def test_a_user_who_switched_off_is_still_closed(monkeypatch):
    """Auto-trade off does not abandon a position we already opened in their account."""
    monkeypatch.setattr(CP.store, "all_accounts", lambda: [{"username": "ann"}])
    monkeypatch.setattr(CP.store, "open_tickets", lambda u: [{"admin_trade_id": "T1", "ticket": 1}])
    monkeypatch.setattr(CP.store, "copying_users", lambda: [])          # nobody is copying any more
    rows = CP.open_rows_all_users()
    assert [r[0] for r in rows] == ["ann"], "the sweep must read the ORDER LOG, not the copying list"


def test_one_users_failure_does_not_stop_the_others(monkeypatch):
    monkeypatch.setattr(CP.store, "all_accounts", lambda: [{"username": "ann"}, {"username": "bob"}])
    monkeypatch.setattr(CP.store, "open_tickets", lambda u: [{"admin_trade_id": "T-%s" % u, "ticket": 1}])

    def close_row(name, row, reason, **k):
        if name == "ann":
            raise RuntimeError("ann's broker is down")
        return {"ok": True}

    monkeypatch.setattr(CP, "close_row", close_row)
    out = CP.sweep_now()
    assert out["closed"] == 1 and out["failed"] == 1


def test_an_unreadable_account_does_not_hide_the_rest(monkeypatch):
    def open_tickets(u):
        if u == "ann":
            raise RuntimeError("db hiccup")
        return [{"admin_trade_id": "T1", "ticket": 1}]

    monkeypatch.setattr(CP.store, "all_accounts", lambda: [{"username": "ann"}, {"username": "bob"}])
    monkeypatch.setattr(CP.store, "open_tickets", open_tickets)
    rows = CP.open_rows_all_users()
    assert [r[0] for r in rows] == ["bob"]


def test_the_sweep_is_started_with_the_worker():
    src = (BACKEND / "main.py").read_text(encoding="utf-8")
    assert "CP.start_sweep()" in src


def test_the_sweep_does_not_touch_the_admin_book():
    src = (BACKEND / "user_copy_service.py").read_text(encoding="utf-8")
    block = src.split("end of day, on OUR clock")[1]
    for forbidden in ("_positions(", "_save_positions", "_book_realized", "AT.sweep", "AT._attempt_close"):
        assert forbidden not in block, "the user sweep must not touch the admin ledger: %s" % forbidden


# --------------------------------------------------------------------------- the words people read
def test_the_switch_is_called_auto_trade_on_screen():
    page = (BACKEND.parent / "frontend" / "src" / "pages" / "MyBrokerPage.tsx").read_text(encoding="utf-8")
    assert "Auto-trade" in page
    assert "Auto-copy" not in page, "the label was renamed; no stale wording may remain"
    assert "auto_copy" in page, "the API key must NOT be renamed - that would break a live route"


def test_strategy_trades_points_at_my_broker():
    page = (BACKEND.parent / "frontend" / "src" / "pages" / "StrategyTradesPage.tsx").read_text(encoding="utf-8")
    assert "Want these trades in your own account?" in page
    assert '"/my-broker"' in page or "to=\"/my-broker\"" in page


def test_strategy_trades_no_longer_claims_paper_only():
    """With Auto-trade on, these signals DO place real orders - in the reader's own account."""
    page = (BACKEND.parent / "frontend" / "src" / "pages" / "StrategyTradesPage.tsx").read_text(encoding="utf-8")
    assert "Paper only — no real orders." not in page
    assert "no real orders are placed" in page and "here" in page
