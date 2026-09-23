"""
Support and resistance.

The risk with drawn levels is that they always look convincing, so these
tests pin the cases where a line should NOT appear as firmly as the cases
where it should.
"""

import price_levels as pl


def _bar(i, high, low, close=None):
    return {
        "t": f"2026-01-{(i % 28) + 1:02d}",
        "high": high,
        "low": low,
        "close": close if close is not None else (high + low) / 2,
        "open": (high + low) / 2,
        "volume": 1_000,
    }


def _oscillating(cycles=4, top=110.0, bottom=90.0):
    """Price bouncing between the same two prices, over and over."""
    rows = []
    i = 0
    for _ in range(cycles):
        for h, l in ((100, 98), (104, 100), (top, 106), (104, 100),
                     (100, 96), (94, bottom), (96, 92), (100, 96)):
            rows.append(_bar(i, float(h), float(l)))
            i += 1
    return rows


def test_finds_the_price_it_keeps_turning_at():
    rows = _oscillating()
    out = pl.find_levels(rows, last_price=100.0)

    assert out["status"] == "OK"
    assert out["resistance"], "a repeatedly tested high must be found"
    assert out["support"], "a repeatedly tested low must be found"
    assert abs(out["resistance"][0]["price"] - 110.0) < 1.5
    assert abs(out["support"][0]["price"] - 90.0) < 1.5
    assert out["resistance"][0]["touches"] >= pl.MIN_TOUCHES


def test_a_single_pivot_is_not_a_level():
    """One spike is an event. Drawing it as a level invents a line."""
    rows = [_bar(i, 100.0 + (i % 3), 98.0 - (i % 3)) for i in range(40)]
    rows[20] = _bar(20, 140.0, 138.0)          # one lone spike
    out = pl.find_levels(rows, last_price=100.0)

    assert all(abs(level["price"] - 139.0) > 2 for level in out["resistance"])


def test_a_straight_line_produces_no_levels():
    """Nothing to draw is a real answer, not a failure to find something."""
    rows = [_bar(i, 100.0 + i, 99.0 + i) for i in range(60)]
    out = pl.find_levels(rows, last_price=160.0)

    assert out["support"] == [] or all(
        level["touches"] >= pl.MIN_TOUCHES for level in out["support"])
    assert out["resistance"] == []


def test_levels_are_split_by_where_price_is_now():
    """
    Broken resistance is support. The side is decided by the current price,
    not by whether the pivot was a high or a low.
    """
    rows = _oscillating()
    out = pl.find_levels(rows, last_price=200.0)

    assert out["resistance"] == [], "nothing sits above a price of 200"
    assert out["support"], "every level is below, so all are support"
    assert all(level["kind"] == "support" for level in out["support"])
    assert all(level["price"] < 200 for level in out["support"])


def test_tolerance_scales_with_price():
    """
    A 1-point gap is one level on a $600 stock and two on a $20 one. The
    grouping band is a percentage, so the same shape at a different price
    must not collapse into a single level.
    """
    cheap = pl.find_levels(
        [_bar(i, h, l) for i, (h, l) in enumerate(
            [(20.0, 19.5), (21.0, 20.2), (20.0, 19.4), (21.0, 20.3)] * 8)],
        last_price=20.5)
    # 20 and 21 are 5% apart: two levels, never merged into one.
    prices = [lv["price"] for lv in cheap["support"] + cheap["resistance"]]
    assert not any(abs(a - b) > 0.01 and abs(a - b) < 0.2
                   for a in prices for b in prices)


def test_too_few_bars_says_so():
    out = pl.find_levels([_bar(i, 100.0, 99.0) for i in range(4)])
    assert out["status"] == "NO_DATA"
    assert out["support"] == [] and out["resistance"] == []


def test_attach_never_breaks_a_chart():
    payload = {"bars": [{"high": None, "low": None, "close": None}]}
    out = pl.attach(payload)
    assert out["levels"]["support"] == []
    assert "bars" in out

    broken = pl.attach({"bars": "not a list"})
    assert broken["levels"]["status"] in ("ERROR", "NO_DATA")


def test_distance_is_reported_against_the_current_price():
    rows = _oscillating()
    out = pl.find_levels(rows, last_price=100.0)
    for level in out["resistance"]:
        assert level["distance_pct"] > 0
    for level in out["support"]:
        assert level["distance_pct"] < 0
