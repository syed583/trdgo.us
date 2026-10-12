"""Central, tunable thresholds for the Buy/Sell logic (Analysis + Trdgo Stock).

Every number the Oct-2026 swing/positional spec makes configurable lives here,
so the decision and trade-management rules can be tuned in one place instead of
being hunted down across the model files.

IMPORTANT: these are DECISION / ENTRY / EXIT thresholds only. The scoring
parameter *weights* (WEIGHTS / PARAM_MAP / INSIDER_SPLIT / ...) are deliberately
NOT here and are never changed by this work.
"""
from __future__ import annotations

# --- Direction decision cuts (0-100 score) --------------------------------
BUY_SCORE_MIN = 60.0          # score >= this is a BUY
STRONG_BUY_SCORE_MIN = 70.0   # >= this is a STRONG BUY
SELL_SCORE_MAX = 40.0         # score <= this is a SELL
STRONG_SELL_SCORE_MAX = 30.0  # <= this is a STRONG SELL

# A held position exits the moment its score crosses back through the middle.
SCORE_EXIT_BUY_BELOW = 50.0   # a long is squared off if score < 50
SCORE_EXIT_SELL_ABOVE = 50.0  # a short is squared off if score > 50

# --- Coverage / confidence (reused from the model) ------------------------
MIN_COVERAGE = 50.0
MIN_CONFIDENCE = 55.0
MIN_AGREEMENT = 58.0

# --- Entry timing / structure (intraday trigger) --------------------------
NO_ENTRY_OPEN_MIN = 15            # no new entry in the first 15 min after open
REQUIRE_OPENING_RANGE_BREAK = True  # BUY needs OR-high break, SELL needs OR-low

# --- Volume / 52-week filter ----------------------------------------------
RVOL_MIN = 1.5                # volume must be >= 1.5x its 20-day average
HI_LO_VOLUME_MULT = 1.5       # a new 52w high/low needs this volume to confirm
DIVERGENCE_SCORE_ADJUST = 10.0  # +/- points on RSI divergence / weak breakout
NEAR_52W_PCT = 2.0            # "within 2% of a 52-week high/low"

# --- Stops & trailing (Phase 2) -------------------------------------------
ATR_PERIOD = 14
RSI_PERIOD = 14
AVG_VOLUME_LOOKBACK = 20
STOP_ATR_MULT = 1.5
STOP_ATR_MULT_NEAR_52W = 1.0
STOP_FALLBACK_PCT = 2.5      # used when ATR is unavailable
BREAKEVEN_TRIGGER_ATR = 1.0  # at +1 ATR of profit, move the stop to entry
TRAIL_ATR_MULT = 1.5
TRAIL_ATR_MULT_NEAR_52W = 1.0

# --- Time exit (Phase 2) ---------------------------------------------------
TIME_EXIT_TRADING_DAYS = 3   # exit if +1 ATR profit not reached in 3 sessions
