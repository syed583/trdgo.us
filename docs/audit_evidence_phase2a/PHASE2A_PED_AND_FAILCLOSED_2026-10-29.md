# Fail-closed candles + PED capture fix - local verification (2026-10-29)

Local only. Not pushed, deployed or migrated; no production records touched. Scoring formulas and `_historical_moves` untouched.

## Replay result that led here (production, read-only)
All eight models cross-checked IDENTICAL on the 99 verified events; PED had 407 rows, all RETROSPECTIVE (0 eligible).

## Root cause of PED having no verifiable history
`main.py` runs `mp.backfill_ped()` before `mp.capture()` every cycle. `backfill_ped` copied the daily `ped_scores` log (date only) into
`model_predictions` stamped `snapshot_date::timestamptz` (midnight UTC, NULL timing). Because the unique key is
(model, symbol, earnings_date, snapshot_date) and capture uses `ON CONFLICT DO NOTHING`, the midnight copy of TODAY's call always won and the
genuine, timed capture was discarded.

## Changes
| File | Change |
|---|---|
| backend/earnings_outcomes.py | `regular_session_closes`: only an explicit market_time 'r' row is a close; unlabelled / pr / po-only dates have none -> PENDING_PRICE, retried. (Removes the highest-volume guess, which on real AMKR rows picks the post-market price.) |
| backend/model_prediction_service.py | `capture()` stores an explicit genuine UTC `predicted_at` (`capture_timestamp()`, same instant for every model, nudged 1 microsecond if it were exactly midnight UTC) with report timing and earnings date. `backfill_ped()` copies only snapshot dates BEFORE today, still INSERT ... DO NOTHING; its rows remain date-only (RETROSPECTIVE). |
| backend/earnings_scoreboard.py | `history_note` on a model whose stored history is retrospective only (no scoring change). |
| frontend/src/pages/ModelAccuracyPage.tsx | Shows "history not verifiable" beside such a model. |
| tests | test_ped_capture.py (15), test_unlabelled_rows_fail_closed.py (3), test_regular_session_closes.py (updated), docs/audit_evidence_phase2a/pg_ped_check.py (PostgreSQL, dev DB, throwaway schema). |

## Behaviour
- New PED rows: real capture time + timing; eligible only if captured on an earlier ET date than the earnings date, or on the day before the provisional release window (BMO before 06:00 ET, AMC before 16:00 ET). Same-day capture with unknown timing, or inside 06:00-09:30 ET for BMO, is AMBIGUOUS and excluded.
- Old PED history: unchanged, RETROSPECTIVE, unverifiable. Nothing rewritten, no timestamps manufactured.
- Capture never overwrites an existing row. A row for TODAY already written by the old backfill (midnight stamp) before deployment stays retrospective; the first genuinely timed PED rows are tomorrow's.
- PED needs a few days of new captures before it has any graded events (each needs its own reaction session to close).

## Tests
Backend suite: 838 passed, 12 skipped, 3 failed (same 3 as base 38ca61d; not production risks). Frontend tsc clean. 30 scoring SHA pins pass (8 earnings + 6 intraday models unchanged). pg_ped_check.py: ALL PASS.

## Rollback
Redeploy the previous build; `earnings_event_outcomes` may stay. New PED rows with real timestamps are valid data and may stay. No migration, no schema change in this step.
