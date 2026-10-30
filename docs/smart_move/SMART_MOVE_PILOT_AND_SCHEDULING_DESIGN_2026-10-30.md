# Smart Move - pilot plan, scheduling redesign, evaluation rules (2026-10-30, revision 2 after the owner's decisions)

Design only. Nothing coded, no table created, no capture job, no pilot request. Applies decisions 1-9 and requirements A-D of the owner's "controlled research approval". Where this document differs from `SMART_MOVE_RECONCILIATION_AND_DESIGN_2026-10-30.md` or `SMART_MOVE_COVERAGE_FINDINGS_AND_PLAN_2026-10-30.md`, this document wins.

## 0. Status of the approved items
| # | Decision | Status |
|---|---|---|
| 1 | 6 depth-probe UW requests | **Already executed once** (14:04 server time, AAPL iv-rank and term-structure at 2025-10-01, 2025-04-01, 2024-10-01; all 200; results file `depth_probe_results_20261030-140424.json`). I did **not** send another 6 because the approval named exactly those dates and they have been used; a repeat would be an unapproved extra. If you meant a second, different set of 6, say which dates/symbols. Logged fields: endpoint, status, UTC time, rows, date range, per-row `updated_at`, response field names; no credential, no raw body |
| 2 | Pilot prepared, not run | section 3 |
| 3 | SEC acceptance is **not** a verified release timestamp | applied everywhere: label `SEC_ACCEPTANCE_TIME` (a filing fact); `VERIFIED_PUBLIC_RELEASE` only with independent support; otherwise `PROVISIONAL_ASSUMPTION`; ambiguous cases excluded from strict validation |
| 4 | Flat band retained with full reporting | section 5 |
| 5 | Four `sm_` tables, design only | section 6 |
| 6 | No 1,000/day cap, smaller pilot budget, no recurring jobs | section 3.5 |
| 7 | Read-only joins to `model_predictions` | section 7 |
| 8 | Local commit | done for the reconciliation docs and the script fix (`61ff0f8c`); this revision committed locally after writing |
| 9 | `sm:*` cache keys investigated | section 8 |

## 1. Factor coverage vs the 100-point model (requirement B)
Reports for Track H always carry two blocks, never one blended number:
* **Factor coverage table (per event and in aggregate):** for each of the eight categories: weight, `available` events (count and %), missing weight, missing reasons. Maximum available weight of Track H is 40 of 100 (categories 3, 5, 7, 8); categories 1, 2, 4, 6 are shown as MISSING with reason codes (`NOT_AVAILABLE_AS_OF`, `NO_SOURCE`, ...), their 60 points are not scored, not neutral.
* **Title and wording:** results are titled "Track H partial-evidence score (max 40 of 100 weighted points)". The text never calls it the Smart Move model, never extrapolates to the 100-point model, and states that the full model's evidence (60 points) has no historical test at all.

## 2. Provider-accessible history vs persisted point-in-time history vs field-level as-of proof (requirement C)
An API returning rows for an old date proves the provider can answer for that date. It does not prove each field was known then (restated consensus, revised ownership, back-computed indicators). Three separate columns are therefore kept, for every field used:

| Field / source | Provider returns old dates (verified by probe) | Persisted by us point-in-time | Field-level as-of proof |
|---|---|---|---|
| UW iv-rank row | yes, AAPL back to 2024-09-25 | no | **partial**: each row carries `updated_at` ~22:35Z on its own date (2024 stamps for 2024 rows). That supports contemporaneous computation, but cannot exclude a later recomputation with the same stamp; to be checked against forward captures |
| UW term-structure | yes, AAPL back to 2024-10-01 | no | **none**: dated, not stamped |
| UW unusual contracts | yes (2026-09-02, one request) | no | **none**: no row timestamp; `next_earnings_date` and `stock_price` suggest later-known fields |
| UW dark pool | yes (2026-09-02), `executed_at` per print | no | **per print** (execution time), not for derived aggregates; no side |
| UW institution ownership | dated query returned 0 rows | no (institutional_holdings 0 rows) | none |
| UW headlines | no date filter; 50 rows = 23 days | no | per headline `created_at`; sentiment field uninformative |
| UW earnings history (`street_mean_est`, moves) | yes | outcomes only since Oct 2026 | **none for the consensus** (may be restated); actual results later-known by definition (use only for events finished before the cutoff) |
| UW daily candles | yes ~10 y | no | prices are final once the session closed (regular session 'r' rows only); split/dividend adjustment risk noted |
| SEC filings | yes (1,000 filings back to 2015) | no | per filing `acceptanceDateTime` (a filing fact; **not** a release time) |
| SEC companyfacts | yes | no | `filed` date per fact; no consensus |

Rule: a Track H feature is admitted only if its row in the last column is at least "per row/print/filing"; fields with "none" are excluded or the event is flagged `ASOF_UNPROVEN` and reported in a separate sensitivity table, never in the strict result.

## 3. Track H pilot (prepared, NOT executed)
### 3.1 Sample-selection method (pre-registered before any request)
1. **Universe (zero provider requests):** read-only export from PostgreSQL of the distinct symbols that have at least one GRADED row in `earnings_event_outcomes` (241 events today) - these also have existing-model predictions, so baselines exist. Exclusions, each counted and listed: no SEC CIK (cannot be mapped from the disk-cached SEC ticker file), foreign private issuers that file 6-K instead of 8-K, ETFs/funds, and symbols whose events are all `PENDING_TIMING`.
2. **Stratification:** 2 x 3 cells: timing label (BMO/AMC) x liquidity tertile, where liquidity = average volume from the already persisted `ua:avgvol` cache entries (zero requests; symbols without it are put in the lowest tertile and flagged). Target 8-9 symbols per cell, 50 in total, deliberately including thin names because the depth probe covered only AAPL.
3. **Selection inside a cell:** symbols ordered by SHA-256(`"smartmove-pilot-1" + symbol`); take the first N of each cell. Seed string, ordering rule, the full eligible list and the 50-symbol list are written to a file and hashed **before** the first request; the hash goes into every later report. No hand-picking.
4. **Events:** every earnings event of those 50 symbols with `report_date >= 2024-10-01` (the verified volatility-history depth), as returned by the UW earnings history. Expected 8-9 per symbol, **about 400-450 events**; the exact dates exist only after stage H1a (cannot be known without that one request per symbol). Events with unknown BMO/AMC label, and cases where the label and the SEC acceptance time disagree or are ambiguous, are excluded from the strict set and counted; they appear in a separately labelled extended set.

### 3.2 Stages, endpoints and exact request counts
| Stage | Purpose | Provider and endpoint | Requests | Time at 1 request / 2 s |
|---|---|---|---|---|
| H1a | exact event list for the 50 symbols | UW `GET /api/earnings/{ticker}` | **50 UW** | about 2 min |
| H1b | prices and filing times | UW `GET /api/stock/{ticker}/ohlc/1d` (limit 2,500, regular session rows) for 50 symbols + SPY + 11 sector SPDR ETFs; SEC `GET data.sec.gov/submissions/CIK##########.json` for 50 symbols (CIK from the disk-cached SEC ticker file, 0 extra requests) | **62 UW + 50 SEC** | about 4 min |
| H1c | category 7, optional subsample | UW `iv-rank?date=` and `volatility/term-structure?date=` once per event, for a seeded 100-event subsample | **200 UW** | about 7 min |
| H1d | category 7, rest of pilot (only if H1c looks usable) | same, remaining events | about 650 UW | about 22 min |
Totals: minimal H1a+H1b **112 UW + 50 SEC**; with the 100-event volatility subsample **312 UW + 50 SEC**; whole pilot about 960 UW + 50 SEC. Each stage is a separate approval gate; H1b and later start only after you have seen the H1a event list and its hash.

### 3.3 Provider limits
* UW: response headers showed `x-uw-req-per-minute-remaining: 1000000` (effectively unlimited per minute) but the project's own rule is 3 concurrent requests on a token shared with the live engine, the warmer and another local app. The pilot is sequential (1 in flight), 2 s apart, no retries, a hard request counter that stops the run.
* SEC: published limit 10 requests/second and a declared User-Agent; pilot uses 0.5 request/second.
* The request budget (counter, stage, endpoint, status, UTC time) is written to a log file for each stage.

### 3.4 Estimated cost
No per-request charge is identifiable in the code or headers (the application's warm state reports `plan_unlimited: true` for UW and SEC is free), so the marginal provider cost of the pilot is **expected to be 0**; this is **not verified** against your billing terms. Local cost: under 50 MB of files (the 50 submissions JSON are about 0.16 MB each; 50 candle responses about 0.3 MB each), no database write, no VPS change. Your time: review of the event list before H1b.

### 3.5 Pilot budget and impact on existing provider usage (decision 6)
Proposed controlled budget: **at most 200 UW requests per day and 100 SEC requests per day during the pilot, manual runs only, no recurring or scheduled job**. Existing load for comparison: the warmer used 10,448 UW requests on 28 Oct and 18,318 so far on the current provider day, so the pilot adds about 1-2 % (H1a+H1b = 112 in one afternoon about 1 %). It runs from the developer machine with the dev key, sequentially, outside US market hours when possible, and a 429 or a concurrency refusal stops the run. This is a pilot allowance only; no daily cap for forward capture is activated (see 4.5 for what such a cap would mean later).

## 4. Prediction scheduling inside the 24-48 h window (requirement A) - DESIGN ONLY
An after-close daily job cannot guarantee the window: the window length is 24 h and its position depends on each event's release time, which may fall on a weekend morning (BMO Monday 06:00 ET opens its window Saturday 06:00 ET). The design is therefore **event-level**, not job-level.

### 4.1 Definitions (all times America/New_York via zoneinfo, stored as UTC)
* `E` = earliest plausible release of the event version: BMO -> 06:00 ET on `earnings_date`; AMC -> 16:00 ET on `earnings_date`; UNKNOWN label -> 06:00 ET on `earnings_date` (conservative; the prediction is then NO_TRADE / UNKNOWN_TIMING but the evidence is still captured). This is a **conservative timing assumption**, stored as `PROVISIONAL_ASSUMPTION`, with SEC acceptance time stored separately as `SEC_ACCEPTANCE_TIME` once the filing exists.
* Window `W = [E - 48 h, E - 24 h]`; deadline `D = E - 24 h`; planned target `P = E - 36 h` (middle of the window, leaves margin both ways).

### 4.2 Scheduler (one process loop behind `SMART_MOVE_ENABLED`, tick every 15 minutes, 7 days a week; ticks make **no provider request** by themselves)
1. Registry refresh: read the event list from caches the application already keeps (calendar/earnings caches), write new event versions when date or timing changes; at most one cheap daily refresh of the calendar source.
2. Selection: events whose `now >= W.start`, with no in-window prediction for the current event version, and attempts left.
3. Capture + predict: run the capture within the per-run request cap; write input snapshots, then the prediction row, in that order.
4. Retry: on failure retry after 30 minutes with the remaining attempts until `D - 2 h`.
5. Last-chance degraded mode at `D - 2 h`: only already-warmed caches (zero new requests); each missing input gets its reason code; prediction still produced if coverage allows, else NO_TRADE with reason.
6. After `D`: if still no prediction, write a `MISSED` ledger row with the reason (`CAPTURE_FAILED`, `BUDGET_STOP`, `LATE_DISCOVERY`, `SCHEDULER_DOWN`, `NO_COVERAGE`). A prediction made after `D` is stored with `in_window = false` and is excluded from strict evaluation; it is never relabelled as in-window.
7. Event changes: a new event version recomputes `W`; earlier predictions are marked `superseded`, and a new in-window prediction is required if the new window is still open.
8. Late discovery: an event first seen when less than 24 h remain gets `MISSED/LATE_DISCOVERY` (no prediction); between 48 h and 24 h it is captured immediately.

### 4.3 Explicit checks
* **Event-level coverage report (daily, read-only):** for every event whose window closed in the last 24 h: `PREDICTED_IN_WINDOW` / `PREDICTED_LATE` / `MISSED(reason)`; headline metric = share in window; target >= 95 %, below that the report says so on the preview page. The metric itself is stored (`sm_capture_runs`) so it can be audited later.
* **Scheduler heartbeat:** last tick time is stored; a gap over 60 minutes is flagged and every event whose window opened during the gap is counted `SCHEDULER_DOWN` if missed.
* **Timing tests (to be written with the code):** table-driven tests for BMO/AMC/UNKNOWN, weekend and Monday windows, the two DST transitions (March, November), early-close days, event date moved, event discovered late, scheduler restart in mid-window.

### 4.4 Request footprint of the scheduler (estimates, not activated)
Per captured event about 4-5 new UW requests (iv-rank, term-structure, headlines, dark pool, earnings history if not warm; unusual/candles come from warmed caches). The earlier "about 590 names" figure was the **warmed** set; the calendar the application actually predicts for is wider: **about 1,520 distinct events per model over the 11 snapshot days** (production audit). So the choice matters:
| Capture scope | Events / year | New UW requests / year | Peak days | Storage / year (compressed) |
|---|---|---|---|---|
| warmed names only (~590) | about 2,400 | about 12,000 | about 700 / day for ~11 days | about 35 MB (one capture per event) |
| whole predicted calendar | about 6,000 | about 30,000 | about 700-1,700 / day | about 90 MB |
### 4.5 What a daily cap would do later
A cap of C requests/day captures only about C/5 events/day. At 150/day that is 30 events/day, which would cause misses in peak weeks (up to ~140-340 events/day). So a cap and the 95 % in-window target conflict in earnings season: the owner must choose between a higher seasonal cap, a narrower event scope (e.g. warmed names only), or accepting lower coverage. **No cap and no job is activated now.**

## 5. Evaluation rules (decision 4)
* **Outcome (provisional):** next-session close-to-close, the Phase 2A rule (`reaction_window`, regular-session closes), classes **UP** (> +0.5 %), **DOWN** (< -0.5 %), **FLAT** (within +-0.5 %). Named "next-session close-to-close"; never an immediate reaction.
* **Three-way table always shown:** rows BUY / SELL / NO_TRADE x columns UP / FLAT / DOWN, with counts and %, for the strict set and the extended set separately, plus the same for each baseline.
* **Denominators stated explicitly, flat events never silently dropped:**
  * coverage = decided calls (BUY+SELL) / all scorable events;
  * strict hit rate = (BUY and UP, or SELL and DOWN) / decided calls (a FLAT outcome is a miss);
  * direction-only hit rate (ex-flat, labelled as such) = same numerator / decided calls whose outcome was not FLAT;
  * FLAT share per class and the outcomes of NO_TRADE shown (cost of abstaining).
  * BUY and SELL always reported separately, each with Wilson 95 % intervals; date-clustered bootstrap for the aggregate.
* **Continuous evaluation:** net return per call (BUY: +move, SELL: -move) after costs: mean, median, hit rate of net return > 0, payoff ratio; Spearman correlation of (bull_pts - bear_pts) with the next-session return; return of top vs bottom score quintile; all with clustered bootstrap intervals; equal-weight, no leverage.
* **Cost model (provisional, shown with a x2 sensitivity):** per side = half the quoted spread (liquid large cap 2-3 bps, mid 8 bps, thin 20 bps) + slippage (2 / 5 / 10 bps) + commission (about 1 bp); SELL adds a borrow cost (assume 1 bp/day for easy-to-borrow, hard-to-borrow names excluded and counted); entry at the close before the release (AMC) or previous close (BMO) requires a market-on-close order before the broker cutoff, so a prediction must exist by `D` (satisfied by section 4) - an event whose prediction is late is not tradable in the simulation. A gap down/up through the stop is not modelled (no stops).
* **Baselines:** always BUY, always SELL, previous-earnings-move sign, 20-day trend sign, SPY direction, random with equal coverage, existing models (section 7).

## 6. Track F schema (design only; additions from the decisions; no table is created)
Four append-only tables, prefix **`smv_`** (changed from `sm_` to avoid confusion with the old `sm:` cache keys, section 8): `smv_event_versions`, `smv_input_snapshots`, `smv_predictions`, `smv_capture_runs`. Changes against the earlier design:
* `smv_event_versions`: `timing_label`, `timing_label_source`, `assumed_release_ts_utc` (the conservative assumption, `PROVISIONAL_ASSUMPTION`), `sec_acceptance_ts_utc` NULL + `sec_accession` (a filing fact, kept apart), `public_release_ts_utc` NULL + `public_release_evidence` NULL (only when independently supported -> status `VERIFIED_PUBLIC_RELEASE`), `window_start_utc`, `deadline_utc`, `recorded_at`. A correction is a new version row, never an update.
* `smv_input_snapshots`: `provider_as_of` (the source's own stamp, NULL if none), `capture_ts_utc` (ours), `as_of_quality` (`PER_ROW_STAMPED` | `DATED_NOT_STAMPED` | `FETCH_TIME_ONLY` | `DERIVED`), `request_hash`, `payload_sha256`, `status`, `missing_reason`, content-addressed id; same missing-reason code list as before.
* `smv_predictions`: `prediction_version` (1, 2, ...) and `supersedes_prediction_id` so a re-run never overwrites; `in_window`, `scheduled_target_utc`, `hours_before_assumed_release`, `model_version`, `config_sha256` (marked `PROVISIONAL_UNVALIDATED`), `signal`, `bull_pts`, `bear_pts`, `coverage_pct`, `reason_codes`, `explanation` with per-category `snapshot_ids` (input provenance).
* `smv_capture_runs`: tick time, events seen, events in window, predictions made, missed-by-reason counts, requests by source and status, cap, stop reason - the audit trail of requirement A.
All four have UPDATE/DELETE/TRUNCATE-rejecting triggers (same pattern as the verified evidence DDL). Outcomes and existing-model signals are read from `earnings_event_outcomes` / `model_predictions`, never copied.

## 7. Read-only joins to `model_predictions` for baselines (decision 7)
* **One row per event and model:** for each (model, symbol, earnings_date) take the snapshot with the latest `predicted_at` that is `<= cutoff` and in the 24-48 h window; ignore later snapshots (future leakage) and earlier duplicates (duplicated events). Rows have unique (model, symbol, earnings_date, snapshot_date), so the choice is deterministic.
* **No future information:** require `predicted_at < D` on the row itself (not only `snapshot_date`); never use the legacy columns `actual_*`/`evaluated_at` (filled later); labels come only from `earnings_event_outcomes` rows with status GRADED, joined by (symbol, earnings_date).
* **Selection bias controls:** the denominator is all events that had a prediction in the window, split into GRADED / PENDING_TIMING / PENDING_* / no outcome yet; ungraded-but-due events are reported as missing, not dropped; grading backlog is cleared before any comparison because the grader works alphabetically 120 symbols per run; events with NULL `report_timing` are counted as unscorable (13.5 % of rows).
* **Frozen export:** one read-only transaction writes a hashed CSV; every report cites the hash. No write, no cache refresh, no new provider request.

## 8. The `sm:*` cache keys (read-only investigation)
* **Owner:** the **Smart Money** tab (not Smart Move): added in commit `f09b8c5e` ("Add Smart Money tab ... behind SMART_MONEY_TAB=on"), extended in `4a09cc88` (progressive scanner) and **removed the same day (2026-10-15) in `b9da488a`** ("Remove the Smart Money tab": `smart_money_model.py` 707 lines, its test, the page, route, client method and sidebar entry). The six prefixes seen in the audit (`sm:one`, `sm:ovser`, `sm:news`, `sm:flow`, `sm:own`, `sm:insider`, 58 keys each, written about 2026-10-15) match that module.
* **Writers/readers today:** none. The removal commit is an ancestor of `38ca61d`, `b4533203`, `669217f3` and the current working branch, so no build that has been or will be deployed contains the code; `git grep` finds no `sm:` key use outside history.
* **Retention:** `db_cache.py` has no delete, purge or expiry for `swr_cache`; rows stay until overwritten, so these 348 rows are orphaned and will persist. Harmless (a few KB each) and not touched by this work; deletion would be a production write needing its own approval.
* **Conflict avoidance:** Smart Move uses table prefix `smv_` and, if it ever needs cache keys, `smv:`; it never reads or writes `sm:`.

## 9. Corrections to earlier statements
* Universe size: earlier "about 590 names" is the warmed set; the application predicts for about 1,520 distinct events per model in the 11 snapshot days (section 4.4).
* "SEC acceptance = verified release time" withdrawn (decision 3): it is a filing acceptance time; label strings `VERIFIED_SEC`/`SEC_8K` in earlier documents are replaced by `SEC_ACCEPTANCE_TIME`.

## 10. Revised implementation plan and outstanding decisions
| Step | Work | Gate |
|---|---|---|
| 1 | You decide section 11; I freeze the pre-registration file (seed, universe export spec, thresholds placeholder) | your approval |
| 2 | Read-only universe export on the VPS (script prepared by me, run by you): distinct symbols with GRADED outcomes + `ua:avgvol` values | your run |
| 3 | H1a (50 UW requests), then I show the event list + hash | your approval of H1a |
| 4 | H1b (62 UW + 50 SEC), H1c (200 UW) | your approval of each |
| 5 | Module skeleton, flag, immutable-store DDL file (not applied), PIT filter + leakage tests, scheduler logic with fake clock tests (section 4.3), all flag-off | your approval to code |
| 6 | Track H partial-evidence backtest and report (sections 1, 2, 5, 7) | after data |
| 7 | Track F capture design review, cap decision (4.5), preview API/page | your approval; no recurring job before |

### 11. Decisions I need from you
1. **Depth probe:** confirm the 6 requests already run satisfy decision 1 (no second set), or name a different set.
2. **H1a only:** approve the 50 UW earnings-history requests (needed to produce exact event dates), or give me the 50 symbols and event dates yourself.
3. **Pilot allowance:** approve 200 UW + 100 SEC requests per day for the pilot (manual, sequential, no recurring job).
4. **Universe export:** approve preparing the read-only VPS script for the symbol/liquidity export (section 3.1).
5. **Capture scope for later:** warmed names (~2,400 events/year) or the whole predicted calendar (~6,000/year)?
6. **Table prefix:** accept `smv_` instead of `sm_`?
7. **Commit:** this revision is committed locally (no push).

---
# Revision 3 (2026-10-30 evening): H1a procedure, freshness separation, calendar edge cases, scope phasing
Applies the owner's H1a decisions. Still research only: no scoring engine, no migration, no recurring job, no push. Supersedes sections 3.5 (H1a has its own strict budget: **50 UW requests, 0 SEC**, nothing else) and the scope row of 4.4.

## 12. H1a status and exact procedure
**H1a has NOT been run.** The 50-symbol sample must come from the production universe (symbols with a GRADED outcome) and that export does not exist yet; it needs a read-only run on the VPS by you. I will not pick symbols by hand or from a stale list. Prepared and tested offline (no real request, no production access):

| Tool (in `docs/smart_move/scripts/`) | Purpose | SHA-256 |
|---|---|---|
| `export_universe.py` (VPS, read-only) | one CSV row per symbol with >= 1 GRADED outcome: counts, majority timing inputs, last base close, dates, `avg_volume_20` from the already-stored `earn:equity:<SYM>` cache entry (empty when absent) | `114fa56fcbd69e99bf13625988175ae461466929728fcd2f996651e2413a43d1` (4,800 bytes) |
| `PASTE_export_universe.ps1` | hash-checked staging block (refuses to overwrite, executes nothing) | - |
| `freeze_sample.py` (local) | pre-registered selection (`smv-pilot-1`, seed `smartmove-pilot-1`), writes `h1a_manifest.json` + `.sha256`, lists every exclusion and the selection-bias notes | `69ba258d2c127a786104bdcc308000858292629d0431774ece075123fe17308f` |
| `h1a_runner.py` (local) | the only request code: refuses unless the manifest SHA-256 matches, max 50 UW (one `GET /api/earnings/{SYM}?limit=24` per symbol, the call the application itself makes), 0 SEC, 2 s apart, no retries, stops on 429, never overwrites output, keeps only rows from 2024-10-01 | `5de19f9cfcf07a6aace1b2475287ad48263a1f41ffeb8be7aa0c82d204cca302` |

Offline tests passed (15 checks): freeze deterministic, 50 unique symbols, mixed/no-timing symbols excluded and listed, price-proxy fallback when volume coverage < 80 %, runner dry run sends nothing, wrong hash refused, non-local base URL refused, stops on a 429 (8 sent, 42 unrequested), exactly 50 requests on a full run, overwrite refused; export script run against a throw-away local PostgreSQL with synthetic rows (gz1-compressed cache value decoded, tables unchanged afterwards, writes rejected in the read-only session).

**Installation procedure on the VPS (verified in the sandbox with the same block; nothing else is touched):**
1. Open `PASTE_export_universe.ps1`, copy all, paste into the elevated PowerShell. Expected: `STAGED C:\audit-reports\smart_move_universe\export_universe.py  4800 bytes  SHA-256 114fa56f...43d1`. `STOPPED` means the text was altered: nothing is written; paste again.
2. Run (read-only, one connection, aggregates only, writes one new CSV + `.sha256`):
```powershell
$py = 'C:\apps\us-stock-reader\backend\.venv\Scripts\python.exe'
& $py -I 'C:\audit-reports\smart_move_universe\export_universe.py'
```
3. Send me the printed lines and the CSV (tickers, counts, one price and one volume figure per symbol: nothing sensitive), or copy the file to this PC.
4. I then run `freeze_sample.py`, show the manifest and its SHA-256 here, and run `h1a_runner.py` (<= 50 UW requests) unless you tell me to wait for your review of the manifest. After H1a I report: actual event count, date coverage, release-time quality, missing fields, sample-selection limitations and the revised H1b request budget, then stop.

## 13. Separating source freshness from prediction timestamp (never label stale evidence fresh)
Every stored input carries four separate facts, and a prediction carries its own:
* `source_as_of_ts` - the provider's stamp (NULL when none; never inferred);
* `capture_ts_utc` - when we fetched it;
* `source_session` - the last COMPLETED trading session the data reflects, computed with the repo's `market_calendar` (trading days, early closes) in America/New_York;
* `freshness_class`, computed at capture and again at prediction time: `FRESH` (reflects the latest completed session and is within the category's maximum age), `LAST_SESSION_WEEKEND` / `LAST_SESSION_HOLIDAY` (the latest session is the correct, but old, evidence: allowed and **always labelled**, never "fresh"), `STALE_AGE` (older than the category maximum: dropped with reason `STALE_AGE`), `UNSTAMPED` (no provider stamp: capture time only). Provisional maxima: prices and IV rank must include the last completed session; flow/dark pool/unusual: last-session data captured within 24 h; headlines captured within 6 h; consensus within 24 h.
* The prediction row has `prediction_ts_utc` (when computed) **distinct from** `evidence_as_of_min/max`, a per-category `freshness` map and `stale_weight_share` (weight of inputs that are not FRESH). Reports split every metric by freshness class; the strict set requires that no available input is `STALE_AGE`. The preview page shows "evidence as of <session>, captured <time>, predicted <time>" and prints "weekend evidence" on Saturday/Sunday windows.

## 14. Calendar and timing edge cases (design; tested with a fake clock when coded)
* **Weekends:** ticks run 7 days; a Monday BMO event's window opens Saturday 06:00 ET; its evidence is the Friday session, class `LAST_SESSION_WEEKEND`.
* **Market holidays and early closes:** from `market_calendar`; the window itself is in wall-clock hours (48-24 h before the assumed release) but `source_session` skips holidays; tests for Thanksgiving week, Good Friday, 3-4 July, Christmas, the day after Thanksgiving (13:00 close), both DST transitions.
* **Changing earnings dates:** every change is a new `smv_event_versions` row; earlier predictions are marked superseded; evaluation uses the FINAL event version: a prediction made in the window of an earlier version but earlier than 48 h before the final assumed release is flagged `EARLY_FOR_FINAL_DATE` (not in the strict set); a date moved earlier so that the new window has already passed is `MISSED/LATE_DISCOVERY`. Whether the provider marks the date confirmed is stored when available, never assumed.
* **BMO/AMC uncertainty:** window from the earliest plausible release (BMO 06:00 ET, AMC 16:00 ET, unknown 06:00 ET on the date); sources that disagree (UW label vs the Finviz clock string) -> `timing_conflict`; unknown, conflicting, "during market hours" and SEC-acceptance-inconsistent cases get signal NO_TRADE (UNKNOWN_TIMING) and are outside the strict set but are captured and reported in a separate extended set.

## 15. Scope phasing
Design for the full predicted calendar (about 6,000 events/year, about 30,000 new UW requests/year, about 90 MB/year compressed): every table, the scheduler loop, the request governor and the coverage report are sized for it from day one, with an `event_scope` column (`WARMED` | `CALENDAR`). Forward-capture validation **starts with the warmed-name subset** (about 2,400 events/year). Expansion to the whole calendar is a separate approval and is gated on measured evidence from the warmed phase, reported together: in-window coverage (target >= 95 % of window-closed events), freshness-class mix, request volume and 429/concurrency incidents against the shared token, missing-reason rates, database growth, and prediction quality shown with intervals (monitoring only: with a small sample it cannot prove an edge, so it informs but does not by itself justify expansion).

## 16. Universe v1 export result and why the sample is NOT frozen from it (2026-10-30)
The owner ran `export_universe.py` (v1): `universe_20261030-111630.csv`, SHA-256 `843cd0349d86fe65c845c1b59645d4e5a2eaa76bd3b3cc7f8095e584878ec4ae`, 240 symbols with a GRADED outcome, **0 with `avg_volume_20`** in the stored cache. The pasted rows run from `AAMI` to `FCFS` and end there: they are the whole file (240 rows), not a truncated paste.
**Finding:** the graded universe is the first 240 symbols **in alphabetical order** (A to FCFS). `earnings_outcomes.run` grades `sorted(symbols)[:120]` per cycle, and two cycles have run. A sample drawn from it would contain no symbol after "FCFS" - an artefact of grading order, not of the market. This is the grading-order bias recorded earlier, now measured. The pre-registered rule (v1 stratification by timing x price tertile among graded symbols) is therefore **not used** and **no H1a request has been sent**.
**Proposed protocol change (needs your approval, made before any request):** universe v2 = symbols with a recorded prediction for an event dated today or earlier, whatever the grading status (`export_universe_v2.py`, SHA-256 `314ae68bcc67329fec939d2dd502e968736d1e83b8a14b961f4035cd54ad5b23`, 4,680 bytes; staging block `PASTE_export_universe_v2.ps1`). Event timing comes from the latest non-empty `report_timing` label of each event (Before Open -> BMO, After Close -> AMC, anything else = no timing, excluded and counted). Ungraded symbols have no stored price and no volume, so liquidity strata are dropped: method `smv-pilot-2` stratifies by timing only (25 BMO + 25 AMC, seeded hash order) when fewer than 80 % have a price or volume, and liquidity is described afterwards from the pilot's own candle volumes (H1b), never used for selection. Known limit: thin names may be over- or under-represented; the post-hoc liquidity description will show it.
Offline tests of v2 against a throw-away PostgreSQL (400 synthetic symbols, grader reaching only the first 120): future-dated events ignored, tables unchanged, selection spans S003..S399 (not just the graded prefix), 25/25 strata. The earlier 15 checks of the freeze/runner pair were re-run after the change: all pass.
**Alternative:** wait until the grading backlog clears (about 2-3 more cycles) and re-run v1; slower, and the universe would still depend on which events have prices.
