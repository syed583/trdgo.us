"""Unusual Activity (Table 2): Part A contract filters + side, Part B disparity."""

import unusual_activity_service as uas


# ---- Part A ---------------------------------------------------------------

def _ua_row(tkr, prem, vol, oi, ask, bid, occ, typ="call", strike=100.0, exp="2026-10-16"):
    return {"ticker_symbol": tkr, "premium": prem, "volume": vol, "open_interest": oi,
            "ask_side_volume": ask, "bid_side_volume": bid, "option_symbol": occ,
            "option_type": typ, "strike": strike, "expiry": exp}


def test_part_a_filters_and_side(monkeypatch):
    rows = [
        # qualifies: prem 200k, vol 9000 >= oi 1000, avg 2000 -> 4.5x, ask-heavy -> BUY
        _ua_row("AAA", 200_000, 9000, 1000, 8000, 500, "AAA261016C00100000"),
        # fails premium (< 100k)
        _ua_row("AAA", 50_000, 9000, 1000, 8000, 500, "AAA261016C00105000"),
        # fails vol >= oi (vol 900 < oi 1000)
        _ua_row("BBB", 300_000, 900, 1000, 800, 50, "BBB261016C00100000"),
        # fails 3x average (avg 5000 -> 1.2x)
        _ua_row("CCC", 300_000, 6000, 1000, 1000, 4500, "CCC261016P00100000", typ="put"),
        # not in universe -> excluded
        _ua_row("ZZZ", 999_999, 9999, 10, 9000, 100, "ZZZ261016C00100000"),
    ]
    avg = {"AAA261016C00100000": 2000.0, "AAA261016C00105000": 2000.0,
           "BBB261016C00100000": 100.0, "CCC261016P00100000": 5000.0}
    monkeypatch.setattr(uas.uw, "unusual_activity", lambda *a, **k: {"data": rows})
    monkeypatch.setattr(uas.uw, "_rows", lambda p: p.get("data", []) if isinstance(p, dict) else p)
    monkeypatch.setattr(uas, "_avg_volume_peek_many", lambda occs: {o: avg.get(o) for o in occs})

    out, missing = uas._part_a({"AAA", "BBB", "CCC"})
    syms = [r["symbol"] for r in out]
    assert syms == ["AAA"]                     # only the one row that meets all three
    r = out[0]
    assert r["right"] == "C" and r["side"] == "BUY"
    assert round(r["vol_mult"], 1) == 4.5
    assert round(r["ask_pct"], 2) == round(8000 / 9000, 2)
    assert missing == []                        # every candidate's average was cached


def test_part_a_lists_missing_average(monkeypatch):
    rows = [_ua_row("AAA", 200_000, 9000, 1000, 8000, 500, "AAA261016C00100000")]
    monkeypatch.setattr(uas.uw, "unusual_activity", lambda *a, **k: {"data": rows})
    monkeypatch.setattr(uas.uw, "_rows", lambda p: p.get("data", []) if isinstance(p, dict) else p)
    monkeypatch.setattr(uas, "_avg_volume_peek_many", lambda occs: {o: None for o in occs})
    out, missing = uas._part_a({"AAA"})
    assert out == [] and missing == ["AAA261016C00100000"]


def test_side_mixed_and_sell():
    assert uas._side(8000, 500, 9000)[0] == "BUY"
    assert uas._side(500, 8000, 9000)[0] == "SELL"
    assert uas._side(4000, 4000, 9000)[0] == "MIXED"   # neither side >= 60%


# ---- Part B ---------------------------------------------------------------

def _series(today_c, today_p, hist):
    rows = [{"call_premium": today_c, "put_premium": today_p}]
    rows += [{"call_premium": c, "put_premium": p} for (c, p) in hist]
    return rows


def test_part_b_call_heavy_and_filtering(monkeypatch):
    # AAA: today ratio 4.0, normal ~1.0 -> disparity 4x -> CALL HEAVY
    # BBB: today ratio 1.1, normal 1.0 -> 1.1x -> not shown
    # CCC: today ratio 0.2, normal 1.0 -> 0.2x -> PUT HEAVY
    data = {
        "AAA": _series(400, 100, [(100, 100)] * 20),
        "BBB": _series(110, 100, [(100, 100)] * 20),
        "CCC": _series(20, 100, [(100, 100)] * 20),
    }
    monkeypatch.setattr(uas, "_ov_series_peek_many", lambda syms: {s: data.get(s) for s in syms})
    out, missing = uas._part_b(["AAA", "BBB", "CCC"])
    leanings = {r["symbol"]: r["leaning"] for r in out}
    assert leanings == {"AAA": "CALL HEAVY", "CCC": "PUT HEAVY"}   # BBB dropped
    assert out[0]["symbol"] == "AAA"                                # farthest from 1x first
    assert missing == []


def test_part_b_lists_missing(monkeypatch):
    monkeypatch.setattr(uas, "_ov_series_peek_many", lambda syms: {s: None for s in syms})
    out, missing = uas._part_b(["AAA", "BBB"])
    assert out == [] and missing == ["AAA", "BBB"]
