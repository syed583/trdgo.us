# Phase 2A — Earnings prediction evaluation integrity (LOCAL ONLY, not deployed)

Branch `fix/earnings-eval-integrity` (from the production commit `38ca61d` + the earlier local Phase 1 / audit commits). Nothing pushed, deployed, restarted or written to production. No provider calls were made; no production data was read except through the already-approved read-only audit stages.

## 1. What changed

### Priority 1 — grading lifecycle (`backend/market_calendar.py`, `backend/earnings_outcomes.py`, `backend/model_prediction_service.py`)
| Rule | Implementation |
|---|---|
| Before Open | reaction session = earnings-day session (next trading day if the date is not one); base = previous trading day close |
| After Close | reaction session = next trading day after the report; base = report-day close |
| Gradable only when | the reaction session has closed (16:00 ET, or 13:00 ET on early-close days) + 45 min for the daily close to publish, **and** both closes exist |
| Calendar | NYSE holidays (incl. Good Friday, observed-day rules, Saturday New Year), early closes (day after Thanksgiving, Dec 24, Jul 3), special closures, weekends; clock arithmetic via `America/New_York` (daylight saving) |
| Price not available | `PENDING_PRICE`, retried hourly for 2 days, 6-hourly to 14 days, daily to 60 days, then weekly — never terminal |
| Reaction window still open | `PENDING_REACTION` (computed on the fly, nothing stored) |
| Timing label missing | `PENDING_TIMING` (never guessed) |
| Idempotent | outcome upsert never rewrites a `GRADED` row (`... WHERE status <> 'GRADED'`); re-running is safe |
| Historical records | `model_predictions` rows are not rewritten. Outcomes go to the new table `earnings_event_outcomes` (one row per symbol + earnings date). The legacy outcome columns are filled only for rows that were never evaluated; rows already stamped (including the premature "unverifiable" stamps) are left exactly as they were |

`grade()` keeps its name and return type, so `main.py` is unchanged.

### Priority 2 — scoreboard (`backend/earnings_scoreboard.py`, `backend/api_routes.py`, `frontend/src/pages/ModelAccuracyPage.tsx`)
- Eligible snapshots only: class `BEFORE_RELEASE` (provisional ET windows). `RETROSPECTIVE`, `AFTER_RELEASE`, `AMBIGUOUS` excluded.
- One prediction per stock / earnings event / model (last eligible snapshot; `first` available for lead-time comparison).
- BUY, SELL, NEUTRAL separate with sample sizes and 95% Wilson intervals; NEUTRAL is a no-trade call (count only); market-direction baseline per model (up / down / flat shares and the "always BUY" rate); no ranking is implied (best/worst removed).
- Per model: eligible, graded, pending, missing-outcome and excluded counts (excluded split into post-release-only, ambiguous-only, retrospective-only) and row counts by class.
- Labelled **provisional** with reasons (no stored release timestamps, no stored input evidence, one week of history).
- Original snapshots are only read. The old computation is kept as `scoreboard_legacy()` / `GET /api/model-accuracy/scoreboard-legacy`. The scoreboard cache key is now `modacc:scoreboard:v2` so an old-format cached answer cannot be served.

### Priority 3 — Claude Pro cache (`backend/db_cache.py`)
Cause (evidence from production): the 15- and 21-day Claude Pro scans are ~1.7 MB, above the unchanged 1,500,000-character persistence limit, and were dropped silently. Fix: values over 200 KB are stored as gzip+base64 behind a `gz1:` prefix (a 537-name Claude Pro-like scan: 1,680,967 bytes → 72,196 stored); smaller values and all existing rows stay plain JSON; the limit is **not** raised; anything still too large is logged (`db_cache: <key> not persisted ...`) and listed by `db_cache.dropped()`.

### Other
- `backend/database.py`: `earnings_event_outcomes` added to `ISOLATED_TABLES`, so non-production runs use a `_local` twin and can never write the real history.
- `backend/tools/phase2a_preview.py` (+ staging parts in `docs/handover_2026-10-28/chunks_p2a_preview/`): read-only production comparison tool (below).

## 2. Models unchanged
`backend/tests/test_scoring_unchanged.py` pins the SHA-256 of 30 scoring modules to commit `38ca61d`: the 8 earnings models (`claude_last_service`, `claude_pro_service`, `gemini_service`, `grok_service`, `chatgpt_service`, `meta_service`, `kimi_service`, `pre_earnings_direction_service`), their shared engine/equity/trade code, `tab_settings.py`, the 6 intraday model services, the intraday engine/indicator/signal modules, the directional model and signal verification. All match. The earlier signal-case suite (111 backend cases) also still passes.

## 3. Tests
| Suite | Result |
|---|---|
| New: `test_market_calendar.py` | 9 pass |
| New: `test_earnings_outcomes.py` (BMO/AMC timing, holidays, early close, DST, flat-band boundaries, missing price, retries, idempotency, legacy rows untouched, fetch failure, timing label arriving late) | 26 pass |
| New: `test_earnings_scoreboard.py` (classification windows incl. DST, one-per-event, last vs first, post-release/ambiguous/retrospective exclusion, BUY/SELL/NEUTRAL separation, pending vs missing, consensus, originals untouched, database layer) | 22 pass |
| New: `test_db_cache_compression.py`, `test_outcomes_isolation.py`, `test_phase2a_preview.py`, `test_scoring_unchanged.py` | 11 + 1 + 2 + 2 pass |
| Whole backend suite (new tree) | **743 passed**, 12 skipped, 3 failed |
| Whole backend suite (production base `38ca61d`) | 674 passed, 12 skipped, **the same 3 failed** (`test_dev_isolation` x2, `test_env_isolation[demo_trades]`: they need a dev `SESSION_SECRET` / a clean temp database; unrelated) |
| Frontend | typecheck and production build pass |
| PostgreSQL dialect check on the dev database (throwaway schema, dropped): upsert never rewrites GRADED, legacy sync, scoreboard, read-only guard | all pass (`pg_dialect_check.py`); it also caught and fixed a repeated-parameter typing error that SQLite hides |

## 4. Before / after evidence (read-only production numbers, provisional)
Source: INTEGRITY stage output of 2026-10-28 (earn_integrity.json). OLD = the app scoreboard (latest snapshot on/before the earnings date, any class). NEW = the corrected selection (last eligible pre-release snapshot) with the same stored outcomes.

| Model | OLD BUY | OLD SELL | OLD BUY+SELL | NEW BUY | NEW SELL | NEW BUY+SELL |
|---|---|---|---|---|---|---|
| Claude | 20/43 47% | 2/9 22% | 22/52 42% | 12/28 43% | 2/7 29% | 14/35 40% |
| Claude Pro | 29/64 45% | 0/5 | 29/69 42% | 20/50 40% | 0/3 | 20/53 38% |
| Gemini | 17/43 40% | 3/17 18% | 20/60 33% | 12/34 35% | 3/12 25% | 15/46 33% |
| Grok | 35/78 45% | 0/1 | 35/79 44% | 23/58 40% | 0/1 | 23/59 39% |
| ChatGPT | 29/64 45% | 0/2 | 29/66 44% | 19/47 40% | 0/2 | 19/49 39% |
| Meta | 24/52 46% | 0/6 | 24/58 41% | 17/39 44% | 1/5 20% | 18/44 41% |
| Kimi | 21/50 42% | 2/13 15% | 23/63 37% | 14/42 33% | 2/11 18% | 16/53 30% |
| Pre-Earnings Direction | 11/21 52% | 0/0 | 11/21 52% | 3/8 38% | 0/0 | 3/8 38% |

The old scoreboard graded 90 events per model, 43 (47.8%) of them from post-release snapshots. After the selection fix, 70 graded events remain and none are post-release; the old figures were inflated (Claude BUY 47% → 43%; Pre-Earnings Direction 52% → 38% on a sample of 8).
Grading lifecycle: every one of the 234 After Close events (and 46 for Pre-Earnings Direction) had been stamped before its reaction session closed; 230 of 234 ended permanently unverifiable. Under the new rules none can be stamped before the close and none is terminal.
**Not yet shown:** how many of those events the corrected lifecycle can actually grade (needs outcome prices). The read-only `phase2a_preview` tool does this on the production data using the calendar table's reaction measure as a provisional outcome source, and reproduces the OLD numbers above as a cross-check. It has been built and tested locally only.

## 5. Migration requirements (for the later deployment, none performed)
1. **New table** `earnings_event_outcomes` — created automatically (`CREATE TABLE IF NOT EXISTS`, additive) by the first grading run. No change to `model_predictions` or any existing table, and no existing row rewritten.
2. **First grading run** after deploy evaluates all past events lacking an outcome (about 400 events, one price-history request per symbol, at most 120 symbols per run, background priority), so full catch-up takes about four 12-hour cycles. Grading keeps using the same price provider as before.
3. **Cache:** new `modacc:scoreboard:v2` key (old key simply unused). `gz1:` cache rows appear for large values.
4. **Frontend:** rebuilt bundle (Phase 1 detail-page fix + scoreboard page).
5. Deployment itself (code copy, frontend build, service restart in the maintenance window) needs your explicit approval and the usual SHA-checked procedure.

## 6. Rollback plan
- Redeploy the previous code (`38ca61d` or the current production build) and the saved frontend ZIP. Database rollback is not required.
- `earnings_event_outcomes` can stay (old code ignores it) or be dropped later by explicit decision.
- `gz1:` cache rows: older code fails to decode them and treats them as cache misses (harmless; rebuilt on demand).
- Legacy outcome columns filled by the new `grade()` are valid values for rows that had never been evaluated; nothing already recorded was changed, so there is nothing to restore.
- The old scoreboard remains available as `scoreboard_legacy()` for comparison.

## 7. Known limits (not in Phase 2A)
- Snapshot capture itself is unchanged: post-release rows can still be written (they are now excluded from the scoreboard). Storing inputs, coverage, tier and a confirmed release timestamp is the Phase 2B snapshot design.
- Classification uses provisional BMO/AMC windows; every figure stays labelled provisional.
- The grading price source is the same provider history as before; its day alignment for After Close reports relies on the calendar label, which the preview tool cross-checks against the calendar table.
