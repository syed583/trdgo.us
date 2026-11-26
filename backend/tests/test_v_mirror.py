"""Kit V, parts B, C and E: phantom positions, the position cap, margin, single-flight closes, per-user queues.

Found on the live server 2026-11-26: account ..7036 had 20 positions open in OUR LOG and 0 in the software.
The broker had closed them by its own stop or target; our close then read "no price returned", wrote
CLOSE_UNCONFIRMED and left the row open for ever. The 20 phantoms filled a ceiling of 20 and 414 of 575
signals were skipped in five days.
"""
import json
import threading
import time

import user_copy_service as CP
from test_c2_live_copy_trading import ENTRY, EXIT, rig  # noqa: F401 - the fixture and the per-account fake broker


def _one(st, **over):
    st.users = [dict({"username": "ann", "shares": 100, "max_positions": 20, "daily_loss_pct": 1.0}, **over)]


def _ticket(broker, tok="tok-ann", i=-1):
    return broker.orders[tok][i]["ticket"]


# ---------------------------------------------------------------- phantoms
def test_a_position_the_broker_closed_itself_is_recorded_closed_with_the_brokers_price(rig):
    broker, st, _ = rig; _one(st)
    CP.dispatch(ENTRY)
    tk = _ticket(broker)
    broker.accounts["tok-ann"]["gone"] = {tk}                              # the broker's own stop took it out
    broker.accounts["tok-ann"]["history"] = {tk: {"price": 99.0, "time": "2026-11-19T15:01:02", "how": "STOP"}}
    CP.dispatch(EXIT)
    closed = st.events("ann", "CLOSED")
    assert len(closed) == 1 and closed[0]["price"] == 99.0
    assert closed[0]["reason"].startswith("AT_BROKER:STOP") and "SCORE_EXIT" in closed[0]["reason"]
    assert closed[0]["pnl"] == round((99.0 - 100.1) * 100, 2)             # real price, real loss
    assert st.open_tickets("ann") == []                                   # never left open again
    assert broker.closes["tok-ann"] == []                                 # and no close was sent for a position that is gone
    assert not st.events("ann", "CLOSE_UNCONFIRMED")


def test_with_no_price_in_the_brokers_history_the_row_is_still_closed_and_says_so(rig):
    broker, st, _ = rig; _one(st)
    CP.dispatch(ENTRY)
    broker.accounts["tok-ann"]["gone"] = {_ticket(broker)}
    CP.dispatch(EXIT)
    closed = st.events("ann", "CLOSED")
    assert len(closed) == 1 and closed[0]["price"] is None and closed[0]["pnl"] is None
    assert closed[0]["reason"].startswith("AT_BROKER:PRICE_UNKNOWN")
    assert st.open_tickets("ann") == []


def test_reconcile_settles_each_phantom_once_and_never_twice(rig):
    broker, st, _ = rig; _one(st)
    CP.dispatch(ENTRY); CP.dispatch(dict(ENTRY, trade_id="T2"))
    broker.accounts["tok-ann"]["gone"] = {_ticket(broker, i=0)}           # one gone, one really open
    first = CP.reconcile_user("ann")
    second = CP.reconcile_user("ann")
    assert first == {"ok": True, "log_open": 2, "really_open": 1, "settled": 1}
    assert second["settled"] == 0 and second["really_open"] == 1
    assert len(st.events("ann", "CLOSED")) == 1
    assert broker.closes["tok-ann"] == []                                 # reconciling reads; it never closes anything


def test_reconcile_does_nothing_when_the_broker_cannot_be_read(rig, monkeypatch):
    broker, st, _ = rig; _one(st)
    CP.dispatch(ENTRY)
    monkeypatch.setattr(broker, "positions", lambda **k: {"ok": False, "reason": "TRANSPORT"})
    CP._live_cache.clear()
    assert CP.reconcile_user("ann")["ok"] is False
    assert len(st.open_tickets("ann")) == 1 and not st.events("ann", "CLOSED")   # unknown is not "closed"


# ---------------------------------------------------------------- the cap
def test_the_cap_counts_only_positions_really_open_at_the_broker(rig):
    broker, st, _ = rig; _one(st, max_positions=1)
    CP.dispatch(ENTRY)
    broker.accounts["tok-ann"]["gone"] = {_ticket(broker)}                # closed at the broker, software not yet out
    CP._live_cache.clear()
    CP.dispatch(dict(ENTRY, trade_id="T2"))
    assert len(broker.orders["tok-ann"]) == 2                             # the phantom did not block the new trade
    assert len(st.events("ann", "CLOSED")) == 1                           # and it was settled on the way
    assert not [r for r in st.events("ann", "SKIPPED") if r["reason"].startswith("MAX_OPEN_POSITIONS")]


def test_the_cap_still_holds_for_positions_that_are_really_open(rig):
    broker, st, _ = rig; _one(st, max_positions=1)
    CP.dispatch(ENTRY); CP.dispatch(dict(ENTRY, trade_id="T2"))
    assert len(broker.orders["tok-ann"]) == 1
    skipped = st.events("ann", "SKIPPED")
    assert skipped and skipped[0]["reason"] == "MAX_OPEN_POSITIONS: 1 open, your limit is 1"


def test_when_the_broker_cannot_be_read_the_cap_fails_closed(rig, monkeypatch):
    broker, st, _ = rig; _one(st, max_positions=1)
    CP.dispatch(ENTRY)
    monkeypatch.setattr(broker, "positions", lambda **k: {"ok": False, "reason": "TRANSPORT"})
    CP._live_cache.clear()
    CP.dispatch(dict(ENTRY, trade_id="T2"))
    assert len(broker.orders["tok-ann"]) == 1                             # the log's count is used: no new risk on a guess


# ---------------------------------------------------------------- margin
def test_not_enough_margin_skips_with_a_plain_reason_and_resumes_by_itself(rig):
    broker, st, _ = rig; _one(st)
    broker.accounts["tok-ann"].update(margin_free=100.0, margin_need=5000.0)
    CP.dispatch(ENTRY)
    assert broker.orders["tok-ann"] == []
    r = st.events("ann", "SKIPPED")[0]["reason"]
    assert r == "NOT_ENOUGH_MARGIN: this order needs 5000.00 and 100.00 is free"
    broker.accounts["tok-ann"]["margin_free"] = 9000.0                    # margin frees up
    CP.dispatch(dict(ENTRY, trade_id="T2"))
    assert len(broker.orders["tok-ann"]) == 1                             # the very next signal is sent - nothing to reset


def test_a_broker_refusal_for_money_is_recorded_as_not_enough_margin(rig, monkeypatch):
    broker, st, _ = rig; _one(st)
    monkeypatch.setattr(broker, "market_order", lambda *a, **k: {"ok": False, "retcode_text": "NO_RETRY", "comment": "Not enough money"})
    CP.dispatch(ENTRY)
    assert not st.events("ann", "REJECTED")
    assert st.events("ann", "SKIPPED")[0]["reason"].startswith("NOT_ENOUGH_MARGIN: the broker refused")


def test_an_unreadable_margin_figure_does_not_block_the_order(rig, monkeypatch):
    broker, st, _ = rig; _one(st)
    monkeypatch.setattr(broker, "required_margin", lambda *a, **k: {"ok": False, "reason": "TRANSPORT"})
    CP.dispatch(ENTRY)
    assert len(broker.orders["tok-ann"]) == 1


# ---------------------------------------------------------------- closes: verified, single-flight, swept
def test_two_closes_for_one_ticket_never_run_at_once(rig):
    broker, st, _ = rig; _one(st)
    CP.dispatch(ENTRY)
    row = st.open_tickets("ann")[0]
    assert CP._begin_close("ann", row["ticket"]) is True                  # the sweep is closing it right now
    try:
        assert CP.close_row("ann", row, "SCORE_EXIT") == {"ok": False, "reason": "CLOSE_IN_PROGRESS"}
        assert broker.closes["tok-ann"] == []
    finally:
        CP._end_close("ann", row["ticket"])
    assert CP.close_row("ann", row, "SCORE_EXIT")["ok"] is True
    assert broker.closes["tok-ann"] == [row["ticket"]]


def test_the_end_of_day_sweep_leaves_nothing_open(rig, monkeypatch):
    broker, st, _ = rig; _one(st)
    CP.dispatch(ENTRY); CP.dispatch(dict(ENTRY, trade_id="T2"))
    phantom, real = _ticket(broker, i=0), _ticket(broker, i=1)
    broker.accounts["tok-ann"]["gone"] = {phantom}
    monkeypatch.setattr(st, "all_accounts", lambda: [{"username": "ann"}], raising=False)
    res = CP.sweep_now("EOD_SWEEP")
    assert res["failed"] == 0 and res["closed"] == 2
    assert st.open_tickets("ann") == []
    assert broker.closes["tok-ann"] == [real]                             # only the position that exists was closed
    assert CP.sweep_now("EOD_SWEEP") == {"closed": 0, "failed": 0}         # and a second pass has nothing to do


# ---------------------------------------------------------------- queues, skips and timing
def test_one_users_slow_broker_does_not_delay_another_users_close(rig, monkeypatch):
    broker, st, _ = rig
    monkeypatch.setattr(st, "not_copying_accounts", lambda: [], raising=False)
    CP.dispatch(ENTRY)                                                    # both users hold the position
    gate = threading.Event()
    real_close = broker.close_position

    def slow(ticket, **kw):
        if kw.get("token") == "tok-ann":
            gate.wait(5.0)                                                # ann's broker hangs
        return real_close(ticket, **kw)
    monkeypatch.setattr(broker, "close_position", slow)
    monkeypatch.setattr(CP, "_user_queues", {})
    assert CP.fan_out(dict(EXIT, _queued_at=time.time())) == 2
    deadline = time.time() + 3.0
    while time.time() < deadline and not st.events("bob", "CLOSED"):
        time.sleep(0.02)
    assert st.events("bob", "CLOSED") and not st.events("ann", "CLOSED")  # bob is out while ann is still waiting
    gate.set()
    deadline = time.time() + 3.0
    while time.time() < deadline and not st.events("ann", "CLOSED"):
        time.sleep(0.02)
    assert st.events("ann", "CLOSED")


def test_a_connected_account_that_is_not_trading_gets_the_reason_in_its_own_log(rig, monkeypatch):
    broker, st, _ = rig; st.users = []
    monkeypatch.setattr(st, "not_copying_accounts", lambda: [{"username": "cat", "shares": 10, "why": "AUTO_TRADE_OFF"},
                                                             {"username": "dan", "shares": 10, "why": "PAUSED_BY_OPERATOR"}], raising=False)
    CP.fan_out(dict(ENTRY))
    CP.fan_out(dict(EXIT))                                                # an exit is not a missed entry: nothing written
    assert [r["reason"] for r in st.events("cat", "SKIPPED")] == ["AUTO_TRADE_OFF"]
    assert [r["reason"] for r in st.events("dan", "SKIPPED")] == ["PAUSED_BY_OPERATOR"]
    assert broker.orders == {"tok-ann": [], "tok-bob": []}                # and no broker was called for them


def test_every_row_carries_how_long_each_step_took(rig):
    broker, st, _ = rig; _one(st)
    CP._for_user(st.users[0], dict(ENTRY, _queued_at=time.time() - 2.0))
    t = json.loads(st.events("ann", "FILLED")[0]["timing"])
    assert t["queue_wait_ms"] >= 1900
    assert {"account_ms", "margin_ms", "quote_ms", "rate_gate_ms", "order_ms"} <= set(t)   # no positions read: nothing was open
    CP._for_user(st.users[0], dict(EXIT, _queued_at=time.time()))
    t = json.loads(st.events("ann", "CLOSED")[0]["timing"])
    assert {"queue_wait_ms", "quote_ms", "rate_gate_ms", "close_ms"} <= set(t)
