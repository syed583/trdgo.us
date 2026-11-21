"""Kit P item 8: Top Movers must list common stock and ADRs, not leveraged or inverse ETFs.

The live sweep found SOXL, SPDN, TQQQ, IREN and SNDK in what reads as a STOCK movers list. The
size floor could never have excluded them: the screener publishes no market cap for an ETF, and
movers() deliberately KEEPS an unknown cap (guessing would hide a real mover). Raising the floor
would not have helped, which is why this needed an issue-type filter instead.

This is the only item in kit P with no before/after screenshot: the browser check runs against a
fixture backend, which replaces this module wholesale, so the filter is invisible there. These
tests are its evidence.
"""
import sys
import types

import pytest

import uw_screener_service as svc


ETFS = {"SOXL", "SPDN", "TQQQ"}
STOCKS = {"NVDA", "AAPL", "MSFT"}


def _feed(*symbols):
    """What uw.get returns for /api/market/movers, in the provider's own shape."""
    return {
        "status": "OK",
        "data": [{"ticker": s, "close": 100.0, "perc_change": 3.2, "volume": 9_000_000}
                 for s in symbols],
    }


@pytest.fixture
def feed(monkeypatch):
    def install(*symbols):
        monkeypatch.setattr(svc.uw, "get", lambda *a, **k: _feed(*symbols))
        # no size floor: this test is about the ISSUE TYPE, and a cap lookup would hit the network
        monkeypatch.setattr(svc, "_caps_by_symbol", dict)
    return install


def _universe(monkeypatch, tickers):
    """Stand in for the watchlist_universe module movers() imports lazily."""
    mod = types.ModuleType("watchlist_universe")
    mod.tickers = (lambda: tickers) if tickers is not None else _raise
    monkeypatch.setitem(sys.modules, "watchlist_universe", mod)


def _raise():
    raise RuntimeError("universe unavailable")


def test_leveraged_and_inverse_etfs_are_dropped(feed, monkeypatch):
    feed(*sorted(ETFS | STOCKS))
    _universe(monkeypatch, STOCKS)

    out = svc.movers(limit=25, min_market_cap=0)

    listed = {r["symbol"] for r in out["rows"]}
    assert listed == STOCKS, f"an ETF reached the movers list: {listed - STOCKS}"
    assert out["floor"]["dropped_not_common_stock_or_adr"] == len(ETFS)
    assert out["floor"]["issue_type_filter"] == "applied"


def test_it_fails_open_when_the_universe_is_unavailable(feed, monkeypatch):
    """An EMPTY movers panel on the busiest page is worse than a few ETFs.

    This is the opposite trade-off from the Watchlist search, which fails CLOSED - offering a fund
    in a box meant for common stock is wrong, whereas a blank panel here is just broken.
    """
    feed(*sorted(ETFS | STOCKS))
    _universe(monkeypatch, None)          # tickers() raises

    out = svc.movers(limit=25, min_market_cap=0)

    assert {r["symbol"] for r in out["rows"]} == ETFS | STOCKS, "a row was dropped with no filter"
    assert out["floor"]["dropped_not_common_stock_or_adr"] == 0
    assert out["floor"]["issue_type_filter"].startswith("unavailable")


def test_the_response_says_which_way_the_filter_went(feed, monkeypatch):
    """Whichever way it goes, the reason is in the payload - no silent filtering."""
    feed(*sorted(STOCKS))
    _universe(monkeypatch, STOCKS)

    out = svc.movers(limit=25, min_market_cap=0)

    assert "issue_type_filter" in out["floor"]
    assert "dropped_not_common_stock_or_adr" in out["floor"]
