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
