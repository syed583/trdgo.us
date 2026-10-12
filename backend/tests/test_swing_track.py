import swing_track as st


def _reset():
    st._POSITIONS.clear()
    st._EVENTS.clear()


def test_open_long_on_buy_then_breakeven_then_exit():
    _reset()
    # open on BUY at 100, atr 2
    p = st.step("AAA", decision="BUY", price=100.0, score=65.0, atr=2.0,
                session_date="2026-10-12")
    assert p and p["bias"] == "LONG" and p["entry"] == 100.0 and p["stop"] == 97.0
    # price to 102 (+1 ATR) -> breakeven move
    p = st.step("AAA", decision="BUY", price=102.0, score=65.0, atr=2.0,
                session_date="2026-10-12")
    assert p["stop"] >= 100.0
    assert st.recent_events()[0]["alert"] == "MOVE STOP TO BREAKEVEN"
    # score falls < 50 -> STRONG SQUARE-OFF, position closes
    p = st.step("AAA", decision="BUY", price=103.0, score=48.0, atr=2.0,
                session_date="2026-10-12")
    assert p is None
    assert st.recent_events()[0]["alert"] == "STRONG SQUARE-OFF"
    assert st.positions() == []


def test_no_open_without_a_signal():
    _reset()
    assert st.step("BBB", decision="NEUTRAL", price=50.0, score=50.0, atr=1.0) is None
    assert st.positions() == []


def test_trail_hit_closes_position():
    _reset()
    st.step("CCC", decision="BUY", price=100.0, score=65.0, atr=2.0,
            session_date="2026-10-12")
    # run up to 110 (sets best, trails stop to ~107)
    st.step("CCC", decision="BUY", price=110.0, score=65.0, atr=2.0,
            session_date="2026-10-12")
    # pull back to 106.5 -> below trailed stop 107 -> exit
    p = st.step("CCC", decision="BUY", price=106.5, score=65.0, atr=2.0,
                session_date="2026-10-12")
    assert p is None
    assert st.recent_events()[0]["alert"] == "EXIT - TRAIL HIT"


def test_short_opens_on_sell_with_bounce_flag_near_low():
    _reset()
    p = st.step("DDD", decision="SELL", price=50.2, score=38.0, atr=1.0,
                hi_52w=100.0, lo_52w=50.0, rvol=0.8, rsi_new_extreme=True,
                session_date="2026-10-12")
    assert p["bias"] == "SHORT" and p["near_52w"] is True
    assert p["flag"] == "bounce risk" and p["stop"] == 51.2  # entry 50.2 + 1.0x ATR
