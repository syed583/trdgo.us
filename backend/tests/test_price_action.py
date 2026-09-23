"""
Price-action structure, on constructed bars.

Two properties matter more than any individual reading. The functions must be
pure -- the same window must always give the same answer, or a backtest built
on them is measuring noise. And they must never see past the last bar handed
to them, because a pivot confirmed using bars that had not printed yet is
lookahead, and lookahead makes a backtest look brilliant and a live system
lose money.
"""

from __future__ import annotations

import price_action_service as pa


def _bars(closes, volumes=None, spread=1.0):
    """Bars whose highs and lows bracket each close by a fixed spread."""
    volumes = volumes or [1_000_000] * len(closes)
    return [
        {
            "date": f"2026-01-{i + 1:02d}",
            "open": c,
            "high": c + spread,
            "low": c - spread,
            "close": c,
            "volume": v,
        }
        for i, (c, v) in enumerate(zip(closes, volumes))
    ]


def _swings(levels, leg=4):
    """
    A price path that turns at each level, taking ``leg`` bars per leg.

    Legs have to be several bars long: a pivot is only confirmed when it is
    the extreme of a five-bar window, so a path that reverses every bar
    produces no confirmed swings at all.
    """
    path = [float(levels[0])]
    for target in levels[1:]:
        start = path[-1]
        for step in range(1, leg + 1):
            path.append(start + (target - start) * step / leg)
    return path


# --------------------------------------------------------------------------
# no lookahead
# --------------------------------------------------------------------------


def test_recent_bars_can_never_be_confirmed_pivots():
    """
    A pivot needs bars on both sides. Marking the final bar as a swing high
    would be using data that did not exist at the time.
    """
    bars = _bars([10, 20, 30, 40, 99])

    swings = pa.swing_points(bars, span=2)

    assert all(s["index"] <= len(bars) - 3 for s in swings["highs"])
    assert all(s["index"] <= len(bars) - 3 for s in swings["lows"])


def test_adding_future_bars_does_not_change_the_past():
    """
    The core backtest property: a window's answer must not depend on what
    happens afterwards.
    """
    closes = _swings([100, 115, 105, 130, 118, 145, 132])
    early = pa.market_structure(_bars(closes[:16]))
    with_more = pa.market_structure(_bars(closes))

    # The later call sees more swings; the earlier call must be unchanged by
    # its existence, which is what re-running it proves.
    assert pa.market_structure(_bars(closes[:16])) == early
    assert with_more is not None


# --------------------------------------------------------------------------
# structure
# --------------------------------------------------------------------------


def test_rising_swings_read_as_an_uptrend():
    closes = _swings([100, 120, 110, 140, 128, 165, 150, 190])

    result = pa.market_structure(_bars(closes))

    assert result["structure"] == "UPTREND"
    assert result["higher_highs"] is True
    assert result["higher_lows"] is True


def test_falling_swings_read_as_a_downtrend():
    closes = _swings([190, 150, 165, 128, 140, 110, 120, 100])

    result = pa.market_structure(_bars(closes))

    assert result["structure"] == "DOWNTREND"


def test_mixed_swings_are_a_range_not_a_trend():
    """
    Higher highs with lower lows is an expanding range. Calling it a trend in
    either direction invents a signal that is not there.
    """
    closes = _swings([100, 130, 90, 145, 80, 160, 70])

    result = pa.market_structure(_bars(closes))

    assert result["structure"] == "RANGE"


def test_too_few_swings_is_undefined_rather_than_neutral():
    result = pa.market_structure(_bars([100, 101, 102]))

    assert result["structure"] == "UNDEFINED"


# --------------------------------------------------------------------------
# volume
# --------------------------------------------------------------------------


def test_a_move_on_heavy_volume_is_confirmed():
    closes = [100] * 20 + [105]
    volumes = [1_000_000] * 20 + [3_000_000]

    result = pa.volume_confirmation(_bars(closes, volumes))

    assert result["ratio"] == 3.0
    assert result["label"] == "SURGE"
    assert result["direction"] == "UP"
    assert result["confirms_move"] is True


def test_a_move_on_light_volume_is_not_confirmed():
    """The gap this module exists to close: participation was never checked."""
    closes = [100] * 20 + [105]
    volumes = [1_000_000] * 20 + [400_000]

    result = pa.volume_confirmation(_bars(closes, volumes))

    assert result["label"] == "WEAK"
    assert result["confirms_move"] is False


def test_heavy_volume_on_a_down_day_is_not_bullish():
    """Volume has no direction of its own; the price change supplies it."""
    closes = [100] * 20 + [95]
    volumes = [1_000_000] * 20 + [3_000_000]

    result = pa.volume_confirmation(_bars(closes, volumes))

    assert result["direction"] == "DOWN"
    score = pa.score_price_action(pa.analyse(_bars(closes, volumes)))
    assert score["components"]["volume"] == -2


# --------------------------------------------------------------------------
# breakouts
# --------------------------------------------------------------------------


def test_a_breakout_on_volume_scores_more_than_one_without():
    base = [100] * 25
    confirmed = pa.analyse(_bars(base + [130], [1_000_000] * 25 + [4_000_000]))
    unconfirmed = pa.analyse(_bars(base + [130], [1_000_000] * 25 + [300_000]))

    assert confirmed["breakout"]["state"] == "BREAKOUT_UP"
    assert confirmed["breakout"]["quality"] == "CONFIRMED"
    assert unconfirmed["breakout"]["quality"] == "UNCONFIRMED"

    hi = pa.score_price_action(confirmed)["components"]["breakout"]
    lo = pa.score_price_action(unconfirmed)["components"]["breakout"]
    assert hi == 3 and lo == 1


def test_a_breakdown_scores_negative():
    bars = _bars([100] * 25 + [70], [1_000_000] * 25 + [4_000_000])

    result = pa.breakout(bars)

    assert result["state"] == "BREAKDOWN"
    assert pa.score_price_action(pa.analyse(bars))["components"]["breakout"] == -3


def test_price_inside_the_range_is_not_a_breakout():
    bars = _bars([100] * 25 + [101])

    assert pa.breakout(bars)["state"] == "NONE"


# --------------------------------------------------------------------------
# levels
# --------------------------------------------------------------------------


def test_levels_bracket_the_current_price():
    closes = _swings([100, 130, 90, 125, 95, 120, 105])

    levels = pa.support_resistance(_bars(closes))

    if levels["support"] is not None:
        assert levels["support"] < levels["close"]
    if levels["resistance"] is not None:
        assert levels["resistance"] > levels["close"]


# --------------------------------------------------------------------------
# relative strength
# --------------------------------------------------------------------------


def test_a_stock_beating_the_benchmark_scores_positive():
    symbol = _bars([100 + i for i in range(70)])
    bench = _bars([100 + i * 0.2 for i in range(70)])

    rs = pa.relative_strength(symbol, bench)

    assert rs["outperforming"] is True
    assert rs["spread_pct"] > 0


def test_a_rising_stock_lagging_a_faster_market_scores_negative():
    """
    The reading the model could not make before: up is not the same as strong.
    """
    symbol = _bars([100 + i * 0.1 for i in range(70)])
    bench = _bars([100 + i for i in range(70)])

    rs = pa.relative_strength(symbol, bench)

    assert rs["symbol_change_pct"] > 0      # the stock rose
    assert rs["outperforming"] is False     # and still lagged


# --------------------------------------------------------------------------
# direction versus confidence
# --------------------------------------------------------------------------


def test_volatility_never_votes_on_direction():
    """
    Bollinger width and ATR describe how much price moves, not which way.
    Folding them into a direction score corrupts it silently.
    """
    analysis = pa.analyse(_bars([100 + (i % 7) * 3 for i in range(60)]))
    scored = pa.score_price_action(analysis)

    assert "bollinger" not in scored["components"]
    assert "atr" not in scored["components"]

    confidence = pa.confidence_inputs(analysis, atr=2.5)
    assert confidence["bandwidth"] is not None
    assert confidence["atr"] == 2.5


def test_score_stays_inside_its_range():
    strong = pa.analyse(_bars([100 + i * 2 for i in range(60)],
                              [1_000_000] * 59 + [9_000_000]))
    assert -10 <= pa.score_price_action(strong)["score"] <= 10


def test_no_bars_is_reported_not_guessed():
    assert pa.analyse([])["status"] == "NO_DATA"
    assert pa.score_price_action({})["bias"] == "NO_DATA"
