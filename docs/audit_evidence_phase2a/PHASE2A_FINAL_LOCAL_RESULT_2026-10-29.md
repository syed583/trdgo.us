# Phase 2A final integrity changes - local verification (2026-10-29)

Local only. Nothing pushed, deployed or written to production. Base for model-pin comparison: 38ca61d.

## Changes (this step)
| File | Change |
|---|---|
| backend/earnings_outcomes.py | `regular_session_closes()` - explicit market_time 'r' selection (order independent); `default_bars_fetcher` uses it. Date with only pre/post rows => no close => PENDING_PRICE (retried). Unlabelled single-row dates use highest volume. |
| backend/model_prediction_service.py | `grade()` no longer calls `sync_legacy_columns` unless `EARNINGS_LEGACY_SYNC` is truthy (default OFF). `LEGACY_OUTCOMES_NOTE`; `history()` and `overlay()` return `legacy_outcomes_invalid: true` + note. |
| backend/api_routes.py | `/model-accuracy/scoreboard-legacy` response carries `legacy_outcomes_invalid` + note. |
| frontend/src/pages/ModelAccuracyPage.tsx | Prediction-verification table titled and bannered as legacy outcomes, INVALID for verified performance; columns renamed "(legacy)". |
| frontend/src/pages/AllModelsPage.tsx | Previous-call / last-move tooltips say "legacy outcome, unverified". |
| backend/tests/test_regular_session_closes.py (14), test_phase2a_final.py (29) | New regression tests. |

Unchanged on purpose: `earnings_trade_service._historical_moves` (model input - needs separate approval), all model scoring, `sync_legacy_columns` itself (kept, opt-in), all historical `model_predictions` rows.

## Tests
New regression cases: reversed/shuffled provider row order (25 shuffles + reversed), mixed pr/r/po with extended row last, missing regular candle (reaction and base) -> PENDING_PRICE then GRADED when the row arrives, incomplete session (11:00 ET partial row, 45-min buffer, early close 13:00 ET), legacy sync default OFF / opt-in, legacy labels (backend + frontend), WSO.B stays PENDING_PRICE with no symbol remapping, scoreboard reproduces the audit sample (Gemini mix 41/16, 24/13, 34 neutral; baseline 45/43/11; always-BUY 45.5% [36.0,55.2]) and all 12 Wilson intervals printed by the audit.
Full backend suite: 813 passed, 12 skipped, 3 failed - the same 3 as base 38ca61d (test_dev_isolation x2, test_env_isolation[demo_trades]). Frontend `tsc --noEmit` clean.
Scoring pins (test_scoring_unchanged, SHA vs 38ca61d): 8 earnings models + engines and 6 intraday models + engines all identical.

## Limits of the audit-sample check
The 99-event raw sample lives only in `outcome_sample.json` on the VPS. The scoreboard was checked against the audit's published aggregates (synthetic events built to those counts) and the real AMKR/BANR rows, not a replay of the raw JSON.

## Migration requirements
- New table `earnings_event_outcomes` (created on first grade run via CREATE TABLE IF NOT EXISTS; no changes to existing tables, no data backfill of model_predictions).
- First run grades from provider candles (<=120 symbols per cycle).
- No env change needed; do NOT set EARNINGS_LEGACY_SYNC.
- New cache key `modacc:scoreboard:v2`; Claude Pro scan cache now gz-compressed above 200 kB.

## Rollback
Redeploy 38ca61d files (service restart). The new table is harmless and may stay; nothing in model_predictions was rewritten, so there is nothing to restore. Legacy sync never ran, so no legacy column needs reverting.

## Deployment readiness: still NOT approved
Open: Phase 2B decision on `_historical_moves` (model inputs), share-class symbol mapping (WSO.B unresolved), NEUTRAL accuracy definition, Pre-Earnings Direction has no graded events in the sample, backfill provider budget, and explicit deployment/migration approval.
