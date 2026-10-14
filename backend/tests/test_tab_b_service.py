"""Short Straddle Scanner v2: next-session filter, exclusions, IV+premium rank."""

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
    assert tb._next_session(today.isoformat(), "AMC") is True       # tonight after close
    assert tb._next_session(nd.isoformat(), "BMO") is True           # next day before open
    assert tb._next_session(today.isoformat(), "BMO") is False       # already out / not next session
    assert tb._next_session((today + timedelta(days=9)).isoformat(), "AMC") is False
    assert tb._next_session(None, "AMC") is False


def _patch(monkeypatch, *, iv, premium=8.0, spot=100.0, leg_spread=2.0,
           oi=2000, ex_date=None, expiry="2026-12-19"):
    import swr
    import uw_volatility_service as v
    import earnings_options_service as o
    import dividends_service as dv
    monkeypatch.setattr(swr, "peek", lambda k: (
        {"expected_move": {"current": spot}, "price": spot} if "earn:trade" in k else None))
    monkeypatch.setattr(v, "get_volatility", lambda s: {"stats": {"iv": iv}})
    monkeypatch.setattr(o, "_atm_straddle", lambda s, spot=None: {
        "premium": premium, "call_strike": 100.0, "expiry": expiry,
        "oi": oi, "leg_spread_pct": leg_spread})
    monkeypatch.setattr(dv, "get_dividends", lambda s: {"next_ex_date": ex_date})


def test_compute_basic(monkeypatch):
    _patch(monkeypatch, iv=0.55, premium=8.0, spot=100.0)
    r = tb.compute("AAA")
    assert r["atm_iv"] == 55.0            # 0.55 normalised to %
    assert r["premium"] == 8.0 and r["premium_pct"] == 8.0
    assert r["exclusion"] is None


def test_exclude_wide_leg_spread(monkeypatch):
    _patch(monkeypatch, iv=0.6, leg_spread=14.0)
    r = tb.compute("BBB")
    assert r["exclusion"] and "Illiquid" in r["exclusion"]


def test_exclude_low_oi(monkeypatch):
    _patch(monkeypatch, iv=0.6, oi=50)
    r = tb.compute("CCC")
    assert r["exclusion"] and "open interest" in r["exclusion"]


def test_exclude_ex_div_before_expiry(monkeypatch):
    _patch(monkeypatch, iv=0.6, ex_date="2026-11-01", expiry="2026-12-19")
    r = tb.compute("DDD")
    assert r["exclusion"] and "Ex-dividend" in r["exclusion"]


def test_ranking_is_avg_of_iv_and_premium(monkeypatch):
    # Craft three names so IV order and premium order differ; combined = avg.
    data = {
        "HI_IV":   {"atm_iv": 90.0, "premium_pct": 5.0},   # iv#1 prem#3 -> avg 2.0
        "HI_PREM": {"atm_iv": 70.0, "premium_pct": 9.0},   # iv#3 prem#1 -> avg 2.0
        "MID":     {"atm_iv": 80.0, "premium_pct": 7.0},   # iv#2 prem#2 -> avg 2.0
    }

    def fake_cached(sym):
        d = data[sym]
        return {"symbol": sym, "spot": 100.0, "atm_iv": d["atm_iv"], "premium": 7.0,
                "premium_pct": d["premium_pct"], "leg_spread_pct": 2.0, "oi": 2000,
                "atm_strike": 100.0, "expiry": "2026-12-19", "ex_div": None,
                "exclusion": None}

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
    ranked = {r["symbol"]: r for r in res["rows"]}
    assert all(ranked[s]["status"] == "SELL STRADDLE" for s in data)
    # All three tie on combined avg (2.0); every name gets a rank 1..3, none skipped.
    assert sorted(r["rank"] for r in res["rows"]) == [1, 2, 3]
    assert ranked["HI_IV"]["rank_iv"] == 1 and ranked["HI_PREM"]["rank_prem"] == 1
