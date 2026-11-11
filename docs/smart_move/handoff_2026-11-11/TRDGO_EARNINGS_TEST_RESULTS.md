# TRDGO.US Earnings / Smart Money Move — TEST RESULTS (2026-11-11, local, offline, no provider calls)

| Suite | Where | Result |
|---|---|---|
| Smart Money Move app suite (kit v6.3 tree) | `wt_sm\docs\smart_move\app\tests` | 656 passed, 2 failed (test_v2c_and_flow_backtest, test_v2r: `reportlab` not installed on this laptop — identical failures on the untouched production commit), 5 modules not collected for the same reason (test_audit, test_collection_plan, test_dq_audit, test_recovery, test_v2_interim) |
| New: hourly-cap pause | test_hourly_cap_pause.py | 16 passed |
| New: planner E01/E02 | test_p1_calendar_tiers.py | 17 passed (3 new) |
| New: runner safety | test_engine20.py | +2 passed (live-gate refusal of a persistent flag; AST pin that `kill_file.unlink()` sits inside the guarded block in both live profiles) |
| New: P0-02 defects D1/D2/D4 | test_p0_02_scanner_defects.py | 4 passed; publisher + variant suites 37 passed |
| New: end-to-end LIVE pass | test_e2e_live_main.py | 2 passed: `main()` -> live gates -> plan -> E20.run -> AttemptRecorder -> real InfraFetcher -> mocked `unusualwhales_service.get` -> store -> publisher (attempts = provider calls = status requests_attempted, 5 records, kill switch re-engaged); a governor DENIAL leaves no attempts line but a store-ledger line |
| Kit v6.4 installer rehearsal (isolated copies) | rehearse_kit_v6_4.py | 20 passed, 0 failed (zip sha c52ce7a7->cf6a581d, anchor 35a07fb3) |
| Kit v6.3 installer rehearsal (isolated copies) | rehearse_kit_v6_3.py | 20 passed, 0 failed (pre-check, install, post, rollback, interrupted install → recover, lock refusal, missing-kill refusal, wrapper install/rollback) |
| App backend suite (security-hardening branch) | `wt_a10\backend\tests` | 1,604 tests: 5 failures, all pre-existing/environmental (zlib-ng compression test; horizon_days date-dependent on a Saturday; 3 order-dependent that pass alone); 28 new hardening tests pass; tracker tests pass incl. 3 new EOD fast-close pins |
| release_check.py | both app trees | PASS |
| Frontend | typecheck, build | clean; `npm audit --omit=dev` 0; dev advisories fixed (vite 8.3.6) |
| pip-audit | backend/requirements.txt | no known vulnerabilities |

## VPS verification (owner-run, read-only)
- Restore to kit v6.1: 5 hashes = kit table; `v6_1_step4_run_live.ps1` gates all PASS.
- Post-restore: 10:05 DRY_RUN wrote status (planned 13, 0 requests); 11:20 LIVE pass: 100 attempts, 87 ok, 3 "failed" (hourly rail), 181 records published, calendar age 0.12 h, 22 upcoming.
- Kit v6.3: pre-check OK, install exit 0, post OK; `v6_3_step4` gates all PASS; plan-only run of `v6_3_live_scheduled.ps1`: every real gate PASS; timing refusal proved exit 3 + BLOCKED record; the next hourly DRY_RUN (12:05) wrote STARTED/FINISHED normally.
- Pending: 17:20 VPS LIVE pass under v6.3 (expect `halted: HOURLY_CAP`, `scan_ok: true`, `last_successful_scan_at` today); Thu 23:35 Monday-BMO pass.

## Not tested (and why)
- PowerShell scheduled-script exit codes (no harness; proven on the VPS by the plan-only run).
- Historical accuracy rebuild (E09/P1-07): needs the production ledger and outcomes; not available locally.
