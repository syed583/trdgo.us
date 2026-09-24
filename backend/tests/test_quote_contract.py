"""
Every quote carries the same fields, whoever answered.

Screens read a quote without asking where it came from. When TWS is not
running the fallback provider answers instead, and the one it returned had
no session clock on it -- so a page rendering "<session> - <time>" from
every quote hit undefined and unmounted itself: chart, option chain, score
and history gone, over a missing timestamp.
"""

import unusualwhales_service as uw

# What a quote promises its callers, regardless of source.
CONTRACT = ("symbol", "price", "change", "change_percent", "previous_close",
            "bid", "ask", "open", "high", "low", "close", "volume",
            "market", "status", "source")


def _stub(monkeypatch):
    monkeypatch.setenv(uw.ENV_KEY, "uw_test")
    monkeypatch.setattr(uw, "quote", lambda s: {
        "status": "OK", "source": uw.SOURCE, "data": {
            "last_trade": {"price": "225.41", "vol": 49875009},
            "quote": {"bid": {"price": "225.40"}, "ask": {"price": "225.42"}},
            "market_time": "regular"}})
    monkeypatch.setattr(uw, "get_daily_bars", lambda s, d: [
        {"date": "2026-09-22", "open": 227.0, "high": 229.0, "low": 226.0,
         "close": 228.87, "volume": 71168747.0},
        {"date": "2026-09-23", "open": 228.1, "high": 228.9, "low": 224.0,
         "close": 225.39, "volume": 49886101.0}])


def test_the_quote_carries_every_promised_field(monkeypatch):
    _stub(monkeypatch)
    quote = uw.get_quote("NVDA")
    missing = [f for f in CONTRACT if f not in quote]
    assert not missing, f"screens read these without checking: {missing}"


def test_the_session_clock_is_present_and_has_a_label(monkeypatch):
    """The exact access that took the Earnings page down."""
    _stub(monkeypatch)
    market = uw.get_quote("NVDA")["market"]
    assert isinstance(market, dict)
    for field in ("label", "date_et", "time_et"):
        assert field in market, f"a screen renders quote.market.{field}"


def test_a_clock_that_cannot_be_read_is_an_empty_dict_not_a_crash(monkeypatch):
    _stub(monkeypatch)

    def broken():
        raise RuntimeError("clock unavailable")

    import live_market_service as market
    monkeypatch.setattr(market, "market_clock", broken)

    quote = uw.get_quote("NVDA")
    assert quote["market"] == {}, "an empty clock beats no quote at all"
