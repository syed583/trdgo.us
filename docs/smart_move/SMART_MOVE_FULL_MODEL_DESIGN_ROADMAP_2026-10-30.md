# Smart Move Earnings AI (100 points) - design-only build roadmap (2026-10-30)

Design only. No scoring implemented, no job activated, no provider request made for this document (facts come from `api_spec.yaml`, the repository code, and the results of the earlier approved probes H0/H1a/H1b), no migration, no production change, no push. Baseline commit just made: **`994e2349`** (frozen pre-registration `9316348b...f77e`, offline code, 15 leakage tests, the single development run; validation and test untouched). Verified before committing: pre-registration hash, input hashes, code hashes equal to those recorded at the run, secret scan clean, and a fresh clone with `core.autocrlf=true` reproduces the hashes byte for byte (a `.gitattributes` with `-text` for `docs/smart_move` was added for that reason). The original continuation rule and contrarian mirror are preserved unchanged; no winner is selected.

Evidence labels used below: **V** = verified by a probe in this project; **S** = documented in the provider spec only; **C** = seen in repository code; **U** = unknown / needs a verification request.

## 1. Non-negotiable rules for the full model
1. Every input is stored as an immutable snapshot with the provider's own timestamp (if any), our capture time, and the trading session it reflects. A provider that returns rows for an old date is not, by itself, proof that the row was known then.
2. Prediction window: capture and predict 24-48 hours before the earliest plausible release (per event, per label; cutoff = release minus 24 h in UTC and New York time), using the event-level scheduler, freshness classes and miss ledger already designed (pilot design document, sections 4, 13, 14). Timing is per event, never per symbol; the release timestamp is never assumed verified (SEC acceptance stays an upper bound).
3. Bullish and bearish evidence are accumulated separately per factor, capped at the factor's weight; missing evidence is not scored and not neutral; BUY / SELL / NO TRADE with reason codes. Weights (20/15/15/15/10/10/10/5) are provisional and unvalidated, and travel with the config hash.
4. No scoring threshold is fitted on validation or test data; each scoring version gets its own hashed pre-registration before any outcome is read (as done for the baseline).

## 2. Factor registry
### F1. Options flow and unusual contracts (20 points)
| | |
|---|---|
| Provider / endpoints | Unusual Whales: `GET /api/option-activity/unusual` (V: dated query accepted; rows lack a row timestamp), `GET /api/option-trades/flow-alerts` (S: `created_at`, `total_ask_side_prem`, `total_bid_side_prem`, `alert_rule`, `has_sweep`, `volume_oi_ratio`, `underlying_price`), `GET /api/stock/{t}/net-prem-ticks` (S, `date`), `GET /api/stock/{t}/greek-flow` (S: per-minute `timestamp`, delta/vega flow), `GET /api/stock/{t}/oi-change` (C: the application already passes `date`; not probed here), `GET /api/stock/{t}/options-pulse` (S: Nasdaq Options Pulse, intraday `hr_min` series, `date`), `GET /api/stock/{t}/flow-per-strike` (S, `date`), `GET /api/option-trades/full-tape/{date}` (S: whole-day tape since 2022-01-01, limited by the plan's lookback) |
| Timestamps | per alert `created_at`; per-minute series carry their own bucket time; unusual-contract rows carry none (V) |
| Historical availability | spec says `date` is supported on most; code note says flow is "not retained beyond ~15 days" (C); full tape might reach 2022 (S, plan dependent, U); the dated unusual query returned rows for 2026-09-02 but rows contained later-known fields (V) |
| Missing fields | trade side of dark/lit flow; no verified aggressor field for unusual rows; hedging versus speculation intent is not provided |
| Limitations | flow is ambiguous (hedges, spreads, multi-leg); thin names have little flow; heavy payloads; the shared token and 3-concurrent rule |
| Point-in-time status | **forward capture only** until a dated series is proven stamped (the stamped per-minute series are the best candidates for later back-testing) |
| Readiness | needs verification, then capture from day one |

### F2. Dark pool and institutional activity (15 points)
| | |
|---|---|
| Provider / endpoints | UW `GET /api/darkpool/{t}` (V: dated, `executed_at` and `trf_executed_at` UTC per print, 50 rows hit the limit, no side), `GET /api/darkpool/{t}/price-levels` (S), `GET /api/lit-flow/{t}` (S); institutional: `GET /api/institution/{t}/ownership` (V: dated query returned 0 rows; S: `filing_date`, `report_date`, `units_change`), `GET /api/institutions/latest_filings`, `GET /api/institution/{name}/activity/v2` (S); SEC 13F bulk datasets via the repo's institutional module (C: production table `institutional_holdings` has 0 rows); insider flow `GET /api/insider/{t}/ticker-flow` and SEC Form 4 (filing date only) as an optional adjunct |
| Timestamps | dark pool per print (V); 13F `filing_date` (quarterly, up to 45 days after quarter end) |
| Historical availability | dark pool: verified 2 months for one day; paging for a full day costs several requests; 13F: quarterly history exists upstream but nothing is loaded in our database |
| Missing fields | trade direction for dark prints; any "institutional flow" within days (13F is slow) |
| Limitations | dark volume is context, not direction; 13F is stale by construction and cannot be called flow before an earnings date |
| Point-in-time status | dark pool: per print stamped, aggregates must be built at capture; institutional: PIT only through `filing_date` |
| Readiness | dark pool: capture-ready after a paging/volume verification; institutional: needs data loading (separate task) and stays a slow context score with a capped weight |

### F3. Historical earnings reaction (15 points)
| | |
|---|---|
| Provider / endpoints | UW `GET /api/earnings/{t}` (V: up to 24 quarters, `report_date`, `report_time` label, `street_mean_est`, `actual_eps`, provider moves), `GET /api/stock/{t}/earnings` (S), daily candles `GET /api/stock/{t}/ohlc/1d` (V) |
| Timestamps | candles are final session data; the label and consensus fields have no as-of stamp |
| Historical availability | **V**: 434 reactions reconstructed from candles; the provider's 1-day move equals our reconstruction on all 434; about 6 years of events per name |
| Missing fields | verified release timestamp; unadjusted corporate actions (FTV) must be detected |
| Limitations | reactions of the same company are only moderately stable; session labels change between quarters (V: TMUS, SPFI, KVUE) |
| Point-in-time status | **usable now**: only reactions whose reaction close precedes the cutoff, recomputed from candles (not provider fields) |
| Readiness | available now |

### F4. EPS and revenue estimate revisions (15 points)
| | |
|---|---|
| Provider / endpoints | UW `GET /api/companies/{t}/earnings-estimates` (S: "Advanced+ tier"; fields `date`, `horizon`, `eps_estimate_average/high/low/analyst_count`, revenue equivalents; only a `ticker` parameter, so no obvious history query) , `GET /api/screener/analysts` (S: `timestamp`, `action`, `firm`, `target`, `recommendation`; `newer_than/older_than`), UW earnings rows `street_mean_est` (V: no as-of), Finnhub earnings/recommendation trends (C: current only) |
| Timestamps | analyst actions are stamped; estimate snapshots carry `date` (meaning unverified) |
| Historical availability | our database: `estimate_snapshots` 0 rows, `estimate_revisions` 10 rows; the analyst history is shallow and a 72-event test showed no incremental value (analyst-history research) |
| Missing fields | historical consensus as of past dates; our plan tier for the estimates endpoint is unknown (U) |
| Limitations | a revision needs at least two dated snapshots; consensus can be restated |
| Point-in-time status | **prospective only**: snapshot the consensus every week from about 30 days before each event so the 7/30-day change exists at the cutoff |
| Readiness | **needs more data** (one full earnings cycle of weekly snapshots) and a tier check |

### F5. Price, volume and technical trend (10 points)
| | |
|---|---|
| Provider / endpoints | UW `GET /api/stock/{t}/ohlc/1d` (V: `pr`/`r`/`po` rows per session, 2,258 rows for 3 years, no timestamps on daily rows), optional `ohlc/5m` with `date` for gap and pre-market context (S) |
| Timestamps | session date only; availability buffer of 45 minutes after the close |
| Historical availability | V: 3 years in one request per symbol |
| Missing fields | split/spin-off adjustment flag (undocumented; probable unadjusted spin-off found) |
| Limitations | unadjusted corporate actions; thin names |
| Point-in-time status | **usable now** with the suspect-window rule and a per-event cutoff (pre-registered baseline code exists) |
| Readiness | available now |

### F6. News, guidance and sentiment (10 points)
| | |
|---|---|
| Provider / endpoints | UW `GET /api/news/headlines` (V: `created_at`, `tickers`, `is_major`, `source`, `tags`, `sentiment`; no date filter, `page` available; 50 headlines spanned 23 days; sentiment "neutral" for all 50), SEC submissions (V: 8-K `items` and `acceptanceDateTime`, e.g. 2.02, 7.01, 8.01), Finnhub company news (C: current only) |
| Timestamps | headline `created_at`; filing acceptance time |
| Historical availability | headlines: depth by paging unknown (U); 8-K flags: years (V) |
| Missing fields | structured guidance (none), usable sentiment (uninformative), transcripts |
| Limitations | headline counts are a weak proxy; guidance would need text parsing of filings (not designed here, not approved) |
| Point-in-time status | headline timing and 8-K item flags: stamped; sentiment: not usable; guidance: **absent** |
| Readiness | partially available (event counts, 8-K item flags); sentiment/guidance stay MISSING with reason codes until a source is proven |

### F7. Implied volatility and expected move (10 points)
| | |
|---|---|
| Provider / endpoints | UW `GET /api/stock/{t}/iv-rank` (V: daily rows with `updated_at` ~22:35Z of their own date, 2 years verified for AAPL), `GET /api/stock/{t}/volatility/term-structure` (V: per expiry `implied_move`, dated not stamped, 2 years), `GET /api/stock/{t}/volatility/context` (S: `iv30`, `iv_rank`, `iv_percentile`, `iv_rv_ratio`, `skew` label, `term_structure` regime, implied move and `earnings_date` for an expiry, VIX), `GET /api/stock/{t}/interpolated-iv` (S: `implied_move_perc`, `percentile`, `days`), `volatility/stats`, `volatility/realized`, `variance-risk-premium` (S), `expected_move_perc` in `/api/earnings/{t}` (V: no as-of) |
| Timestamps | iv-rank row stamps; others dated |
| Historical availability | V for iv-rank and term-structure on one liquid symbol; the others unverified (U) |
| Missing fields | per-field as-of for term-structure and context; thin-name coverage untested |
| Limitations | implied move is a market price, not a forecast of direction; skew labels are provider-defined |
| Point-in-time status | forward capture of the whole set at the cutoff is clean; historical back-testing only through iv-rank (stamped) |
| Readiness | available now for capture; historical coverage needs the deferred volatility stage |

### F8. Sector, market and peer direction (5 points)
| | |
|---|---|
| Provider / endpoints | candles for SPY and the 11 sector ETFs (V), `GET /api/market/market-tide` and `GET /api/market/{sector}/sector-tide` (S: `timestamp`, net call/put premium, `date`), `GET /api/market/economic-calendar` (S), peer reactions already in our own event set |
| Timestamps | session dates; tide series per minute |
| Historical availability | candles V; tide with `date` (S, U) |
| Missing fields | peer map quality (internal grouping only) |
| Limitations | five points only; peers' reactions count only if their reaction closed before the cutoff |
| Point-in-time status | usable now (candles); tide forward capture |
| Readiness | available now |

## 3. Prospective evidence capture
* **Registry and scheduling:** `smv_event_versions` and the event-level scheduler (15-minute ticks that make no request by themselves; window opens 48 h and closes 24 h before the earliest plausible release; retries; last-chance degraded mode on warmed caches; `MISSED` ledger with reason codes; late predictions marked out-of-window and excluded from strict evaluation). Weekends, holidays, DST and changing earnings dates as in the pilot design document. A kill switch and the master flag `SMART_MOVE_ENABLED` (default off) gate everything; no job runs until approved.
* **Adapters:** one adapter per factor returns `FactorEvidence`: factor, provider, endpoint, parameter hash (no secrets), `provider_as_of`, `capture_ts_utc`, `session_reflected`, `freshness_class` (FRESH / LAST_SESSION_WEEKEND / LAST_SESSION_HOLIDAY / STALE_AGE / UNSTAMPED), payload reference, status, `missing_reason`. A failed or unavailable call produces a MISSING record with a reason, never a default value.
* **Immutable snapshots:** `smv_input_snapshots` content-addressed by SHA-256, append-only (UPDATE / DELETE / TRUNCATE rejected by triggers, the pattern verified on PostgreSQL for the earnings evidence tables); payload stored reduced and compressed, with the request log in `smv_capture_runs`. Existing caches (`swr_cache`, one overwritten row per key) may be read as a source but the value is copied into a snapshot at capture time and labelled `FETCH_TIME_ONLY`.
* **Predictions:** `smv_predictions` (prediction version, supersedes, in-window flag, model version, config hash, bull/bear points per factor, coverage, reason codes, explanation with snapshot ids). Outcomes and existing-model signals are read from `earnings_event_outcomes` / `model_predictions`, never copied.
* **Request budget per captured event (estimate, nothing sent):** F1 5-6, F2 3-4, F3 1 (monthly cache), F4 2, F5 1 (shared with F3), F6 1, F7 4, F8 shared per day (12 candle requests + 1 market tide + 11 sector tides = about 24) -> **about 15-18 UW requests per event** (about 15 with the slow factors cached). Warmed names (about 2,400 events a year): 36-43 thousand requests a year; in peak season (about 150 events a day) 2,300-2,700 a day, i.e. **+13-26 % on the 10-18 thousand the warmer already uses**. Whole predicted calendar (about 6,000 events): 90-108 thousand a year and 6-7 thousand a day in peak weeks (**+35-70 %**), which needs an explicit capacity decision and a daily governor. These are three to four times the earlier "4-5 requests per event" figure, which covered only part of the factors.
* **Storage:** about 160 KB raw per event, about 30-40 KB after reduction and gzip: **about 85 MB a year for the warmed names, about 210 MB a year for the whole calendar** (the full logical dump today is 74 MB). Option: keep payloads as hashed compressed files on the VPS and only metadata in PostgreSQL.
* **Connections and priority:** capture runs at background priority behind the live engine, sequentially with the 3-concurrent rule, using at most one database connection (the production limit is 15 connections).

## 4. Scoring design (structure only; no thresholds fixed here)
* Per factor, sub-signals produce `(bull_pts, bear_pts, available)` capped by the factor's weight; examples: F1 premium-weighted ask-side call versus put imbalance of recent unusual contracts and flow alerts, net premium ticks, near-expiry OI change; F2 dark-pool notional versus its 20-day norm (confirmation only, no direction) and slow 13F change by filing date; F3 stability and sign of the last reactions, and implied versus realized move history; F4 7/30-day consensus change and analyst actions; F5 trend, relative strength, volume, gap, extension; F6 major-headline count and timing, 8-K item flags; F7 implied move versus historical reaction (rich/cheap), IV rank, skew label, term-structure regime (also an eligibility gate); F8 SPY and sector trend, tides, peer reactions already closed.
* Decision: bullish total and bearish total kept separate; BUY / SELL only when one side is above a minimum, the other below a ceiling, the margin is sufficient and coverage (available weight) is sufficient; otherwise NO TRADE with codes (INSUFFICIENT_COVERAGE, CONFLICT, BELOW_MARGIN, ILLIQUID, UNKNOWN_TIMING, TIMING_CONFLICT, STALE_EVIDENCE, IMPLIED_MOVE_GATE, OUT_OF_WINDOW). The exact numbers are set in a hashed pre-registration written before any outcome of that version is seen. Explanations list each factor's evidence, snapshot ids and missing reasons.

## 5. Outcome grading (future)
* Primary: `earnings_event_outcomes` (Phase 2A) next-session close-to-close, flat band +-0.5 %, graded after the reaction close plus the buffer; predictions are never edited; evaluation joins by event version and uses only in-window predictions; BUY, SELL, NO TRADE by UP / FLAT / DOWN confusion matrices, strict and direction-only rates, market-adjusted and cost-adjusted returns, week-clustered intervals, benchmarks (always BUY/SELL, momentum, previous reaction, coverage-matched random, existing models).
* Immediate reaction (+5/15/30/60 minutes) only where a release timestamp has independent support; collect one-minute candles for the event day (`ohlc/1m` with `date`) after the release and store them as separate immutable outcome snapshots; otherwise report "not measurable". Close-to-close is never described as an immediate reaction.
* Statistical honesty: first forward season is a shadow period; intervals always shown; no accuracy claim before the evidence audit and a locked evaluation.

## 6. What is available now, what needs more data, what needs verification
| Factor | Weight | Now | Needs more data | Needs verification before use |
|---|---|---|---|---|
| F1 options flow | 20 | capture design | forward capture only (history unproven) | row timestamps, flow-alert filters, plan lookback, `net-prem-ticks`/`options-pulse`/`greek-flow` with `date` |
| F2 dark pool / institutional | 15 | dark pool prints (stamped) | 13F loading, institutional is slow context | full-day paging cost, date semantics of ownership, no-side limitation |
| F3 historical reaction | 15 | **yes** (434 reactions reconstructed) | more symbols/years only if requested | timing labels per event, corporate-action flags |
| F4 estimate revisions | 15 | no history | weekly snapshots for one cycle | plan tier for `earnings-estimates`, meaning of `date`/`horizon` |
| F5 price / technical | 10 | **yes** | none | adjustment handling (spin-off) |
| F6 news / guidance / sentiment | 10 | headline counts, 8-K flags | headline history by paging | sentiment usefulness (all "neutral" in the probe), guidance source (none) |
| F7 implied volatility | 10 | capture ready; history for liquid names (2 years, stamped iv-rank) | history for thin names | `volatility/context`, `interpolated-iv` dated history |
| F8 sector / market / peers | 5 | **yes** (candles) | tide history | tide `date` depth |
Summary: 3 factors usable now (F3, F5, F8 = 30 points); F7 (10) usable prospectively and partly historically; F1, F2, F4, F6 (60 points) require forward capture and verification. A full 100-point model cannot be back-tested; it will be evaluated prospectively.

## 7. Verification still required (proposal only; none of these requests has been made)
| # | Question | Requests |
|---|---|---|
| V1 | `earnings-estimates`: plan tier available? fields and meaning of `date` / `horizon` | 1 |
| V2 | `option-trades/flow-alerts`: time filters, `created_at` depth, payload size | 2 |
| V3 | `net-prem-ticks`, `greek-flow`, `options-pulse` with a `date` 60 days back (stamped series?) | 3 |
| V4 | `institution/{t}/ownership` with `start_date`/`end_date` and `date` semantics | 1 |
| V5 | `news/headlines` page 2-3 depth and timestamps | 2 |
| V6 | `volatility/context` and `interpolated-iv` with a date one year back | 2 |
| V7 | `darkpool/{t}/price-levels` and a full-day paging cost | 2 |
| V8 | `screener/analysts` with `newer_than/older_than` paging depth | 2 |
Total proposal: 15 Unusual Whales requests, sequential, after your approval; plus an offline review of the plan's historical lookback in the account dashboard (no request).

## 8. Roadmap and launch gates
1. **Phase 0 (now):** this design; decisions below.
2. **Phase 1 - verification probes (after approval, <= 15 UW requests)** and a dashboard check of the plan tier and lookback; update this document with measured facts.
3. **Phase 2 - offline build, everything flag-off:** `smv_` DDL files (not applied), snapshot store with triggers tested on a local PostgreSQL, adapters with fake-provider tests, event scheduler with fake-clock tests (weekend, holiday, DST, moved dates, late discovery), freshness classification tests, request governor, kill switch.
4. **Phase 3 - scoring v1 pre-registration:** factor sub-signal definitions, weights, thresholds, coverage rules written and hashed before outcomes of that version exist; leakage tests as for the baseline.
5. **Phase 4 - shadow capture (needs your approval; first flag-on):** forward capture for the warmed names for one full earnings cycle (about 12 weeks), no predictions shown; daily window-coverage report (target >= 95 % in-window), freshness mix, miss reasons, request and database growth against budget.
6. **Phase 5 - first evaluation of the shadow season:** locked evaluation with intervals and benchmarks; no accuracy claim unless the evidence audit passes (verified or conservative cutoffs, point-in-time inputs, sample size).
7. **Phase 6 - preview API and page** (read-only, flag-off) showing predictions, evidence, missing reasons and freshness; **Phase 7 - expansion** to the whole calendar only after measured coverage, freshness, cost and quality; production deployment is a separate release with its own approval, backup and preflight, independent of the earnings-fix release.
**Launch gates (all required):** hashed pre-registration for the scoring version; leakage and immutability tests green; scheduler coverage >= 95 % over the shadow season; no stale-as-fresh incident; request budget and database growth within the approved caps; connection use within the production limit; tests covering the existing earnings models and the pinned scoring files unchanged; owner approval for each flag activation.

## 9. Decisions needed
1. Approve the 15-request verification proposal (V1-V8), or a smaller subset?
2. Capture scope for the shadow season: warmed names (about 2,400 events a year) or more? Accept the request cost of 15-18 per event (about +13-26 % daily UW load in peak weeks), or cap the number of factors/events captured per day?
3. Payload storage: all in PostgreSQL (about 85 MB a year warmed) or hashed files on the VPS with metadata in the database?
4. Is a first scoring version allowed to use only F3, F5, F7 and F8 (the 40 points with usable inputs) while F1, F2, F4, F6 are captured but not scored until validated?
5. Approve a local commit of this design document (currently uncommitted)?
