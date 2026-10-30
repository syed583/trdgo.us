# Smart Move shadow model: local implementation report

Scope: local only, synthetic fixtures, throw-away PostgreSQL on 127.0.0.1. No provider request, no production database access, no VPS execution, no push.
Pre-registrations unchanged: baseline `9316348b…f77e`, score40 `b25bd8d7…eb87`. No existing earnings model touched.

## A. Components and test results

Code: `docs/smart_move/shadow_impl/smvshadow/` (about 1,370 lines), schema `schema/smv_schema_v1.sql`, tests `tests/`.
Result: **55 tests pass** (3 of them need the scratch PostgreSQL and were run against it); design-proof 5 pass; event-count v2 + v3 script tests 8 pass.

| # | Component | Module | Covered by |
|---|---|---|---|
| 1 | Calendar validation, event versioning (moved / duplicate / concurrent dates) | `calendar_validation.py` | test_calendar_selection_governor |
| 2 | Deterministic selection, earliest-deadline-first, overflow ledger | `selection.py` | same; plan hash identical for shuffled input |
| 3 | Shared request governor (default authorization 0, 1,700/day ceiling, 400/h shared, 300/h reserved, 2 s spacing, 1 retry, 429/401 stop, kill switch, core reservation) | `governor.py` | same |
| 4 | Append-only schema, immutable hashed payload store, idempotent writes | `store.py`, `smv_schema_v1.sql` | test_store_db (PG) |
| 5 | Adapters F3 reaction history, F5 price/technical, F7 volatility, F8 sector/market | `adapters.py`, `capture.py` | test_adapters_scorer_grader, e2e |
| 6 | Frozen 40-point scorer, both variants, hash-checked prereg | `scorer.py` | same |
| 7 | Outcome grader and audit report (no claim until 100 decided per variant, 8 earnings weeks, passed audit) | `grader.py` | same |
| 8 | Cutoff, freshness and point-in-time rules | `cutoff.py`, `freshness.py` | test_cutoff_freshness |
| 9 | End-to-end synthetic rehearsal | `runner.py` | test_e2e_rehearsal |

Required tests, mapped: leakage prevention (stamp later than capture raises `LeakageError`; nothing captured after cutoff); duplicate and moved events; BMO/AMC cutoffs; DST and holiday boundaries; missing and stale evidence (never counts, coverage drops, F3 missing caps the score at 25); request budget and priority (exact 4×4 + 2 shared + 2×13 requests in the rehearsal; zero authorization makes no request); append-only (UPDATE, DELETE, TRUNCATE refused); payload hash (tamper detected, corrupt file never overwritten); crash recovery (leftover temp files removed, rerun after a mid-run crash loses and duplicates nothing); continuation and contrarian share one prereg hash and are stored as separate rows; end-to-end run with idempotent replay in PostgreSQL.

## B. Commits
See the final line of the hand-off message (hashes are added after the commit).

## C. Event-count v3 (read-only, for review, NOT executed)
`scripts/event_count_universe_v3.py` (repo SHA-256 `0faf4398…d5` with trailing newline; the staged file has no trailing newline, SHA-256 `98d89e1798f978f3542067a9aa0adf027e749c9683a4cbae6cf427e3ab62c53a`, 25,521 bytes) and `PASTE_event_count_universe_v3.ps1` (hash-checked, never overwrites, executes nothing).
Same safety design as v2 (read-only session verified before any query, only SELECT/WITH/SHOW/SET, no provider request, output not overwritten, credentials never printed). Adds: staleness by days to earnings (0–3, 4–10, 11–21, over 21); BMO/AMC/unknown counts per day; a date-confidence **proxy** (HIGH/MEDIUM/LOW from our own snapshots; it is not the validator's CONFIRMED status); peak request demand at 220 core + 30 full with a 10 % retry margin and the 1,700 reference; moved, uncertain and projected future events. Tested against a synthetic scratch database; a read-statement audit and an unchanged-database check pass.

## D. Remaining data and provider gaps
- No verified release-timestamp source: the label time (06:00 / 16:00 ET) is a conservative assumption; the SEC acceptance time is only an upper bound.
- No point-in-time source found for flow, dark pool, estimates or sentiment (stamped only far back, or snapshot-only). Those payloads are captured raw for the future and never scored; factors F1, F2, F4, F6 stay outside the 40 points.
- The calendar has no per-source confirmation record; the validator needs two sources and a company-confirmed date, and the data source for that is not yet connected (the v3 proxy is a stand-in).
- UW is shared with another local application (3 concurrent); the 300/h reservation for the warmer is a provisional parameter awaiting a measured value.
- 160 past events have no outcome row (grading backlog); 379 future events were projected or unconfirmed in the last count.
- Sector ETF mapping is not defined for every symbol (missing means F8 sector part unavailable, never defaulted).
- Real provider payload shapes are verified for AAPL only; the adapters read documented fields and need a one-event live check after approval.
- `capture.py` pagination and error bodies are exercised only with the fake provider.

## E. Production readiness checklist (all OPEN)
1. Owner review of this code and the v3 script.
2. Owner approval and execution of event-count v3 on the VPS (read-only), then analysis.
3. Decision on the daily request authorization (default stays 0) and the warmer reservation.
4. Separate approval for the production schema (`smv`, migrations) after a dump and restore rehearsal.
5. Storage location, free space, backup and retention of the payload store.
6. Calendar source for confirmation and release time.
7. One-event live dry run with a supervised budget.
8. Recurring job approval, monitoring and the kill-switch procedure.
9. The earnings-fix release (669217f3) is independent and still awaits its own approval.

## F. Needs your next approval
- Review result of this report.
- Run v3 on the VPS (read-only, no provider call).
- Any provider request, production database change, migration, scheduled job, live capture or deployment (all remain NOT approved).
- Opening sealed validation or test data (remains NOT approved).
