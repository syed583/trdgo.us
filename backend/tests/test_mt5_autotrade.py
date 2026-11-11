"""MT5 auto-trading (mt5_bridge + mt5_autotrade + mt5_routes) against a FAKE MetaTrader5 module: no terminal, no broker.

Pins the owner's policy: switch default OFF, unmapped never trades, exact paper quantity rounded DOWN to the broker step (below
the minimum -> skip + log), BUY/SELL market orders with server-side SL/TP, score exits close with up to 3 attempts then a
logged failure, broker-side SL/TP detected and logged, rejections logged with the full reason and never retried forever,
partial fills recorded, one position per symbol/side, operator-only routes, password never in the audit log."""
from __future__ import annotations

import json
import types

import pytest

import mt5_autotrade as AT
import mt5_bridge as B


class FakeMT5:
    """The subset of the MetaTrader5 package the bridge uses, with scripted behaviour per test."""
    TRADE_ACTION_DEAL = 1
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
    ORDER_TIME_GTC = 0
    POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1
    DEAL_ENTRY_OUT = 1
    DEAL_REASON_SL, DEAL_REASON_TP, DEAL_REASON_SO = 4, 5, 6
    TRADE_RETCODE_DONE, TRADE_RETCODE_DONE_PARTIAL = 10009, 10010

    def __init__(self):
        self.symbols = {"AAPL": {"visible": True, "trade_mode": 4, "volume_min": 1.0, "volume_max": 10000.0, "volume_step": 1.0, "digits": 2, "point": 0.01, "filling_mode": 2, "bid": 100.0, "ask": 100.1},
                        "TSLA.NAS": {"visible": True, "trade_mode": 4, "volume_min": 10.0, "volume_max": 10000.0, "volume_step": 10.0, "digits": 2, "point": 0.01, "filling_mode": 1, "bid": 200.0, "ask": 200.2}}
        self.ticks = {"AAPL": (100.0, 100.1), "TSLA.NAS": (200.0, 200.2)}
        self.sent, self.checks, self.closes = [], [], []
        self.positions = {}
        self.deals = {}
        self.next_ticket = 500
        self.send_script = []            # retcodes to answer in order (None = DONE)
        self.close_script = []
        self.login_ok = True
        self.account = {"login": 123, "server": "STGlobalMarketsLtd-Live-1", "name": "Demo", "company": "ST", "currency": "USD", "balance": 10000.0, "equity": 10000.0, "margin": 0.0, "margin_free": 10000.0, "leverage": 100, "trade_allowed": True, "trade_mode": 0, "margin_mode": 0}

    # session
    def initialize(self, *a, **kw):
        return self.login_ok

    def login(self, login, password=None, server=None):
        return self.login_ok

    def shutdown(self):
        return True

    def last_error(self):
        return (-1, "fake error")

    def account_info(self):
        return types.SimpleNamespace(**self.account) if self.login_ok else None

    def terminal_info(self):
        return types.SimpleNamespace(connected=True, trade_allowed=True, tradeapi_disabled=False, ping_last=12000, company="ST", name="MT5", path="C:/mt5", build=4000)

    # symbols
    def symbol_info(self, sym):
        d = self.symbols.get(sym)
        return types.SimpleNamespace(**d) if d else None

    def symbol_select(self, sym, flag):
        return sym in self.symbols

    def symbol_info_tick(self, sym):
        t = self.ticks.get(sym)
        return types.SimpleNamespace(bid=t[0], ask=t[1], time=1_700_000_000) if t else None

    # orders
    def order_check(self, req):
        self.checks.append(dict(req))
        return types.SimpleNamespace(retcode=0, comment="ok", request_id=1)

    def order_send(self, req):
        if req.get("position"):
            self.closes.append(dict(req))
            code = self.close_script.pop(0) if self.close_script else None
            if code:
                return types.SimpleNamespace(retcode=code, comment="refused %s" % code, deal=0, order=0, volume=0.0, price=0.0, request_id=2)
            self.positions.pop(int(req["position"]), None)
            return types.SimpleNamespace(retcode=10009, comment="closed", deal=900, order=901, volume=req["volume"], price=req["price"], request_id=2)
        self.sent.append(dict(req))
        code = self.send_script.pop(0) if self.send_script else None
        if code:
            return types.SimpleNamespace(retcode=code, comment="refused %s" % code, deal=0, order=0, volume=0.0, price=0.0, request_id=3)
        self.next_ticket += 1
        vol = req["volume"]
        if req.get("comment", "").startswith("TRDGO half"):
            vol = req["volume"] / 2
        self.positions[self.next_ticket] = {"ticket": self.next_ticket, "symbol": req["symbol"], "type": req["type"], "volume": vol, "price_open": req["price"], "sl": req.get("sl"), "tp": req.get("tp"), "profit": 0.0, "magic": req["magic"], "comment": req.get("comment"), "time": 1_700_000_000}
        return types.SimpleNamespace(retcode=10010 if vol != req["volume"] else 10009, comment="done", deal=self.next_ticket, order=self.next_ticket, volume=vol, price=req["price"], request_id=3)

    def positions_get(self, symbol=None, ticket=None):
        rows = [types.SimpleNamespace(**p) for p in self.positions.values() if (symbol is None or p["symbol"] == symbol) and (ticket is None or p["ticket"] == ticket)]
        return rows

    def history_deals_get(self, *a, position=None, **kw):
        return [types.SimpleNamespace(**d) for d in self.deals.get(position, [])]


@pytest.fixture
def fake(tmp_path, monkeypatch):
    m = FakeMT5()
    B._reset_for_tests(m)
    monkeypatch.setattr(AT, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(AT, "CRED_FILE", tmp_path / "sec" / "mt5_credentials.json")
    monkeypatch.setenv("AUTO_TRADE", "1")
    monkeypatch.delenv("MT5_LOGIN", raising=False)
    monkeypatch.delenv("MT5_PASSWORD", raising=False)
    monkeypatch.setattr(AT, "software_symbols", lambda: ["AAPL", "TSLA", "NVDA"])
    AT._conn.update(connected=False, account=None, since=None, last_error=None, server=None)
    yield m
    B._reset_for_tests(None)
    AT._conn.update(connected=False, account=None, since=None, last_error=None, server=None)


def _audit(tmp_path):
    p = AT.STATE_DIR / AT.AUDIT_FILE
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []


def _connected(fake):
    r = AT.connect(123, "s3cret-pass", "STGlobalMarketsLtd-Live-1")
    assert r["ok"] and AT.status()["connected"]
    AT.set_mapping("AAPL", "AAPL")
    AT.set_mapping("TSLA", "TSLA.NAS")
    return r


def entry(**kw):
    e = {"type": "ENTRY", "trade_id": 1, "strategy": "grok", "symbol": "AAPL", "side": "LONG", "shares": 99, "entry": 100.05, "stop": 98.0, "target": 104.0}
    e.update(kw)
    return e


# ---------------------------------------------------------------- switch, connection, credentials
def test_master_switch_defaults_off_and_blocks_every_order(fake, monkeypatch, tmp_path):
    monkeypatch.delenv("AUTO_TRADE", raising=False)
    assert AT.auto_trade_enabled() is False
    _connected(fake)
    assert AT.on_paper_event(entry()) == {"ok": False, "reason": "AUTO_TRADE_OFF"} and fake.sent == []


def test_connect_stores_credentials_obfuscated_and_never_logs_the_password(fake, tmp_path):
    _connected(fake)
    raw = AT.CRED_FILE.read_text(encoding="utf-8")
    assert "s3cret-pass" not in raw and AT.load_credentials()["password"] == "s3cret-pass"
    assert "s3cret-pass" not in (AT.STATE_DIR / AT.AUDIT_FILE).read_text(encoding="utf-8")
    st = AT.status()
    assert st["connected"] and st["account"]["login"] == 123 and st["account"]["account_kind"] == "DEMO" and "password" not in json.dumps(st)
    AT.disconnect(forget=True)
    assert not AT.CRED_FILE.exists() and AT.status()["connected"] is False


def test_a_failed_login_is_reported_and_audited(fake):
    fake.login_ok = False
    r = AT.connect(123, "x", "STGlobalMarketsLtd-Live-1")
    assert not r["ok"] and r["reason"] == "INITIALIZE_FAILED" and AT.status()["connected"] is False
    assert any(a["event"] == "CONNECT_FAILED" for a in _audit(None))


def test_without_the_package_everything_answers_not_installed(fake, monkeypatch):
    B._reset_for_tests(None)
    monkeypatch.setattr(B, "_load", lambda: False)
    assert AT.status()["installed"] is False and "pip install MetaTrader5" in AT.status()["install_note"]
    assert AT.connect(1, "p", "s")["reason"] == "NOT_INSTALLED"


# ---------------------------------------------------------------- mapping
def test_mapping_verifies_against_the_broker_and_unmapped_never_trades(fake):
    rows = AT.mapping_rows()
    assert rows["summary"] == "0 of 3 symbols mapped"
    _connected(fake)
    m = AT.mapping_rows()
    assert m["mapped"] == 2 and m["summary"] == "2 of 3 symbols mapped"
    assert {r["symbol"]: r["status"] for r in m["rows"]} == {"AAPL": "MAPPED", "TSLA": "MAPPED", "NVDA": "UNMAPPED"}
    assert AT.set_mapping("NVDA", "NVDA.XYZ")["status"] == "UNVERIFIED"                      # the broker does not offer it
    assert AT.on_paper_event(entry(symbol="NVDA"))["reason"] == "UNMAPPED" and fake.sent == []
    assert any(a["event"] == "SKIPPED" and a["symbol"] == "NVDA" and a["reason"] == "UNMAPPED" for a in _audit(None))
    assert AT.set_mapping("TSLA", "")["status"] == "UNMAPPED" and AT.mapped("TSLA") is None


def test_automap_tries_the_plain_ticker_then_suffixes(fake):
    AT.connect(123, "p", "STGlobalMarketsLtd-Live-1")
    r = AT.automap()
    assert {x["symbol"]: x["broker_symbol"] for x in r["mapped"]} == {"AAPL": "AAPL", "TSLA": "TSLA.NAS"}
    assert [x["symbol"] for x in r["unmapped"]] == ["NVDA"] and r["summary"] == "2 of 3 symbols mapped"


# ---------------------------------------------------------------- entries
def test_buy_signal_sends_a_market_buy_with_exact_quantity_and_server_side_sl_tp(fake):
    _connected(fake)
    r = AT.on_paper_event(entry(shares=99))
    assert r["ok"] and len(fake.sent) == 1
    req = fake.sent[0]
    assert req["symbol"] == "AAPL" and req["type"] == FakeMT5.ORDER_TYPE_BUY and req["volume"] == 99.0 and req["sl"] == 98.0 and req["tp"] == 104.0
    assert req["price"] == 100.1 and req["magic"] == AT.MAGIC and req["type_filling"] == FakeMT5.ORDER_FILLING_IOC
    assert len(fake.checks) == 1                                                          # validated before sending
    a = _audit(None)[-1]
    assert a["event"] == "FILLED" and a["quantity"] == 99.0 and a["ticket"] == r["ticket"] and a["action"] == "LONG"
    assert str(1) in AT.open_positions()


def test_sell_signal_sends_a_market_sell_and_volume_rounds_down_to_the_broker_step(fake):
    _connected(fake)
    r = AT.on_paper_event(entry(trade_id=2, symbol="TSLA", side="SHORT", shares=47, stop=206.0, target=190.0))
    assert r["ok"] and fake.sent[-1]["symbol"] == "TSLA.NAS" and fake.sent[-1]["type"] == FakeMT5.ORDER_TYPE_SELL
    assert fake.sent[-1]["volume"] == 40.0 and fake.sent[-1]["price"] == 200.0 and fake.sent[-1]["type_filling"] == FakeMT5.ORDER_FILLING_FOK
    r2 = AT.on_paper_event(entry(trade_id=3, symbol="TSLA", side="SHORT", shares=7))
    assert r2["reason"] == "SKIPPED_DUPLICATE"
    AT._positions()["open"].clear()
    AT._save_positions({"version": 1, "open": {}})
    r3 = AT.on_paper_event(entry(trade_id=4, symbol="TSLA", side="SHORT", shares=7))             # below the 10-share minimum
    assert r3["reason"] == "BELOW_MIN_VOLUME" and len(fake.sent) == 1
    assert any(a["event"] == "SKIPPED" and "BELOW_MIN_VOLUME" in a["reason"] for a in _audit(None))


def test_round_volume_rules():
    assert AT.round_volume(99, 1, 1) == 99.0 and AT.round_volume(47, 10, 10) == 40.0 and AT.round_volume(7, 10, 10) is None
    assert AT.round_volume(15, 0.01, 0.01) == 15.0 and AT.round_volume(0.5, 1, 1) is None and AT.round_volume(99, 1, 1, volume_max=50) == 50.0


def test_one_position_per_symbol_and_side_and_no_opposite_direction_while_open(fake):
    _connected(fake)
    assert AT.on_paper_event(entry(trade_id=1, strategy="grok"))["ok"]
    assert AT.on_paper_event(entry(trade_id=2, strategy="meta"))["reason"] == "SKIPPED_DUPLICATE"
    assert AT.on_paper_event(entry(trade_id=3, strategy="kimi", side="SHORT", stop=104.0, target=96.0))["reason"] == "SKIPPED_OPPOSITE_OPEN"
    assert len(fake.sent) == 1


def test_a_rejection_is_logged_with_the_full_reason_and_not_retried_forever(fake):
    _connected(fake)
    fake.send_script = [10019]                                                                # NO_MONEY
    r = AT.on_paper_event(entry())
    assert not r["ok"] and len(fake.sent) == 1
    a = _audit(None)[-1]
    assert a["event"] == "REJECTED" and "NO_MONEY (10019)" in a["reason"] and a["detail"]["request"]["symbol"] == "AAPL"
    assert AT.open_positions() == {}


def test_a_requote_is_repriced_once_then_given_up(fake):
    _connected(fake)
    fake.send_script = [10004, 10004]
    r = AT.on_paper_event(entry())
    assert not r["ok"] and len(fake.sent) == 2
    ev = [a["event"] for a in _audit(None)]
    assert ev.count("REPRICE") == 1 and ev[-1] == "REJECTED"
    fake.send_script = [10020]
    assert AT.on_paper_event(entry(trade_id=9))["ok"] and len(fake.sent) == 4                 # price changed once, second try filled


def test_a_partial_fill_is_recorded_with_the_filled_volume(fake):
    _connected(fake)
    r = AT.on_paper_event(entry(trade_id=7, strategy="half"))                                 # the fake fills half when the comment starts with 'TRDGO half'
    assert r["ok"] and r["volume"] == 49.5
    a = _audit(None)[-1]
    assert a["event"] == "PARTIAL_FILL" and a["quantity"] == 49.5 and "requested 99.0, filled 49.5" in a["reason"]
    assert AT.open_positions()["7"]["volume"] == 49.5


# ---------------------------------------------------------------- exits
def test_score_exit_closes_the_position_and_is_audited(fake):
    _connected(fake)
    AT.on_paper_event(entry(trade_id=1))
    ticket = AT.open_positions()["1"]["ticket"]
    r = AT.on_paper_event({"type": "EXIT", "trade_id": 1, "strategy": "grok", "symbol": "AAPL", "side": "LONG", "shares": 99, "exit_price": 101.0, "reason": "SCORE_EXIT"})
    assert r["ok"] and r["attempts"] == 1 and len(fake.closes) == 1 and fake.closes[0]["position"] == ticket and fake.closes[0]["type"] == FakeMT5.ORDER_TYPE_SELL
    assert AT.open_positions() == {} and _audit(None)[-1]["event"] == "CLOSED" and "SCORE_EXIT" in _audit(None)[-1]["reason"]


def test_close_retries_three_times_then_logs_a_failure(fake, monkeypatch):
    monkeypatch.setattr(AT.time, "sleep", lambda s: None)
    _connected(fake)
    AT.on_paper_event(entry(trade_id=1))
    fake.close_script = [10004, 10021, 10012]
    r = AT.on_paper_event({"type": "EXIT", "trade_id": 1, "symbol": "AAPL", "side": "LONG", "reason": "EOD_CLOSE"})
    assert not r["ok"] and r["reason"] == "CLOSE_FAILED" and len(fake.closes) == 3
    ev = [a["event"] for a in _audit(None)]
    assert ev.count("CLOSE_RETRY") == 3 and ev[-1] == "CLOSE_FAILED" and "STILL OPEN" in _audit(None)[-1]["reason"]
    assert "1" in AT.open_positions()                                                        # kept in the ledger so reconcile / a later exit can act
    fake.close_script = []
    assert AT.on_paper_event({"type": "EXIT", "trade_id": 1, "symbol": "AAPL", "side": "LONG", "reason": "EOD_CLOSE"})["ok"]


def test_a_no_retry_rejection_stops_the_close_loop_early(fake, monkeypatch):
    monkeypatch.setattr(AT.time, "sleep", lambda s: None)
    _connected(fake)
    AT.on_paper_event(entry(trade_id=1))
    fake.close_script = [10018]                                                               # MARKET_CLOSED
    r = AT.on_paper_event({"type": "EXIT", "trade_id": 1, "symbol": "AAPL", "side": "LONG", "reason": "SCORE_EXIT"})
    assert not r["ok"] and len(fake.closes) == 1


def test_a_stop_loss_hit_on_mt5_is_logged_not_closed_again(fake):
    _connected(fake)
    AT.on_paper_event(entry(trade_id=1))
    ticket = AT.open_positions()["1"]["ticket"]
    fake.positions.pop(ticket)                                                                # the broker closed it ...
    fake.deals[ticket] = [{"ticket": 777, "entry": 1, "reason": 4, "time": 1_700_000_500, "price": 98.0, "volume": 99.0, "profit": -200.0, "comment": "[sl 98.00]"}]
    r = AT.on_paper_event({"type": "EXIT", "trade_id": 1, "symbol": "AAPL", "side": "LONG", "reason": "STOP"})
    assert r == {"ok": True, "closed_by": "SL"} and fake.closes == []
    a = _audit(None)[-1]
    assert a["event"] == "CLOSED_BY_BROKER" and a["result"] == "SL" and a["price"] == 98.0 and AT.open_positions() == {}


def test_an_exit_for_a_trade_that_never_traded_on_mt5_is_a_no_op(fake):
    _connected(fake)
    assert AT.on_paper_event({"type": "EXIT", "trade_id": 42, "symbol": "AAPL", "side": "LONG", "reason": "SCORE_EXIT"}) == {"ok": True, "reason": "NO_MT5_POSITION"}
    assert fake.closes == []


def test_reconcile_notices_positions_the_broker_closed_meanwhile(fake):
    _connected(fake)
    AT.on_paper_event(entry(trade_id=1))
    ticket = AT.open_positions()["1"]["ticket"]
    fake.positions.pop(ticket)
    fake.deals[ticket] = [{"ticket": 778, "entry": 1, "reason": 5, "time": 1_700_000_900, "price": 104.0, "volume": 99.0, "profit": 390.0, "comment": "[tp 104.00]"}]
    r = AT.reconcile()
    assert r["ok"] and r["closed_meanwhile"] == [{"trade_id": 1, "ticket": ticket, "reason": "TP"}] and AT.open_positions() == {}


def test_pause_file_blocks_entries_but_exits_still_close(fake):
    _connected(fake)
    AT.on_paper_event(entry(trade_id=1))
    AT.set_paused(True)
    assert AT.on_paper_event(entry(trade_id=2, symbol="TSLA", side="SHORT", shares=20))["reason"] == "PAUSED"
    assert AT.on_paper_event({"type": "EXIT", "trade_id": 1, "symbol": "AAPL", "side": "LONG", "reason": "SCORE_EXIT"})["ok"]
    AT.set_paused(False)
    assert AT.paused() is False


# ---------------------------------------------------------------- routes
def test_routes_are_operator_only_and_wired_into_the_app(fake, monkeypatch):
    from fastapi.testclient import TestClient
    import auth_service as auth
    import main
    with TestClient(main.app) as c:
        monkeypatch.setattr(auth, "current_user", lambda request: {"username": "someone", "role": "user"})
        monkeypatch.setattr(auth, "require_session", lambda request: None)
        assert c.get("/api/mt5/status").status_code == 403
        monkeypatch.setenv("MT5_OPERATOR", "someone")
        r = c.get("/api/mt5/status")
        assert r.status_code == 200 and r.json()["auto_trade"] is True and r.json()["connected"] is False
        r = c.post("/api/mt5/connect", json={"login": "123", "password": "s3cret-pass", "server": "STGlobalMarketsLtd-Live-1"}, headers={"origin": "http://testserver"})
        assert r.status_code == 200 and r.json()["status"] == "OK" and "s3cret-pass" not in r.text
        r = c.get("/api/mt5/symbols")
        assert r.status_code == 200 and r.json()["total"] == 3
        r = c.post("/api/mt5/symbols/map", json={"symbol": "aapl", "broker_symbol": "AAPL"}, headers={"origin": "http://testserver"})
        assert r.json()["status"] == "OK" and r.json()["symbol"] == "AAPL"
        r = c.get("/api/mt5/audit?limit=5")
        assert r.status_code == 200 and r.json()["rows"][0]["event"] in ("MAPPED", "CONNECTED")
        assert c.post("/api/mt5/disconnect", json={}, headers={"origin": "http://testserver"}).json()["connected"] is False


def test_the_tracker_emits_entry_and_exit_events_to_registered_hooks(fake):
    import demo_trade_service as dts
    seen = []
    dts.HOOKS.append(seen.append)
    try:
        dts._emit([{"type": "ENTRY", "trade_id": 1}])
        assert seen == [{"type": "ENTRY", "trade_id": 1}]
        bad = lambda ev: (_ for _ in ()).throw(RuntimeError("boom"))  # noqa: E731
        dts.HOOKS.append(bad)
        dts._emit([{"type": "EXIT", "trade_id": 1}])                                          # a failing hook never raises into the tracker
        assert len(seen) == 2
    finally:
        dts.HOOKS[:] = [h for h in dts.HOOKS if h is not seen.append and h is not bad]
