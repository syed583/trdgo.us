"""Intraday Model v2 -- correctness fixes: ET session dates, wall scoring,
freshness-by-date, liquidity excluding today's partial bar."""

from datetime import date, datetime

import intraday_model as im
import intraday_indicators as ind


def test_market_phase_by_time():
    ny = ind._ET
    at = lambda h, m: datetime(2026, 10, 15, h, m, tzinfo=ny)   # Thu (weekday)
    assert ind.market_phase(at(6, 0)) == "pre"
    assert ind.market_phase(at(11, 0)) == "rth"
    assert ind.market_phase(at(17, 0)) == "post"
    assert ind.market_phase(at(21, 0)) == "closed"
    assert ind.market_phase(datetime(2026, 10, 17, 11, 0, tzinfo=ny)) == "closed"  # Sat


def test_session_bars_uses_today_not_last_present(monkeypatch):
    ny = ind._ET
    # Bars contain Wednesday's full RTH session but no Thursday RTH yet.
    wed = [{"t": f"2026-10-14T{h:02d}:30:00-04:00", "open": 1, "high": 1, "low": 1,
            "close": 1, "volume": 10} for h in range(10, 15)]
    # Pre-market Thursday -> today's (Thu) RTH session is empty, NOT Wednesday's.
    monkeypatch.setattr(ind, "now_et", lambda: datetime(2026, 10, 15, 6, 0, tzinfo=ny))
    assert ind.session_bars(wed) == []
    # Once Thursday's session is underway, only Thursday's bars are the session.
    thu = wed + [{"t": "2026-10-15T10:00:00-04:00", "open": 2, "high": 2, "low": 2,
                  "close": 2, "volume": 20}]
    monkeypatch.setattr(ind, "now_et", lambda: datetime(2026, 10, 15, 10, 5, tzinfo=ny))
    sess = ind.session_bars(thu)
    assert len(sess) == 1 and sess[0]["c"] == 2


# --- Fix 1: ET session date, not the Dubai server day ----------------------
def test_et_date_uses_new_york_not_server():
    # 02:00 UTC on Oct 15 is still Oct 14 in New York (and Oct 15 in Dubai).
    assert im._et_date("2026-10-15T02:00:00Z") == date(2026, 10, 14)
    # 14:00 UTC on Oct 15 is Oct 15 in New York.
    assert im._et_date("2026-10-15T14:00:00Z") == date(2026, 10, 15)


def test_news_eval_filters_by_et_session():
    today = date(2026, 10, 14)
    rows = [
        {"created_at": "2026-10-15T02:00:00Z", "sentiment": "Bullish"},   # ET Oct 14 -> counts
        {"created_at": "2026-10-15T14:00:00Z", "sentiment": "Bearish"},   # ET Oct 15 -> ignored
    ]
    v, detail = im._news_eval(rows, today)
    assert v > 0 and "1 headline" in detail            # only the bullish ET-Oct-14 one


def test_unusual_eval_counts_only_todays_et_prints():
    today = date(2026, 10, 14)
    rows = [
        # ET Oct 14 call sweep at ask, 5 DTE -> counts bullish
        {"last_fill": "2026-10-15T02:00:00Z", "expiry": "2026-10-19",
         "ask_side_volume": 900, "bid_side_volume": 100, "premium": 500000, "option_type": "call"},
        # ET Oct 15 (tomorrow) -> ignored even though it's a big put
        {"last_fill": "2026-10-15T14:00:00Z", "expiry": "2026-10-19",
         "ask_side_volume": 900, "bid_side_volume": 100, "premium": 9000000, "option_type": "put"},
        # far-dated (>14 DTE) -> ignored
        {"last_fill": "2026-10-15T02:00:00Z", "expiry": "2026-12-19",
         "ask_side_volume": 900, "bid_side_volume": 100, "premium": 9000000, "option_type": "put"},
    ]
    net = im._unusual_eval(rows, today, 14)
    assert net == 500000                               # only the first print


# --- Fix 2: walls can reach the full -5..+5 range --------------------------
def test_levels_call_break_is_full_positive():
    # Price crossed above the call wall (100) since the open (95).
    v, _, has = im._levels_eval(105.0, 95.0, {100: 1000, 110: 500}, {}, None)
    assert has and v == im.MAX["levels"]               # +5


def test_levels_put_break_is_full_negative():
    v, _, has = im._levels_eval(95.0, 105.0, {}, {100: 1000}, None)
    assert has and v == -im.MAX["levels"]              # -5


def test_levels_no_break_when_open_already_past_wall():
    # Spot above the call wall but it opened above it too -> not a fresh break.
    v, _, _ = im._levels_eval(105.0, 106.0, {100: 1000}, {}, None)
    assert v == 0.0


def test_levels_handles_string_keys_from_json_cache():
    # After the dict round-trips through the JSON cache, strike keys are strings.
    v, _, has = im._levels_eval(105.0, 95.0, {"100": 1000, "110": 500}, {}, None)
    assert has and v == im.MAX["levels"]               # coerced to float, not a crash


def test_levels_pinned_at_max_pain_zeroes():
    # Would be +5 (crossed the 95 call wall), but pinned at max pain 100.
    v, _, _ = im._levels_eval(100.0, 90.0, {95: 1000}, {}, 100.0)
    assert v == 0.0


# --- Fix 3: 20-day normalizer excludes today BY DATE -----------------------
def test_flow_normalizer_excludes_today_by_date():
    today = date(2026, 10, 14)
    rows = [{"date": "2026-10-14", "call_premium": 9e9, "put_premium": 9e9}]   # today, huge
    rows += [{"date": f"2026-10-{d:02d}", "call_premium": 100, "put_premium": 100}
             for d in range(13, 1, -1)]               # prior sessions, 100+100 each
    avg = im._flow_norm_from_rows(rows, today)
    assert avg == 200.0                                # today's 1.8e10 excluded


# --- Fix 4: liquidity avg excludes today's partial bar ---------------------
def test_bars_meta_critical_retries_3x_and_reuses_fresh_stale(monkeypatch):
    import swr
    import time
    calls = {"n": 0}

    def _fake_fetch(sym, timeout=9.0):
        calls["n"] += 1
        return []                                    # always empty -> exhaust retries

    monkeypatch.setattr(im, "_fetch_bars_once", _fake_fetch)
    monkeypatch.setattr(swr, "serve", lambda key, fn, ttl: fn())   # run builder, no cache
    monkeypatch.setattr(swr, "peek",
                        lambda key: {"bars": [{"t": "x"}], "ts": time.time()} if "lastbars" in key else None)

    bars, stale = im.bars_meta("SPY", critical=True)
    assert calls["n"] == 3                           # critical = 1 try + 2 retries
    assert stale is True and bars == [{"t": "x"}]    # reused fresh (<5 min) last-good


def test_bars_meta_stock_retries_once_no_stale_when_old(monkeypatch):
    import swr
    import time
    calls = {"n": 0}
    monkeypatch.setattr(im, "_fetch_bars_once",
                        lambda s, timeout=9.0: (calls.__setitem__("n", calls["n"] + 1), [])[1])
    monkeypatch.setattr(swr, "serve", lambda key, fn, ttl: fn())
    # last-good exists but is 10 min old -> must NOT be reused
    monkeypatch.setattr(swr, "peek",
                        lambda key: {"bars": [{"t": "x"}], "ts": time.time() - 600} if "lastbars" in key else None)
    bars, stale = im.bars_meta("AAA", critical=False)
    assert calls["n"] == 2                           # stock = 1 try + 1 retry
    assert bars == [] and stale is False             # stale copy too old -> no data


def test_liquidity_excludes_todays_partial_bar():
    today = date(2026, 10, 14)
    # 15 prior sessions at 2M, plus today at a tiny partial 1k.
    candles = [{"date": f"2026-09-{d:02d}", "volume": 2_000_000} for d in range(16, 30)]
    candles += [{"date": "2026-10-13", "volume": 2_000_000},
                {"date": "2026-10-14", "volume": 1_000}]       # today's partial
    ok = im._liquidity_from_candles(candles, spot=50.0, today=today,
                                    min_price=10.0, min_avgvol=1_000_000)
    assert ok is True                                  # avg stays ~2M, not dragged by today's 1k
    # If today's tiny bar were included wrongly the avg could still pass, so also
    # assert a thin name (all prior 500k) fails regardless of today's bar.
    thin = [{"date": f"2026-09-{d:02d}", "volume": 500_000} for d in range(16, 30)]
    thin += [{"date": "2026-10-14", "volume": 9_000_000}]      # today spike must NOT rescue it
    assert im._liquidity_from_candles(thin, 50.0, today, 10.0, 1_000_000) is False
