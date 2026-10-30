# Offline baseline - development-period pipeline check (run 1 of 1), 2026-10-30

Development period only (earnings dates 2024-10-01 .. 2025-09-30). Validation and test were never loaded; no threshold was changed; no provider request, migration, scheduled job or production change. **These are descriptive pipeline-check statistics, not prediction accuracy.** All 186 events are timing-uncertain exploratory events (release time never verified).

## 1. Frozen pre-registration
`prereg_baseline_v1.json`, SHA-256 **`9316348ba0d290342c7cefb551ec973b463029e1371a3db8a81a18493f8ff77e`** (frozen before the code existed; it holds the rule constants, splits, cost model, sealing rules and the hashes of the input files; the code refuses to run if the file or any input differs). Code hashes used for the run are in `results/dev_run_record.json`.

## 2. Leakage and guard tests (`test_smart_baseline.py`: 15 passed with the local data, 14 passed + 1 skipped without it)
* pre-registration hash verified and tampering detected; code constants (costs, liquidity gate, flat band, buffer, order deadline) equal the pre-registered ones;
* sealed splits (`validation`, `test`, anything else) raise `SealedDataError`; sealed rows are never returned; candle loading is truncated at 2025-10-01 and a later (or post-market) price cannot reach a feature;
* features are identical when every price after the last usable session is deleted or multiplied by 7; known-answer return test; a suspect jump inside a window makes the feature missing (not repaired) and a jump outside it does not; the event's own reaction session is exempt;
* prior-reaction feature counts only reactions closed (plus 45 min) before the cutoff and needs 3; late/future events cannot enter;
* bullish and bearish evidence are counted separately and never net (conflict -> NO TRADE); missing features give no vote; coverage and liquidity gates; contrarian mirror;
* cutoff / calendar: weekend, Monday holiday, DST start and standard-time cases for the last usable session; entry order must precede the close by 10 minutes (Sunday cutoff against a Friday close fails; a Saturday is not a session);
* costs known answers; FLAT outcomes stay in the denominators; a second run is refused.
Real-data checks over all 186 development events (inside the run and in the tests): last usable session close + 45 min <= cutoff **0 violations**; feature source date after the last session **0**; entry (base close) not after the cutoff **0**; entry order deadline missed **0**; entry on or before the last feature session **0**; truncated-data recomputation changes a feature **0**; recomputed last session and reconstructed return equal the stored values for **186 of 186** events (so **no event is excluded** for an unreliable return).

## 3. Eligible development events
186 (AMC 94, BMO 92; tier AMC-not-contradicted 94, BMO-filing-bound 92); liquidity (20-day dollar volume): liquid 49, mid 93, thin or unknown 44. Eligible = reconstructable return, tradable under the entry rule (all 186).

## 4. BUY / SELL / NO TRADE counts
| Rule | BUY | SELL | NO TRADE | Coverage | Abstention |
|---|---|---|---|---|---|
| continuation (pre-declared primary) | 28 | 26 | 132 | 29.0 % | 71.0 % |
| contrarian mirror (pre-declared second rule) | 26 | 28 | 132 | 29.0 % | 71.0 % |
| always BUY / always SELL | 186 / 0 | 0 / 186 | 0 | 100 % | 0 |
| momentum sign | 91 | 92 | 3 | 98.4 % | 1.6 % |
| previous-reaction sign | 68 | 60 | 58 | 68.8 % | 31.2 % |
Reasons for the 132 abstentions of the continuation rule: below margin or conflict 88, illiquid or unknown liquidity 41, insufficient coverage 3 (54 events produced a call). Votes available per event: 0: 1, 1: 2, 2: 142, 3: 41 (the history vote exists for only 43 of 186 events because the data hold no reactions before 2024-10).

## 5. Development-only performance versus baselines
Strict hit rate counts FLAT as a miss; direction-only excludes FLAT (shown separately). 95 % Wilson intervals; returns are next-session close-to-close, in %, per decided call, cost model x1 (about 18 bp median round trip), 95 % weekly-cluster bootstrap intervals.
| Rule | Decided | Strict hit [95 % CI] | Direction-only [CI] (n) | Net mean x1 [CI] | SPY-adjusted net x1 [CI] |
|---|---|---|---|---|---|
| continuation | 54 (FLAT 2) | 33.3 % [22.2, 46.6] | 34.6 % [23.2, 48.2] (52) | -2.62 [-5.90, +0.83] | -2.35 [-5.57, +0.99] |
| contrarian mirror | 54 (FLAT 2) | 63.0 % [49.6, 74.6] (Bonferroni 2-rule [47.7, 76.0]) | 65.4 % [51.8, 76.8] (52) | +2.32 [-1.11, +5.62] | +2.06 [-1.27, +5.33] |
| always BUY | 186 (FLAT 13) | 51.6 % [44.5, 58.7] | 55.5 % [48.0, 62.7] (173) | +1.02 [-0.42, +2.41] | +0.91 [-0.77, +2.59] |
| always SELL | 186 (FLAT 13) | 41.4 % [34.6, 48.6] | 44.5 % [37.3, 52.0] (173) | -1.45 [-2.82, 0.00] | -1.35 [-3.01, +0.34] |
| momentum sign | 183 (FLAT 13) | 37.7 % [31.0, 44.9] | 40.6 % [33.5, 48.1] (170) | -1.60 [-2.64, -0.77] | -1.43 [-2.54, -0.61] |
| previous-reaction sign | 128 (FLAT 9) | 48.4 % [40.0, 57.0] | 52.1 % [43.2, 60.9] (119) | +0.15 [-1.26, +1.25] | +0.23 [-1.24, +1.37] |
| coverage-matched random (1,000 draws of 54 calls) | 54 | mean 46.1 % (2.5-97.5 %: 33.3 - 59.3) | - | mean -0.28 (-2.69 to +2.22) | - |
Confusion matrices (decision x UP / FLAT / DOWN): continuation BUY 12 / 1 / 15, SELL 19 / 1 / 6, NO TRADE 65 / 11 / 56; contrarian BUY 19 / 1 / 6, SELL 12 / 1 / 15. The full set is in `results/dev_run_results.json`.
Cost and slippage sensitivity (continuation net mean per call at cost x0 / x1 / x2 / x3: -2.47 / -2.62 / -2.76 / -2.91; slippage-only x2 / x3: -2.74 / -2.86; contrarian: +2.47 / +2.32 / +2.18 / +2.03 and +2.20 / +2.08). Vote-threshold sensitivity (x0.6 / x1.4): continuation strict hit 33.8 % over 71 calls and 24.4 % over 41 calls, shown only, never used to choose anything.

### How to read this (important)
* **The pre-declared primary rule (continuation) did not work in the development period** (33 % strict hit rate, negative net return). Its pre-declared mirror shows 63 % / +2.3 %. The mirror is not independent evidence: it is the same 54 events with the sides swapped. Its 95 % interval (49.6 - 74.6 %) and its net-return interval (-1.1 to +5.6 %) both include no-edge values, and with two rules the Bonferroni interval is wider still. 54 calls in 12 months of one market regime, clustered by earnings week, with a single development period cannot support a claim.
* The mirror stays a **secondary hypothesis declared in advance**; it must not replace the primary rule after seeing these results, and nothing here selects a rule. Whether to test either rule on the sealed validation period is a decision for the owner.
* Momentum-sign's 37.7 % (interval below 50 %) points in the same direction (stocks with strong trailing returns reacted worse on average in this period); this may reflect the regime, the cluster structure or the flat-band convention, not a stable property.
* "Always BUY" earned +1.0 % per event after costs in this period (interval includes zero): a positive base drift that any rule must beat, which is why market-adjusted figures are shown.

## 6. Missing-feature and corporate-action exclusions
* Missing features (events x feature): `mean4` (needs >= 3 earlier reactions) 143 of 186; ret20, rel20, rv20, adv20 each missing for 3 events because of a suspect price jump in their window (VRT 2025-02-12, CNC 2025-07-25, FTV 2025-07-30). These 3 events stay in the dataset with those features missing (no repair, no imputation); with ret20, rel20 and adv20 missing they produce NO TRADE (insufficient coverage / unknown liquidity). Other suspect jumps in the dev series (NVCR 2024-12-02, PI 2024-04-25) do not touch any development feature window.
* Excluded outcomes: **none**. The reaction windows of the FTV, CNC and VRT events do not contain their jump sessions, and all 186 reconstructed returns equal the stored ones.
* **FTV corporate-action investigation (existing evidence only):** 2025-06-30 close-to-close -27.2 % (71.60 to 52.13); open 52.05 = -27.3 % gap with a 4.8 % day range; volume 4.1x the 20-day average; no rebound; consensus EPS steps down from 1.12 and 0.85 (previous two reports) to 0.60 for the quarter ending 2025-06-30 (actual 0.58); an off-cycle 8-K Item 2.02 accepted 2025-06-30 10:05 ET plus another 2025-06-10. This pattern is consistent with a separation / distribution (the series is therefore unadjusted for such events) but the cause is **not verified from an external source**. Treatment: ret20, rel20, rv20 and adv20 are missing for the FTV event 2025-07-30 (20 sessions after the gap); its reaction return is unaffected (base and reaction closes lie after the gap) and was kept; the provider's EPS fields for that quarter are on a different perimeter and are not used.

## 7. Remaining limitations
1. Release times unverified for every event (SEC acceptance is an upper bound; AMC cannot be positively supported); cutoffs are conservative assumptions.
2. 186 events, 48 symbols, one 12-month period and regime; survivorship bias (today's universe); events cluster by earnings week; effective sample smaller than the count.
3. The history vote is mostly unavailable (data start in 2024-10); with ret20 and rel20 correlated, "two bullish votes" mostly means "strong trailing 20-day return".
4. Prices have no documented adjustment and at least one probable unadjusted corporate action (FTV).
5. Costs are a provisional model, no market impact beyond the sensitivity, shorting availability not modelled.
6. Several comparisons (7 reported rules/benchmarks, 2 sensitivities) from one period: no multiplicity correction beyond the two-rule interval.
7. Validation (93 events) and test (140 events) remain sealed and untouched; the 100-point Smart Move model is not implemented; no accuracy claim is made.

## 8. Reproducible commands
See `README.md` in this folder (hash check, tests, the single run). Local result files: `results/dev_run_results.json`, `results/dev_run_record.json`.
