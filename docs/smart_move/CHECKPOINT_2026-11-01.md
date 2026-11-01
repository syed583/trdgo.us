# Smart Move restart checkpoint — 2026-11-01

Branch `feature/smart-move` (worktree `...\scratchpad\wt_smart`), all commits local, nothing pushed or deployed. No live provider request was sent in this phase.

## Done (code complete, tested locally)
| # | Item | Files | Tests |
|---|---|---|---|
| 1 | Versioned event registry; board universe derived from it (122 = 122, APPS listed as removed, GRAB-type newly confirmed events added) | `backend/smart_move_event_registry.py`, `backend/smart_move_board.py` (`derive_registry`, `load_run_reports`, `registry=`), `backend/smart_move_board_routes.py` | `tests/test_event_registry.py` (7) |
| 2 | Liquidity diagnosis: all 42 ILLIQUID_OR_UNKNOWN are genuine 20-day avg dollar volume < $20M (0.1M–19.6M); the 22 NOT_TRADABLE are BMO events whose market-on-close entry session had already passed. No defect found, no rule changed | `docs/smart_move/app/liquidity_diagnosis.py` | (diagnostic, run on the real run folder) |
| 4 | Outcome pipeline (reaction session, 30 min safety delay, bars check, append-only corrections, budgeted fetch plan) | `backend/smart_move_outcomes.py` | `tests/test_outcomes.py` (6) |
| 5 | Accuracy breakdowns with Wilson intervals; simulation refused; no claim | `backend/smart_move_analytics.py` | `tests/test_analytics.py` (3) |
| 6 | Scheduler decision logic (tick every 30 min, DST-proof, closing-soonest, budgets, alerts, plan-only unless authorized) | `backend/smart_move_scheduler.py` | `tests/test_scheduler.py` (7) |
| 9 | Health checks, backup + SHA-256 verify, `/api/smart-move/health` | `backend/smart_move_health.py`, board routes | `tests/test_health.py` (7) |

Existing and reused: prediction history / versions / late flag / outcome import / frozen grader in `backend/smart_move_tracking.py`.

Full suite: `python -m pytest docs/smart_move/app/tests docs/smart_move/baseline -q` → 115 passed, 1 skipped.

## Remaining (not done)
- Wire `smart_move_outcomes`, `smart_move_analytics`, `smart_move_scheduler` into Tracking routes/pages and the Earnings Board UI (registry fields, `removed_events`, version column, filters by date/sector/evidence/eligibility, F3/F5/F7/F8 status, liquidity reason text, evidence-hash details). Frontend untouched this phase; `tsc`/`npm run build` not run.
- A runnable tick CLI (executes `tick()` actions through the existing collectors) plus Windows Task Scheduler XML and VPS timer files — NOT registered or activated.
- Persist the registry (`EventRegistry(path)`) from the collector instead of only deriving it in memory.
- Store adv20 and sector with predictions so analytics by liquidity/sector work on real data.
- Provider audit (documented limits, subscription status) needs the owner's dashboard; unverified today.
- Live end-to-end browser pass of Board + Tracking + Health.

## Next command
```
cd <wt_smart> ; python -m pytest docs/smart_move/app/tests docs/smart_move/baseline -q
```
then wire the Board UI to `removed_events`/`registry` fields in `frontend/src/pages/SmartMoveBoardPage.tsx`.

---
## Update (second phase) — everything below is done and committed locally
- Board UI: filters (date, sector, evidence completeness, status, timing, variant, search), event version column, removed/rescheduled events banner, evidence list with hashes in the detail row, operations panel (scheduler plan-only, kill switch, provider attempts/errors, health, alerts).
- Tracking UI: real stores discovered by default (`out/run_*/store`), deduped, scoped to the evaluated universe (122 events / 344 versions of 668 saved), analytics by sector/timing/coverage/liquidity/score with 95% intervals, outcomes count.
- Prediction records now carry `meta` (sector, kind, adv20, coverage, company) for NEW records; old records show liquidity as unknown, sector/kind joined from the saved calendar/run report only.
- `backend/smart_move_outcome_collect.py` (governor-bound `call`, mock-tested), `docs/smart_move/app/scheduler_tick.py` (plan-only default; execute needs `--execute` + `SMART_MOVE_LIVE_COLLECTION=1` + `LIVE_COLLECTION_ENABLED` file), `docs/smart_move/deploy/*` (Task Scheduler XML Enabled=false, systemd service/timer, not installed).
- `docs/smart_move/app/reconcile_board.py`: all 244 displayed statuses (122 x 2 variants) reconcile with the saved run report, 0 mismatches.
- Tests: `python -m pytest docs/smart_move/app/tests docs/smart_move/baseline -q` -> 125 passed, 1 skipped. `npx tsc -p tsconfig.app.json --noEmit` clean, `npm run build` OK (fixed a pre-existing `sub` prop type error in the tracking page).
- `backend/tests` has failures unrelated to Smart Move (dev DB guard / missing `tools/moves_replay.py` on this branch).
## Still open
- scheduler_tick execution path (full_universe_run seed arguments) never run live; needs a reviewed seed-store convention before enabling.
- Provider limits/renewal unverified; outcome collection never run live; no outcomes yet so no accuracy claim.

---
## Update (third phase): V2, audit, final QA — committed
- V2 (experimental, `backend/smart_move_v2*.py`): 10 factors, config hash, independent Continuation/Contrarian, never normalised; on the saved 122-stock run max evidence is 55/100 (60 needed) so every V2 result is NO TRADE / UNAVAILABLE. Data audit: `docs/smart_move/V2_DATA_AVAILABILITY.md`. V1 vs V2: INCONCLUSIVE (no outcomes).
- Audit: `backend/smart_move_audit.py` (JSON), `smart_move_audit_pdf.py` (two A4 pages, enforced), `smart_move_audit_run.py`, routes in `smart_move_v2_audit_routes.py` (`POST /api/smart-move/audits/generate`, `GET /audits`, `GET /audits/{name}.pdf|json`, `GET /v2`). Generated: `docs/smart_move/audits/audit_run_20261101_full_universe_20261101T101441Z.{pdf,json}` (copy; originals in `C:\Users\initi\SmartMoveRun\audits`). Verified backup: `C:\Users\initi\SmartMoveRun\backups\run_20261101_full_universe_20261101T095303Z`.
- Tick `--execute` path: seeds from the newest earlier run, resumes an interrupted folder, no-op when finished, exit 5 on provider stop, refused without all three switches; mock-tested.
- QA: Smart Move suite 143 passed/1 skipped; tsc + build clean; main-repo backend suite 1262 passed/4 skipped; worktree-only backend failures are environment (missing gitignored .env.dev, tests from another branch).
- Open: owner decisions listed in the V2 doc; collection of analyst/flow/short data (needs approval); outcome collection live; provider dashboard checks.

---
## Update (fourth phase): V2 rules, evidence pipeline, outcome->tracking, scheduler V2 actions
- `smart_move_v2.py` (v2-exp-2): explicit rules for analyst revisions, flow (aggressor side, context filters), implied move (risk only), short interest (change-based), synchronised relative strength, news (BLOCKED until sentiment semantics verified); 60-point minimum applies to DIRECTIONAL evidence only (max today 45 -> all 122 DATA INCOMPLETE).
- `smart_move_v2_collect.py`: governor-bound evidence pipeline (dedupe, validation, hashes, checkpoint, accounting, as-of reads); InfraFetcher gained 6 additive V2 kinds (not used by V1).
- `smart_move_outcome_collect.py` now also imports graded outcomes into the frozen grader's outcome file so Tracking/analytics update; predictions are never modified.
- Scheduler: tick plans COLLECT_V2 / FETCH_OUTCOMES / GENERATE_AUDIT; `run_service_actions` executes them through the service (kill switch re-engaged in finally); mock-tested end to end.
- `backend/requirements.txt`: reportlab==5.1.0, pypdf==6.21.4, pymupdf==2.0 (dev).
- Tests: Smart Move suite 164 passed / 1 skipped. tsc + build clean.
- Final audit: `docs/smart_move/audits/audit_run_20261101_full_universe_20261101T102934Z.{pdf,json}`.
- NEXT (needs owner authorization): (1) supervised V2 evidence collection for the 122 stocks (~610 requests) then weekly/10-day repeats to build revision and short-interest history; (2) one news sample to verify the sentiment field; (3) live outcome collection after reaction sessions; (4) dashboard confirmation of subscription/limits.
