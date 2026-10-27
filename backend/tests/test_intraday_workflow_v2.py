"""Intraday paper-trading workflow v2: the scenarios A-T of the specification, plus the schema, evidence and calendar rules.

Nothing here touches a strategy's score: rows come in with the strategy's own signal, score and parameters already computed."""
import json
from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError

import demo_trade_service as dts
import intraday_execution as ex
import intraday_workflow as wf
import market_calendar as mc
import models_demo_trades as m
import tab_settings as cfg
from wf_harness import (STRATEGIES, TUE, ET, bar, book, et, falling_bars, make_params, open_via_premarket, quote, rising_bars,
                        row)


# ===================================================================== A / B  pre-market entries ====================
def test_A_premarket_buy_opens_a_long_with_full_evidence(book):
    now, t = open_via_premarket(book, et(*TUE, 7, 15))
    assert t is not None and t.side == "LONG" and t.status == "OPEN" and t.entry_session == "PREMARKET"
    assert t.shares == 100 and t.session_date == et(*TUE).date()
    assert t.entry_price == pytest.approx(100.02)                      # the ask of the fresh two-sided quote
    assert t.stop == 99.0 and t.target == 102.0                        # the strategy's own levels, unchanged
    ev = json.loads(t.validation)
    assert ev["quote"]["ok"] and ev["risk"]["ok"] and ev["persistence"]["ok"] and ev["data"]["ok"]
    assert json.loads(t.entry_quote)["assumption"].startswith("entry at the ask")
    assert t.snapshot_id and t.signal_event_id
    events = book.events()
    assert len(events) == 1 and events[0].session_type == "PREMARKET" and events[0].eligibility_status == "TRADED"
    assert events[0].trade_id == t.id and events[0].validation_status == "PASS"
    snap = [s for s in book.snapshots() if s.id == t.snapshot_id][0]
    params = json.loads(snap.params)
    assert {p["name"] for p in params} == set(wf.__dict__["ex"].REGISTRY["chatgpt"]["equity"])
    assert all("category" in p and "points" in p for p in params)
