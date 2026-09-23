"""
The Today and Tomorrow horizons.

Built from synthetic bars so the rules are pinned independently of the
market. Most of these tests exist because the first version got the rule
wrong on live data: pre-market bars leaked into the opening range, relative
volume compared mismatched units, and a recovering down-gap was described as
"giving it back".
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import horizon_model as hm

ET = ZoneInfo("America/New_York")


def _bar(t: datetime, o, h, l, c, v):
    # Stamped in a non-Eastern zone on purpose: the live feed writes local time.
    return {"t": t.astimezone(timezone(timedelta(hours=4))).isoformat(),
            "open": o, "high": h, "low": l, "close": c, "volume": v}


def _session(day: datetime, n: int, start: float = 100.0, step: float = 0.1,
             vol: float = 1000.0, minutes: int = 5):
    t0 = day.replace(hour=9, minute=30)
    out, px = [], start
    for i in range(n):
        t = t0 + timedelta(minutes=minutes * i)
        out.append(_bar(t, px, px + 0.2, px - 0.2, px + step, vol))
        px += step
    return out


DAY = datetime(2026, 9, 16, tzinfo=ET)


# --- session filtering ------------------------------------------------------

def test_pre_market_bars_are_excluded():
    pre = [_bar(DAY.replace(hour=4, minute=50) + timedelta(minutes=5 * i),
                90, 91, 89, 90, 50) for i in range(12)]
    rth = _session(DAY, 10)
    got = hm._today_bars({"bars": pre + rth})
    assert len(got) == 10
    assert hm._et(got[0]["t"]).strftime("%H:%M") == "09:30"


def test_opening_range_uses_the_first_thirty_minutes_of_the_session():
    pre = [_bar(DAY.replace(hour=5, minute=0) + timedelta(minutes=5 * i),
                50, 999, 1, 50, 10) for i in range(6)]
    rth = _session(DAY, 12, start=100.0, step=0.1)
    bars = hm._today_bars({"bars": pre + rth})
    sig = hm._opening_range(bars, 105.0, 12)
    assert sig.available
    # The pre-market spike to 999 must not be the range high.
    assert sig.evidence["or_high"] < 110
    assert sig.bias > 0, "price above the opening range is bullish"


def test_no_opening_range_before_thirty_minutes():
    bars = hm._today_bars({"bars": _session(DAY, 4)})
    assert not hm._opening_range(bars, 100.5, 12).available


# --- gap wording ---------------------------------------------------------------

def test_a_down_gap_that_recovers_is_bullish_and_says_so():
    daily = {"bars": [_bar(DAY - timedelta(days=1), 100, 101, 99, 100, 1)]}
    bars = hm._today_bars({"bars": _session(DAY, 6, start=98.0, step=0.2)})
    sig = hm._gap_hold(daily, bars, price=99.5, w=6)
    assert sig.bias > 0
    assert "recovering" in sig.detail
    assert "giving it back" not in sig.detail


def test_an_up_gap_that_fades_is_bearish():
    daily = {"bars": [_bar(DAY - timedelta(days=1), 100, 101, 99, 100, 1)]}
    bars = hm._today_bars({"bars": _session(DAY, 6, start=102.0, step=-0.1)})
    sig = hm._gap_hold(daily, bars, price=101.0, w=6)
    assert sig.bias < 0
    assert "fading" in sig.detail


# --- relative volume ------------------------------------------------------------

def test_relative_volume_compares_the_same_stretch_of_earlier_sessions():
    today = hm._today_bars({"bars": _session(DAY, 24, vol=2000.0)})  # 2h of 5-min
    history = {"bars": []}
    for back in (1, 2, 3):
        d = DAY - timedelta(days=back)
        # 15-minute bars, 6000 each: the same volume per minute as today's 2000
        # per 5 minutes, so today should read about 1.0x.
        history["bars"] += _session(d, 26, vol=6000.0, minutes=15)
    sig = hm._relative_volume_matched(today, history, day_pct=1.0, w=8)
    assert sig.available
    assert 0.9 <= sig.evidence["relative_volume"] <= 1.1
    assert sig.evidence["baseline_sessions"] == 3


def test_heavy_volume_backs_the_direction_of_the_move():
    today = hm._today_bars({"bars": _session(DAY, 24, vol=8000.0)})
    history = {"bars": _session(DAY - timedelta(days=1), 26, vol=6000.0, minutes=15)}
    up = hm._relative_volume_matched(today, history, day_pct=1.5, w=8)
    down = hm._relative_volume_matched(today, history, day_pct=-1.5, w=8)
    assert up.bias > 0 > down.bias


def test_no_relative_volume_in_the_first_quarter_hour():
    today = hm._today_bars({"bars": _session(DAY, 2)})
    sig = hm._relative_volume_matched(today, {"bars": []}, day_pct=1.0, w=8)
    assert not sig.available


# --- scoring --------------------------------------------------------------------

def test_every_horizon_weighs_to_one_hundred():
    for name, weights in hm.WEIGHTS.items():
        assert sum(weights.values()) == 100, name


def test_after_the_close_the_default_is_tomorrow():
    # Before the bell the question is still today's session.
    for session in ("OPEN", "PRE_MARKET"):
        assert hm.default_horizon(session) == "TODAY"
    for session in ("AFTER_HOURS", "CLOSED"):
        assert hm.default_horizon(session) == "TOMORROW"


def test_thin_coverage_withholds_the_call():
    """Two strong signals out of a hundred points of weight is not a call."""
    sigs = [hm._signal("vwap", 1.0, 14), hm._signal("opening_range", 1.0, 12),
            hm._signal("intraday_trend", None, 12)]
    out = hm._score(sigs, 100)
    assert out["decision"] == "DO NOT TRADE"
    assert any("returned data" in b for b in out["blocked_reasons"])


def test_close_location_reads_where_the_day_finished():
    high = {"bars": [_bar(DAY, 100, 110, 100, 109, 1)]}
    low = {"bars": [_bar(DAY, 100, 110, 100, 101, 1)]}
    assert hm._close_location(high, 14).bias > 0.5
    assert hm._close_location(low, 14).bias < -0.5
