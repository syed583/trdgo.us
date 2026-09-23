"""
A today-only chart must not be cached as if it were five days of history.

Relative volume compares today against earlier sessions. When the 5D fetch
comes back holding only today -- which it does, intermittently -- storing it
as a success keeps it for half an hour, and for that half hour the parameter
reports "no earlier sessions" on a stock whose history is sitting one retry
away. NVDA spent a live afternoon in exactly that state while every other
symbol scored normally.
"""

from datetime import datetime, timedelta, timezone

import horizon_model as hm


def _bar(when, volume=1000.0):
    return {"t": when.isoformat(), "volume": volume, "close": 10.0}


def _et_today_at(hour, minute, days_ago=0):
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/New_York"))
    return (now - timedelta(days=days_ago)).replace(
        hour=hour, minute=minute, second=0, microsecond=0)


def test_a_chart_with_only_today_is_not_treated_as_history():
    today_only = {"bars": [_bar(_et_today_at(9, 30)), _bar(_et_today_at(10, 0))]}
    assert hm._has_prior_session(today_only) is False


def test_a_chart_holding_an_earlier_session_is_history():
    chart = {"bars": [_bar(_et_today_at(10, 0)),
                      _bar(_et_today_at(10, 0, days_ago=3))]}
    assert hm._has_prior_session(chart) is True


def test_overnight_bars_alone_are_not_an_earlier_session():
    """04:50 and 20:00 ET are not the regular session this compares."""
    chart = {"bars": [_bar(_et_today_at(4, 50, days_ago=2)),
                      _bar(_et_today_at(20, 15, days_ago=2))]}
    assert hm._has_prior_session(chart) is False


def test_a_today_only_answer_is_retried_soon_not_kept(monkeypatch):
    import live_market_service as market

    today_only = {"bars": [_bar(_et_today_at(9, 30))]}
    real = {"bars": [_bar(_et_today_at(9, 30)),
                     _bar(_et_today_at(9, 30, days_ago=1))]}
    answers = [today_only, today_only, real]
    monkeypatch.setattr(market, "get_chart", lambda s, r: answers.pop(0))
    monkeypatch.setattr(hm, "_history", {})

    first = hm._prior_sessions("TST")
    assert len(first["bars"]) == 1, "the caller still gets what came back"
    assert hm._history["TST"][1] == {}, "a useless answer must not be kept"

    # It is retried on the short failure window, not held for the full
    # half-hour a real history earns.
    stamp = hm._history["TST"][0]
    hm._history["TST"] = (stamp - hm._HISTORY_FAIL_TTL - 1, {})
    second = hm._prior_sessions("TST")
    assert len(second["bars"]) == 2
    assert hm._history["TST"][1] is second, "a real history is kept"


def test_a_refused_request_is_retried_once(monkeypatch):
    """TWS refuses one request and serves the next; that is not 'no history'."""
    import live_market_service as market

    answers = [{"bars": []},
               {"bars": [_bar(_et_today_at(9, 30)),
                         _bar(_et_today_at(9, 30, days_ago=1))]}]
    calls = []

    def get_chart(symbol, rng):
        calls.append(rng)
        return answers.pop(0)

    monkeypatch.setattr(market, "get_chart", get_chart)
    monkeypatch.setattr(hm, "_history", {})

    out = hm._prior_sessions("TST")
    assert len(out["bars"]) == 2 and calls == ["5D", "5D"]


def test_the_retry_stops_at_one(monkeypatch):
    import live_market_service as market

    calls = []
    monkeypatch.setattr(market, "get_chart",
                        lambda s, r: calls.append(r) or {"bars": []})
    monkeypatch.setattr(hm, "_history", {})

    hm._prior_sessions("TST")
    assert len(calls) == 2, "a down feed must not be hammered"


def test_a_real_history_is_kept():
    import live_market_service as market
    good = {"bars": [_bar(_et_today_at(9, 30)),
                     _bar(_et_today_at(9, 30, days_ago=1))]}
    hm._history.clear()
    original = market.get_chart
    market.get_chart = lambda s, r: good
    try:
        hm._prior_sessions("TST2")
        assert hm._history["TST2"][1] is good
    finally:
        market.get_chart = original
        hm._history.clear()


def test_missing_volume_and_missing_history_read_differently():
    bars = [{"t": _et_today_at(10, 0).isoformat(), "volume": 0},
            {"t": _et_today_at(11, 0).isoformat(), "volume": 0}]
    no_volume = hm._relative_volume_matched(bars, {"bars": []},
                                            day_pct=0.5, w=5)
    assert "no volume" in (no_volume.unavailable_reason or "").lower()

    traded = [{**b, "volume": 500} for b in bars]
    no_history = hm._relative_volume_matched(traded, {"bars": []},
                                             day_pct=0.5, w=5)
    assert "earlier sessions" in (no_history.unavailable_reason or "")
