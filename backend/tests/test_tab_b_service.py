"""Tab B -- short straddle scanner: bands, action, hard exclusions."""

import tab_b_service as tb
import tab_settings as cfg


def test_bands():
    B = cfg.TAB_B_BANDS
    assert tb._band(B["iv_rank"], 85) == 40
    assert tb._band(B["iv_rank"], 65) == 30
    assert tb._band(B["iv_rank"], 50) == 15
    assert tb._band(B["iv_rank"], 30) == 0
    assert tb._band(B["implied_move"], 9) == 25
    assert tb._band(B["impl_vs_hist"], 1.4) == 20
    assert tb._band(B["impl_vs_hist"], 0.8) == 0


def test_liquidity():
    # Full points now need a tight spread AND OI >= the floor; thin OI caps at mid.
    assert tb._liquidity_pts(2, 2000) == 15
    assert tb._liquidity_pts(2, 100) == 10        # tight spread, thin OI
    assert tb._liquidity_pts(2) == 10             # tight spread, OI unknown
    assert tb._liquidity_pts(5, 2000) == 10
    assert tb._liquidity_pts(9, 2000) == 5
    assert tb._liquidity_pts(12, 2000) == 0
    assert tb._liquidity_pts(None, 2000) == 0


def _patch(monkeypatch, *, implied, hist, iv, spread, oi=2000,
           date="2026-10-20", dec="BUY"):
    import swr
    import uw_volatility_service as v
    import earnings_options_service as o
    monkeypatch.setattr(swr, "peek", lambda k: (
        {"expected_move": {"percent": implied, "current": 100.0},
         "historical_moves": [{"move_pct": hist}, {"move_pct": -hist}],
         "earnings": {"date": date}, "overall_decision": dec}
        if "earn:trade" in k else None))
    monkeypatch.setattr(v, "get_volatility", lambda s: {"stats": {"iv_rank": iv}})
    monkeypatch.setattr(o, "_atm_straddle",
                        lambda s, spot: {"spread_pct": spread / 100.0, "oi": oi})


def test_sell_candidate(monkeypatch):
    _patch(monkeypatch, implied=9.0, hist=5.0, iv=85.0, spread=2.0, oi=2000)
    r = tb.compute("AAA")
    assert r["score"] == 100 and r["action"] == "SELL" and r["tilt"] == "bullish"
    assert r["exclusion"] is None
    assert r["oi"] == 2000


def test_thin_oi_caps_liquidity(monkeypatch):
    # Tight spread but OI below the floor -> liquidity 10 not 15 -> score 95.
    _patch(monkeypatch, implied=9.0, hist=5.0, iv=85.0, spread=2.0, oi=50)
    r = tb.compute("DDD")
    assert r["score"] == 95 and r["action"] == "SELL"


def test_hard_exclusion_low_ratio(monkeypatch):
    # implied 4 vs historical 6 -> ratio 0.67 < 0.90 -> SKIP regardless of score.
    _patch(monkeypatch, implied=4.0, hist=6.0, iv=85.0, spread=2.0, dec="SELL")
    r = tb.compute("BBB")
    assert r["action"] == "SKIP" and r["exclusion"]
    assert r["tilt"] == "bearish"


def test_illiquid_excluded(monkeypatch):
    _patch(monkeypatch, implied=9.0, hist=5.0, iv=85.0, spread=14.0)
    r = tb.compute("CCC")
    assert r["action"] == "SKIP" and "Illiquid" in (r["exclusion"] or "")
