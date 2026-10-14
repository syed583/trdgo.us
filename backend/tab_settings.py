"""Tunable weights / bands / cut-offs for the two new earnings tabs.

Kept in one place (per the build spec) so they can be tuned without touching
code. These are SCORING weights -- deliberately not in trade_settings.py, which
holds only decision/entry/exit thresholds.
"""
from __future__ import annotations

# --- Claude Last: Pre-Earnings Direction Model v3 (two-sided, SELL-capable) --
# Replaces Tab A v2. Same all-positive point rule (points = weight*(0.5+0.5*bias)),
# but every bias is symmetric so a stock can score below 50 and signal SELL.
# More weight on the inputs that turn bearish on their own (price trend, skew,
# insider selling); revenue estimates dropped (duplicate of EPS, almost always
# positive). Weights sum to 100, so a no-signal name lands at exactly 50.
# v3.1: skew/disparity split out from options flow, and insider split from short
# interest (inside view vs outside view -- they can disagree, so combining would
# cancel the signal). Revenue estimates stay dropped. Sum = 100.
V3_WEIGHTS: dict[str, int] = {
    "price_action": 20,            # EMA trend + 15d drift + relative strength vs SPY/sector
    "options_flow_unusual": 13,    # net directional flow + OI build + unusual prints
    "skew_disparity": 9,           # ATM call/put disparity + put-call skew trend (own param)
    "eps_rev_dir": 12,             # DIRECTION of EPS revisions (raised vs cut)
    "hist_price_reaction": 12,     # actual share-price move over the last ~8 reports
    "insider": 10,                 # company insiders' open-market buys/sells (inside view)
    "peers": 9,                    # peers that already reported, up or down
    "short_interest": 6,           # funds' short positioning, read with price (outside view)
    "prior_guidance": 5,           # consensus vs last quarter's guidance midpoint
    "dark_pool": 4,                # block prints, confirmation only
}
V3_PRICE_SPLIT: dict[str, int] = {"ema_trend": 7, "drift_15d": 7, "rel_strength": 6}
V3_FLOW_SPLIT: dict[str, int] = {"flow_oi": 8, "unusual": 5}
V3_SKEW_SPLIT: dict[str, int] = {"atm_disparity": 5, "skew_trend": 4}
# Short-interest thresholds: "high" short, and "rising" between readings.
V3_SHORT_INT: dict[str, float] = {"high_pct_float": 0.15, "high_days_to_cover": 5.0,
                                  "rising_pct": 0.20}
V3_CUTOFFS: dict[str, float] = {"buy": 55.0, "sell": 45.0}   # set 50/50 to test
# Below either threshold -> LOW CONFIDENCE, do not trade.
V3_CONFIDENCE: dict[str, float] = {"min_coverage": 0.50, "min_agreement": 0.55}

# --- Tab B: Short Straddle Scanner (banded, 100 pts) ------------------------
TAB_B_WEIGHTS: dict[str, int] = {
    "iv_rank": 40, "implied_move": 25, "impl_vs_hist": 20, "liquidity": 15,
}
TAB_B_CUTOFFS: dict[str, float] = {"sell": 70.0, "watch": 50.0}
# Band tables: (threshold, points), highest first; value below the last -> 0.
TAB_B_BANDS: dict[str, list[tuple[float, int]]] = {
    "iv_rank":      [(80, 40), (60, 30), (40, 15)],          # IVR %
    "implied_move": [(8, 25), (5, 18), (3, 10)],             # implied move %
    "impl_vs_hist": [(1.30, 20), (1.10, 14), (0.90, 6)],     # implied / avg hist
}
# Liquidity is a compound rule (spread % + OI), handled in code.
TAB_B_LIQUIDITY = {"full_spread": 3.0, "full_oi": 1000,
                   "mid_spread": 6.0, "low_spread": 10.0}  # pct of straddle
# Hard exclusions (skip regardless of score).
TAB_B_MIN_IMPL_VS_HIST = 0.90

# --- Short Straddle Scanner v2 (Earnings Options Module spec, Oct 2026) --------
# New, simpler ranking: no IV-history and no implied-vs-historical filter. Rank
# qualifying names by absolute ATM IV and by straddle premium % of price, then
# take the average of the two ranks (1 = most inflated). Only names reporting the
# NEXT SESSION qualify, and a few hard exclusions force SKIP.
# --- Short Straddle Change Spec (14 Oct 2026) -- all thresholds here, not in code
SS_LOOKBACK_SESSIONS = 10         # 10-session multiples: today vs this many trading sessions ago
SS_UNIVERSE_TDAYS = 10            # IV Crush Check universe: every calendar name reporting within
                                  # this many trading sessions (next session + next 10 trading days)
# IV Crush liquidity filter: drop a name when the event-expiry ATM is too thin to
# trade -- combined call+put open interest below SS_MIN_ATM_OI, or an ATM bid-ask
# spread wider than SS_MAX_ATM_SPREAD_PCT of mid.
SS_MIN_ATM_OI = 1000              # minimum combined ATM call + put open interest (contracts)
SS_MAX_ATM_SPREAD_PCT = 15.0      # maximum ATM bid-ask spread as a % of mid
# Next-session reporters are re-fetched once at this New York time so the rows are
# fresh right before the close (24h clock, America/New_York).
SS_REFRESH_NY_HHMM = "15:55"
SS_MIN_INFLATION = 1.15           # inflation_score below this -> NO REAL INFLATION
SS_MOVE_RATIO_SELL = 1.2          # Move ratio >= this (with inflation) -> SELL STRADDLE
SS_MOVE_RATIO_FLOOR = 1.0         # Move ratio < this -> NO EDGE (regardless of inflation)
SS_WIDE_SPREAD_PCT = 15.0         # bid-ask > this % of mid on a leg -> WIDE SPREAD tag (blocks SELL)
SS_BACK_GAP_DAYS = 21             # back expiry must be >= this many days after the event expiry
SS_BACK_MAX_DAYS = 60             # if no back expiry within this, use the furthest + tag BACK FAR
SS_HIST_REPORTS = 8               # historical move = avg of the last N reaction-session moves
SS_HIST_MIN = 8                   # fewer than this many reports -> tag LOW HISTORY (value still shown)
SS_ATM_MODE = "nearest"           # "nearest" | "delta50"

# The 67-name straddle watchlist (high-beta, liquid options), per the spec. The
# scanner ranks ONLY these names (intersected with the upcoming-earnings window),
# because they are pre-selected for the liquidity a short straddle needs.
TAB_B_WATCHLIST: list[str] = [
    "NVDA", "AMD", "AVGO", "MU", "TSM", "ARM", "SMCI", "MRVL", "QCOM", "INTC",
    "AMAT", "LRCX", "ON", "TSLA", "META", "AMZN", "NFLX", "GOOGL", "ORCL",
    "PLTR", "CRWD", "SNOW", "NET", "DDOG", "MDB", "ZS", "SHOP", "NOW", "ADBE",
    "TEAM", "OKTA", "APP", "U", "COIN", "MSTR", "MARA", "RIOT", "HOOD", "SOFI",
    "AFRM", "UPST", "PYPL", "XYZ", "UBER", "ABNB", "DASH", "RBLX", "SNAP",
    "PINS", "ROKU", "DKNG", "ETSY", "CVNA", "RIVN", "LCID", "NIO", "ENPH",
    "FSLR", "BABA", "PDD", "JD", "IONQ", "RKLB", "CELH", "LULU", "CHWY", "W",
]


# --- Table 2: Unusual Activity & Call/Put Disparity (14 Oct 2026) -----------
# Its own tab. Universe = IV Crush universe (next session + next 10 trading days,
# SS_UNIVERSE_TDAYS). All thresholds live here, not in code.
UA_REFRESH_SEC = 300.0            # refresh cadence (every 5 min during market hours)
# Part A -- Unusual Activity (one row per contract). A contract qualifies only if
# it meets ALL three:
UA_MIN_PREMIUM = 100_000.0        # premium (total $ traded today) at least this
UA_VOL_AVG_MULT = 3.0             # today's volume at least this x its 20-day average volume
UA_VOL_OI_MIN = 1.0               # today's volume at least this x open interest
UA_AVG_SESSIONS = 20              # "20-day average volume" = mean of this many prior sessions
UA_SIDE_PCT = 0.60                # BUY if >= this share at the ask; SELL if >= this at the bid
UA_MAX_ROWS = 40                  # show at most this many rows (sorted by premium, largest first)
UA_SCAN_PREMIUM_FLOOR = 90_000    # market-wide unusual pull floor (just under UA_MIN_PREMIUM)
UA_SCAN_LIMIT = 500               # rows to pull from the market-wide unusual scan
# Part B -- Call/Put Disparity (one row per stock).
UA_NORMAL_SESSIONS = 20           # "normal ratio" = avg call/put premium ratio over this many sessions
UA_CALL_HEAVY = 2.0               # disparity (today ratio / normal) >= this -> CALL HEAVY
UA_PUT_HEAVY = 0.5                # disparity <= this -> PUT HEAVY
UA_PARTB_MIN_PREMIUM = 500_000    # skip a stock whose total (call + put) premium today is below this


# --- Pre-Earnings Direction (v2 build spec, 13 Oct 2026) --------------------
# All-positive 0..weight scale: points = weight * (0.5 + 0.5 * bias); neutral or
# missing data = half the weight (never 0). Nine parameters, sum = 100. Signal
# BUY >= 65 / NEUTRAL / SELL <= 35. Exactly as the spec sketch.
PED_WEIGHTS: dict[str, int] = {
    "options": 16,        # disparity/skew + flow/OI + unusual activity
    "price_action": 14,   # the stock's own trend and its divergence from market/sector
    "insider": 12,        # open-market insider buying / selling (exclude 10b5-1)
    "peers": 10,          # peers that already reported, or peer sentiment
    "eps_rev": 10,        # direction + size of EPS revisions
    "hist_reaction": 10,  # how the stock moved after past results
    "rev_rev": 8,         # revenue revision direction (overlaps EPS -> lower)
    "prior_guidance": 6,  # tracking vs the guidance given last quarter
    "dark_pool": 4,       # block prints, confirmation only
}
PED_OPTIONS_SPLIT: dict[str, int] = {"disparity_skew": 7, "flow_oi": 6, "unusual": 3}  # sum 16
PED_CUTOFFS: dict[str, int] = {"buy": 65, "sell": 35}
