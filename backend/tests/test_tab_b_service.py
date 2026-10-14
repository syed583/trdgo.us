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
    "expiry": "2026-10-16",
    "call_today": {"strike": 440.0, "rate": 11.88, "iv": 55.6},
    "put_today": {"strike": 440.0, "rate": 6.62, "iv": 55.6},
    "call_10d": {"strike": 457.5, "rate": 11.12, "iv": 40.2},
    "put_10d": {"strike": 457.5, "rate": 10.62, "iv": 38.9},
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
    assert r["call_today"]["iv"] == 55.6 and r["call_10d"]["iv"] == 40.2
    assert r["put_today"]["rate"] == 6.62 and r["put_10d"]["strike"] == 457.5
    assert r["date_10d"] is not None


def test_compute_missing_history_is_none(monkeypatch):
    q = dict(_QUOTE, call_10d=None, put_10d=None)
    _patch(monkeypatch, quote=q)
    r = tb.compute("AAA")
    assert r["call_10d"] is None and r["put_10d"] is None       # frontend shows "—"
    assert r["call_today"] is not None


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


def test_scan_drops_name_whose_date_moved(monkeypatch):
    nd = _next_trading_day(date.today())
    cal_rows = [{"symbol": "TSM", "date": nd.isoformat(), "reporting_time": "BMO"}]
    cached = {"TSM": {"symbol": "TSM", "call_today": {"strike": 1, "rate": 1, "iv": 1}}}
    _scan_env(monkeypatch, cal_rows, cached, last_report=lambda s: "2026-01-01")  # stored differs
    res = tb.scan()
    assert [r["symbol"] for r in res["rows"]] == []     # dropped out
