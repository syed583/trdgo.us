# Smart Move - reconciliation with production coverage, Track F / Track H design, budgets (2026-10-30)

> **ERRATUM 2026-10-30 (owner decisions):** SEC 8-K Item 2.02 acceptance time is a *filing acceptance time*, not a verified public release timestamp; wherever this document says "verified release time" read "SEC acceptance time (supporting evidence only)". Table prefix `sm_` is replaced by `smv_`; capture scheduling, pilot and evaluation rules are superseded by `SMART_MOVE_PILOT_AND_SCHEDULING_DESIGN_2026-10-30.md`.

Design only. No code written, no migration, no provider request, no database access by me (the production numbers are the owner's pasted read-only audit), existing models and the pending 669217f3 deployment untouched. Code facts below come from reading `feature/smart-move` (= `fix/earnings-freshness-evidence` + docs): `earnings_outcomes.py`, `db_cache.py`, `models.py`, `models_snapshots.py`, `tab_b_service.py`, `earnings_signal_history_service.py`.

## 1. Reconciliation (what the production audit changed)
| Earlier statement | Production fact | Status |
|---|---|---|
| Our graded set is 115 events (52 at 24-48 h) | `earnings_event_outcomes` has 303 rows: 241 GRADED, 62 PENDING_TIMING; predictions exist at lead 1 d for 551 events and 2 d for 490 events | superseded: larger, but all inside one 2.5-week window |
| `estimate_snapshots` coverage "unknown" | 0 rows; `estimate_revisions` 10 rows (2 symbols) | cat 4 has **no stored history** |
| 13F tables hold quarterly data | `institutional_holdings` 0 rows | cat 2 institutional: **nothing persisted** |
| `straddle_snap_v2` is a source of IV history | 142 rows, 4 dates, mostly empty fields | **not usable** |
| `price_bars` empty/legacy | 0 rows | confirmed unused |
| 13.5 % unknown timing | 10,620 of 78,641 `model_predictions` rows have NULL `report_timing`; 62 outcome rows PENDING_TIMING | confirmed: those events are excluded |
| `score_snapshots` unknown | 4,945 rows, 318 symbols, 47 dates since 2026-09-14, per-parameter `signals` JSON | a small stored source for the directional model only (not earnings; not inspected) |

## 2. Updated coverage matrix: provider-accessible history vs persisted in PostgreSQL
"PIT" = usable as of a past moment without later information. Probe = the 8 requests of 2026-10-30.

| # | Category (pts) | Provider can supply history? (probe / spec / code) | Persisted in our PostgreSQL? | Historical backtest (Track H) | Track F |
|---|---|---|---|---|---|
| 1 | Options flow & unusual (20) | dated query accepted, rows have no timestamp, later-known fields present -> PIT **unproven**; code note: ~15-day retention | no (cache only, overwritten) | **No** | capture from day 1 |
| 2 | Institutional & dark pool (15) | dark pool: dated, timestamped prints, 2 months verified, no side, paging needed; institution dated query returned 0 rows | **no** (institutional_holdings 0; nothing for dark pool) | **No** (dark-pool size features only, if later approved, are weak without side) | dark pool prints capture; institutional: NOT_AVAILABLE until a source is loaded |
| 3 | Historical earnings reaction (15) | UW earnings history (<=24 quarters/name) + daily candles; SEC 8-K Item 2.02 acceptance gives release times | outcomes only since Oct 2026 (303 rows) | **Yes** | derived at capture |
| 4 | EPS & revenue revisions (15) | SEC: actuals with `filed` date, **no consensus**; UW `street_mean_est` has no as-of | **no** (0 / 10 rows) | **No** | capture consensus now; revisions need >=2 snapshots (reason INSUFFICIENT_HISTORY until then) |
| 5 | Price, volume, technical (10) | UW daily candles ~10 y | no (price_bars 0) | **Yes** | derived |
| 6 | News, guidance, sentiment (10) | headlines timestamped but 50 rows = 23 days; sentiment all "neutral" in probe; no guidance source | no | **No** | headline counts/timing only; sentiment flagged UNINFORMATIVE; guidance NOT_AVAILABLE |
| 7 | Implied move & volatility (10) | iv-rank daily rows with per-day stamp (5 months verified), term-structure dated (2 months verified), `expected_move_perc` in earnings history | no (straddle_snap_v2 unusable) | **Yes, depth-limited** (needs the depth probe below) | capture |
| 8 | Sector, market, peer (5) | candles of SPY/sector ETFs/peers | no | **Yes** | derived |

Genuinely backtestable factors: **3, 5, 7 (to the verified depth), 8 = 40 of 100 points**. Everything else is forward-only or absent. The historical score is reported as "H-40" and is never described as the full model.

## 3. Are the 241 GRADED outcomes suitable for validation? (read-only assessment, rows not touched)
What the table is (from `earnings_outcomes.py`): one row per (symbol, earnings_date); BMO = previous regular close -> earnings-day regular close, AMC = earnings-day close -> next trading day close; regular-session closes only (fail-closed on pre/post rows); graded only after the reaction close + 45 min; a GRADED row is never rewritten (upsert has `WHERE status <> 'GRADED'`); `graded_at`, `method_version` "2A-1" stored; direction uses a +-0.5 % flat band.

Suitable for: a **close-to-close next-session** label, with a clean, audited lineage. Not suitable as evidence of an immediate reaction.
Limits to respect:
1. **Label timing is the provider's BMO/AMC string, unverified.** A wrong label shifts the window one session. Cross-check against SEC acceptance time where possible; report mismatches.
2. **Selection:** only events that had a prediction, with known timing (62 PENDING_TIMING are excluded; if missing timing is not random this biases the set).
3. **Grading order:** each run grades at most 120 symbols taken alphabetically (`sorted(by_symbol)[:max_symbols]`), so a partially graded snapshot over-represents early letters. Evaluate only after the due backlog is cleared, and treat ungraded-but-due events as missing, not as random.
4. **Size and clustering:** 241 events from about 2.5 weeks of one earnings season: a plumbing check and a comparison baseline, not enough to validate a model (see the interval illustration in the findings doc).
5. The legacy columns in `model_predictions` (4,480 graded rows = 158 events) use the older method; validation must use `earnings_event_outcomes` only.
6. Method for use without changing anything: export with a read-only transaction to a hashed file; join by (symbol, earnings_date); record the export hash and `method_version` in every report.

## 4. The `price_bars_by_timeframe` audit error
`PriceBar` (`models.py:144`) has `company_id` (FK to `companies`), `timeframe`, `timestamp`, OHLC, `volume`, `created_at` - **no `symbol` column**. The audit query did `count(distinct symbol)` -> `UndefinedColumn`. It is the audit script's bug, not a database problem; the table has 0 rows, so nothing was lost. Fixed locally in `scripts/coverage_audit.py` (`count(distinct company_id)`; new SHA-256 `7ebd07a85d845f63faaf2a97f5f29f15c4af5df54e3845dbaa02aa6a60ccce9a`, 8,142 bytes, regenerated `PASTE_coverage_audit.ps1`). A rerun is **not needed**; if you want it anyway the VPS copy must be renamed first (the block never overwrites). Not executed, not committed.

## 5. Timestamps in existing cache/snapshot tables (none is immutable evidence)
| Table | Time fields | Mutability | Use |
|---|---|---|---|
| `swr_cache` | `updated_at` DOUBLE PRECISION epoch seconds (this is why the audit printed numbers like 1793334661) | one row per key, **overwritten** on refresh; value has no per-field as-of | can only say "this value was refreshed at X"; use only as a source that Track F snapshots copy at capture time |
| `model_predictions` | `predicted_at` timestamptz, `snapshot_date` | UNIQUE (model, symbol, earnings_date, snapshot_date) + `DO NOTHING`: first write wins (best existing); legacy outcome columns can be filled later for unevaluated rows | stores signal and score only, no inputs |
| `earnings_event_outcomes` | `graded_at`, `last_attempt_at`, close dates | GRADED rows never rewritten; non-graded rows are updated | labels only |
| `score_snapshots` | `snapshot_date`; `signals` JSON per parameter | unique per (symbol, date), re-run of the same day overwrites; forward-return columns filled later by design | directional model only |
| `straddle_snap_v2`, `earnings_signal_snapshots`, `ped_scores` | `snapshot_date` | upserts overwrite the same day | not evidence |
| `pe_predictions` | `signal_ts`, `factor_readings` | immutable inserts, UNIQUE(event_id, model_version) | only 7 rows; pattern worth copying |

## 6. Track F minimum immutable evidence schema (DESIGN ONLY; created only when `SMART_MOVE_ENABLED=on`)
Pattern: the verified append-only design of the evidence tables (content-addressed rows, UPDATE/DELETE/TRUNCATE rejected by triggers). Four tables, all names prefixed `sm_`, none touching existing tables:

1. `sm_event_versions` (event registry; a correction is a new version, never an update)
   `symbol, earnings_date, version, timing_label (BMO|AMC|UNKNOWN), timing_source (PROVIDER|SEC_ACCEPTANCE_TIME|MANUAL), timing_status (PROVISIONAL|SEC_ACCEPTANCE_TIME|UNKNOWN), release_ts_utc NULL, cutoff_ts_utc, sec_accession NULL, recorded_at` - PK (symbol, earnings_date, version).
2. `sm_input_snapshots` (one row per category x source per capture, content-addressed)
   `snapshot_id = sha256(payload)`, `symbol, earnings_date, category (1-8), source_name (e.g. uw.iv_rank), request_hash (endpoint + parameters, no secrets), provider_as_of NULL, as_of_quality (PROVIDER_STAMPED | FETCH_TIME_ONLY | DERIVED_FROM_STAMPED), fetched_at_utc, status (OK | MISSING), missing_reason NULL, payload (compressed JSON of the exact reduced fields used), payload_bytes`.
   Missing-data reason codes (never an imputed value): `NO_SOURCE`, `NOT_AVAILABLE_AS_OF`, `PROVIDER_EMPTY`, `HTTP_ERROR`, `TIMEOUT`, `BUDGET_STOP`, `INSUFFICIENT_HISTORY`, `UNINFORMATIVE_FIELD`, `UNKNOWN_TIMING`, `FLAG_OFF`.
3. `sm_predictions`
   `prediction_id, symbol, earnings_date, event_version, model_version, config_sha256 (weights/thresholds, marked PROVISIONAL_UNVALIDATED), track ('F'), predicted_at_utc, cutoff_ts_utc, hours_to_cutoff, signal (BUY|SELL|NO_TRADE), bull_pts, bear_pts, coverage_pct, reason_codes[], explanation JSON` where the explanation holds, per category: `bull_pts, bear_pts, weight, available, missing_reason, snapshot_ids[]` (this is the per-input provenance link). UNIQUE (symbol, earnings_date, model_version, predicted_date), first write wins.
4. `sm_capture_runs` (audit of the job itself)
   `run_id, started_at, finished_at, events_considered, events_captured, provider_requests_by_source, budget_cap, stopped_reason, errors` - proves the request budget was respected.

Outcomes are not copied: the evaluator reads `earnings_event_outcomes` (and later an immediate-reaction table) read-only and joins by (symbol, earnings_date). Existing-model signals for the same events are likewise read from `model_predictions`, never copied.
Per-category capture (reduced fields only): 1 unusual contracts (top 25 + totals) from the already-warmed cache where possible; 2 dark-pool aggregate + 20 prints, institutional NOT_AVAILABLE; 3 prior-event reactions strictly before the cutoff; 4 consensus now, revisions only after >=2 snapshots; 5 last 60 daily bars; 6 last 20 headlines (timestamps, source, counts), sentiment stored but flagged, guidance NOT_AVAILABLE; 7 iv-rank last 5 rows, term-structure, earnings-expiry implied move; 8 SPY + sector ETF + peer bars and peer reactions finished before the cutoff.

## 7. Track H backtest design (separate code, tables and reports from Track F; never pooled)
* **Universe and events:** app universe (~590 names) x UW earnings history (<=24 quarters). Keep events with a known BMO/AMC label; classify each against the SEC Item 2.02 acceptance time (ET): 16:00-20:00 -> AMC, 04:00-09:30 -> BMO, otherwise AMBIGUOUS (excluded). Label/SEC mismatches excluded and counted.
* **Cutoff:** T = release - 24 h, with release = SEC acceptance time when present (`SEC_ACCEPTANCE_TIME`), else nominal BMO 06:00 / AMC 16:00 ET (`PROVISIONAL`). Results are reported separately for the two classes.
* **Features at T (only data whose stamp <= T):** cat 3 prior events whose reaction close precedes T; cat 5 and 8 candles whose session had closed; cat 7 iv-rank row dated before T (its stamp is ~22:35Z of its own day); earnings-history `expected_move_perc` only for the event itself if it is published before T (otherwise excluded). Cat 1, 2, 4, 6 are absent, so coverage <= 40 %.
* **Label:** the Phase 2A rule `reaction_window` (BMO previous close -> earnings-day close; AMC earnings-day close -> next close), regular-session closes, flat band +-0.5 % = no move. Named "next-session close-to-close" in every table. It is not an immediate reaction.
* **Decision:** thresholds and margins pre-registered in a hash-stamped file before any result is seen; NO TRADE for low coverage, conflict, below margin, illiquid, unknown timing.
* **Splits:** the provisional weights are not fitted, so the main protection is point-in-time construction, enforced by a leakage test that fails if any input is stamped after T. Evaluation is chronological: events ordered by cutoff, per-quarter folds shown for stability, the most recent quarter held out and opened once; an embargo of one earnings cycle between any threshold-selection period and the test period if thresholds are ever tuned.
* **Metrics:** accuracy on decided calls; BUY and SELL separately; coverage; precision when acting; calibration (bull-minus-bear bucket vs hit rate, with Wilson 95 % intervals); date-clustered bootstrap intervals; comparison with baselines (always BUY, always SELL, previous-earnings-move sign, 20-day trend sign, SPY direction, random with equal coverage, existing models on the same events where they exist). Simulated returns: entry at the close before the release (AMC) or previous close (BMO), exit at the reaction close, costs = half-spread proxy + slippage by liquidity bucket + commission per side, liquidity floor, missing prints excluded and counted.
* **Honesty rules:** report counts of events dropped at each filter; no result is called validation of the 100-point model; no multiple-testing shortcut (one pre-registered primary comparison).

## 8. Storage, request budget, frequency (estimates; nothing implemented)
**Track F storage (compressed, deduplicated by content hash):** about 60 KB raw per captured event (unusual 8, dark pool 5, term structure 4, iv-rank 1, headlines 7, 60 bars 6, earnings history 10, peers/ETF bars shared across events) -> roughly 12-15 KB per event-capture after gzip, plus about 2 KB per prediction row. App universe ~590 names x 4 = ~2,400 events/year: **about 35 MB/year for one capture per event, about 70 MB/year for two** (today's full logical dump is 74 MB). Tables are created only when the flag is on.
**Track F requests:** most inputs already sit in warmed caches (`earn:equity`, `uw:unusual`, candles), so new requests are about 4-5 per captured event (iv-rank, term-structure, headlines, dark pool, earnings history if not cached). One capture per event in the 24-48 h window: about 12,000 UW requests/year; in peak season (about 1,500 events in 11 days) about 700/day, roughly +4-7 % on the current 10-18 k/day, below the plan's limits (probe showed 1,000,000/min) but it shares the token and the 3-concurrent rule with the live engine and another local app, so Smart Move runs at background priority with a hard daily cap. SEC: one submissions request per company per quarter plus one per event on release day, about 3,000/year, free, under 10 requests/s.
**Capture frequency:** one daily job after the close (about 17:30 ET): events whose cutoff is 24-48 h away get a prediction; a second job on release day reads SEC acceptance times and writes a new event version (SEC_ACCEPTANCE_TIME). No 12-hour loop; flag off by default.
**Track H budget (phased):**
* H0 depth probe, 6 UW requests: iv-rank and term-structure at 2025-10-01, 2025-04-01 and 2024-10-01 for one symbol, to learn how far volatility history reaches before sizing anything.
* H1 pilot, 50 symbols: 50 SEC submissions + 50 UW earnings-history + 50 UW daily-candles + 3 ETF candles = 50 SEC (free) + 103 UW requests.
* H2 full, about 590 symbols: 590 SEC (free) + about 1,200 UW (earnings history, candles, ETFs); category 7 per event (iv-rank + term-structure) only for a 300-event pilot (about 600 requests) and only to the depth H0 proves.

## 9. Outstanding decisions
1. **H0 depth probe:** approve 6 UW requests (listed above)? Without it category 7 stays "5 months verified".
2. **H1 pilot:** approve 50 symbols: 50 SEC + about 103 UW requests (symbol list from you or the 50 largest in the universe)?
3. **Release-time authority:** accept SEC 8-K Item 2.02 acceptance time as the "verified" release time with the BMO/AMC classification windows above?
4. **Evaluation label:** keep the Phase 2A close-to-close +-0.5 % flat band for Smart Move (named "next-session close-to-close"), counting flat outcomes as neither hit nor miss?
5. **Track F schema:** approve the four-table design (section 6) for implementation behind the flag, and one capture per event at the first run after the 24-48 h window opens?
6. **Request caps for Track F** (background priority, proposal 1,000 UW requests/day hard cap, SEC 10/s)?
7. **Existing-model comparison:** read-only join to `model_predictions` for baselines (no copy)?
8. **Commit** of these docs and the coverage-script fix on `feature/smart-move` (local only)?
9. **Cache `sm:*` keys** (58 each, written about 15 Oct): trace their origin (read-only code search in other branches) before Smart Move uses the prefix `sm_`?

## 10. H0 depth probe result (approved and run 2026-10-30, 6 UW requests, all 200, no retry; file `depth_probe_results_20261030-140424.json`, script `scripts/depth_probe.py`)
| Request (AAPL) | Time UTC | Rows | Coverage |
|---|---|---|---|
| iv-rank date=2025-10-01 | 10:04:24 | 5 | 2025-09-25..10-01, each row `updated_at` ~22:35Z of its own day |
| term-structure date=2025-10-01 | 10:04:27 | 21 expiries | dated 2025-10-01 |
| iv-rank date=2025-04-01 | 10:04:30 | 5 | 2025-03-26..04-01, daily stamps |
| term-structure date=2025-04-01 | 10:04:32 | 20 | dated 2025-04-01 |
| iv-rank date=2024-10-01 | 10:04:35 | 5 | 2024-09-25..10-01, daily stamps |
| term-structure date=2024-10-01 | 10:04:37 | 20 | dated 2024-10-01 |
Result: for AAPL, IV-rank and term-structure history is reachable **at least 2 years back** with per-day stamps (iv-rank). Limits: one symbol (a very liquid name) only; each iv-rank call returns about 5 trading days, so one request per event (or per 5 days); term-structure rows carry a date but no `updated_at`, so their as-of quality is "dated, not stamped"; smaller or thinly traded names are untested. Category 7 can therefore be backtested to at least 2024-10 for liquid names, subject to a per-symbol check in the H1 pilot. Total approved request usage so far: 12 UW + 2 SEC. Budget now exhausted again.
