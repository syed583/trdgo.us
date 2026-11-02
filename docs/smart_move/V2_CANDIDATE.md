# V2 research candidate v2c-1 (2026-11-02)

Baselines kept frozen: V1 (40 points) and V2 v2-exp-2 (100 points). The candidate is separately versioned (`backend/smart_move_v2c.py`, hash in every result), research only, accuracy NOT VERIFIED.

## What the evidence allowed today
| Question | Evidence | Decision |
|---|---|---|
| Point-in-time analyst revisions | UW calendar `street_mean_est` saved at 3-4 request times: 116 stocks, longest span 13.3 h, 0 changed values; provider revision fields null in 2,913 rows; no other saved consensus history | **No revision history exists.** Revision factor stays unavailable; nothing manufactured; today's consensus never reused as an older one |
| Options-flow lookback 5/10/15/30 d | PIT backtest on 4,625 past events (alerts <= release-24 h, same rule): qualifying events 3 / 3 / 6 / 12; decided BUY/SELL <= 8 (hits 1-4); alert depth is the limit | **INCONCLUSIVE, no window ranked.** Stays 5 days. Coverage grows with the window, accuracy cannot be judged |
| News sentiment | 56 labels, all "neutral"; spec defines nothing | **Unverified, unscored, BLOCKED.** Points not redistributed. Analyst actions are already in the ANALYST factor (no double count); no timestamped guidance field is saved |
| Why 90 of 95 are DATA INCOMPLETE | 42 captured against a > 6 h calendar; 47 have 55 directional points (flow valid-absence/estimates/news missing); 1 failed API call | Genuine, not a bug in the thresholds. The status logic now names the cause |

## What changed in the candidate (status logic only)
GENUINE_NO_TRADE for a known ineligibility with complete data (20-day dollar volume known and < $20M); a named primary cause for every DATA INCOMPLETE (STALE_CALENDAR > API_FAILED > MISSING_REQUIRED > EVENT_NOT_CONFIRMED) with secondary causes (VALID_ABSENCE of flow = informational, no directional credit; UNVERIFIED_SEMANTICS). Weights, thresholds (60 directional points; bull >= 35, bear <= 10, margin >= 25), factor rules and the 100 points are identical; `scoring_changes`, `factor_redistribution` and `fallbacks` are empty.

## Result on the 95 collected stocks
Continuation and Contrarian: BUY 0, SELL 0, NO TRADE 5, DATA INCOMPLETE 90 (stale calendar 42, missing required 47, API failed 1). Identical counts to the frozen V2: no signal was created by re-labelling.

## Historical accuracy
NOT VERIFIED. No outcome exists for any prediction made here. The earliest collectable outcomes: BMO events of 2 Nov after 21:30 UTC on 2 Nov; AMC events after 21:30 UTC on 3 Nov (needs your authorization to fetch).

## Promotion
Not promoted. Needs: outcomes, >= 100 decided events per variant over >= 8 weeks, a stable calendar guard in a supervised run, and your approval.

---
## v2r-1: the new research candidate (2 Nov, later)
Supersedes v2c-1 on the dashboard (v2c-1 data remains). Scoring, weights, thresholds and factor rules are still identical to the frozen V2 (`scoring_changes = []`, `factor_redistribution = []`). Adds two transparent layers:

* **flowx-1** (`smart_move_flowx.py`): the V2 rule, with a per-stock waterfall, the observed lean when below the minimum, consistency, sweep share, unattributed share and a lookback comparison on the same alerts. Verified against the saved data: 5,863 alerts, 0 null fields, 0 future stamps; premium ask 44.3 % / bid 46.1 % / unattributed 9.6 %; `all_opening_trades` is true for 39 alerts and 0 inside 5 days (cannot replace volume/OI); no truncation inside the 5-day window. Only 3 stocks qualify with a direction at capture time (ON bearish, PLTR bullish, SNDX bearish); 3 more have enough activity but are balanced; 14 show a lean below the minimum; 57 have no alert in the last 5 days. By lookback 5/10/15/30 d: 3/3/6/12 qualify. **No parsing or classification bug exists; the low count is thin activity and a short alert history.**
* **news-x1** (`smart_move_news.py`): POSITIVE / NEGATIVE / NEUTRAL / UNVERIFIED with source, entity, timestamp and no-double-count rules (analyst actions, price moves, earlier results excluded). On the 56 saved headlines: 34 UNVERIFIED (crypto-futures commentary and ticker collisions such as "Vortex Energy" tagged VRTX and "New Book from AAPL"), 20 NEUTRAL, 1 NEGATIVE, 1 POSITIVE. It matches the author's labels 56/56 (**not independent**), so it earns **0 points**; displayed only.

### Result on the 95 stocks (both variants): BUY 0, SELL 0, NO TRADE 5, DATA INCOMPLETE 90
Binding constraints, in order:
1. **Coverage (60 directional points):** 90 stocks have 55 or less (42 also captured against a stale calendar). Only 5 reach 70, and all five are below the margin.
2. **Direction strength:** the highest bull score is 35 and the highest bear 40; only 6 stocks have bull >= 25 and 4 have bear >= 25 (Continuation).
3. **Near misses** (the unchanged rule WOULD fire if coverage allowed): Continuation BUY: VAL (bull 35, bear 2.5, 55 directional points). Contrarian SELL: CE (bear 40) and FANG (bear 36), both at 55. Each needs only +5 directional points, which could come from: a second analyst-consensus snapshot (from 8 Nov), qualifying options flow (15 points), or independently validated news (10 points). They are NOT signals today.

Evidence required for genuine signals: (a) the second consensus snapshot and a calendar-guarded collection, (b) flow inside the 5-day window before earnings, (c) independently labelled headlines for the news layer, (d) outcomes (>= 100 decided per variant over >= 8 weeks) before any accuracy statement.
