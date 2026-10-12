import swing_manager as sm
import trade_settings as cfg


def test_initial_stop_long_and_short():
    assert sm.initial_stop("LONG", 100.0, 2.0) == 97.0      # 100 - 1.5*2
    assert sm.initial_stop("SHORT", 100.0, 2.0) == 103.0
    assert sm.initial_stop("LONG", 100.0, 2.0, near_52w=True) == 98.0  # 1.0x
    assert sm.initial_stop("LONG", 100.0, None) == 97.5     # 2.5% fallback


def test_breakeven_move_at_plus_one_atr():
    r = sm.manage(bias="LONG", entry=100.0, price=102.0, best_price=102.0,
                  stop=97.0, score=65.0, atr=2.0)
    assert r["moved_to_breakeven"] is True
    assert r["stop"] >= 100.0
    assert r["alert"] == "MOVE STOP TO BREAKEVEN"
    assert r["exit"] is None


def test_trailing_tightens_from_best_price():
    # best 110, atr 2 -> trail 110 - 1.5*2 = 107
    r = sm.manage(bias="LONG", entry=100.0, price=108.0, best_price=110.0,
                  stop=100.0, score=65.0, atr=2.0)
    assert r["stop"] == 107.0
    assert r["exit"] is None


def test_stop_never_moves_against_the_trade():
    # a weaker tick must not lower a long's stop
    r = sm.manage(bias="LONG", entry=100.0, price=104.0, best_price=110.0,
                  stop=108.0, score=65.0, atr=2.0)
    assert r["stop"] == 108.0  # held, not lowered to 107


def test_exit_on_trail_hit():
    r = sm.manage(bias="LONG", entry=100.0, price=106.9, best_price=110.0,
                  stop=107.0, score=65.0, atr=2.0)
    assert r["exit"] == "TRAIL"
    assert r["alert"] == "EXIT - TRAIL HIT"


def test_score_exit_is_strong_square_off():
    r = sm.manage(bias="LONG", entry=100.0, price=103.0, best_price=103.0,
                  stop=100.0, score=49.0, atr=2.0)
    assert r["exit"] == "SCORE"
    assert r["alert"] == "STRONG SQUARE-OFF"
    # short mirror
    r2 = sm.manage(bias="SHORT", entry=100.0, price=97.0, best_price=97.0,
                   stop=100.0, score=51.0, atr=2.0)
    assert r2["exit"] == "SCORE"


def test_time_exit_when_no_progress():
    r = sm.manage(bias="LONG", entry=100.0, price=100.5, best_price=101.0,
                  stop=97.0, score=65.0, atr=2.0,
                  days_held=cfg.TIME_EXIT_TRADING_DAYS)
    assert r["exit"] == "TIME"


def test_short_breakeven_and_trail():
    # short entry 100, price 98 (=+1 atr with atr 2) -> breakeven
    r = sm.manage(bias="SHORT", entry=100.0, price=98.0, best_price=98.0,
                  stop=103.0, score=35.0, atr=2.0)
    assert r["moved_to_breakeven"] is True
    assert r["stop"] <= 100.0


# --- 52-week filter (spec 1C / 2C) ----------------------------------------

def test_strong_breakout_is_allowed_no_penalty():
    r = sm.hi_lo_filter(bias="LONG", price=101.0, hi_52w=100.0, lo_52w=50.0,
                        rvol=2.0, rsi_new_extreme=True)
    assert r["near"] and r["at_extreme"]
    assert r["score_adjust"] == 0.0 and r["flag"] is None


def test_weak_breakout_thin_volume_is_cooled():
    r = sm.hi_lo_filter(bias="LONG", price=101.0, hi_52w=100.0, lo_52w=50.0,
                        rvol=0.9, rsi_new_extreme=True)
    assert r["score_adjust"] == -10.0 and r["flag"] == "exhaustion risk"


def test_rsi_divergence_at_high_is_cooled():
    r = sm.hi_lo_filter(bias="LONG", price=101.0, hi_52w=100.0, lo_52w=50.0,
                        rvol=2.0, rsi_new_extreme=False)
    assert r["score_adjust"] == -10.0 and r["flag"] == "exhaustion risk"


def test_away_from_extreme_no_effect():
    r = sm.hi_lo_filter(bias="LONG", price=80.0, hi_52w=100.0, lo_52w=50.0,
                        rvol=0.5, rsi_new_extreme=False)
    assert not r["near"] and r["score_adjust"] == 0.0


def test_short_near_low_bounce_risk():
    r = sm.hi_lo_filter(bias="SHORT", price=50.3, hi_52w=100.0, lo_52w=50.0,
                        rvol=0.8, rsi_new_extreme=True)
    assert r["near"] and r["score_adjust"] == 10.0 and r["flag"] == "bounce risk"
