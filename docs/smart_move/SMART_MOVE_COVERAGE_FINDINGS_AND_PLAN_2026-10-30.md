# Earnings Smart Move AI - coverage findings and implementation plan (2026-10-30)

Branch `feature/smart-move` (from `fix/earnings-freshness-evidence` @ 31f71e5c), worktree `scratchpad/wt_smart`. Nothing committed, pushed, deployed or run against production. No scoring code written. Existing models, pinned files, production configuration and the pending earnings deployment untouched.

## A. Provider history probe (approved: max 6 UW + 2 SEC) - executed once, no retry
Tool: `docs/smart_move/scripts/provider_probe.py` (fixed 8-request plan, sequential, 2 s apart, no redirects followed, key read from the dev machine's `backend/.env`, never printed or written). Results file: `docs/smart_move/probe_results_20261030-122235.json` (summaries only, no raw bodies, no key). Provider limits: UW answered `x-uw-req-per-minute-remaining: 1000000` (unlimited plan; far below the 3-concurrent rule because requests were sequential); SEC sent no limit headers (8 requests, 2 s apart, declared User-Agent, well below 10/s).

| # | Time (UTC, 2026-10-30) | Request | Status | What came back | Remaining gap |
|---|---|---|---|---|---|
| 1 | 08:22:35 | UW `stock/AAPL/iv-rank?date=2026-06-01` | 200 | 5 daily rows 2026-05-26..06-01 (`iv_rank_1y`, `volatility`, `close`, per-row `updated_at` ~22:35Z of that day) | depth beyond ~5 months unknown; one request returns only ~5 days |
| 2 | 08:22:37 | UW `stock/AAPL/volatility/term-structure?date=2026-09-02` | 200 | 23 expiries with `implied_move`, `implied_move_perc`, `volatility`, `date`=2026-09-02 | depth beyond 2 months unknown |
| 3 | 08:22:40 | UW `option-activity/unusual` (AAPL, date 2026-09-02, limit 50) | 200 | 18 contracts; **no row-level timestamp**; `next_earnings_date` = 2026-11-02, `stock_price` present | cannot prove rows are "as of" 2026-09-02 (possible later-known fields) |
| 4 | 08:22:43 | UW `darkpool/AAPL` (date 2026-09-02, limit 50) | 200 | 50 prints, `executed_at` 2026-09-02 20:00..23:54Z (page filled: limit reached) | a full day needs paging (many requests); no buyer/seller side |
| 5 | 08:22:45 | UW `institution/AAPL/ownership` (date 2026-09-02) | 200 | **0 rows** | the `date` filter is not usable as an as-of view; not tested further (budget) |
| 6 | 08:22:48 | UW `news/headlines` (AAPL, limit 50) | 200 | 50 headlines with `created_at`, spanning 2026-10-06..10-29 (only 23 days for 50 rows); `sentiment` = "neutral" in **all 50** | no date filter; history depth by paging unknown; sentiment field uninformative |
| 7 | 08:22:51 | SEC `submissions/CIK0000320193.json` | 200 | 1,000 recent filings back to 2015-10-05, **all with `acceptanceDateTime`**; 45 8-K Item 2.02 filings (latest accepted 2026-07-30 20:30:28Z = 16:30 ET, 2026-04-30 20:30:41Z, 2026-01-29 21:30:33Z); 601 Form 4 with acceptance time | one company per request; some companies release before filing |
| 8 | 08:22:53 | SEC `api/xbrl/companyfacts/CIK0000320193.json` | 200 (3.8 MB) | diluted EPS 338 facts and revenue facts since 2009/2019, each with `filed` date, form and fiscal period | actual results only; **no consensus estimates** in SEC data |

8 requests were sent, 8 succeeded, none repeated.

## B. Actual coverage per category (your eight categories)
"Historical test" = can a chronological, point-in-time (PIT) backtest be built from data that exists today. Counts of rows inside our own database are pending the VPS coverage script (section D); until then DB-based coverage is **NOT VERIFIED**.

| # | Category (pts) | Evidence from code audit + probe | Historical test? | Track |
|---|---|---|---|---|
| 1 | Options Flow & Unusual Contracts (20) | Live fetch only in code; code notes "not retained beyond ~15 days". The dated query works (probe 3) but rows carry no timestamp and contain later-known fields, so PIT is unproven | **No (not verified)** | F only |
| 2 | Institutional Activity & Dark Pool (15) | Dark pool: dated, timestamped prints exist 2 months back (probe 4) but no side, full days need paging. Institutional: UW dated query returns nothing (probe 5); our 13F tables carry `filing_date` only, quarterly, up to 45 days late | **Partly**: dark-pool size/count features possible; "institutional flow" only as a slow quarterly context stamped by filing date | H (reduced) + F |
| 3 | Historical Earnings Reaction (15) | UW earnings history (up to 24 quarters per name) + daily candles; SEC 8-K Item 2.02 acceptance times can **verify release times retroactively** (probe 7) | **Yes** | H + F |
| 4 | EPS & Revenue Estimate Revisions (15) | SEC gives actuals with filed dates, not estimates; UW `street_mean_est` has no as-of; our `estimate_revisions` is overwritten; `estimate_snapshots` coverage unknown | **No today** (pending DB counts) | F only |
| 5 | Price, Volume & Technical Trend (10) | UW daily candles ~10 years | **Yes** | H + F |
| 6 | News, Guidance & Sentiment (10) | Headlines are timestamped but only ~23 days reached by 50 rows, sentiment uninformative (all neutral), guidance text not available (8-K item codes give timing, not content) | **No** | F only (timing counts; sentiment/guidance stay "missing") |
| 7 | Implied Move & Volatility (10) | IV rank daily rows with per-day `updated_at` stamp, 5 months verified; term-structure implied move dated, 2 months verified; `expected_move_perc` in earnings history | **Yes** (depth to be extended) | H + F |
| 8 | Sector, Market & Peer Direction (5) | candles for SPY, sector ETFs, peers | **Yes** | H + F |

Historically testable weight: categories 3, 5, 7, 8 = 40 of 100 points (plus a partial category 2). **A historical backtest of the full 100-point model is not possible today.** Track H will test only a reduced, explicitly labelled score (max 40 points, scaled for display as "H-40") and must never be reported as the performance of the full model.

## C. Rules adopted (from your instructions)
* Missing data = not counted, no neutral points. Each category returns `bull_pts`, `bear_pts`, `weight`, `available`. Coverage = available weight / 100. NO TRADE reasons: INSUFFICIENT_COVERAGE, CONFLICT, BELOW_MARGIN, ILLIQUID, UNKNOWN_TIMING. Weights are provisional (marked `PROVISIONAL_UNVALIDATED` in the stored config) and thresholds are fixed before any test.
* Cutoff: verified 8-K acceptance time when it exists (label `VERIFIED_SEC`), otherwise conservative nominal (BMO 06:00 / AMC 16:00 ET, label `PROVISIONAL`); features must be stamped at least 24 h before it. IV-rank values are used only from the previous day or earlier (their stamp is ~22:35Z of the same day).
* Outcome label for Track H: next-session close-to-close move, named exactly that in every table and report. It is **not** an immediate-reaction measure; immediate reaction (+5/15/30/60 min) is only collected forward (Track F) from verified release times, and only where minute data exists.
* Track H and Track F use separate code paths, tables and report sections and are never pooled.
* Stored per prediction (immutable, append-only, triggers on UPDATE/DELETE/TRUNCATE as in the verified evidence DDL): input snapshot (content hash, as-of stamps), predicted_at, model_version, config hash, reason codes, explanation.
* `SMART_MOVE_ENABLED` default off: no router mounted, no capture loop, no tables created, frontend nav hidden (build flag).
* Statistical honesty: report hit rates with Wilson 95 % intervals, BUY and SELL separately, date-clustered bootstrap, and a pre-registered minimum sample. Illustration: with 300 independent events and a true hit rate near 50 %, the 95 % interval is about +-5.7 points overall and about +-8 points for BUY and SELL separately, and events cluster on a few earnings dates, so the effective sample is smaller. 300 events or eight weeks therefore cannot prove an edge; they only bound how large an edge could be hidden. Baselines: always BUY, always SELL, previous-earnings-move sign, 20-day trend sign, SPY direction, random with equal coverage, and the existing models where available. Costs: half-spread proxy + slippage + commission per side, flat band +-0.5 %, liquidity floor.

## D. Read-only VPS coverage script (prepared, NOT executed)
* `docs/smart_move/scripts/coverage_audit.py` (SHA-256 `d2c28ab51b1089eeda9f79adb18a18665152532bb9445af817b0a1b36dc0803c`, 8,138 bytes, ASCII). One connection, session set read-only, 30 s statement timeout, aggregate SELECTs only, missing tables/columns recorded; writes one new JSON (never overwritten) to `C:\audit-reports\smart_move_coverage`. Tested here against a throw-away local PostgreSQL 17 with synthetic tables (row counts unchanged afterwards); not tested against production.
* `docs/smart_move/PASTE_coverage_audit.ps1`: hash-checked staging block (refuses to overwrite, executes nothing).
* Commands for you (elevated PowerShell on the VPS): paste the block, then

```powershell
$py = 'C:\apps\us-stock-reader\backend\.venv\Scripts\python.exe'
& $py -I 'C:\audit-reports\smart_move_coverage\coverage_audit.py'
```

## E. Plan
| Step | Work | Needs |
|---|---|---|
| 1 | You run section D; I fold the real DB counts into section B | your run |
| 2 | Decide the Track H universe and a request budget: SEC 8-K lookups (1 request per company, free) to verify historical release times; UW iv-rank / term-structure windows (one request returns ~5 days or one date) | exact request budget approved by you |
| 3 | Module skeleton `smart_move_*` (settings, flag, immutable store DDL, PIT cutoff filter, leakage test), all flag-off | none |
| 4 | Track H builders + walk-forward backtest tool (H-40 only) + report | step 2 data |
| 5 | Track F forward capture + preview API + frontend page, behind flags | flag-on needs your approval |
| 6 | Pre-registered Track F evaluation with intervals | sample accrual |
Open risks: none of the flow-type categories (1, 4, 6, most of 2) can be validated historically; sample sizes will be small; BMO/AMC label errors; shared UW token. The module will display these limits on the preview page.
