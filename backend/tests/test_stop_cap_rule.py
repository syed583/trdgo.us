"""STOP CAP (owner decision 2026-11-12): a paper trade whose stop sits further than PAPER_EXECUTION['max_stop_pct'] from the
entry is not opened. Pure rule tests; the board levels themselves are untouched."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import demo_trade_service as D  # noqa: E402
import tab_settings as cfg  # noqa: E402


def test_cap_is_two_and_a_half_percent_by_default():
    assert cfg.PAPER_EXECUTION["max_stop_pct"] == 2.5


def test_stop_inside_the_cap_opens_and_outside_does_not(monkeypatch):
    monkeypatch.setitem(cfg.PAPER_EXECUTION, "max_stop_pct", 2.5)
    assert D.stop_cap_reason(100.0, 97.6) is None                                      # 2.40 %
    assert D.stop_cap_reason(100.0, 97.5) is None                                      # exactly 2.50 % opens
    r = D.stop_cap_reason(100.0, 97.49)                                                # 2.51 %
    assert r and r.startswith("STOP_CAP") and "2.51%" in r and "cap 2.50%" in r
    assert D.stop_cap_reason(100.0, 102.51)                                            # a SHORT's stop above the entry counts the same way
    assert D.stop_cap_reason(50.0, 48.74) and D.stop_cap_reason(50.0, 48.76) is None


def test_missing_stop_or_disabled_cap_never_blocks(monkeypatch):
    assert D.stop_cap_reason(100.0, None) is None
    monkeypatch.setitem(cfg.PAPER_EXECUTION, "max_stop_pct", 0)
    assert D.stop_cap_reason(100.0, 90.0) is None
    monkeypatch.setitem(cfg.PAPER_EXECUTION, "max_stop_pct", None)
    assert D.stop_cap_reason(100.0, 90.0) is None


def test_hits_are_counted_for_the_status_page(monkeypatch):
    monkeypatch.setattr(D, "_stop_cap_hits", {"count": 0, "last": None})
    D._note_stop_cap("grok", "DELL", "STOP_CAP: x")
    D._note_stop_cap("meta", "AMD", "STOP_CAP: y")
    h = D.stop_cap_hits()
    assert h["count"] == 2 and h["last"]["symbol"] == "AMD" and h["last"]["strategy"] == "meta"
