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
TAB_B_MAX_LEG_SPREAD_PCT = 10.0   # bid-ask > this % of mid on EITHER leg -> illiquid
TAB_B_MIN_OI = 200                # ATM open interest below this -> illiquid (configurable)

# --- Long Straddle (IV run-up) — Strategy A of the Earnings Options Module -----
# Buy the ATM call + ATM put ~15 days before earnings (IV still has room to rise),
# exit the day BEFORE the result. Entry wants LOW IV rank (room to rise), tight
# spreads and healthy OI -- the mirror of the short-straddle liquidity gate.
LS_ENTRY_DAYS = 15            # ideal entry = ~15 calendar days before the result
LS_ENTRY_TOL = 3             # the entry window is ENTRY_DAYS +/- this (12..18)
LS_MAX_IV_RANK = 40.0        # above this at entry, the IV run-up edge is mostly gone
LS_MAX_LEG_SPREAD_PCT = 5.0  # entry checklist: ATM spread under ~5% of mid
LS_MIN_OI = 200              # healthy open interest floor

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
