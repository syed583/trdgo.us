"""MT5 auto-trading (mt5_bridge + mt5_autotrade + mt5_routes) against a FAKE mtapi.io HTTP endpoint: no network, no broker.

Pins the owner's policy: switch default OFF, unmapped never trades, exact paper quantity converted to lots with the contract
size and rounded DOWN to the broker step (below the minimum -> skip + log), BUY/SELL market orders with server-side SL/TP,
score exits close with up to 3 attempts then a logged failure, broker-side SL/TP detected and logged, rejections (HTTP 201
ExceptionResult) logged with the full reason and never retried forever, partial fills recorded, one position per symbol/side,
operator-only routes, password and session token never in the audit log or any response."""
from __future__ import annotations

import json
import urllib.parse

import pytest

import mt5_autotrade as AT
import mt5_bridge as B

TOKEN = "sess-9f3a-TOKEN"


class FakeMtapi:
    """Answers the mtapi.io paths the bridge uses, with scripted refusals per test."""

    def __init__(self):
        self.calls = []                       # (path, params)
        self.login_ok = True
        # SymbolParams = {symbol, symbolInfo, symbolGroup} (swagger): rules in symbolGroup (minLots/maxLots/lotsStep/tradeMode), contract in symbolInfo
        self.symbols = {"AAPL": {"symbol": "AAPL", "symbolInfo": {"contractSize": 1.0, "digits": 2, "tickSize": 0.01, "description": "Apple"}, "symbolGroup": {"groupName": "Stocks", "tradeMode": 4, "minLots": 1.0, "maxLots": 10000.0, "lotsStep": 1.0, "minVolume": 10000, "maxVolume": 100000000, "volumeStep": 10000}},
                        "TSLA.NAS": {"symbol": "TSLA.NAS", "symbolInfo": {"contractSize": 100.0, "digits": 2, "tickSize": 0.01}, "symbolGroup": {"groupName": "Stocks", "tradeMode": 4, "minLots": 0.1, "maxLots": 100.0, "lotsStep": 0.1}}}
        self.quotes = {"AAPL": (100.0, 100.1), "TSLA.NAS": (200.0, 200.2)}
        self.positions = {}
        self.deals = {}
        self.next_ticket = 500
        self.send_script = []                 # list of ExceptionResult dicts or None per OrderSendSafe
        self.close_script = []
        self.fill_fraction = 1.0

    def __call__(self, url, timeout=None):
        u = urllib.parse.urlparse(url)
        path = u.path.strip("/").split("/")[-1]
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        self.calls.append((path, q))
        if path == "ConnectEx":
            assert "id" not in q and q["user"] == "123" and q["server"] == "STGlobalMarketsLtd-Live-1"
            return (200, json.dumps(TOKEN)) if self.login_ok else (201, json.dumps({"code": 65538, "message": "Invalid account", "nodeName": "mt5-1", "stackTrace": "...", "user": 123}))
        if q.get("id") != TOKEN:
            return 201, json.dumps({"code": 1, "message": "Invalid token", "nodeName": "mt5-1", "user": 0})
        if path == "AccountSummary":
            return 200, json.dumps({"balance": 10000.0, "equity": 10010.0, "currency": "USD", "freeMargin": 9000.0, "leverage": 100, "type": "Demo", "name": "Demo", "company": "ST"})
        if path == "SymbolParams":
            d = self.symbols.get(q["symbol"])
            return (200, json.dumps(d)) if d else (201, json.dumps({"code": 4301, "message": "Symbol not found: " + q["symbol"], "nodeName": "mt5-1", "user": 123}))
        if path == "GetQuote":
            t = self.quotes.get(q["symbol"])
            return (200, json.dumps({"symbol": q["symbol"], "bid": t[0], "ask": t[1], "last": t[0], "time": "2026-11-11T15:00:00", "volume": 0})) if t else (201, json.dumps({"code": 2, "message": "No quote", "nodeName": "mt5-1"}))
        if path == "OrderSendSafe":
            code = self.send_script.pop(0) if self.send_script else None
            if code:
                return 201, json.dumps(code)
            self.next_ticket += 1
            lots = float(q["volume"]) * self.fill_fraction
            px = self.quotes[q["symbol"]][1 if q["operation"] == "0" else 0]
            self.positions[self.next_ticket] = {"ticket": self.next_ticket, "symbol": q["symbol"], "orderType": "Buy" if q["operation"] == "0" else "Sell", "lots": lots, "openPrice": px,
                                                "stopLoss": float(q.get("stoploss") or 0), "takeProfit": float(q.get("takeprofit") or 0), "profit": 0.0, "expertId": int(q["expertID"]), "comment": q.get("comment"), "openTime": "2026-11-11T15:00:00"}
            return 200, json.dumps(self.positions[self.next_ticket])
        if path == "OpenedOrders":
            return 200, json.dumps(list(self.positions.values()))
        if path == "OrderCloseSafe":
            code = self.close_script.pop(0) if self.close_script else None
            if code:
                return 201, json.dumps(code)
            p = self.positions.pop(int(q["ticket"]), None)
            if not p:
                return 201, json.dumps({"code": 10036, "message": "Position not found", "nodeName": "mt5-1"})
            return 200, json.dumps({"ticket": p["ticket"], "lots": p["lots"], "closePrice": self.quotes[p["symbol"]][0]})
        if path == "HistoryDealsByPositionId":
            return 200, json.dumps(self.deals.get(int(q["ticket"]), []))
        if path == "Disconnect":
            return 200, "true"
        return 404, "no such path"


@pytest.fixture
def fake(tmp_path, monkeypatch):
    m = FakeMtapi()
    B._reset_for_tests(m)
    monkeypatch.setattr(AT, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(AT, "CRED_FILE", tmp_path / "sec" / "mt5_credentials.json")
    monkeypatch.setenv("AUTO_TRADE", "1")
    for k in ("MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER", "MT5_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(AT, "software_symbols", lambda: ["AAPL", "TSLA", "NVDA"])
    AT._conn.update(connected=False, account=None, since=None, last_error=None, server=None, base_url=None)
    yield m
    B._reset_for_tests(None)
    AT._conn.update(connected=False, account=None, since=None, last_error=None, server=None, base_url=None)


def _audit():
    p = AT.STATE_DIR / AT.AUDIT_FILE
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []


def _connected(fake):
    r = AT.connect(123, "s3cret-pass", "STGlobalMarketsLtd-Live-1", base_url="https://mt5.mtapi.io/")
    assert r["ok"] and AT.status()["connected"], r
    AT.set_mapping("AAPL", "AAPL")
    AT.set_mapping("TSLA", "TSLA.NAS")
    return r


def entry(**kw):
    e = {"type": "ENTRY", "trade_id": 1, "strategy": "grok", "symbol": "AAPL", "side": "LONG", "shares": 99, "entry": 100.05, "stop": 98.0, "target": 104.0}
    e.update(kw)
    return e


def _sent(fake):
    return [q for p, q in fake.calls if p == "OrderSendSafe"]


def _closes(fake):
    return [q for p, q in fake.calls if p == "OrderCloseSafe"]


# ---------------------------------------------------------------- switch, connection, credentials
def test_master_switch_defaults_off_and_blocks_every_order(fake, monkeypatch):
    monkeypatch.delenv("AUTO_TRADE", raising=False)
    assert AT.auto_trade_enabled() is False
    _connected(fake)
    assert AT.on_paper_event(entry()) == {"ok": False, "reason": "AUTO_TRADE_OFF"} and _sent(fake) == []


def test_connect_uses_connectex_keeps_the_token_in_memory_and_never_logs_secrets(fake):
    _connected(fake)
    assert fake.calls[0][0] == "ConnectEx" and fake.calls[1] == ("AccountSummary", {"id": TOKEN})
    raw = AT.CRED_FILE.read_text(encoding="utf-8")
    assert "s3cret-pass" not in raw and TOKEN not in raw and AT.load_credentials()["password"] == "s3cret-pass" and AT.load_credentials()["base_url"] == "https://mt5.mtapi.io/"
    aud = (AT.STATE_DIR / AT.AUDIT_FILE).read_text(encoding="utf-8")
    assert "s3cret-pass" not in aud and TOKEN not in aud
    st = AT.status()
    assert st["connected"] and st["account"]["login"] == 123 and st["account"]["account_kind"] == "DEMO" and st["account"]["balance"] == 10000.0
    assert "s3cret-pass" not in json.dumps(st) and TOKEN not in json.dumps(st) and st["base_url"] == "https://mt5.mtapi.io/"
    AT.disconnect(forget=True)
    assert not AT.CRED_FILE.exists() and AT.status()["connected"] is False and B.connected() is False


def test_a_failed_login_is_reported_with_the_api_message_and_audited(fake):
    fake.login_ok = False
    r = AT.connect(123, "x", "STGlobalMarketsLtd-Live-1")
    assert not r["ok"] and r["reason"] == "API_ERROR" and "Invalid account" in r["detail"] and r["error_code"] == 65538 and AT.status()["connected"] is False
    assert any(a["event"] == "CONNECT_FAILED" and "Invalid account" in a["detail"] for a in _audit())


def test_a_transport_error_is_a_result_not_an_exception(fake, monkeypatch):
    _connected(fake)
    monkeypatch.setattr(B, "_http_get", lambda url, timeout=None: (_ for _ in ()).throw(OSError("network down")))
    r = B.account()
    assert not r["ok"] and r["reason"] == "TRANSPORT" and "network down" in r["detail"]


# ---------------------------------------------------------------- mapping
def test_mapping_verifies_against_the_broker_and_unmapped_never_trades(fake):
    assert AT.mapping_rows()["summary"] == "0 of 3 symbols mapped"
    _connected(fake)
    m = AT.mapping_rows()
    assert m["summary"] == "2 of 3 symbols mapped" and {r["symbol"]: r["status"] for r in m["rows"]} == {"AAPL": "MAPPED", "TSLA": "MAPPED", "NVDA": "UNMAPPED"}
    assert AT.set_mapping("NVDA", "NVDA.XYZ")["status"] == "UNVERIFIED"
    assert AT.on_paper_event(entry(symbol="NVDA"))["reason"] == "UNMAPPED" and _sent(fake) == []
    assert any(a["event"] == "SKIPPED" and a["symbol"] == "NVDA" and a["reason"] == "UNMAPPED" for a in _audit())
    assert AT.set_mapping("TSLA", "")["status"] == "UNMAPPED" and AT.mapped("TSLA") is None


def test_automap_tries_the_plain_ticker_then_suffixes(fake):
    AT.connect(123, "p", "STGlobalMarketsLtd-Live-1")
    r = AT.automap()
    assert {x["symbol"]: x["broker_symbol"] for x in r["mapped"]} == {"AAPL": "AAPL", "TSLA": "TSLA.NAS"}
    assert [x["symbol"] for x in r["unmapped"]] == ["NVDA"] and r["summary"] == "2 of 3 symbols mapped"


# ---------------------------------------------------------------- entries
def test_buy_signal_sends_a_market_buy_with_exact_quantity_in_lots_and_server_side_sl_tp(fake):
    _connected(fake)
    r = AT.on_paper_event(entry(shares=99))
    assert r["ok"] and len(_sent(fake)) == 1
    q = _sent(fake)[0]
    assert q["symbol"] == "AAPL" and q["operation"] == "0" and q["volume"] == "99.0" and q["stoploss"] == "98.0" and q["takeprofit"] == "104.0"
    assert q["expertID"] == str(AT.MAGIC) and "#1" in q["comment"] and q["id"] == TOKEN
    a = _audit()[-1]
    assert a["event"] == "FILLED" and a["quantity"] == 99.0 and a["ticket"] == r["ticket"] and a["action"] == "LONG" and a["detail"]["paper_shares"] == 99 and a["detail"]["contract_size"] == 1.0
    assert "1" in AT.open_positions()


def test_sell_signal_converts_shares_to_lots_with_the_contract_size_and_rounds_down(fake):
    _connected(fake)
    r = AT.on_paper_event(entry(trade_id=2, symbol="TSLA", side="SHORT", shares=47, stop=206.0, target=190.0))
    assert r["ok"] and _sent(fake)[-1]["symbol"] == "TSLA.NAS" and _sent(fake)[-1]["operation"] == "1"
    assert _sent(fake)[-1]["volume"] == "0.4"                                         # 47 shares / contract 100 = 0.47 lots -> step 0.1 -> 0.4
    a = _audit()[-1]
    assert a["quantity"] == 0.4 and a["detail"]["paper_shares"] == 47 and a["detail"]["lots"] == 0.4
    AT._save_positions({"version": 1, "open": {}})
    r3 = AT.on_paper_event(entry(trade_id=4, symbol="TSLA", side="SHORT", shares=7))         # 0.07 lots < min 0.1
    assert r3["reason"] == "BELOW_MIN_VOLUME" and len(_sent(fake)) == 1
    assert any(a["event"] == "SKIPPED" and "BELOW_MIN_VOLUME" in a["reason"] and "0.0700 lots" in a["reason"] for a in _audit())


def test_sizing_rules():
    assert AT.shares_to_lots(99, 1) == 99.0 and AT.shares_to_lots(47, 100) == 0.47 and AT.shares_to_lots(250, None) == 250.0
    assert AT.round_volume(99, 1, 1) == 99.0 and AT.round_volume(0.47, 0.1, 0.1) == 0.4 and AT.round_volume(0.07, 0.1, 0.1) is None
    assert AT.round_volume(15, 0.01, 0.01) == 15.0 and AT.round_volume(99, 1, 1, volume_max=50) == 50.0


def test_missing_volume_rules_are_a_logged_rejection_not_a_guess(fake):
    _connected(fake)
    fake.symbols["AAPL"] = {"symbol": "AAPL", "symbolInfo": {"contractSize": 1.0, "digits": 2}, "symbolGroup": {"tradeMode": 4}}
    r = AT.on_paper_event(entry())
    assert r["reason"] == "NO_VOLUME_RULES" and _sent(fake) == [] and _audit()[-1]["event"] == "REJECTED"


def test_one_position_per_symbol_and_side_and_no_opposite_direction_while_open(fake):
    _connected(fake)
    assert AT.on_paper_event(entry(trade_id=1, strategy="grok"))["ok"]
    assert AT.on_paper_event(entry(trade_id=2, strategy="meta"))["reason"] == "SKIPPED_DUPLICATE"
    assert AT.on_paper_event(entry(trade_id=3, strategy="kimi", side="SHORT", stop=104.0, target=96.0))["reason"] == "SKIPPED_OPPOSITE_OPEN"
    assert len(_sent(fake)) == 1


def test_a_rejection_is_logged_with_the_full_api_reason_and_not_retried(fake):
    _connected(fake)
    fake.send_script = [{"code": 10019, "message": "Not enough money (margin 9000 < 9900)", "nodeName": "mt5-1", "user": 123}]
    r = AT.on_paper_event(entry())
    assert not r["ok"] and len(_sent(fake)) == 1
    a = _audit()[-1]
    assert a["event"] == "REJECTED" and "Not enough money" in a["reason"] and "(10019)" in a["reason"] and a["detail"]["request"]["symbol"] == "AAPL"
    assert AT.open_positions() == {}


def test_a_requote_is_repriced_once_then_given_up(fake):
    _connected(fake)
    fake.send_script = [{"code": 10004, "message": "Requote", "nodeName": "mt5-1"}, {"code": 10004, "message": "Requote", "nodeName": "mt5-1"}]
    r = AT.on_paper_event(entry())
    assert not r["ok"] and len(_sent(fake)) == 2
    ev = [a["event"] for a in _audit()]
    assert ev.count("REPRICE") == 1 and ev[-1] == "REJECTED"
    fake.send_script = [{"code": 10020, "message": "Price changed", "nodeName": "mt5-1"}]
    assert AT.on_paper_event(entry(trade_id=9))["ok"] and len(_sent(fake)) == 4


def test_a_partial_fill_is_recorded_with_the_filled_lots(fake):
    _connected(fake)
    fake.fill_fraction = 0.5
    r = AT.on_paper_event(entry(trade_id=7))
    assert r["ok"] and r["volume"] == 49.5
    a = _audit()[-1]
    assert a["event"] == "PARTIAL_FILL" and a["quantity"] == 49.5 and "requested 99.0 lots, filled 49.5" in a["reason"]
    assert AT.open_positions()["7"]["volume"] == 49.5


def test_the_session_token_is_never_in_an_audit_record_even_on_failure(fake):
    _connected(fake)
    fake.send_script = [{"code": 1, "message": "boom", "nodeName": "mt5-1"}]
    AT.on_paper_event(entry())
    assert TOKEN not in (AT.STATE_DIR / AT.AUDIT_FILE).read_text(encoding="utf-8")


# ---------------------------------------------------------------- exits
def test_score_exit_closes_the_position_with_ordercloasesafe_and_is_audited(fake):
    _connected(fake)
    AT.on_paper_event(entry(trade_id=1))
    ticket = AT.open_positions()["1"]["ticket"]
    r = AT.on_paper_event({"type": "EXIT", "trade_id": 1, "strategy": "grok", "symbol": "AAPL", "side": "LONG", "shares": 99, "exit_price": 101.0, "reason": "SCORE_EXIT"})
    assert r["ok"] and r["attempts"] == 1 and len(_closes(fake)) == 1 and _closes(fake)[0]["ticket"] == str(ticket)
    assert AT.open_positions() == {} and _audit()[-1]["event"] == "CLOSED" and "SCORE_EXIT" in _audit()[-1]["reason"] and _audit()[-1]["price"] == 100.0


def test_close_retries_three_times_then_logs_a_failure(fake, monkeypatch):
    monkeypatch.setattr(AT.time, "sleep", lambda s: None)
    _connected(fake)
    AT.on_paper_event(entry(trade_id=1))
    fake.close_script = [{"code": 10004, "message": "Requote"}, {"code": 10021, "message": "Off quotes"}, {"code": 10012, "message": "Timeout"}]
    r = AT.on_paper_event({"type": "EXIT", "trade_id": 1, "symbol": "AAPL", "side": "LONG", "reason": "EOD_CLOSE"})
    assert not r["ok"] and r["reason"] == "CLOSE_FAILED" and len(_closes(fake)) == 3
    ev = [a["event"] for a in _audit()]
    assert ev.count("CLOSE_RETRY") == 3 and ev[-1] == "CLOSE_FAILED" and "STILL OPEN" in _audit()[-1]["reason"]
    assert "1" in AT.open_positions()
    fake.close_script = []
    assert AT.on_paper_event({"type": "EXIT", "trade_id": 1, "symbol": "AAPL", "side": "LONG", "reason": "EOD_CLOSE"})["ok"]


def test_a_no_retry_refusal_stops_the_close_loop_early(fake, monkeypatch):
    monkeypatch.setattr(AT.time, "sleep", lambda s: None)
    _connected(fake)
    AT.on_paper_event(entry(trade_id=1))
    fake.close_script = [{"code": 10018, "message": "Market closed"}]
    r = AT.on_paper_event({"type": "EXIT", "trade_id": 1, "symbol": "AAPL", "side": "LONG", "reason": "SCORE_EXIT"})
    assert not r["ok"] and len(_closes(fake)) == 1


def test_a_stop_loss_hit_on_the_broker_is_logged_not_closed_again(fake):
    _connected(fake)
    AT.on_paper_event(entry(trade_id=1))
    ticket = AT.open_positions()["1"]["ticket"]
    fake.positions.pop(ticket)
    fake.deals[ticket] = [{"ticketNumber": 776, "direction": 0, "type": 0, "price": 100.1, "volume": 990000, "profit": 0.0, "comment": "TRDGO grok #1"},
                          {"ticketNumber": 777, "direction": 1, "type": 1, "openTime": "2026-11-11T16:00:00", "price": 98.0, "volume": 990000, "profit": -200.0, "comment": "[sl 98.00]"}]
    r = AT.on_paper_event({"type": "EXIT", "trade_id": 1, "symbol": "AAPL", "side": "LONG", "reason": "STOP"})
    assert r == {"ok": True, "closed_by": "SL"} and _closes(fake) == []
    a = _audit()[-1]
    assert a["event"] == "CLOSED_BY_BROKER" and a["result"] == "SL" and a["price"] == 98.0 and AT.open_positions() == {}


def test_an_exit_for_a_trade_that_never_traded_is_a_no_op(fake):
    _connected(fake)
    assert AT.on_paper_event({"type": "EXIT", "trade_id": 42, "symbol": "AAPL", "side": "LONG", "reason": "SCORE_EXIT"}) == {"ok": True, "reason": "NO_MT5_POSITION"}
    assert _closes(fake) == []


def test_reconcile_notices_positions_the_broker_closed_meanwhile(fake):
    _connected(fake)
    AT.on_paper_event(entry(trade_id=1))
    ticket = AT.open_positions()["1"]["ticket"]
    fake.positions.pop(ticket)
    fake.deals[ticket] = [{"ticketNumber": 778, "direction": 1, "type": 1, "price": 104.0, "volume": 990000, "profit": 390.0, "comment": "[tp 104.00]"}]
    r = AT.reconcile()
    assert r["ok"] and r["closed_meanwhile"] == [{"trade_id": 1, "ticket": ticket, "reason": "TP"}] and AT.open_positions() == {}


def test_pause_file_blocks_entries_but_exits_still_close(fake):
    _connected(fake)
    AT.on_paper_event(entry(trade_id=1))
    AT.set_paused(True)
    assert AT.on_paper_event(entry(trade_id=2, symbol="TSLA", side="SHORT", shares=200))["reason"] == "PAUSED"
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
        assert r.status_code == 200 and r.json()["auto_trade"] is True and r.json()["connected"] is False and r.json()["base_url"] == "https://mt5.mtapi.io/"
        r = c.post("/api/mt5/connect", json={"login": "123", "password": "s3cret-pass", "server": "STGlobalMarketsLtd-Live-1", "base_url": "https://mt5.mtapi.io/"}, headers={"origin": "http://testserver"})
        assert r.status_code == 200 and r.json()["status"] == "OK" and "s3cret-pass" not in r.text and TOKEN not in r.text
        assert c.get("/api/mt5/symbols").json()["total"] == 3
        r = c.post("/api/mt5/symbols/map", json={"symbol": "aapl", "broker_symbol": "AAPL"}, headers={"origin": "http://testserver"})
        assert r.json()["status"] == "OK" and r.json()["map_status"] == "MAPPED"
        assert c.get("/api/mt5/audit?limit=5").json()["rows"][0]["event"] in ("MAPPED", "CONNECTED")
        assert c.post("/api/mt5/disconnect", json={}, headers={"origin": "http://testserver"}).json()["connected"] is False


def test_the_tracker_emits_entry_and_exit_events_to_registered_hooks(fake):
    import demo_trade_service as dts
    seen = []
    dts.HOOKS.append(seen.append)
    bad = lambda ev: (_ for _ in ()).throw(RuntimeError("boom"))  # noqa: E731
    try:
        dts._emit([{"type": "ENTRY", "trade_id": 1}])
        assert seen == [{"type": "ENTRY", "trade_id": 1}]
        dts.HOOKS.append(bad)
        dts._emit([{"type": "EXIT", "trade_id": 1}])
        assert len(seen) == 2
    finally:
        dts.HOOKS[:] = [h for h in dts.HOOKS if h is not seen.append and h is not bad]
