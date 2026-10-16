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
# v2 spec (Pre_Earnings_Model_v2.pdf, Oct 2026): 14 parameters, Max sums to 100.
# Scored on the spec's 5-step scale (strong bull=100% / mild bull=75% / neutral or
# no-data=50% / mild bear=25% / strong bear=0). #14 Implied Move is new and also
# drives the Implied-Move GATE (size/no-trade). Order = the spec's table.
# Owner override: the Implied-Move PARAMETER was removed from scoring (the IM
# gate below still runs). The remaining 13 directional params are rescaled from
# the spec's 96 up to 100 (proportional rounding, ranking preserved).
V3_WEIGHTS: dict[str, int] = {
    "eps_rev_dir": 13,             # 1  analyst EPS revision direction (30/90d)
    "options_flow": 11,            # 2  net bullish vs bearish premium, last 5 sessions
    "unusual_skew": 9,             # 3  unusual call/put prints + 25-delta skew
    "prior_guidance": 9,           # 4  last 4 quarters: beat + raised guide
    "hist_price_reaction": 8,      # 5  direction of next-day move, last 8 reports
    "price_action": 8,             # 6  daily structure (HH/HL vs LH/LL, 20-day hi/lo)
    "ema_trend": 7,                # 7  20/50/200 EMA stack
    "peers": 7,                    # 8  same-sector peers already reported
    "disparity": 6,                # 9  call/put volume ratio vs its 20-day average
    "realized_vs_implied": 6,      # 10 did the pre-result straddle lean match the move
    "insider": 6,                  # 11 open-market insider buys/sells (not 10b5-1)
    "short_interest": 5,           # 12 short %float + days-to-cover, read with price
    "dark_pool": 5,                # 13 block prints vs VWAP, confirmation only
}                                  # sum = 100 (implied_move dropped from scoring)
# Implied-Move parameter (#14) points by IM ratio = implied move % / hist avg move %
# (spec 3b). Expressed as a long-side bias fed to the 5-step scale.
V3_IM_POINTS = [  # (ratio_below, bias)  -- first match wins; last is the >=1.5 case
    (0.9, 1.0),    # market under-expects the move  -> 4/4
    (1.1, 0.5),    # 0.9-1.1                         -> 3/4
    (1.3, 0.0),    # 1.1-1.3                         -> 2/4
    (1.5, -0.5),   # 1.3-1.5                         -> 1/4
]                  # > 1.5 (or priced-in)            -> 0/4  (bias -1.0)
# Implied-Move GATE (spec 3c): ratio decides size / no-trade regardless of score.
V3_IM_GATE: dict[str, float] = {"half_above": 1.1, "no_trade_above": 1.5}
# Short-interest thresholds: "high" short, and "rising" between readings.
V3_SHORT_INT: dict[str, float] = {"high_pct_float": 0.15, "high_days_to_cover": 5.0,
                                  "rising_pct": 0.20}
# Owner override of the spec §4 bands: BUY >= 55, NEUTRAL 45-55, SELL <= 45.
V3_CUTOFFS: dict[str, float] = {"buy": 55.0, "sell": 45.0}
# Below either threshold -> LOW CONFIDENCE, do not trade.
V3_CONFIDENCE: dict[str, float] = {"min_coverage": 0.50, "min_agreement": 0.55}

# --- Grok: Pre-Earnings Direction (9 research-strength factors) --------------
# Weights set by each factor's evidence strength (the owner's research table):
# High / Moderate-High / Moderate / Low-Moderate / Low. Sum = 100. Same all-
# positive 5-step scale and BUY>=55 / SELL<=45 bands as Claude New.
GROK_WEIGHTS: dict[str, int] = {
    "eps_revisions": 18,         # High
    "prior_beat_history": 15,    # Moderate-High
    "options_skew_flow": 12,     # Moderate
    "realized_vs_implied": 11,   # Moderate
    "peer_readthrough": 11,      # Moderate (timing dependent)
    "short_interest": 10,        # Moderate
    "analyst_dispersion": 10,    # Moderate
    "premarket_momentum": 8,     # Low-Moderate
    "dark_pool_insiders": 5,     # Low
}                                # sum = 100

# --- Meta: Pre-Earnings Direction (owner's 11-factor / 100-pt model) ----------
# Adds Implied-vs-Historical move, IV Rank and Gamma/OI concentration to the
# direction factors. All read from the warmed equity signals + trade cache, so
# the list scan stays fast. Same 5-step scale and BUY>=55 / SELL<=45 bands.
META_WEIGHTS: dict[str, int] = {
    "eps_revisions": 13,
    "implied_vs_hist": 13,       # NEW -- implied move vs historical avg move
    "options_flow": 11,
    "iv_rank": 11,               # NEW -- IV level signal (engine implied_volatility)
    "unusual_skew": 9,
    "prior_guidance": 9,
    "revenue_revisions": 9,      # NEW -- revenue estimate revisions / whisper
    "hist_reaction": 8,
    "gamma_oi": 8,               # NEW -- gamma / OI concentration (oi_positioning)
    "peers": 7,
    "short_interest": 2,
}                                # sum = 100

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
