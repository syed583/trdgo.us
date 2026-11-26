"""Kit V, part A: the paid MT5 gateway. The licence key is a secret - it goes out in ONE header to ONE host."""
import io
import json
import logging

import pytest

import mt5_bridge as B

KEY = "test-key-0000-not-a-real-licence"
HOST = "https://paid.gateway.test/"


class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture()
def wire(monkeypatch):
    """Capture every outgoing request; answer from a queue of (status, body)."""
    sent, answers = [], []

    def fake_urlopen(req, timeout=None):
        sent.append({"url": req.full_url, "headers": {k.lower(): v for k, v in req.header_items()}})
        status, body = answers.pop(0) if answers else (200, '"OK"')
        r = _Resp(body.encode("utf-8")); r.status = status
        return r

    monkeypatch.setattr(B.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(B, "_http_get", B._real_http_get)
    monkeypatch.setenv(B.GATEWAY_URL_ENV, HOST)
    monkeypatch.setenv(B.GATEWAY_KEY_ENV, KEY)
    monkeypatch.delenv(B.ALERT_WEBHOOK_ENV, raising=False)
    B._gw.update(ok=True, kind=None, detail=None, since=None, last=None, count=0, alerted_at=0.0)
    yield sent, answers
    B._gw.update(ok=True, kind=None, detail=None, since=None, last=None, count=0, alerted_at=0.0)


def test_every_call_goes_to_the_paid_host_with_the_key_in_the_header_only(wire):
    sent, _ = wire
    # a caller's own base URL and the operator's saved one are both overridden by the configured host
    B._call("AccountSummary", token="user-session", base_url="https://mt5.mtapi.io/")
    B._call("OpenedOrders", token="user-session", strict_token=True)
    B._call("Ping", with_token=False)
    assert len(sent) == 3
    for s in sent:
        assert s["url"].startswith(HOST)
        assert s["headers"].get("apikey") == KEY
        assert KEY not in s["url"]                       # never a query parameter


def test_the_key_is_not_sent_to_any_other_host(wire, monkeypatch):
    sent, _ = wire
    B._real_http_get("https://somewhere-else.test/AccountSummary?id=x")
    assert "apikey" not in sent[-1]["headers"]
    monkeypatch.delenv(B.GATEWAY_URL_ENV)                # no paid host configured: the trial host, and no key
    B._call("Ping", with_token=False)
    assert sent[-1]["url"].startswith(B.DEFAULT_BASE_URL) and "apikey" not in sent[-1]["headers"]


def test_the_key_never_reaches_a_result_or_a_log_line(wire, caplog):
    _, answers = wire
    # a provider that echoes the request back inside its error
    answers.append((201, json.dumps({"errorCode": 5, "message": "bad key %s" % KEY, "details": "ApiKey=%s" % KEY})))
    answers.append((500, "upstream said %s" % KEY))
    with caplog.at_level(logging.DEBUG):
        r1 = B._call("AccountSummary", token="t")
        r2 = B._call("AccountSummary", token="t")
        B.gateway_status(); B.gateway_health()
    assert KEY not in json.dumps([r1, r2]) and "***" in r1["detail"]
    assert KEY not in caplog.text
    assert KEY not in json.dumps(B.gateway_status())


def test_only_a_gateway_problem_is_a_gateway_problem():
    assert B.problem_kind({"ok": False, "reason": "API_ERROR",
                           "detail": "CloudTrial failed(mt5rest.1.2.3.4): Trial expired. Please purchase full version."}) == "LICENCE"
    assert B.problem_kind({"ok": False, "reason": "HTTP_401", "detail": ""}) == "LICENCE"
    assert B.problem_kind({"ok": False, "reason": "TRANSPORT", "detail": "TimeoutError: timed out"}) == "GATEWAY_DOWN"
    assert B.problem_kind({"ok": False, "reason": "HTTP_503", "detail": ""}) == "GATEWAY_DOWN"
    # a broker saying no to one order, or a wrong password, must not raise the banner
    assert B.problem_kind({"ok": False, "reason": "API_ERROR", "detail": "Not enough money"}) is None
    assert B.problem_kind({"ok": False, "reason": "API_ERROR", "detail": "Invalid account"}) is None
    assert B.problem_kind({"ok": True}) is None


def test_an_expired_licence_alerts_once_shows_a_status_and_clears_on_recovery(wire, caplog):
    _, answers = wire
    expired = (201, json.dumps({"errorCode": 1, "message": "CloudTrial failed(mt5rest.1.2.3.4): Trial expired. Please purchase full version."}))
    answers.extend([expired, expired, expired, (200, '{"balance": 1}')])
    with caplog.at_level(logging.ERROR, logger="mt5"):
        for _ in range(3):
            B._call("AccountSummary", token="t")
    alerts = [r for r in caplog.records if "MT5 GATEWAY ALERT" in r.getMessage()]
    assert len(alerts) == 1                                # three failures, one alert
    st = B.gateway_status()
    assert st["ok"] is False and st["kind"] == "LICENCE" and st["failed_calls"] == 3 and "licence" in st["message"]
    B._call("AccountSummary", token="t")
    assert B.gateway_status()["ok"] is True


def test_closed_order_reads_the_brokers_own_close_and_never_confuses_unknown_with_absent(wire):
    _, answers = wire
    hist = [{"ticket": 111, "closePrice": 0, "closeComment": ""},                       # still open: ignored
            {"ticket": 906001, "closePrice": 201.25, "closeTime": "2026-11-19T15:01:02", "closeComment": "[sl 201.25]",
             "profit": -12.5, "commission": -0.2, "swap": 0}]
    answers.append((200, json.dumps(hist)))
    got = B.closed_order(906001, 1_795_000_000, token="t", strict_token=True)
    assert got["ok"] and got["found"] and got["price"] == 201.25 and got["how"] == "STOP" and got["commission"] == -0.2
    answers.append((200, json.dumps(hist)))
    assert B.closed_order(555, 1_795_000_000, token="t") == {"ok": True, "found": False, "ticket": 555}
    answers.append((500, "down"))
    assert B.closed_order(906001, 1_795_000_000, token="t")["ok"] is False
    assert B.closed_order(906001, 1_795_000_000, strict_token=True)["ok"] is False   # no user session: refused


def test_required_margin_is_a_read(wire):
    sent, answers = wire
    answers.append((200, "512.4"))
    assert B.required_margin("AAPL.m", "LONG", 0.1, token="t") == {"ok": True, "margin": 512.4}
    assert "RequiredMargin" in sent[-1]["url"] and "type=0" in sent[-1]["url"]


def test_connect_on_the_paid_host_sends_a_fresh_session_id_and_never_logs_it(wire, caplog):
    """W: the paid host answered 'Id parameter is required' to ConnectEx without one (2026-11-26)."""
    sent, answers = wire
    ids = []
    for _ in range(2):
        answers.extend([(200, '"returned-token"'), (200, json.dumps({"balance": 1.0, "equity": 1.0}))])
        sess = {"base_url": None, "token": None, "login": None, "server": None}
        with caplog.at_level(logging.DEBUG):
            res = B.connect(250094, "pw", "Some-Server", session=sess)
        assert res["ok"] and sess["token"] == "returned-token"      # the host's answer is the token we keep
        url = [s["url"] for s in sent if "ConnectEx" in s["url"]][-1]
        sid = B.urllib.parse.parse_qs(B.urllib.parse.urlsplit(url).query)["id"][0]
        assert len(sid) >= 32 and sid not in caplog.text and sid not in json.dumps(res)
        ids.append(sid)
    assert ids[0] != ids[1]


def test_connect_without_the_paid_host_is_unchanged(wire, monkeypatch):
    sent, answers = wire
    monkeypatch.delenv(B.GATEWAY_URL_ENV, raising=False)
    answers.extend([(200, '"tok"'), (200, json.dumps({"balance": 1.0}))])
    B.connect(1, "pw", "S", session={"base_url": None, "token": None, "login": None, "server": None})
    assert "id=" not in [s["url"] for s in sent if "ConnectEx" in s["url"]][0]
