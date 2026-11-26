"""IV Crush Check: next-session filter, today-vs-10d option data, date re-check."""

from datetime import date, timedelta

import tab_b_service as tb


def _next_trading_day(d):
    nd = d + timedelta(days=1)
    while nd.weekday() >= 5:
        nd += timedelta(days=1)
    return nd


def test_reaction_date_bmo_and_amc():
    assert tb._reaction_date("2026-10-15", "BMO") == "2026-10-15"       # BMO = report day
    assert tb._reaction_date("2026-10-15", "AMC") == "2026-10-16"       # AMC Thu -> Fri


def test_next_session():
    today = date.today()
    nd = _next_trading_day(today)
    assert tb._next_session(today.isoformat(), "AMC") is True
    assert tb._next_session(nd.isoformat(), "BMO") is True
    assert tb._next_session(today.isoformat(), "BMO") is False


_QUOTE = {
    "expiry": "2026-10-16", "strike": 455.0,          # one locked strike
    "call_then": {"rate": 15.25, "iv": 37.6},
    "call_now": {"rate": 3.08, "iv": 62.8},
    "put_then": {"rate": 13.12, "iv": 34.7},
    "put_now": {"rate": 19.07, "iv": 62.7},
}


def _patch(monkeypatch, *, quote=_QUOTE, code="BMO"):
    import swr
    import earnings_options_service as o
    import unusualwhales_service as uw
    today = date.today()
    rd = today.isoformat() if code == "AMC" else _next_trading_day(today).isoformat()
    monkeypatch.setattr(tb, "_report_meta", lambda s: (rd, code))
    monkeypatch.setattr(swr, "peek", lambda k: (
        {"expected_move": {"current": 440.0}, "price": 440.0} if "earn:trade" in k else None))
    bars = [{"date": (today - timedelta(days=40 - i)).isoformat(), "close": 450.0}
            for i in range(40)]
    monkeypatch.setattr(uw, "candles", lambda *a, **k: {"data": bars})
    monkeypatch.setattr(uw, "_rows", lambda payload: payload.get("data", []) if isinstance(payload, dict) else payload)
    monkeypatch.setattr(o, "iv_crush_quote", lambda *a, **k: quote)
    return rd


def test_compute_iv_crush_row(monkeypatch):
    _patch(monkeypatch)
    r = tb.compute("TSM")
    assert r["result_code"] == "BMO" and r["expiry"] == "2026-10-16"
    assert r["strike"] == 455.0                                 # one locked strike
    assert r["call_then"]["iv"] == 37.6 and r["call_now"]["iv"] == 62.8   # same strike, run-up
    assert r["put_then"]["rate"] == 13.12 and r["put_now"]["rate"] == 19.07
    assert r["date_10d"] is not None


def test_compute_missing_history_is_none(monkeypatch):
    q = dict(_QUOTE, call_then=None, put_then=None)
    _patch(monkeypatch, quote=q)
    r = tb.compute("AAA")
    assert r["call_then"] is None and r["put_then"] is None     # frontend shows "—"
    assert r["call_now"] is not None


def test_build_occ():
    import earnings_options_service as o
    assert o._build_occ("TSM", "2026-10-16", 445.0, "C") == "TSM261016C00445000"
    assert o._build_occ("TSM", "2026-10-16", 457.5, "P") == "TSM261016P00457500"


def _scan_env(monkeypatch, cal_rows, cached, last_report=lambda s: None):
    import swr
    import uw_earnings_calendar as cal
    import tab_settings as cfg
    monkeypatch.setattr(cfg, "TAB_B_WATCHLIST", [r["symbol"] for r in cal_rows])
    monkeypatch.setattr(cal, "calendar", lambda **k: {"rows": cal_rows})
    monkeypatch.setattr(swr, "peek_many", lambda keys: {k: cached.get(k.split(":")[1]) for k in keys})
    monkeypatch.setattr(tb, "_last_report_date", last_report)
    monkeypatch.setattr(tb, "log_scores", lambda rows: 0)


def test_scan_sorts_bmo_before_amc_then_ticker(monkeypatch):
    today = date.today()
    nd = _next_trading_day(today)
    cal_rows = [
        {"symbol": "ZZZ", "date": nd.isoformat(), "reporting_time": "BMO"},
        {"symbol": "AAA", "date": nd.isoformat(), "reporting_time": "BMO"},
        {"symbol": "MMM", "date": today.isoformat(), "reporting_time": "AMC"},
    ]
    cached = {s: {"symbol": s, "call_today": {"strike": 1, "rate": 1, "iv": 1}} for s in ("ZZZ", "AAA", "MMM")}
    _scan_env(monkeypatch, cal_rows, cached)
    res = tb.scan()
    order = [r["symbol"] for r in res["rows"]]
    # Today AMC sorts before next-day BMO (earlier date); then BMO AAA before ZZZ.
    assert order == ["MMM", "AAA", "ZZZ"]


def test_liquidity_filter():
    thin_oi = {"atm_oi": 500, "atm_spread_pct": 5.0}
    wide = {"atm_oi": 5000, "atm_spread_pct": 22.0}
    ok = {"atm_oi": 5000, "atm_spread_pct": 8.0}
    missing = {"atm_oi": None, "atm_spread_pct": None}   # unknown -> not dropped
    assert tb._liquid_enough(thin_oi) is False
    assert tb._liquid_enough(wide) is False
    assert tb._liquid_enough(ok) is True
    assert tb._liquid_enough(missing) is True


def test_scan_drops_thin_names(monkeypatch):
    today = date.today()
    nd = _next_trading_day(today)
    cal_rows = [
        {"symbol": "OK1", "date": nd.isoformat(), "reporting_time": "BMO"},
        {"symbol": "THIN", "date": nd.isoformat(), "reporting_time": "BMO"},
    ]
    cached = {
        "OK1": {"symbol": "OK1", "call_now": {"rate": 1, "iv": 1}, "atm_oi": 4000, "atm_spread_pct": 6.0},
        "THIN": {"symbol": "THIN", "call_now": {"rate": 1, "iv": 1}, "atm_oi": 100, "atm_spread_pct": 6.0},
    }
    _scan_env(monkeypatch, cal_rows, cached)
    res = tb.scan()
    assert [r["symbol"] for r in res["rows"]] == ["OK1"]   # THIN dropped (OI < 1000)


def test_scan_universe_is_next_10_trading_days(monkeypatch):
    # Every upcoming name within the window shows (not just the next session);
    # a name reporting well beyond the window, and a past date, drop out.
    today = date.today()
    nd = _next_trading_day(today)
    far = today + timedelta(days=40)       # ~28 trading days out -> excluded
    past = today - timedelta(days=3)       # already reported -> excluded
    cal_rows = [
        {"symbol": "NOW1", "date": nd.isoformat(), "reporting_time": "BMO"},
        {"symbol": "SOON", "date": (today + timedelta(days=9)).isoformat(), "reporting_time": "AMC"},
        {"symbol": "FAR", "date": far.isoformat(), "reporting_time": "BMO"},
        {"symbol": "OLD", "date": past.isoformat(), "reporting_time": "AMC"},
    ]
    cached = {s: {"symbol": s, "call_today": {"strike": 1, "rate": 1, "iv": 1}}
              for s in ("NOW1", "SOON", "FAR", "OLD")}
    _scan_env(monkeypatch, cal_rows, cached)
    res = tb.scan()
    got = {r["symbol"] for r in res["rows"]}
    assert "NOW1" in got and "SOON" in got     # both inside the 10-trading-day window
    assert "FAR" not in got and "OLD" not in got


# ---------------------------------------------------------------- stuck rows (found 2026-11-26)
# 57 of 64 live rows were computed EMPTY on 20-21 November and never computed again: scan() reads
# held rows whatever their age, and only a missing row or a 15:55 next-session row was ever queued.

def _stamp(seconds_ago):
    from datetime import datetime, timedelta as _td, timezone
    return (datetime.now(timezone.utc) - _td(seconds=seconds_ago)).isoformat()


def test_an_old_empty_row_is_queued_again(monkeypatch):
    nd = _next_trading_day(_next_trading_day(date.today()))          # not next-session: no 15:55 rule
    cal_rows = [{"symbol": "STUCK", "date": nd.isoformat(), "reporting_time": "AMC"}]
    cached = {"STUCK": {"symbol": "STUCK", "spot": 114.56, "call_now": None, "put_now": None,
                        "expiry": None, "updated_at": _stamp(6 * 86400)}}
    _scan_env(monkeypatch, cal_rows, cached)
    res = tb.scan()
    assert res["refetch"] == ["STUCK"]


def test_a_fresh_empty_row_is_not_hammered(monkeypatch):
    nd = _next_trading_day(_next_trading_day(date.today()))
    cal_rows = [{"symbol": "NOOPT", "date": nd.isoformat(), "reporting_time": "AMC"}]
    cached = {"NOOPT": {"symbol": "NOOPT", "call_now": None, "put_now": None, "updated_at": _stamp(120)}}
    _scan_env(monkeypatch, cal_rows, cached)
    assert tb.scan()["refetch"] == []


def test_a_priced_row_refreshes_after_its_freshness_and_not_before(monkeypatch):
    nd = _next_trading_day(_next_trading_day(date.today()))
    cal_rows = [{"symbol": "OLD", "date": nd.isoformat(), "reporting_time": "AMC"},
                {"symbol": "NEW", "date": nd.isoformat(), "reporting_time": "AMC"}]
    leg = {"rate": 1, "iv": 40.0}
    cached = {"OLD": {"symbol": "OLD", "call_now": leg, "put_now": leg, "updated_at": _stamp(7 * 3600)},
              "NEW": {"symbol": "NEW", "call_now": leg, "put_now": leg, "updated_at": _stamp(600)}}
    _scan_env(monkeypatch, cal_rows, cached)
    res = tb.scan()
    assert res["refetch"] == ["OLD"]
    assert {r["symbol"] for r in res["rows"]} == {"OLD", "NEW"}      # the old value is still shown meanwhile


def test_needs_refill_rules():
    leg = {"rate": 1, "iv": 40.0}
    assert tb._needs_refill({"call_now": None, "put_now": None, "updated_at": _stamp(1801)}) is True
    assert tb._needs_refill({"call_now": None, "put_now": None, "updated_at": _stamp(60)}) is False
    assert tb._needs_refill({"call_now": leg, "updated_at": _stamp(21601)}) is True
    assert tb._needs_refill({"call_now": leg, "updated_at": _stamp(3600)}) is False
    assert tb._needs_refill({"call_now": leg}) is True                # no stamp: age cannot be vouched for
    assert tb._needs_refill({"call_now": leg, "updated_at": "not a date"}) is True
