"""
Contract tests for the /api surface.

The service layer is stubbed so these run in milliseconds without touching
TWS. What is being checked is the wiring: routes exist, parameters reach the
service, CORS is configured for the dev frontend, and the legacy /market and
/companies routes were not disturbed by the new router.
"""

import pytest
from fastapi.testclient import TestClient

import api_routes
import main


@pytest.fixture
def client():
    return TestClient(main.app)


# ------------------------------------------------------------------- wiring


def test_every_dashboard_route_is_registered():
    paths = {r.path for r in main.app.routes if hasattr(r, "path")}
    for expected in (
        "/api/status",
        "/api/quote/{symbol}",
        "/api/chart/{symbol}",
        "/api/indices",
        "/api/watchlist",
        "/api/options/overview/{symbol}",
        "/api/options/chain/{symbol}",
        "/api/options/flow/{symbol}",
        "/api/options/metrics/{symbol}",
        "/api/options/expirations/{symbol}",
        "/api/earnings/overview/{symbol}",
        "/api/score/{symbol}",
        "/api/health",
        "/api/dashboard",
        "/api/tickers/strip",
        "/api/market/overview",
        "/api/earnings/calendar",
        "/api/scanner/run",
        "/api/news/{symbol}",
        "/api/sentiment/{symbol}",
        "/api/insights/{symbol}",
        "/api/alerts",
        "/api/journal",
        "/api/strategy",
        "/api/backtest",
        "/api/symbols/search",
    ):
        assert expected in paths, f"missing route {expected}"


def test_legacy_routes_are_untouched():
    """The new router is additive; the original API must still be mounted."""
    paths = {r.path for r in main.app.routes if hasattr(r, "path")}
    for legacy in (
        "/health",
        "/companies/{symbol}",
        "/market/score/{symbol}",
        "/market/analysis/{symbol}",
        # /market/options/{symbol} is deliberately absent: it and its two
        # siblings were served by a legacy chain that opened its own TWS
        # sockets, and nothing in the UI ever called them.
        "/market/environment-score",
    ):
        assert legacy in paths, f"legacy route {legacy} disappeared"


def test_cors_allows_the_vite_dev_origin(client):
    response = client.options(
        "/api/indices",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code in (200, 204)
    allowed = response.headers.get("access-control-allow-origin")
    assert allowed == "http://127.0.0.1:5173"


# ------------------------------------------------------------ parameters


def test_quote_route_upper_cases_and_forwards_the_symbol(client, monkeypatch):
    seen = {}

    def fake(symbol):
        seen["symbol"] = symbol
        return {"symbol": symbol, "status": "OK"}

    monkeypatch.setattr(api_routes.market, "get_quote", fake)
    body = client.get("/api/quote/nvda").json()
    assert seen["symbol"] == "nvda"        # the service does the upper-casing
    assert body["status"] == "OK"


def test_chart_route_defaults_and_forwards_the_range(client, monkeypatch):
    seen = {}

    def fake(symbol, range_key):
        seen["args"] = (symbol, range_key)
        return {"symbol": symbol, "range": range_key, "bars": [], "status": "OK"}

    monkeypatch.setattr(api_routes.market, "get_chart", fake)

    client.get("/api/chart/NVDA")
    assert seen["args"] == ("NVDA", "6M")

    client.get("/api/chart/NVDA?range=1Y")
    assert seen["args"] == ("NVDA", "1Y")


def test_ticker_strip_falls_back_to_the_default_basket(client, monkeypatch):
    """
    The scored strip lives at /api/tickers/strip. /api/watchlist is the
    persisted user list and is a different resource.
    """
    seen = {}

    def fake(symbols):
        seen["symbols"] = symbols
        return {"cards": [], "status": "OK"}

    monkeypatch.setattr(api_routes.scores, "get_watchlist", fake)
    monkeypatch.setattr(api_routes.workspace, "list_watchlist",
                        lambda with_quotes=False: {"rows": []})

    client.get("/api/tickers/strip")
    assert seen["symbols"] == api_routes.DEFAULT_STRIP

    client.get("/api/tickers/strip?symbols=aapl,msft")
    assert seen["symbols"] == ["AAPL", "MSFT"]


def test_ticker_strip_leads_with_the_saved_watchlist(client, monkeypatch):
    """
    Saved symbols come first, then the default basket fills the rest so a
    one-name watchlist does not leave the strip nearly empty.
    """
    seen = {}

    def fake(symbols):
        seen["symbols"] = symbols
        return {"cards": [], "status": "OK"}

    monkeypatch.setattr(api_routes.scores, "get_watchlist", fake)
    monkeypatch.setattr(
        api_routes.workspace, "list_watchlist",
        lambda with_quotes=False: {"rows": [{"symbol": "TSLA"}, {"symbol": "AMD"}]},
    )

    client.get("/api/tickers/strip")
    assert seen["symbols"][:2] == ["TSLA", "AMD"]
    assert len(seen["symbols"]) == 10
    # No symbol appears twice even though both lists contain TSLA and AMD.
    assert len(set(seen["symbols"])) == len(seen["symbols"])


def test_ticker_strip_ignores_blank_entries(client, monkeypatch):
    seen = {}

    def fake(symbols):
        seen["symbols"] = symbols
        return {"cards": [], "status": "OK"}

    monkeypatch.setattr(api_routes.scores, "get_watchlist", fake)
    client.get("/api/tickers/strip?symbols=aapl,,%20,msft")
    assert seen["symbols"] == ["AAPL", "MSFT"]


def test_watchlist_route_serves_the_persisted_list(client, monkeypatch):
    monkeypatch.setattr(
        api_routes.workspace, "list_watchlist",
        lambda: {"rows": [{"symbol": "MSFT", "price": 400}], "count": 1,
                 "status": "OK", "source": "DATABASE"},
    )
    body = client.get("/api/watchlist").json()
    assert body["source"] == "DATABASE"
    assert body["rows"][0]["symbol"] == "MSFT"


def test_flow_route_forwards_the_limit(client, monkeypatch):
    seen = {}

    def fake(symbol, limit):
        seen["args"] = (symbol, limit)
        return {"trades": [], "status": "OK"}

    monkeypatch.setattr(api_routes.options, "get_flow", fake)
    client.get("/api/options/flow/NVDA?limit=5")
    assert seen["args"] == ("NVDA", 5)


# --------------------------------------------------- degraded-provider paths


def test_metrics_route_reports_the_chain_status_when_it_is_unavailable(
    client, monkeypatch
):
    monkeypatch.setattr(
        api_routes.options, "load_chain",
        lambda symbol, expiry=None: {
            "status": "PROVIDER_OFFLINE", "error": "the feed did not answer",
        },
    )
    body = client.get("/api/options/metrics/NVDA").json()
    assert body["status"] == "PROVIDER_OFFLINE"
    assert body["error"] == "the feed did not answer"
    assert body["symbol"] == "NVDA"


def test_status_route_reports_a_dead_database_without_raising(client, monkeypatch):
    class Boom:
        def connect(self):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(api_routes, "engine", Boom())

    body = client.get("/api/status").json()
    assert body["database"]["connected"] is False
    # The exception text names the DB host and user, so it must NOT reach the
    # client; a static message stands in and the detail is logged instead.
    assert body["database"]["error"] == "database unreachable"
    assert "connection refused" not in str(body)
    # "live" now follows the market feed rather than a broker socket, so a
    # dead database does not make it false -- the two are reported apart.
    assert isinstance(body["live"], bool)
    assert "feed" in body
    assert body["market"]["session"] in (
        "OPEN", "PRE_MARKET", "AFTER_HOURS", "CLOSED",
    )


def test_expirations_route_passes_through_chain_metadata(client, monkeypatch):
    monkeypatch.setattr(
        api_routes.options, "load_chain",
        lambda symbol, expiry=None: {
            "expirations": ["20260918", "20260925"],
            "expiration_labels": ["09/18/26", "09/25/26"],
            "expiry": "20260918",
            "status": "OK",
        },
    )
    body = client.get("/api/options/expirations/NVDA").json()
    assert body["labels"] == ["09/18/26", "09/25/26"]
    assert body["selected"] == "20260918"
    assert body["status"] == "OK"
