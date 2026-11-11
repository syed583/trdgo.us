# TRDGO.US Earnings / Smart Money Move — CHANGELOG (2026-11-11)

## A. Previous ChatGPT-guided changes (Nov 10, on the VPS, not in git) — VERIFIED, NOT KEPT
| File (VPS) | Backup | Diff confirmed | Verdict |
|---|---|---|---|
| `backend\smart_move_calendar_tiers.py` | `.before-small-budget-fix.20261110_161317.bak`, `.before-t0-priority-fix.20261110_161527.bak` | Yes (Compare-Object pasted by owner): the "small-budget exception" and "t0_all_funded" snippets exactly as in the handoff | Correct idea, wrong vehicle: editing a protected file breaks the hash gate (scanner stopped). Re-implemented as E01/E02 in git with tests; the Nov 10 copy is preserved, not used |
| `collector_tools\run_engine20_collection.ps1` | `.before-f7.bak`, `.before-f7-parser-fix.bak`, `.before-killfix.20261110_152939.bak`, `.before-killpathfix.20261110_153737.bak` | Yes: settings parser regex widened to accept `WithF7`; kill path moved from `sm_collector_state\KILL` to `sm_evidence_store\KILL` (the RUNNER kill, present by design); LIVE now *requires* that file, DRY_RUN skips when it exists | The kill-path change inverted the design and stopped every hourly DRY_RUN; the F7 parser change is harmless. Wrapper restored to v2 (3fc8a069). F7-via-settings can be re-added properly if the owner enables F7 |
| `backend\smart_move_collection_runner.py`, `smart_move_engine20.py`, `smart_move_live.py` | NONE on the VPS | Content preserved in `C:\backups\sm_edits_20261110`; diff vs kit v6.1 requested from the owner (`DIFFS_vs_kit_v6_1.txt`), not yet received | Restored to kit v6.1 bytes. Unknown intent; to be reviewed when the diff arrives |
| `backend\main.py`, `backend\.env`, `smart_move_collection_runner.py` (claimed "inspected only"), `collector.settings.txt` | — | `collector.settings.txt` LastWriteTime 11/10 08:48 (owner to confirm the content; gates say Mode=DRY_RUN, 150/700, P1 on → PASS) | No evidence of other changes |
| Pilot `-Authorize 5 -DailyLimit 5` at 15:00Z | — | live_status.json showed limits 5/5, used 10, 0 predictions | Explained (P0-03); such budgets are now refused by the scheduled scripts |

## B. New Claude changes (all LOCAL in git; only kit v6.3 + scripts were installed, with the owner's OK)
Branch `feature/sm-handoff-nov10` (worktree `wt_sm`, parent = production `d47a2a3f`):
| Commit | Files | Change | Tests |
|---|---|---|---|
| `b92b9f54` (cherry-pick 2c33a00c) | smart_move_engine20.py, smart_move_collection_runner.py, smart_move_monday_bmo.py | v6.2: own hourly rail denial = `halted="HOURLY_CAP"` pause, `scan_ok_of()` | test_hourly_cap_pause.py (16) |
| `37327023` | smart_move_calendar_tiers.py | E01 floor for urgent slots within the hard ceiling (T1 keeps the 30 % share); E02 T1 funded only when every T0 slot is | test_p1_calendar_tiers.py +3 |
| `eb8ff75f` | runner, monday_bmo | kill-switch `unlink()` moved inside the try/finally; `persistent_live_flag()` refuses a User/Machine-scope live flag | test_engine20.py +2 |
| `a2a0ccfe`, README fix | docs/smart_move/p1/kit_v6_3/* | manifest/installer/rehearsal/build tooling for 4 changed files | rehearsal 20/20 |
| `3c9811e8`, `6893873f` | smart_move_live_publisher.py, smart_move_monday_bmo.py | D1 stale calendar rows counted as "calendar observation stale"; D2 PENDING_REFRESH-only events get WINDOW_MISSED at close (reason ONLY_PENDING_REFRESH_BEFORE_CLOSE / …_FRIDAY_DEADLINE); D4 status `schedule` = {every_minutes 360 LIVE / 60 DRY_RUN, live_every_minutes 360} | test_p0_02_scanner_defects.py (4) |
Scripts (outside git, `release_candidates\sm_v6_3\`): `v6_3_live_scheduled.ps1`, `v6_3_monday_bmo_scheduled.ps1` (Authorize/DailyLimit mandatory and pinned 150/700 unless `-PilotApproved YES-PILOT-<a>-<d>`; the task's own arguments checked; refusal → exit 3 + BLOCKED record in `collection_runs.jsonl` + `last_gate_refusal.json`; plan-only exit 5; wrapper skip exit 9; kit table re-pinned), `v6_3_step4_run_live.ps1` (table only).
NOTE: commits `3c9811e8`/`6893873f` (D1/D2/D4) are NOT in the installed kit v6.3; they are kit v6.4 candidates.

Branch `feature/security-hardening` (app side, from deployed A.10 `923b480c`): `f5aa95cb` security fixes (20-item audit), `889e1332` tracker 15:50 fast close. Not deployed.

## C. Existing functionality inspected and preserved
Frozen scorer (sha 62e09aa0…, untouched), event windows (release−48h..−24h), two-vendor calendar confirmation, governor
(150/700, 100/h live-hours rail, 429/401 day stop), ledger hash chain and dedup, universe/change tracking, Monday-BMO
Friday variant, shared-key attestation gate, collector lock, wrapper v2, kill switches (runner present-by-design,
wrapper absent-by-design), dashboard reader, A.10 intraday tracker rules.

## D. Intentionally removed / superseded — NOT restored
See TRDGO_EARNINGS_REMOVED_FEATURES.md.

## E. Remaining blockers
See TRDGO_EARNINGS_PENDING_APPROVALS.md.
