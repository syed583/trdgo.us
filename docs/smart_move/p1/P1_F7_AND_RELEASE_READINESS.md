# SMART MONEY MOVE - P1 F7 investigation and release readiness - 2026-11-05

**Final verdict: CONDITIONAL.** F7 is real in the installed kit v4 (reproduced), was NOT observed in the Nov 6 batch (explained), is fixed locally behind an opt-in switch, and the kit v5 installer/rollback pass a local rehearsal on a copy of the real kit v4 bytes. It is not READY: the rehearsal was on temp copies, the release packages are not built, the VPS settings and raw Nov 6 store were not inspected, and several approvals are still needed (section 8).
Everything here is local development. No provider request, no LIVE run, no production change, no deployment, no push or merge, no service restart. Frozen scorer, thresholds, windows, ADV20 gate and ledger are unchanged (scorer 62e09aa0ed785e2a, smart_move_v2 de2d8f9612934338, grader 70078fb2996fc7a6, publisher ff3afb96b488c74d: all pinned and still passing). Limits stay 50 attempts per run, 100 per New York day.

## 1. Historical F7 findings (confirmed history vs synthetic estimates)
**Evidence I could use:** the raw Nov 6 ledger and evidence store are on the VPS only; I have no local copy and did not touch the VPS. What I could read without modifying anything are the owner-pasted VPS read-only run reports of the three supervised runs (Nov 4 23:16Z, Nov 4 23:23Z, Nov 5 09:38Z), which list per-run result counts, NO VALID reasons and missing-factor reasons.
| Question | Answer | Basis |
|---|---|---|
| Events that received `RETRIEVED_BEFORE_WINDOW` | **0 in all three reports** | CONFIRMED HISTORICAL (report text; only the tallies, not raw rows) |
| Caused by premature reuse of SPY / sector ETF | **0 observed** | CONFIRMED HISTORICAL. Reason: all 53 events' windows (BMO opened Nov 4 11:00Z, AMC Nov 4 21:00Z) had already opened before the first shared fetch (Nov 4 ~23:17Z), so the shared evidence postdated every window start |
| Wrongly classified NO VALID PREDICTION | **None attributable to F7.** Every NVP carries `ILLIQUID_OR_UNKNOWN` (2 + 12 + 14) or `INSUFFICIENT_COVERAGE` from `F3 INSUFFICIENT_HISTORY` (1 + 1) | CONFIRMED HISTORICAL |
| The 25 PENDING_REFRESH of run 1 | Evidence not collected yet (F3/F5 MISSING for those 25); all were collected in runs 2-3 | CONFIRMED HISTORICAL |
| Related but different shared-evidence gap | `F8 NO_SECTOR_MAPPING_OR_ETF_EVIDENCE` on 21 (run 1) and 14 (run 2) events, 0 in run 3: the sector ETFs are funded after the stocks, so a tight budget leaves them for the next run. Not F7; predictions stayed valid (coverage 27/40 >= 24) | CONFIRMED HISTORICAL |
| Does the defect exist in installed kit v4 | **Yes.** Reproduced on commit `274fccac` (bytes identical to kit v4 / collector v3): run 1 Sat 22:00Z serves Monday AMC; run 2 Sun 13:00Z serves Tuesday BMO; the planner funds only the stocks' own bars/history, never SPY or the sector ETF; the Tuesday events end `PENDING FINAL REFRESH: F5 RETRIEVED_BEFORE_WINDOW; F8 RETRIEVED_BEFORE_WINDOW`, and a third run sends nothing (it never heals) | REPRODUCED on the installed code with the real scorer, synthetic inputs |
| Other prediction windows | Structural: any window that opens after the shared evidence was fetched and before the next completed session. In the Nov 9-13 replay (synthetic) only Tue Nov 10 BMO and AMC were hit (cap+options: 7 + 11 events; options: 11 + 18); the other days heal through the session rollover after the 16:45 ET close, which is schedule- and budget-dependent | SYNTHETIC REPLAY ESTIMATE |
| Raw confirmation still available | `docs/smart_move/p1/f7_history_check.py` (read-only, reads `cal_obs.jsonl` + `evidence.jsonl` only, writes nothing) reports per event whether its own bars, own history, SPY and sector ETF were retrieved before the window. Tested to flag exactly the two Tuesday events without the fix and none with it. **Not run on the VPS store: that needs your action.** | tool ready |

## 2. Root cause
The frozen scorer (`LiveScorer.score_all`, helper `latest()`) discards any evidence retrieved before the event's own prediction window opened. The planner (`RefreshService.plan_evidence`) judged freshness by SESSION only (`rec_fresh`). Evidence fetched for an earlier window therefore looked fresh to the planner and stale to the scorer, so later-opening windows were never re-funded.

## 3. Exact local changes (commit on `feature/smart-move-p1`, local only)
* `backend/smart_move_live.py`: new `LiveConfig.window_aware_evidence` (default **False** = installed behaviour); `plan_evidence()` computes each event's window start, the latest window start over all open events for SPY, the latest per sector for each ETF, and (only when the option is on) also requests any record whose `retrieved_at` is earlier than that start (the same comparison the scorer makes). Valid cached evidence is still reused.
* `backend/smart_move_collection_runner.py`: `--window-aware-evidence`; implied by `--calendar-horizon-days`; `build_service(window_aware=...)`.
* `backend/smart_move_universe.py`: evidence status `BEFORE_WINDOW`; an event in that state is `DEFERRED` with the reason "retrieved before this event's prediction window opened and not re-requested yet", never `EVIDENCE_COMPLETE`.
* Budgeting: the extra requests are ordinary entries in the evidence plan (SPY first), funded inside the 50-attempt run budget and the 100/day cap before anything is sent; an unfunded request leaves the event `PENDING FINAL REFRESH` (not a prediction) - tested with budgets of 0 and 1.
* New: `docs/smart_move/p1/f7_history_check.py`, `docs/smart_move/p1/kit_v5/install_kit_v5.py`, tests `test_p1_f7_window_aware.py` (10), `test_p1_kit_v5_installer.py` (14), wrapper cases (10).

## 4. Before / after prediction coverage - Nov 9-13, same synthetic assumptions for both runs
Real-code replay (`docs/smart_move/p1/engine_replay.py`; stub provider; events from the probe counts; ADV20 by proxy tier: A, B, C = 100M USD pass, D = 10M USD fail the frozen 20M floor; calendar cost charged per run, not sent; weekend-heavy run schedule). "Before" = installed planner, "after" = `window_aware_evidence=True`. Valid = the frozen engine answered BUY / SELL / genuine NO TRADE.
| Universe | Tradable events | Served before / after | **Valid before -> after** | Deferred (never funded in window) | Pending only because of F7 |
|---|---|---|---|---|---|
| Cap >= $2B and options | 82 | 82 / 82 | **64 -> 82** | 0 / 0 | 18 -> 0 |
| Options listed | 119 | 119 / 119 | **90 -> 119** | 0 / 0 | 29 -> 0 |
| Price >= $15 | 141 | 137 / 137 | **102 -> 134** | 4 / 4 | 32 -> 0 |
| Broad (all US-listed) | 443 | 202 / 202 | **102 -> 134** | 241 / 241 | 32 -> 0 |
Not valid in the "after" column: broad = 241 never funded in their window (budget) + 65 below the liquidity floor (tier-D assumption) + 3 short history; price = 4 unfunded + 3 short history. These are model outputs, not forecasts.

## 5. Request-budget impact (same replay)
| Universe | Shared-evidence attempts before -> after | Total attempts before -> after | Attempts per NY day Sat/Sun/Mon/Tue/Wed | Max in one run |
|---|---|---|---|---|
| Cap + options | 36 -> 65 | 266 -> 295 | 84/25/71/81/5 -> 84/32/83/91/5 | 50 / 50 |
| Options | 28 -> 44 | 336 -> 352 | 100/45/92/94/5 -> 100/48/93/100/11 | 50 / 50 |
| Price floor | 33 -> 48 | 383 -> 392 | 100/53/100/100/30 -> 100/55/100/100/37 | 50 / 50 |
| Broad | 23 -> 23 | 493 -> 493 | 100/100/100/100/93 both | 50 / 50 |
Cost of the fix in the three filtered universes: shared-evidence attempts +29, +16, +15; total attempts over the week +29, +16, +9 (+11 %, +5 %, +2 %). In the broad universe the budget is saturated on four days either way, so the same attempts are simply spent at the right time. No run exceeded 50 attempts and no day exceeded 100.

## 6. Complete regression results (final code)
* Private app suite **646 passed** (was 567 before P1); shadow_impl 104 passed / 5 skipped; scripts 3 / 8 skipped; baseline 14 / 1 skipped; design_proof 4 / 1 skipped.
* Public: UI tests 39 passed, typecheck clean, build OK; backend **830 passed / 12 skipped / 2 failed** - the same 2 environmental failures (`test_dev_isolation`, no `SESSION_SECRET` in `.env.dev`) also fail on the installed A.7 tree.
* Collector wrapper: the installed v3 wrapper's own test script **38/38**; the P1 wrapper with the same test content plus 10 P1 cases **48/48** (default path still passes `--calendar-days-ahead 3` and no new flag; horizon 30 passes `--calendar-horizon-days 30`; 1, 2, 31, 99, -5 refused; settings-file keys honoured; 150/700 ceilings unchanged). One transient failure of the old script appeared once under heavy CPU load and did not repeat.
* Default behaviour check: with every new option off, the installed code and the new code produce identical plans and selection hashes; the window-aware switch is off by default, so a kit v5 install changes nothing until it is enabled.
* Scorer and frozen files: `git diff` against the installed base on `docs/smart_move/shadow_impl`, `smart_move_v2.py` and the ledger publisher is empty.

## 7. Installer and rollback rehearsal (LOCAL, temp folders, never a real application folder)
* Manifest `KIT_V5_MANIFEST.json` regenerated for the final bytes: 3 CHANGED files with before/after SHA-256 (runner `4e67bd9bb6d8` -> `d4719d8530fe`, engine20 `2a907f41371b` -> `c6fab3f4c2e8`, live `e114d57d40a4` -> `e2e3d54f3c9e`), 3 NEW (tiers `c533ced46da5`, changes `f7b6bda580e6`, universe `078e5c69fbca`), 4 protected files pinned.
* Installer design: pre-check (complete payload, exact v4 base, protected files) -> byte-verified backup + journal -> NEW modules first, runner last -> per-file atomic replace (temp file, hash, `os.replace`) -> post-verify. Any failure restores and verifies v4 (exit 4); exit 5 if a restore does not verify.
* Rehearsal results (14 tests, all pass): clean install replaces exactly six files and leaves everything else byte-identical; **forced failure after 1, 2, 3, 4, 5 and 6 files each restores the exact v4 bytes**; an injected disk error on the fifth file does the same; a hard process kill after four files leaves a state that verification reports as PARTIAL (exit 3), a second install is refused, `recover` restores v4 exactly and a clean install then works; **rollback v5 -> v4 is byte-exact** and is refused if an installed file was edited after the install; refusals touch nothing (unknown base, missing or tampered payload file, installing twice, lock held); the six installed files run the real runner (old defaults, `--calendar-horizon-days 30 --select-policy deadline_priority`, `--window-aware-evidence`) with 0 provider requests.
* Same procedure on a temp copy of the REAL kit v4 + collector v3 payload bytes: install OK, post-verify OK, rollback restored directories byte-identical (`diff -rq` clean), forced failure after 4 files restored byte-identical.
* Wrapper compatibility: see section 6 (48/48). Dashboard compatibility: a collector folder that also holds `universe.json`, `calendar_slots.json` and `event_changes.jsonl` gives the same live-scanner view as one without them; the universe tab on a folder from the installed collector says "not written yet"; UI tests render all statuses.
* **Rollback readiness is now demonstrated only for the local rehearsal.** It is not yet demonstrated for the VPS procedure (see blockers 2 and 3).

## 8. Remaining blockers
1. Decide whether to enable window-aware evidence: it is a planner change (not scorer/threshold/window) that adds a measured +2 % to +11 % of attempts in filtered universes and was verified only against the stub provider. It stays OFF unless `--window-aware-evidence` or `--calendar-horizon-days` is used.
2. The installer is Python; the earlier installers were PowerShell with their own backup and anchor discipline. It needs (a) your approval of the approach, (b) to respect the collector's `collection.lock` and the runner kill switch (it does not take them yet - install only when no run is in progress), and (c) a rehearsal that follows the exact VPS paths and permissions.
3. Release packages not built: kit v5 zip + anchor, and the dashboard release (A.8: `smart_live.py`, `routes.py`, new `frontend_dist`, manifest) with its own preflight / update / rollback rehearsal in the A.7 runner style.
4. Not inspected: the VPS settings file and task registration (`v3_step2_readonly.ps1` output) and the raw Nov 6 ledger/evidence (`f7_history_check.py` on the VPS store). Neither is required to deploy, both would turn assumptions into facts.
5. Approvals still required: pushing the public dashboard branch; kit v5 installation; wrapper; dashboard release; enabling F7 and the horizon/policy settings; every LIVE run (with the safety gate and attestation); any limit change (none proposed).
6. Known limits: capacity numbers rest on synthetic events and an assumed liquidity split; real provider payload shapes for the new calendar slots were not exercised; two public `test_dev_isolation` failures are environmental.

## 9. Final verdict: CONDITIONAL
Fixed and verified locally: F1, F2, F4, F7 (opt-in). Rehearsed locally: kit v5 install, forced and crash failures, recovery, rollback. Not done: release packaging, VPS-shaped rehearsal, the approvals above. I am stopping here and waiting for your explicit approval before any action outside local development.
