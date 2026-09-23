"""
Finviz Elite exports: a spreadsheet CSV read back as data.

Their export is written for Excel -- "1.2B", "3.40%", "-" for nothing -- and
an unauthenticated request answers 200 with an empty body rather than an
error. Both of those quietly become wrong data if nobody checks, so these
tests check.
"""

import io
import json

import finviz_service as fv

CSV = (
    "Ticker,Company,Sector,Market Cap,P/E,Dividend %,Change,Volume\n"
    "AAPL,Apple Inc,Technology,3.50B,28.40,0.42%,1.20%,52000000\n"
    "MSFT,Microsoft,Technology,2.90B,-,0.75%,-0.30%,21000000\n"
)


class _Response:
    def __init__(self, body):
        self.body = body.encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _stub(monkeypatch, body=CSV, calls=None):
    def urlopen(request, *a, **k):
        if calls is not None:
            calls.append(request.full_url)
        if isinstance(body, Exception):
            raise body
        return _Response(body)

    monkeypatch.setenv(fv.ENV_KEY, "token123")
    monkeypatch.setattr(fv.urllib.request, "urlopen", urlopen)
    fv.cache.clear()


def test_the_export_path_and_filters_are_the_ones_finviz_documents(monkeypatch):
    calls = []
    _stub(monkeypatch, calls=calls)
    fv.screen(filters="fa_div_pos,sec_technology")
    assert calls[0].startswith("https://elite.finviz.com/export/screener?")
    assert "f=fa_div_pos%2Csec_technology" in calls[0]
    assert "v=111" in calls[0]


def test_spreadsheet_values_become_numbers(monkeypatch):
    _stub(monkeypatch)
    rows = {r["Ticker"]: r for r in fv.screen()["rows"]}
    # Their market cap is written in millions: "3.50B" would be a suffix, but
    # a plain 3.50 means three and a half million dollars.
    assert rows["AAPL"]["Market Cap"] == 3.5e9 * 1e6
    assert rows["AAPL"]["Dividend %"] == 0.42
    assert rows["MSFT"]["Change"] == -0.3
    # A dash means the company has no value there, not zero.
    assert rows["MSFT"]["P/E"] is None


def test_an_empty_export_is_read_as_a_rejected_token(monkeypatch):
    """Finviz answers 200 with nothing when the token is missing or lapsed."""
    _stub(monkeypatch, body="")
    out = fv.screen()
    assert out["status"] == "ENTITLEMENT_REQUIRED"
    assert out["rows"] == []


def test_a_web_page_instead_of_csv_is_also_a_rejected_token(monkeypatch):
    _stub(monkeypatch, body="<!DOCTYPE html><html>sign in</html>")
    assert fv.screen()["status"] == "ENTITLEMENT_REQUIRED"


def test_without_a_token_nothing_is_sent(monkeypatch):
    monkeypatch.delenv(fv.ENV_KEY, raising=False)

    def forbidden(*a, **k):
        raise AssertionError("no request should be made without a token")

    monkeypatch.setattr(fv.urllib.request, "urlopen", forbidden)
    assert fv.screen()["status"] == "PROVIDER_NOT_CONFIGURED"


def test_a_screen_can_be_read_as_a_list_of_tickers(monkeypatch):
    _stub(monkeypatch)
    assert fv.symbols(filters="sec_technology") == ["AAPL", "MSFT"]


def test_one_company_comes_back_as_its_own_row(monkeypatch):
    calls = []
    _stub(monkeypatch, calls=calls)
    out = fv.fundamentals("AAPL")
    assert out["status"] == "OK" and out["row"]["Company"] == "Apple Inc"
    assert "t=AAPL" in calls[0]


def test_the_status_screen_never_shows_the_token(monkeypatch):
    _stub(monkeypatch)
    monkeypatch.setenv(fv.ENV_KEY, "secret_token_value")
    status = fv.provider_status()
    assert "secret_token_value" not in json.dumps(status)
    assert status["configured"] is True


def test_a_portfolio_exports_from_the_documented_path(monkeypatch):
    calls = []
    _stub(monkeypatch, calls=calls)
    out = fv.portfolio("12345", order="price")
    assert out["status"] == "OK" and out["count"] == 2
    assert out["portfolio_id"] == "12345"
    assert calls[0].startswith("https://elite.finviz.com/export/portfolio?")
    assert "pid=12345" in calls[0] and "o=price" in calls[0]


def test_a_portfolio_id_that_is_not_an_id_is_refused_before_asking(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("no request should be made for a bad id")

    monkeypatch.setenv(fv.ENV_KEY, "token123")
    monkeypatch.setattr(fv.urllib.request, "urlopen", forbidden)
    assert fv.portfolio("my portfolio")["status"] == "INVALID_REQUEST"


def test_a_portfolio_is_read_with_whatever_columns_it_carries(monkeypatch):
    """The app must not assume a cost basis the portfolio does not have."""
    _stub(monkeypatch, body=(
        "Ticker,Company,Cost,Shares,Change\n"
        "AAPL,Apple Inc,180.50,100,1.20%\n"))
    out = fv.portfolio("77")
    assert out["rows"][0]["Cost"] == 180.5
    assert out["rows"][0]["Shares"] == 100
    assert out["columns"] == ["Ticker", "Company", "Cost", "Shares", "Change"]


def test_a_calendar_always_sends_a_window(monkeypatch):
    """Finviz answers 400 for a calendar with no dateFrom."""
    calls = []
    _stub(monkeypatch, calls=calls,
          body="Date,Ticker,Company\n2026-09-23,AAPL,Apple Inc\n")
    out = fv.calendar("earnings")
    assert out["status"] == "OK"
    assert "dateFrom=" in calls[0]
    assert calls[0].startswith("https://elite.finviz.com/export/calendar/earnings?")


def test_an_unknown_calendar_is_refused_before_asking(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("no request should be made for an unknown kind")

    monkeypatch.setenv(fv.ENV_KEY, "token123")
    monkeypatch.setattr(fv.urllib.request, "urlopen", forbidden)
    assert fv.calendar("holidays")["status"] == "INVALID_REQUEST"


def test_tables_without_a_ticker_column_are_kept(monkeypatch):
    """News, calendars and futures have no Ticker; dropping them read as empty."""
    _stub(monkeypatch, body=(
        "Title,Source,Date\n"
        "Nasdaq opens lower,MarketWatch,2026-09-23 09:37:10\n"))
    out = fv.news()
    assert out["status"] == "OK" and out["count"] == 1
    assert out["rows"][0]["Source"] == "MarketWatch"


def test_a_dead_site_is_a_status_not_a_crash(monkeypatch):
    _stub(monkeypatch, body=TimeoutError("finviz is down"))
    out = fv.screen()
    assert out["status"] == "PROVIDER_OFFLINE" and out["rows"] == []
