"""Tunable weights / bands / cut-offs for the two new earnings tabs.

Kept in one place (per the build spec) so they can be tuned without touching
code. These are SCORING weights -- deliberately not in trade_settings.py, which
holds only decision/entry/exit thresholds.
"""
from __future__ import annotations

# --- Tab A: Pre-Earnings Stock Direction (all-positive 0..weight scale) -----
# Each parameter scores 0..weight; half the weight = neutral; a bullish lean
# goes above half, bearish below. Missing data = half (never 0). Sum = 100.
TAB_A_WEIGHTS: dict[str, int] = {
    "options": 16,          # disparity/skew + flow/OI + unusual activity (merged)
    "price_action": 14,     # the stock's own trend + divergence from mkt/sector
    "insider": 12,          # open-market insider buying/selling
    "peers": 10,            # peer read-through
    "eps_rev": 10,          # EPS estimate revisions
    "hist_reaction": 10,    # historical earnings reaction
    "rev_rev": 8,           # revenue revisions
    "prior_guidance": 6,    # on track to meet prior guidance
    "dark_pool": 4,         # block prints, confirmation only
}
# Split of the 16-pt "options" parameter into its three sub-signals.
TAB_A_OPTIONS_SPLIT: dict[str, int] = {"disparity": 7, "flow_oi": 6, "unusual": 3}
TAB_A_CUTOFFS: dict[str, float] = {"buy": 65.0, "sell": 35.0}

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
