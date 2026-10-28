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
# Claude = "Pre-Earnings Direction Model v2 (Trade Go US)" — 12 parameters / 100 pts.
# Revised from Anas v1 for trades 1-2 days before the result: Forward Guidance
# (post-result) dropped; Historical Earnings Reaction (8) and Big Fund Positions
# (3) added -> sums to exactly 100. Bands BUY > 70 / SELL < 50 (CLAUDE_CUTOFFS).
V3_WEIGHTS: dict[str, int] = {
    "options_combined":  19,   # Options Flow + Unusual Activity + Put/Call Skew
    "eps_estimates":     15,   # EPS Estimates & Revisions
    "price_action_rs":   15,   # Price Action & Relative Strength
    "revenue_estimates": 10,   # Revenue Estimates & Revisions
    "sector_market":     10,   # Sector & Market Regime
    "hist_reaction":      8,   # Historical Earnings Reaction (last 8 quarters)
    "peer_readthrough":   7,   # Peer Earnings Read-through
    "implied_vs_realized": 5,  # Implied vs Realized Move Factor
    "big_fund":           3,   # Big Fund Positions (BlackRock/Vanguard add/reduce)
    "dark_pool":          3,   # Dark Pool Confirmation
    "short_insider":      3,   # Short Interest & Insider Activity
    "calendar_timing":    2,   # Earnings Calendar & Timing
}                              # sum = 100

# Claude v2 bands: BUY > 70, SELL < 50, 50-70 NEUTRAL. (Grok/Meta keep V3_CUTOFFS.)
CLAUDE_CUTOFFS: dict[str, float] = {"buy": 70.0, "sell": 50.0}
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
# Grok = "Anas Earnings Prediction Model v1" (11 parameters). The spec's raw
# weights sum to 97 (EPS 15, Price/RS 15, Options-combined 19, Revenue 10,
# Sector 10, Guidance 8, Peers 7, Dark Pool 3, Short/Insider 3, Impl-vs-Real 5,
# Calendar 2). Rescaled x100/97 so the total is 100 and the 55/45 bands stay
# centred at 50 (same approach as Kimi's 88->100). Relative weights unchanged.
GROK_WEIGHTS: dict[str, float] = {
    "eps_estimates":     15.46,   # EPS Estimates & Revisions (15)
    "price_action_rs":   15.46,   # Price Action & Relative Strength (15)
    "options_combined":  19.59,   # Options Flow + Unusual + Put/Call Skew (19)
    "revenue_estimates": 10.31,   # Revenue Estimates & Revisions (10)
    "sector_market":     10.31,   # Sector & Market Regime (10)
    "forward_guidance":   8.25,   # Forward Guidance & Analyst Outlook (8)
    "peer_readthrough":   7.22,   # Peer Earnings Read-through (7)
    "implied_vs_realized": 5.15,  # Implied vs Realized Move Factor (5)
    "dark_pool":          3.09,   # Dark Pool Confirmation (3)
    "short_insider":      3.09,   # Short Interest & Insider Activity (3)
    "calendar_timing":    2.06,   # Earnings Calendar & Timing (2)
}                                 # sum = 100.0 (97 rescaled x100/97)

# --- ChatGPT: US Pre-Earnings Direction Model V1 (16 features / 100 pts) -------
# TRDGO.ai spec. Directional score 0-100 from 16 normalised (-1..+1) features;
# BULLISH / NEUTRAL / BEARISH by configurable thresholds. Reads the warmed equity
# signals + trade cache (analyst dispersion is detail-only via Finnhub).
CHATGPT_WEIGHTS: dict[str, int] = {
    "eps_estimates":     16,   # EPS Estimates & Revisions
    "revenue_estimates": 10,   # Revenue Estimates & Revisions
    "price_action_rs":   16,   # Price Action & Relative Strength
    "options_flow_oi":   15,   # Options Flow & Open Interest Positioning
    "sector_market":     10,   # Sector & Market Regime
    "forward_guidance":   8,   # Forward Guidance & Analyst Outlook
    "peer_readthrough":   7,   # Peer Earnings Read-through
    "unusual_options":    5,   # Unusual Options Activity
    "disparity_skew":     5,   # Options Disparity & Put/Call Skew
    "dark_pool":          3,   # Dark Pool Confirmation
    "short_insider":      3,   # Short Interest & Insider Activity
    "calendar_timing":    2,   # Earnings Calendar & Timing
}                                # sum = 100
# 5-tier bands: 80-100 STRONG BUY / 65-79 BUY / 40-64 NEUTRAL / 21-39 SELL / 0-20 STRONG SELL
CHATGPT_THRESHOLDS: dict[str, float] = {"strong_buy": 80.0, "buy": 65.0,
                                        "neutral": 40.0, "sell": 21.0}

# --- Kimi: Pre-Earnings Direction (14 factors, category-grouped) --------------
# From the owner's CSV (weights summed to 88); rescaled x100/88 to 100 so the
# 55/45 bands stay meaningful (relative weights unchanged). Adds price-target /
# rating changes, relative strength vs sector (replaces EMA) and a SPY+VIX market
# regime. Same 5-step scale and BUY>=55 / SELL<=45 bands as the other models.
_KIMI_RAW = {                 # Anas v1 review spec (sums to 90) -> rescaled x100/90
    "eps": 12, "revenue": 6, "guidance": 10, "options": 20,
    "price_rs": 12, "hist_reaction": 7, "peers": 6, "sector_market": 8,
    "short_interest": 3, "insider": 1, "dark_pool": 2, "timing": 3,
}
KIMI_WEIGHTS: dict[str, float] = {k: round(v * 100.0 / 90.0, 2) for k, v in _KIMI_RAW.items()}
# Kimi 5-tier bands: >=65 strong bull, 58-64 bull, 43-57 noise, 36-42 bear, <=35 strong bear.
KIMI_CUTOFFS: dict[str, float] = {"strong_buy": 65.0, "buy": 58.0, "sell": 42.0, "strong_sell": 35.0}

# --- Gemini: 90-Day US Earnings Model (6 weighted categories) -----------------
# Category-level weighting from the spec; each category is the average of its
# sub-components (normalised -1..+1), then weighted. Sum = 100.
GEMINI_WEIGHTS: dict[str, int] = {      # Anas Engine v2 (11 params)
    "eps_revision": 12, "revenue_revision": 8,
    "price_rs": 12, "sector_market": 8, "options_flow": 14, "iv_crush": 8,
    "forward_guidance": 8, "guidance_hit_rate": 8, "short_squeeze": 6,
    "insider_buyback": 8,
}                                 # sum = 92; scores are rescaled to 100 from the present weights
# Bands: STRONG BUY >=68, SPEC BUY 58-67, NOISE 43-57, SPEC SHORT 33-42, STRONG SHORT <=32.
GEMINI_CUTOFFS: dict[str, float] = {"strong_buy": 68.0, "buy": 58.0, "sell": 42.0, "strong_sell": 32.0}

# --- Claude Pro: refined direction model (merged blocks, magnitude separated) -
# Addresses the critique: positioning signals merged into one block (they
# co-move), technicals merged into trend/run-up, magnitude pulled OUT of the
# direction score into a separate Move-Size field, heavier weight on expectations.
# Block weights sum to 100; sub-weights sum to their block.
CLAUDE_PRO_BLOCKS: dict[str, dict] = {
    "expectations": {"label": "Expectations & Fundamentals", "subs": {
        "eps_revisions": 12, "revenue_revisions": 9, "guidance": 8,
        "estimate_dispersion": 5, "implied_vs_hist": 6}},          # 40
    "positioning": {"label": "Positioning (flow/skew/dark pool)", "subs": {
        "options_flow": 7, "unusual": 5, "skew_disparity": 5, "dark_pool": 3}},  # 20
    "trend_runup": {"label": "Pre-earnings Trend / Run-up", "subs": {
        "price_structure": 5, "ema_trend": 4, "runup": 3}},        # 12
    "history": {"label": "History (reaction + peers)", "subs": {
        "hist_reaction": 9, "peers": 6}},                          # 15
    "short_insider": {"label": "Short Interest & Insiders", "subs": {
        "short_interest": 5, "insider": 3}},                       # 8
    "regime": {"label": "Sector / Market Regime", "subs": {
        "spy_vix": 5}},                                            # 5
}                                 # block weights sum to 100

# --- Grok Intraday: short-term directional model (10 live signals) ------------
# Intraday/short-term weighting: heavy on options flow + unusual activity +
# participation, with trend/levels/vol as filters. All from the live equity
# signals. Sum = 100.
INTRADAY_GROK_WEIGHTS: dict[str, int] = {
    "options_flow": 34,       # 1  options flow (sweeps + premium)
    "trend_price": 24,        # 2  price trend & structure
    "unusual_activity": 10,   # 3  unusual activity (filtered)
    "volume_pcr": 8,          # 4  volume + put/call
    "key_levels": 8,          # 5  gamma / key option levels
    "oi_change_expiry": 6,    # 6  near-term OI change
    "event_radar": 5,         # 7  event / catalyst
    "rsi_momentum": 3,        # 8  RSI / momentum
    "disparity": 2,           # 9  options disparity (C/P)
    "volatility_em": 0,       # -- expected move (sizing only, no score)
}                             # sum = 100

# --- Kimi Intraday: same flat 10-signal model as Grok, Kimi weight profile -----
INTRADAY_KIMI_WEIGHTS: dict[str, int] = {
    "options_flow": 28,       # 1  options flow (sweeps + premium)
    "trend_price": 23,        # 2  price trend & structure
    "unusual_activity": 13,   # 3  unusual activity (filtered)
    "volume_pcr": 11,         # 4  volume + put/call
    "key_levels": 9,          # 5  gamma / key option levels
    "oi_change_expiry": 7,    # 6  near-term OI change
    "event_radar": 4,         # 7  event / catalyst
    "rsi_momentum": 3,        # 8  RSI / momentum
    "disparity": 2,           # 9  options disparity (C/P)
    "volatility_em": 0,       # -- expected move (sizing only, no score)
}                             # sum = 100

# --- Meta Intraday: 7-signal short-term model (live board like Trdgo Stock) ----
META_INTRADAY_WEIGHTS: dict[str, int] = {
    "options_flow": 25,       # 1  options flow (sweeps + premium)
    "ema_trend_price": 20,    # 2  price trend & structure
    "unusual_activity": 15,   # 3  unusual activity (filtered)
    "key_levels": 12,         # 4  gamma / key option levels
    "volume_pcr": 10,         # 5  volume + put/call
    "daily_oi_change": 8,     # 6  near-term OI change
    "event_radar": 5,         # 7  event / catalyst
    "rsi_momentum": 5,        # 8  RSI / momentum
}                             # sum = 100

# --- Gemini Intraday: 80/20 market/company execution model (rescaled to 100) --
# From the demo scorecard (directional weights summed to 91; IV/EM is sizing-only
# and excluded). Rescaled x100/91 to 100. Equity-engine signals.
GEMINI_INTRADAY_WEIGHTS: dict[str, int] = {
    "options_flow": 13,       # market pillar
    "unusual_activity": 12,
    "disparity": 9,
    "volume_pcr": 8,
    "key_levels": 7,
    "oi_change": 8,           # daily OI + expiry + OI positioning
    "ema_trend": 11,
    "rsi_price": 10,          # RSI + price action
    "insider_ownership": 10,  # company pillar
    "earnings_catalyst": 10,  # earnings / mergers / funding
    "dividends_event": 2,     # dividends + event radar
}                             # sum = 100

# --- Meta: Pre-Earnings Direction (owner's 11-factor / 100-pt model) ----------
# Adds Implied-vs-Historical move, IV Rank and Gamma/OI concentration to the
# direction factors. All read from the warmed equity signals + trade cache, so
# the list scan stays fast. Same 5-step scale and BUY>=55 / SELL<=45 bands.
# Meta = owner's 11-parameter / 100-point model (same spec as Grok "Anas v1").
# Spec weights sum to 97; rescaled x100/97 to 100 so the 55/45 bands stay centred
# at 50 (relative weights unchanged). Calendar & Timing has no directional bias.
META_WEIGHTS: dict[str, float] = {
    "eps_estimates":     15.46,   # EPS Estimates & Revisions (15)
    "price_action_rs":   15.46,   # Price Action & Relative Strength (15)
    "options_combined":  19.59,   # Options Flow + Unusual + Put/Call Skew (19)
    "revenue_estimates": 10.31,   # Revenue Estimates & Revisions (10)
    "sector_market":     10.31,   # Sector & Market Regime (10)
    "forward_guidance":   8.25,   # Forward Guidance & Analyst Outlook (8)
    "peer_readthrough":   7.22,   # Peer Earnings Read-through (7)
    "implied_vs_realized": 5.15,  # Implied vs Realized Move Factor (5)
    "dark_pool":          3.09,   # Dark Pool Confirmation (3)
    "short_insider":      3.09,   # Short Interest & Insider Activity (3)
    "calendar_timing":    2.06,   # Earnings Calendar & Timing (2)
}                                 # sum = 100.0 (97 rescaled x100/97)

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


# Minimum data coverage for a directional call. Below this, a model shows "Low data"
# instead of BUY/SELL (earnings tabs + Pre-Earnings Direction).
MIN_COVERAGE_PCT: float = 70.0

# Calendar timing (BMO/AMC, weekday) is shown as a label only until a backtest
# justifies a weight: every multiplier is 1.0 and the parameter is not scored.
TIMING_SCORED: bool = False
TIMING_MULT: dict[str, float] = {"bmo": 1.0, "amc": 1.0, "tue_thu": 1.0, "mon_fri": 1.0}


# Earnings analysis (earn:equity / earn:trade) freshness. The budgeted cache warmer
# refreshes each name about once a day; a long TTL means page views never trigger a
# paid recompute in between (a stale value is served while the warmer catches up).
EARN_CACHE_TTL: float = 48 * 3600.0


# Signal verification (signal_verification.py): independently re-derives each BUY / SELL's score from its parameters, weights and the
# existing formula and judges the data behind it. "off" | "audit", set by the environment variable INTRADAY_VERIFICATION_MODE
# (default off). AUDIT ONLY: it records the outcome after the paper-trade tick has committed and never blocks, delays or changes a
# trade; there is deliberately no enforcing mode.
import os as _os
INTRADAY_VERIFICATION: dict = {"mode": (_os.getenv("INTRADAY_VERIFICATION_MODE") or "off").strip().lower(), "score_tolerance": 0.05,
                               "max_seconds": 5.0}


# Unusual Whales plan policy for the cache warmer. The subscription (Whale Bundle Advanced) has UNLIMITED daily
# requests; the provider's only limit is 3 simultaneous in-flight REST requests per token across every process
# that uses it (enforced in unusualwhales_service). Set unlimited_daily False only if the account ever gets a quota.
UW_PLAN: dict = {
    "unlimited_daily": True,
    "daily_limit_if_capped": 13000,    # used ONLY when unlimited_daily is False
    "hourly_cap_live_hours": 400,      # warming requests per clock hour, weekdays 04:00-20:00 ET (live trading owns the connections)
    "hourly_cap_off_hours": 6000,      # ... and outside those hours
    "max_tries_per_symbol": 3,         # then the symbol ends as PARTIAL_DATA / failed for the day
    "retry_delay_s": 600,              # base delay between a symbol's tries (x the try number)
}
