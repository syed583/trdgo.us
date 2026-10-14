"""Short Straddle change spec: event/back expiry, move ratio, ranking, status."""

from datetime import date, timedelta

import tab_b_service as tb


def _next_trading_day(d):
    nd = d + timedelta(days=1)
    while nd.weekday() >= 5:
        nd += timedelta(days=1)
    return nd


# --- Test 1 & 2: event expiry selection (reaction session) -------------------
def test_reaction_date_bmo_is_report_day():
    assert tb._reaction_date("2026-10-15", "BMO") == "2026-10-15"


def test_reaction_date_amc_is_next_trading_day():
    # AMC Thursday -> reaction = Friday.
    assert tb._reaction_date("2026-10-15", "AMC") == "2026-10-16"  # Thu -> Fri


def test_next_session():
    today = date.today()
    nd = _next_trading_day(today)
    assert tb._next_session(today.isoformat(), "AMC") is True
    assert tb._next_session(nd.isoformat(), "BMO") is True
    assert tb._next_session(today.isoformat(), "BMO") is False


# --- Helper to drive compute with crafted option data ------------------------
def _patch(monkeypatch, *, front_iv, back_iv, call_mid, put_mid, spot=100.0,
           hist=3.0, n_hist=8, report_date=None, code="BMO", wide=False,
           event_dte=2, prev_report=None, snap=None):
    import swr
    import earnings_options_service as o
    import earnings_trade_service as ets
    today = date.today()
    # A genuine next-session date: AMC reports today, BMO reports next trading day.
    if report_date is None:
        report_date = today.isoformat() if code == "AMC" else _next_trading_day(today).isoformat()
    monkeypatch.setattr(tb, "_report_meta", lambda s: (report_date, code))
    monkeypatch.setattr(tb, "_last_report_date", lambda s: prev_report)
    monkeypatch.setattr(tb, "_snapshot_prev", lambda s, lb: snap)
    monkeypatch.setattr(swr, "peek", lambda k: (
        {"expected_move": {"current": spot}, "price": spot} if "earn:trade" in k else None))
    monkeypatch.setattr(o, "short_straddle_quote", lambda *a, **k: {
        "event_expiry": "2026-12-18", "event_dte": event_dte, "back_expiry": "2027-01-15",
        "atm_strike": 100.0,
        "call": {"bid": call_mid - 0.1, "mid": call_mid, "ask": call_mid + 0.1,
                 "iv": front_iv, "spread_pct": 20 if wide else 3, "delta": 0.5, "oi": 1000, "vol": 100},
        "put": {"bid": put_mid - 0.1, "mid": put_mid, "ask": put_mid + 0.1,
                "iv": front_iv, "spread_pct": 3, "delta": -0.5, "oi": 1000, "vol": 100},
        "straddle": round(call_mid + put_mid, 2), "front_iv": front_iv,
        "wide_spread": wide, "back_iv": back_iv, "back_far": False})
    monkeypatch.setattr(ets, "_historical_moves",
                        lambda s, limit=0: [{"move_pct": hist}] * n_hist)


# --- Test 3: straddle maths --------------------------------------------------
def test_straddle_and_implied_move(monkeypatch):
    _patch(monkeypatch, front_iv=60, back_iv=30, call_mid=3.2, put_mid=3.05, spot=100.0, hist=3.0)
    r = tb.compute("AAA")
    assert r["straddle"] == 6.25            # 3.20 + 3.05
    assert r["implied_move"] == 6.25        # 6.25 / 100 * 100
    assert r["front_back"] == 2.0           # 60 / 30


# --- Test 4: event-only move clips negative variance -------------------------
def test_event_only_move_clips_negative(monkeypatch):
    _patch(monkeypatch, front_iv=30, back_iv=60, call_mid=3.0, put_mid=3.0)  # front < back
    r = tb.compute("BBB")
    assert r["event_move"] == 0.0           # negative variance -> 0, no crash


# --- Test 6: NO EDGE when move ratio < 1.0 regardless of inflation -----------
def test_no_edge_overrides_inflation(monkeypatch):
    # Inflation high (front/back = 3.0) but implied move < historical -> NO EDGE.
    _patch(monkeypatch, front_iv=90, back_iv=30, call_mid=1.0, put_mid=1.0, spot=100.0, hist=5.0)
    r = tb.compute("CCC")
    assert r["move_ratio"] < 1.0
    assert r["status"] == "NO EDGE"


def test_sell_straddle_happy_path(monkeypatch):
    _patch(monkeypatch, front_iv=60, back_iv=30, call_mid=3.2, put_mid=3.05, spot=100.0, hist=3.0)
    r = tb.compute("DDD")
    assert r["method"] == "TS" and r["inflation"] == 2.0
    assert r["move_ratio"] >= 1.2 and r["status"] == "SELL STRADDLE"


# --- Test 7: changed earnings date -> NOT DUE --------------------------------
def test_date_change_is_not_due(monkeypatch):
    today = date.today().isoformat()
    _patch(monkeypatch, front_iv=60, back_iv=30, call_mid=3.2, put_mid=3.05,
           report_date=today, prev_report="2026-01-01")   # stored date differs
    r = tb.compute("EEE")
    assert r["date_status"] == "CHANGED" and r["status"] == "NOT DUE"
    assert "DATE CHANGED" in r["tags"]


# --- Test 5: ranking -- 10S above TS, ties by move ratio ---------------------
def test_ranking_10s_above_ts(monkeypatch):
    rowmeta = {
        "T10A": {"method": "10S", "inflation": 1.5, "move_ratio": 1.3},
        "T10B": {"method": "10S", "inflation": 1.5, "move_ratio": 1.6},   # ties inflation, higher MR -> above
        "TSX":  {"method": "TS", "inflation": 9.9, "move_ratio": 2.0},    # TS always below 10S
    }

    def fake_cached(sym):
        m = rowmeta[sym]
        return {"symbol": sym, "status": "SELL STRADDLE", "spot": 100.0, **m}

    import swr
    import uw_earnings_calendar as cal
    import tab_settings as cfg
    monkeypatch.setattr(cfg, "TAB_B_WATCHLIST", list(rowmeta.keys()))
    today = date.today()
    rows = [{"symbol": s, "date": today.isoformat(), "reporting_time": "AMC",
             "time_label": "After Close"} for s in rowmeta]
    monkeypatch.setattr(cal, "calendar", lambda **k: {"rows": rows})
    monkeypatch.setattr(swr, "peek_many", lambda keys: {k: fake_cached(k.split(":")[1]) for k in keys})
    monkeypatch.setattr(tb, "log_scores", lambda rows: 0)

    res = tb.scan()
    rank = {r["symbol"]: r["rank"] for r in res["rows"]}
    assert rank["T10B"] == 1 and rank["T10A"] == 2 and rank["TSX"] == 3
