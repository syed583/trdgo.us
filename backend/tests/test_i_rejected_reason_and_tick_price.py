"""Kit I: the order actually gets sent, the refusal names itself, and the tick log carries a price.

G built `info` for market_order from the user's stored mapping. market_order starts with
`if not info.get("ok"): return info` - and the mapping dict had no "ok", so EVERY user order returned that
dict before OrderSendSafe was called. No order was sent; the REJECTED row read "None (None): None".
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import user_copy_service as CP                                   # noqa: E402


# --------------------------------------------------------------------------- the bug
def test_a_mapping_produces_an_info_dict_market_order_accepts(monkeypatch):
    monkeypatch.setattr(CP.symmap, "get", lambda u, s: {
        "broker_symbol": "CAT", "contract_size": 100.0, "volume_min": 0.01, "volume_step": 0.01,
        "volume_max": 50.0})
    bsym, info = CP._broker_symbol("ann", "TOK", "CAT")
    assert bsym == "CAT"
    assert info.get("ok") is True, "market_order returns early on a dict without ok, before sending anything"
    assert info["contract_size"] == 100.0 and info["volume_step"] == 0.01


def test_market_order_would_have_returned_early_without_ok():
    """The exact line that caused it, asserted so the shape requirement cannot be forgotten again."""
    src = (BACKEND / "mt5_bridge.py").read_text(encoding="utf-8")
    block = src.split("def market_order")[1].split("\ndef ")[0]       # that function only
    assert 'if not info.get("ok"):' in block
    assert "return info" in block


def test_an_unsized_mapping_is_still_refused(monkeypatch):
    monkeypatch.setattr(CP.symmap, "get", lambda u, s: {"broker_symbol": "CAT", "contract_size": 100.0,
                                                        "volume_min": None, "volume_step": None})
    bsym, why = CP._broker_symbol("ann", "TOK", "CAT")
    assert bsym is None and why == "NOT_MAPPED"


# --------------------------------------------------------------------------- the reason
@pytest.mark.parametrize("res,expect", [
    ({"ok": False, "retcode_text": "NO_MONEY", "retcode": 10019}, "NO_MONEY"),
    ({"ok": False, "reason": "NOT_CONNECTED", "detail": "no session token"}, "NOT_CONNECTED"),
    ({"ok": False, "comment": "market closed"}, "market closed"),
    ({"ok": False, "stage": "order_send"}, "order_send"),
])
def test_a_refusal_names_itself(res, expect):
    assert expect in CP._refusal_text(res)


def test_a_refusal_with_nothing_usable_says_so_instead_of_printing_none():
    """This is the case that produced "None (None): None" - it must never be silent again."""
    out = CP._refusal_text({"ok": False, "contract_size": 100.0})
    assert "None" not in out.replace("without a reason", "")
    assert "without a reason" in out and "contract_size" in out, "name the shape so it can be diagnosed"


def test_the_rejected_row_uses_the_helper():
    """Checked against the CODE: the comments explain the old format string, and a grep over raw text
    matches that explanation rather than any surviving code."""
    src = (BACKEND / "user_copy_service.py").read_text(encoding="utf-8")
    code = ast.unparse(ast.parse(src))        # comments gone; docstrings remain but carry no format string
    block = src.split('_safe_log(name, "REJECTED"')[0][-400:]
    assert "_refusal_text" in block, "the REJECTED row must use the helper"
    assert '"%s (%s): %s"' not in code, "the three-Nones format string must be gone from the code"


# --------------------------------------------------------------------------- the tick price
def test_the_score_tick_log_records_a_price():
    """Without a price no alternative exit rule can be priced: the score-exit backtest needs a series."""
    src = (BACKEND / "demo_trade_service.py").read_text(encoding="utf-8")
    rec = src.split("rec = {")[1].split("}")[0]
    for field in ('"price"', '"stop"', '"target"', '"score"', '"at"'):
        assert field in rec, "the tick record is missing %s" % field


def test_the_tick_logger_still_cannot_break_a_trade():
    """A.27: a logging failure must never reach the tracker."""
    src = (BACKEND / "demo_trade_service.py").read_text(encoding="utf-8")
    fn = src.split("def _log_score_tick")[1].split("\ndef ")[0]
    assert "except Exception" in fn and "pass" in fn


def test_a_trade_object_without_the_new_fields_still_records(monkeypatch, tmp_path):
    """The bug this test exists for: kit I read t.last_price directly, and _log_score_tick swallows every
    exception (A.27). A trade object without that attribute therefore wrote NOTHING, silently - the exact
    failure that left the tick log useless. Reads are tolerant now."""
    import json
    from datetime import datetime, timezone
    from types import SimpleNamespace
    import demo_trade_service as dts

    monkeypatch.setenv("SCORE_TICK_DIR", str(tmp_path))
    monkeypatch.setattr(dts, "_cfg", lambda: {"score_tick_log": True})
    when = datetime(2026, 11, 17, 15, 0, tzinfo=timezone.utc)
    bare = SimpleNamespace(id=1, strategy="grok", symbol="AAA", side="LONG", score_at_entry=60.0,
                           status="OPEN")            # no last_price, no stop, no target
    dts._log_score_tick(bare, 50.0, when)
    f = tmp_path / ("%s.jsonl" % when.date().isoformat())
    assert f.exists(), "a missing attribute must not stop the tick being recorded"
    rec = json.loads(f.read_text(encoding="utf-8").splitlines()[0])
    assert rec["score"] == 50.0 and rec["price"] is None


def test_a_logging_failure_is_no_longer_silent():
    """It still cannot break a trade, but it must leave a trace - a log that stops recording while
    everything looks healthy is how this went unnoticed for days."""
    src = (BACKEND / "demo_trade_service.py").read_text(encoding="utf-8")
    fn = src.split("def _log_score_tick")[1].split("\ndef ")[0]
    assert "_log.warning" in fn and "exc_info=True" in fn
