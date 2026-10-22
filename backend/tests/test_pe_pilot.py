"""Phase 8.2 pilot — pure gating tests (schedule window, cap constants, safety gate)."""
import os
import pe_pilot as pl


def test_schedule_window_rth_only():
    assert pl.in_schedule_window("OPEN") is True
    # extended-hours capture is unavailable (no EH data) -> only the regular session
    for s in ("PRE_MARKET", "AFTER_HOURS", "CLOSED"):
        assert pl.in_schedule_window(s) is False


def test_pilot_caps():
    assert pl.PILOT_API_CAP == 2000
    assert pl.PILOT_WARN == 1600
    assert pl.PILOT_TRADING_DAYS == 5
    assert pl.PILOT_WARN < pl.PILOT_API_CAP


def test_loop_refuses_without_gate(capsys, monkeypatch):
    monkeypatch.delenv("PE_PILOT", raising=False)
    pl.loop(interval_s=1)                       # must return immediately, not loop
    out = capsys.readouterr().out
    assert "refusing" in out.lower()
