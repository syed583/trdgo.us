# Smart Move - offline price-only baseline feasibility plan (2026-10-30)

H1b local commit: **`9a8ff591`** (branch `feature/smart-move`, not pushed). Everything below uses existing data only: no provider request, no migration, no recurring job, no production change, no model change. The 100-point Smart Move model stays unchanged and unimplemented. **No prediction accuracy is claimed anywhere in this document**; the only outcome numbers shown are descriptive statistics of naive baselines on the development period.

## A. Reconciliation of every provider row (no double counting)
Each row falls into exactly one category (fixed precedence; `h1b/baseline/reconciliation.json`, assertion: categories sum to the 441 past events).
| Category | Events |
|---|---|
| Provider rows returned by H1a (2024-10-01 onward) | 488 |
| - undated rows, kept as missing (INBK 8, FSUN 1, WT 1, SW 1) | 11 |
| - upcoming dated events (not scored; 34 are projected dates) | 36 |
| - **past dated events** | **441** |
| Past: reaction session not yet closed (PENDING_REACTION) | 4 |
| Past: provider timing label unknown (UNKNOWN_TIMING; FSUN) | 3 |
| Past: excluded, no defensible cutoff evidence (11 with no SEC 8-K 2.02 match: EDU 9, SPFI 1, EXLS 1; 1 BMO filed late: PFS 2025-07-24) | 12 |
| Past: excluded, entry before cutoff, not tradable (Monday-morning type BMO events: T 2025-01-27, INCY 2025-02-10, BLDR 2026-02-17: the entry close is the previous Friday, before a 24 h-earlier cutoff on Sunday) | 3 |
| Past: **exploratory, timing uncertain** - BMO with SEC acceptance before the open (release bound, `EXPLORATORY_UNCERTAIN_BMO_FILING_BOUND`) | 208 |
| Past: **exploratory, timing uncertain** - AMC, SEC acceptance not contradicting (`EXPLORATORY_UNCERTAIN_AMC_NOT_CONTRADICTED`) | 211 |
| **Verified public release timestamp** | **0** |
Check: 4 + 3 + 12 + 3 + 208 + 211 = 441. The exploratory set is **419 events** (not 418: the earlier set also removed 4 events near price jumps; those jumps are examined in section B and the events are kept and flagged instead). The 419 are "timing not contradicted", never "verified".

### Conservative cutoff per event (`h1b/baseline/cutoffs.csv`)
* Earliest plausible release E = nominal session time of the **event's own label** (BMO 06:00 ET, AMC 16:00 ET on the event date), **or the SEC acceptance time when that is earlier** (the release cannot be later than the filing; 11 events). Timing is per event, never per symbol (H1b showed that TMUS, SPFI and KVUE change session between quarters).
* Cutoff = E minus exactly 24 absolute hours, stored in **UTC and America/New_York** (with the zone abbreviation, so EST/EDT is explicit). No data stamped after the cutoff enters a feature.
* Last usable session = the last trading session whose close plus a 45-minute availability buffer (the Phase 2A buffer) is at or before the cutoff, using the repository's exchange calendar (holidays, early closes).
* Flags recorded per event: weekend/holiday between the last session and the cutoff (145 events), cutoff on a non-trading day (38), release bounded by an earlier SEC acceptance (11), DST transition between cutoff and release (0 events). Events where the cutoff is not before the entry close are excluded (3, above).

## B. The large price jumps (all 17 daily moves above 25 % in the 62 series, all investigated)
Offline checks per jump: close ratio against integer split ratios, open gap versus intraday range, volume versus the 20-session average, same-day move of SPY and the sector ETF, persistence over 5 and 20 sessions, nearby earnings event. Source: `h1b/baseline/price_jumps.json`.
* **No jump is shaped like an integer split** (none within 3 % of a ratio of 2, 3, 4, 5, 6, 8, 10, 15, 20 or its inverse); no OHLC inconsistency, zero-volume run or stale-price run was found in any of the 62 series.
* **Earnings-related (the move equals the earnings reaction and matches the provider's 1-day move):** LRN +39.1 % and -54.4 %, PRLB +38.7 %, +28.0 %, RDDT +42.0 %, LBRT +28.3 %, PI +26.5 %, NVCR +27.5 %, +28.4 % (2026-04-30 and 2026-07-23). Volume 4-32 times average, gap at the open, idiosyncratic (sector ETF within +-3 %).
* **Not earnings dates (no event within 3 days):**
  * **CNC -40.4 % on 2025-07-02**: open gap -34 %, volume 23.7x, SPY +0.45 %, XLV -1.0 %, -22.8 % further over 20 sessions: persistent, idiosyncratic, consistent with company news. 
  * **NVCR +49.0 % on 2024-12-02**: open gap +41 %, volume 15x, sector flat, price level held (-0.2 % over 20 sessions): consistent with company news.
  * **VRT -29.9 % on 2025-01-27**: market-wide AI-related sell-off day (SPY -1.4 %, XLI -1.3 %), high volume.
  * **FTV -27.2 % on 2025-06-30**: gap -27.3 % at the open with a narrow 4.8 % day range, volume 4x, no rebound: the profile of an ex-distribution date (**consistent with a spin-off or other corporate action; I recall one around that date but this cannot be verified offline**). It means the provider's price series is **not adjusted for such actions**.
  * **NVCR +27.7 % on 2026-02-25**: the session before its earnings (earnings reaction on 02-26 was -8.3 %), volume 3x, drifted -22.5 % over the next 20 sessions: a pre-earnings move that is not the earnings reaction.
  * PI 2024-04-25, PRLB 2023-11-03 and RDDT 2024-03-25 precede the event window (probably earnings or the IPO week; not checked further).
* **Verdict:** no split and no data error is evidenced. At least one probable unadjusted corporate action (FTV) exists, and the real causes of CNC, NVCR and FTV cannot be established without an external source (the SEC 8-K item list for those dates would need a new, unapproved request). Treatment adopted: every move above 25 % that is not an event's own reaction session marks a **suspect window**; any feature whose window (up to 120 sessions) contains one is set to *missing* for that event rather than repaired. This affects **10 of the 419 exploratory events** (NVCR 4, VRT 2, FTV 2, CNC 2). The earnings-reaction labels themselves are unaffected (both closes lie on the same side of any such jump in every case checked).

## C. Price-only baseline research plan (features with demonstrable point-in-time availability)
All features are computed from regular-session closes and volumes (market_time `r`) of the symbol, SPY and its sector ETF, **only for sessions up to the event's last usable session**. Nothing from the provider's earnings fields is used (no as-of proof): not `street_mean_est`, `expected_move*`, straddle fields, `pre_/post_earnings_move_*`, nor the provider's session label as a feature (it only selects the reaction window).
| Feature | Definition | Missing when |
|---|---|---|
| ret20, ret60 | close[last]/close[last-20 or 60] - 1 | fewer than 61 sessions (1 event) or a suspect window inside |
| rel20 | ret20 minus SPY ret20 | same |
| runup5 | close[last]/close[last-5] - 1 | same |
| rv20 | stdev of 20 daily returns (diagnostic and NO TRADE gate only) | same |
| adv20 | mean(close x volume) over 20 sessions (liquidity bucket, cost model) | volume missing |
| prior-reaction mean4, sign | mean and sign of the symbol's last up to 4 reconstructed reactions whose **reaction close precedes the cutoff** | fewer than 3 earlier events in the data (the first quarters of 2024-25 have none: coverage cost) |
Execution convention (same as Phase 2A labels): enter at the **base close** (BMO: previous session close, AMC: earnings-day close), exit at the **reaction close**. A prediction made at the cutoff must precede the entry close (enforced, 3 exclusions).

## D. BUY / SELL / NO TRADE rules, fixed before any evaluation outcome is looked at
Constants below are declared now and are never tuned on validation or test (a sensitivity set of +-40 % on each threshold is reported separately and never selects the primary rule).
* Votes (each computed from available features only; a missing feature gives no vote, not a neutral one): trend vote +1 if ret20 > +5 %, -1 if < -5 %; relative-strength vote +1 if rel20 > +3 %, -1 if < -3 %; earnings-history vote +1 if mean4 > +1 %, -1 if < -1 % (needs >= 3 prior events).
* Bullish evidence = number of +1 votes, bearish evidence = number of -1 votes, kept separate.
* **BUY** if bull >= 2 and bear = 0; **SELL** if bear >= 2 and bull = 0; otherwise **NO TRADE**.
* NO TRADE regardless of votes if: fewer than 2 votes available (coverage), adv20 below $20 M (thin), or a suspect window or unresolved timing flag.
* Hypothesis direction (continuation) is pre-declared; the contrarian mirror is reported as a second pre-declared rule with equal standing and a Bonferroni-adjusted interval; the direction is never flipped after seeing results.
* Benchmarks: always BUY, always SELL, SPY direction on the day, previous-reaction sign, a coverage-matched random rule (1,000 seeded draws), and the existing application models on the same events via a read-only join to `model_predictions` (leakage rules as in the design document, section 7).

## E. Chronological design and leakage control
* **Splits by calendar (chosen before looking at any rule outcome; the aggregate outcome mix of all 434 events was seen in H1b and is disclosed):** development 2024-10-01 .. 2025-09-30, validation 2025-10-01 .. 2026-03-31, **test 2026-04-01 .. 2026-10-28, untouched**.
* Exploratory events per split: **development 186, validation 93, test 140** (total 419); symbols 48 / 48 / 49; 418 of 419 have the required 61 sessions of history. By quarter: 2024Q4 47, 2025Q1 45, 2025Q2 47, 2025Q3 47 | validation 2025Q4 47, 2026Q1 46 | test 2026Q2 48, 2026Q3 49, 2026Q4 43 (to 2026-10-28).
* The rules above have no fitted parameters. If any parameter is ever fitted it is fitted on development only, chosen on validation, and the test period is opened once, with a code guard that refuses to load test labels without a recorded pre-registration hash.
* **Same-symbol and overlapping events:** consecutive events of one symbol are about 63 sessions apart; features with 60-120 session windows can overlap an earlier event's reaction only through information that was public before the cutoff (allowed). No event's feature may use another event's label unless that reaction closed before the cutoff (checked by construction and by a truncated-data recomputation test). Evaluation uncertainty is estimated with a **block bootstrap by earnings week** (market-wide shocks hit many symbols the same week), per-symbol counts are reported, and results are shown both raw and market-relative (symbol return minus SPY).
* A 5-trading-day embargo separates the periods for any fitted component.

## F. What can be reported (and what cannot yet)
Planned report: sample sizes and coverage (decided calls / scorable events), BUY, SELL and NO TRADE by UP / FLAT / DOWN confusion matrices, strict hit rate (FLAT counts as a miss) and direction-only rate (ex-flat) with Wilson 95 % intervals, net returns with a cluster-bootstrap interval, cost sensitivity at x0 / x1 / x2 / x3 of the cost model, results by liquidity bucket and by timing tier. Cost model (provisional): per side half-spread + slippage 3 / 8 / 20 bps for ADV above $200 M / $20-200 M / below $20 M, plus 1 bp commission per side; SELL adds 1 bp for borrow.
Feasibility numbers available now (descriptive, **development period only**; validation and test are counted, not inspected):
* Dev events 186: UP 96 (51.6 %), DOWN 77 (41.4 %), FLAT 13 (7.0 %); liquidity buckets liquid 51 / mid 94 / thin 41; median round-trip cost 18 bps.
* Naive baseline "always BUY": strict hit rate 51.6 % (Wilson 95 % 44.5 - 58.7 %), direction-only 55.5 % (48.0 - 62.7 %); mean next-session return before costs +1.23 % and after costs x1 / x2 / x3 +1.02 / +0.82 / +0.61 % with weekly-cluster 95 % intervals that include zero (e.g. -0.36 to +2.37 % at x1). "Always SELL" is the mirror: strict 41.4 % (34.6 - 48.6 %), net -1.44 % at x1 (interval -2.78 to -0.05 %). **This shows the dev period had a positive average reaction (+1.2 %), so any rule must be judged against a market-relative and base-rate benchmark; it is not evidence of any edge.**
* **Statistical power is the binding limit:** Wilson half-widths at a 50 % hit rate are +-7.1 points (dev, n = 186), +-10.1 (validation, 93), +-8.3 (test, 140). A rule that trades on a quarter to a third of events leaves about 35-50 decided test calls, i.e. a half-width of about +-14-17 points: only a hit rate above roughly 65-70 % could be told apart from the 52 % base rate. Smaller edges cannot be demonstrated with this sample.

## G. Evidence audit (conditions before any accuracy claim)
| Condition | Status |
|---|---|
| Verified public release timestamps | **0 events** (uncertain-only) |
| Per-event cutoff recorded in UTC and ET | done (419 events) |
| Features restricted to data stamped before the cutoff | designed; unit tests not yet written |
| Leakage test (truncated-data recomputation) | not yet written |
| Pre-registration hash of rules, splits and cost model | not yet frozen |
| Corporate-action handling | suspect-window rule defined; 3 causes unverified |
| Test period untouched | yes |
| Sample large enough to show a plausible edge | **no** (see power) |
Until these pass, the output of this work is a feasibility and descriptive study, not a performance claim. The 100-point model remains unimplemented; categories 1, 2, 4, 6 and the volatility category have no historical point-in-time evidence.

## Decisions needed
1. Approve freezing the pre-registration (rules D, splits E, cost model F) as a hashed file and writing the offline implementation with its leakage tests, still without evaluating validation or test?
2. Approve running the pre-registered rules once on the **development** period for pipeline validation (counts, coverage, confusion matrices), keeping validation and test sealed?
3. Keep the 10 suspect-window events as "missing feature" or drop them from the set entirely?
4. Given the power result, do you want to continue the offline baseline at all, or first decide how to obtain a larger point-in-time sample (more symbols from the existing universe would need new provider requests and a new approval)?
