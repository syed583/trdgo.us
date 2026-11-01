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
