"""
The Unusual Whales client: one subscription standing in for eight feeds.

What these hold in place is the same discipline the other paid provider gets:
the token travels in a header and appears nowhere else, their published
limits are read and obeyed rather than retried against, a settled day is
asked for once, and every failure is reported as the thing that actually
happened -- an expired subscription and an outage must not both read as "no
data", because only one of them is worth telling the user about.
"""

import io
import json

import unusualwhales_service as uw


class _Response:
    def __init__(self, body, headers=None):
        self.body = json.dumps(body).encode()
        self.headers = headers or {}

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _stub(monkeypatch, body=None, calls=None, headers=None, error=None):
    def urlopen(request, *a, **k):
        if calls is not None:
            calls.append((request.full_url, dict(request.headers)))
        if error is not None:
            raise error
        return _Response(body if body is not None else {"data": []},
                         headers or {})

    monkeypatch.setenv(uw.ENV_KEY, "uw_test_token")
    monkeypatch.setattr(uw.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(uw, "_spent", {"day": None, "count": 0})
    monkeypatch.setattr(uw, "_blocked_until", 0.0)
    uw.cache.clear()


def _http_error(code, body, headers=None):
    return uw.urllib.error.HTTPError(
        "https://api.unusualwhales.com/api/stock/NVDA/quote", code, "err",
        headers or {}, io.BytesIO(json.dumps(body).encode()))


def test_the_token_travels_in_the_header_and_nowhere_else(monkeypatch):
    calls = []
    _stub(monkeypatch, calls=calls)
    uw.quote("NVDA")

    url, headers = calls[0]
    assert "uw_test_token" not in url, "a token on the URL ends up in logs"
    assert headers.get("Authorization") == "Bearer uw_test_token"


def test_without_a_token_nothing_is_sent(monkeypatch):
    uw.cache.clear()
    monkeypatch.delenv(uw.ENV_KEY, raising=False)

    def forbidden(*a, **k):
        raise AssertionError("no request should be made without a token")

    monkeypatch.setattr(uw.urllib.request, "urlopen", forbidden)
    assert uw.quote("NVDA")["status"] == "PROVIDER_NOT_CONFIGURED"


def test_the_answer_is_unwrapped_whichever_way_they_send_it(monkeypatch):
    _stub(monkeypatch, body={"data": [{"close": "1.5"}]})
    assert uw.candles("NVDA")["data"] == [{"close": "1.5"}]

    uw.cache.clear()
    _stub(monkeypatch, body={"close": "1.5"})
    assert uw.quote("NVDA")["data"] == {"close": "1.5"}


def test_a_429_stops_rather_than_retries(monkeypatch):
    _stub(monkeypatch, error=_http_error(
        429, {"message": "rate limit"}, {"Retry-After": "30"}))

    out = uw.quote("NVDA")
    assert out["status"] == "RATE_LIMITED" and out["retry_in"] == 30
    assert uw.budget()["blocked"] is True

    # And the next call does not go out at all.
    def forbidden(*a, **k):
        raise AssertionError("asked again while rate limited")

    monkeypatch.setattr(uw.urllib.request, "urlopen", forbidden)
    assert uw.candles("NVDA")["status"] == "RATE_LIMITED"


def test_a_rejected_token_says_so_rather_than_no_data(monkeypatch):
    _stub(monkeypatch, error=_http_error(403, {"message": "forbidden"}))
    out = uw.quote("NVDA")
    assert out["status"] == "ENTITLEMENT_REQUIRED"
    assert uw.ENV_KEY in out["detail"]


def test_a_symbol_they_do_not_carry_is_not_an_outage(monkeypatch):
    _stub(monkeypatch, error=_http_error(404, {"message": "not found"}))
    assert uw.quote("ZZZZZ")["status"] == "NO_DATA"


def test_repeated_reads_of_one_symbol_cost_one_request(monkeypatch):
    calls = []
    _stub(monkeypatch, body={"data": [{"volume": 1}]}, calls=calls)
    uw.earnings_history("NVDA")
    uw.earnings_history("NVDA")
    assert len(calls) == 1


def test_a_settled_day_is_asked_for_once_and_kept(monkeypatch):
    calls = []
    _stub(monkeypatch, calls=calls)
    uw.unusual_activity("NVDA", date="2026-09-22")
    uw.unusual_activity("NVDA", date="2026-09-22")
    assert len(calls) == 1, "a finished day cannot change under the cache"


def test_the_market_wide_scan_is_one_request_for_everyone(monkeypatch):
    calls = []
    _stub(monkeypatch, calls=calls)
    uw.unusual_activity()
    uw.unusual_activity()
    url = calls[0][0]
    assert len(calls) == 1
    assert "ticker_symbol" not in url, "the scan covers the market, not one name"


def test_the_app_keeps_its_own_ceiling(monkeypatch):
    calls = []
    _stub(monkeypatch, calls=calls)
    monkeypatch.setattr(uw, "APP_DAILY_BUDGET", 2)

    assert uw.get("/api/x")["status"] == "OK"
    assert uw.get("/api/y")["status"] == "OK"
    out = uw.get("/api/z")
    assert out["status"] == "BUDGET_EXHAUSTED"
    assert len(calls) == 2


def test_the_status_screen_never_carries_the_token(monkeypatch):
    _stub(monkeypatch)
    monkeypatch.setenv(uw.ENV_KEY, "uw_secret_value")
    assert "uw_secret_value" not in json.dumps(uw.provider_status())
