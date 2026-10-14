"""Short Straddle Scanner: next-session filter, exclusions, 10-session inflation."""

from datetime import date, timedelta

import tab_b_service as tb


def _next_trading_day(d):
    nd = d + timedelta(days=1)
    while nd.weekday() >= 5:
        nd += timedelta(days=1)
    return nd


def test_next_session():
    today = date.today()
    nd = _next_trading_day(today)
    assert tb._next_session(today.isoformat(), "AMC") is True
    assert tb._next_session(nd.isoformat(), "BMO") is True
    assert tb._next_session(today.isoformat(), "BMO") is False
    assert tb._next_session((today + timedelta(days=9)).isoformat(), "AMC") is False
    assert tb._next_session(None, "AMC") is False


def _patch(monkeypatch, *, iv_now, iv_prev, premium=8.0, spot=100.0,
           leg_spread=2.0, oi=2000, ex_date=None, expiry="2026-12-19"):
    import swr
    import uw_volatility_service as v
    import earnings_options_service as o
    import dividends_service as dv
    # 11-point IV series so ser[-(lb+1)] == iv_prev (lb defaults to 10).
    series = [{"iv": iv_prev}] + [{"iv": iv_now}] * 10
    monkeypatch.setattr(swr, "peek", lambda k: (
        {"expected_move": {"current": spot}, "price": spot} if "earn:trade" in k else None))
    monkeypatch.setattr(v, "get_volatility",
                        lambda s: {"stats": {"iv": iv_now}, "iv_rv_series": series})
    monkeypatch.setattr(o, "_atm_straddle", lambda s, spot=None: {
        "premium": premium, "call_strike": 100.0, "expiry": expiry,
        "oi": oi, "leg_spread_pct": leg_spread})
    monkeypatch.setattr(dv, "get_dividends", lambda s: {"next_ex_date": ex_date})
    monkeypatch.setattr(tb, "_snapshot_prev", lambda s, lb: None)   # no stored history yet


def test_iv_inflation_ratio(monkeypatch):
    _patch(monkeypatch, iv_now=60.0, iv_prev=40.0)
    r = tb.compute("AAA")
    assert r["atm_iv"] == 60.0 and r["iv_prev"] == 40.0
    assert r["iv_ratio"] == 1.5           # 60 / 40
    assert r["premium_ratio"] is None     # no stored snapshot yet
    assert r["exclusion"] is None


def test_exclude_no_inflation(monkeypatch):
    # IV barely changed and no premium history -> "no real inflation".
    _patch(monkeypatch, iv_now=41.0, iv_prev=40.0)
    r = tb.compute("BBB")
    assert r["exclusion"] and "No real inflation" in r["exclusion"]


def test_exclude_wide_leg_spread(monkeypatch):
    _patch(monkeypatch, iv_now=60.0, iv_prev=40.0, leg_spread=14.0)
    r = tb.compute("CCC")
    assert r["exclusion"] and "Illiquid" in r["exclusion"]


def test_exclude_low_oi(monkeypatch):
    _patch(monkeypatch, iv_now=60.0, iv_prev=40.0, oi=50)
    r = tb.compute("DDD")
    assert r["exclusion"] and "open interest" in r["exclusion"]


def test_exclude_ex_div(monkeypatch):
    _patch(monkeypatch, iv_now=60.0, iv_prev=40.0, ex_date="2026-11-01", expiry="2026-12-19")
    r = tb.compute("EEE")
    assert r["exclusion"] and "Ex-dividend" in r["exclusion"]


def test_ranking_by_iv_inflation(monkeypatch):
    data = {"BIG": 2.0, "MID": 1.5, "SMALL": 1.2}   # iv_ratio per name

    def fake_cached(sym):
        return {"symbol": sym, "spot": 100.0, "atm_iv": 60.0, "iv_prev": round(60 / data[sym], 1),
                "iv_ratio": data[sym], "iv_spark": [], "premium": 8.0, "premium_prev": None,
                "premium_ratio": None, "premium_prev_src": None, "premium_pct": 8.0,
                "leg_spread_pct": 2.0, "oi": 2000, "atm_strike": 100.0, "expiry": "2026-12-19",
                "ex_div": None, "lookback": 10, "exclusion": None}

    import swr
    import uw_earnings_calendar as cal
    import tab_settings as cfg
    monkeypatch.setattr(cfg, "TAB_B_WATCHLIST", list(data.keys()))
    today = date.today()
    rows = [{"symbol": s, "date": today.isoformat(), "reporting_time": "AMC",
             "time_label": "After Close"} for s in data]
    monkeypatch.setattr(cal, "calendar", lambda **k: {"rows": rows})
    monkeypatch.setattr(swr, "peek_many", lambda keys: {k: fake_cached(k.split(":")[1]) for k in keys})
    monkeypatch.setattr(tb, "log_scores", lambda rows: 0)

    res = tb.scan()
    ranked = {r["symbol"]: r["rank"] for r in res["rows"]}
    assert ranked["BIG"] == 1 and ranked["MID"] == 2 and ranked["SMALL"] == 3
    assert all(r["status"] == "SELL STRADDLE" for r in res["rows"])
