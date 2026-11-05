# SMART MONEY MOVE - P1 fix verification (F1, F2, F4; F3 preparation) - 2026-11-05

**Final status: CONDITIONAL.** F1, F2 and F4 are fixed and verified locally. F3 is prepared (manifest + fail-closed verifier) but NOT rehearsed, so rollback readiness is NOT claimed. A new material finding (F7, section 6) needs your decision before any deployment.
Everything below is local development. No provider request, no LIVE run, no production change, no deployment, push, merge or restart. The frozen scorer, thresholds, windows, ADV20 gate and ledger are unchanged. Commits (local only): private `feature/smart-move-p1` `0d1d7af8`; public `feature/smart-universe-p1` `01830fdb`.

## 1. Files and functions modified
Private (`backend/`):
* `smart_move_engine20.py`: new `tradability(row)` (F1); `select_top` now calls it for `deadline_priority`; new `attempts_of(svc)`; `run()` hard per-run cap in attempts, returns `attempts` (F4).
* `smart_move_collection_runner.py`: `tiered_refresh()` sizes the calendar budget for the worst case of retries and reports `attempts`; `engine20_profile()` subtracts calendar ATTEMPTS (not only successes) from the evidence budget in BOTH the tiered and the legacy path and prints attempts as `requests_attempted` (F4).
* `smart_move_universe.py`: `build()` statuses NOT_TRADABLE, ELIGIBILITY_UNKNOWN, rows for events whose vendors disagree on the date; new fields `tradable`, `status_note`, `date_confirmed` (F2).
Public (`backend/earnings_monitor/smart_live.py` `load_universe` keeps the new fields; `frontend/src/pages/SmartMoveEngine20Page.tsx` `UniverseView` + status tables and filters) (F2).
New: `docs/smart_move/p1/engine_replay.py` (engine-in-the-loop replay), `docs/smart_move/p1/kit_v5/kit_v5_manifest.py` + `KIT_V5_MANIFEST.json` (F3 preparation), tests `test_p1_fix_f1_f2_f4.py` (12), `test_p1_kit_v5_manifest.py` (7); public `test_smart_universe.py` (updated fixture from the real builder) and one new UI test.
Not touched: `docs/smart_move/shadow_impl` (scorer, cutoff, grader), `smart_move_v2.py`, `smart_move_live_publisher.py` (ledger writer). `git diff 274fccac..HEAD` on those paths is empty.

## 2. Root cause and correction
* **F1 (blocking).** Root cause: the selection rule read `plan["tradable"]`, but rows from the real `LiveScorer.score_all` carry a plan with only four keys, so the rule never fired in production (my earlier tests used hand-built rows). Correction: `tradability(row)` recomputes tradability from the event's own date and session with the SAME function the scorer uses (`cutoff.plan_event`). It returns `True`/`False` only when it can be established, and `None` (never skipped) if the session is not BMO/AMC, the date is missing or unreadable, or the recomputed window differs from the row's own window. Only `False` skips.
* **F2.** Root cause: the universe knew only "vendor-eligible" and called everything unfunded DEFERRED. Correction: five distinct meanings: PREDICTED / EVIDENCE_COMPLETE (served), QUEUED_THIS_RUN, DEFERRED (eligible, budget), NOT_TRADABLE (frozen rule, no evidence requested), ELIGIBILITY_UNKNOWN (session unknown, vendors disagree on date or tradability not computable; such events are now listed with the dates seen instead of silently dropped). The dashboard shows the same labels with explanations and filters.
* **F4.** Root cause: the run ceiling counted tasks, not provider attempts, and calendar attempts that failed (or were retried) were not charged to the evidence budget. Correction: the governor's attempt counter is the measure everywhere; a task whose worst case (1 + retries) would pass the ceiling is not started; calendar requests are budgeted for the worst case and their ATTEMPTS are subtracted. The 100-per-New-York-day cap is still enforced by the governor (tests).

## 3. Before / after request budget (same scenarios, stub provider, old = commit `ce972f40`, new = `0d1d7af8`)
| Scenario | Ceiling | Provider attempts before | after |
|---|---|---|---|
| Every request fails, 2 retries each | 10 | **30** | 9 |
| 5 failing symbols, 1 retry | 25 | **35** | 24 |
| Calendar + evidence, 1 retry, 2 failing symbols | 50 | **54** | 49 |
| Calendar failing (2 retries) + evidence | 50 | **61** | 48 |
| 95 attempts already used today (100 cap) | 50 | 5 | 5 |
With retries off (the engine20 profile's setting) attempts equal tasks and behaviour is unchanged. Old default behaviour check: the same dry run of the installed commit `274fccac` and the new code (`cap` and `deadline`, `--calendar-days-ahead 3`) produce identical plan and selection hashes (`b5081116058a3f15`, `818c4f4e08b4b620`, `e275d834f56ec576`).

## 4. Test results
* Tripwire: the 12 new F1/F2/F4 tests: **11 fail on the old code, all 12 pass on the new**.
* F1 integration tests use the real scorer: rows carry no `tradable` flag; the rule agrees with the scorer's own NOT_TRADABLE verdict for 40 events (exactly 5 gated: the four Monday BMO events and Fri Nov 27 BMO); prediction outcomes are identical with and without the skip (NO VALID PREDICTION for the untradable event, identical scores for the tradable one); uncertain inputs are never skipped.
* Private suites: app **615 passed** (567 baseline + 36 P1 + 12 fix tests; includes the 7 kit-manifest tests); shadow_impl 104 passed / 5 skipped; scripts 3 / 8 skipped; baseline 14 / 1 skipped; design_proof 4 / 1 skipped.
* Public: frontend UI tests 39 passed, typecheck clean, production build OK; backend (full) 829 passed / 12 skipped / 2 failed; the SAME 2 fail on the installed A.7 tree (825 passed / 12 / 2): `test_dev_isolation` needs a `SESSION_SECRET` in `.env.dev` - environmental, unrelated. (pytest printed no final summary line; counts are taken from its progress dots.)
* Scorer pins unchanged: `scorer.py` 62e09aa0ed785e2a, `smart_move_v2.py` de2d8f9612934338, `grader.py` 70078fb2996fc7a6, publisher ff3afb96b488c74d.

## 5. Updated capacity, Nov 9-13 (engine-in-the-loop replay of the REAL code path; weekend-heavy schedule; 50 per run / 100 per NY day)
Method: `docs/smart_move/p1/engine_replay.py`. Synthetic: events (probe counts), flat-wave market data, ADV20 assumed by proxy tier (A, B, C = 100M USD, D = 10M USD, against the frozen 20M floor), calendar cost charged per run (not sent). **Served** = F3 and F5 collected inside the event's own window. **Valid** = the frozen engine answered BUY / SELL / genuine NO TRADE. Valid counts depend on my ADV assumption; they are a structural estimate, not a forecast. Every run stayed at or under 50 attempts and every day at or under 100.
| Universe | Tradable events | Served | Valid (as built) | Valid if shared evidence is refreshed per window (F7 variant) |
|---|---|---|---|---|
| Broad: all US-listed | 443 | **202** (46 %) | **102** (23 %) | 134 (30 %) |
| Price floor >= $15 | 141 | 137 (97 %) | 102 (72 %) | 134 (95 %) |
| Options listed | 119 | 119 (100 %) | 90 (76 %) | 119 (100 %) |
| Options and cap >= $2B | 82 | 82 (100 %) | 64 (78 %) | 82 (100 %) |
Broad, why not valid (as built): 241 never funded inside their window (budget), 65 served but below the liquidity floor (tier D assumption), 32 served but pending because shared evidence predates their window (F7), 3 other history gaps.
**Changes from the earlier 190 and 173 broad figures.** 190 came from the arithmetic model assuming the untradable skip worked and a flat 12 shared requests per session; 173 was the same model with the skip a no-op. The replay (skip now really working) serves 202 because the real planner reuses SPY / sector-ETF evidence and charges fewer shared requests than the flat model. The two numbers are not like for like: 190 and 173 were "served by arithmetic", 202 is "served by the real engine"; the real news is the valid column (102), which no earlier figure showed. Plain `deadline` order would again spend requests on untradable events (17 / 24 / 25 events funded for nothing in the filtered universes); I did not finish the broad plain-deadline replay (omitted, informational).

## 6. New finding F7 (material, needs your decision; NOT fixed - outside the approved scope)
The frozen scorer rejects evidence retrieved BEFORE an event's window opened (`RETRIEVED_BEFORE_WINDOW`). The evidence planner treats shared SPY / sector-ETF bars as fresh by SESSION only, so after the first run of a weekend it never re-requests them for events whose windows open later (Tue BMO opens Sun 06:00 ET, after a Saturday-night SPY fetch). Those events end as PENDING FINAL REFRESH / NO VALID although their own bars and history were collected. In the replay this hits 18 of 82 (cap+options), 29 of 119 (options), 32 of 141 (price floor) and 32 of 443 (broad) served events. The variant that re-requests shared evidence when it predates the latest open window turns all of them valid at about 29 extra requests for the week (cap+options universe). It is a planner change (not a scorer, threshold or window change) and exists in the installed v4, so it may already have affected real predictions: a read-only count of `RETRIEVED_BEFORE_WINDOW` in the Nov 6 ledger/evidence would show it. I recommend that read-only check first, then a decision.

## 7. F3 preparation (nothing installed)
`docs/smart_move/p1/kit_v5/KIT_V5_MANIFEST.json` lists exactly six files (3 CHANGED with before and after SHA-256, 3 NEW) and pins four protected files. `kit_v5_manifest.py verify --phase pre|post` is all-or-nothing and fail-closed: missing, extra, tampered, unknown-base, conflicting or protected-changed files exit 2; a half-installed state exits 3 (PARTIAL: restore the backup). Checked against the REAL kit v4 + collector v3 payload bytes in a temp copy: pre-check OK; with one payload file removed: refused. The three installed base hashes match (runner 4e67bd9bb6d8, engine20 2a907f41371b, live e114d57d40a4). **There is no installer and no rollback rehearsal yet, so rollback readiness is NOT claimed.**

## 8. Remaining deployment blockers
1. F7 decision (read-only Nov 6 check first, then approve or reject the planner change; if approved it needs its own tests).
2. Build the kit v5 installer (copy, backup, automatic restore using the verifier) and pass a local install + forced-failure + rollback rehearsal on a copy.
3. Rehearse the new wrapper (`-CalendarHorizonDays`, `deadline_priority`) and the dashboard release (A.8) build and rollback.
4. Read-only confirmation of the live VPS settings (`v3_step2_readonly.ps1` output).
5. Approvals still required: pushing the public branch, kit v5, wrapper, dashboard release, policy choice, any LIVE run, any limit change. Limits stay 50 / 100.
6. Note: the two public `test_dev_isolation` failures are pre-existing and environmental.

## 9. Final status: CONDITIONAL
Fixed and verified: F1, F2, F4. Prepared only: F3. Open: F7 and the installer/rehearsal items. I am stopping here and waiting for your explicit approval.
