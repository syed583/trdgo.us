# Earnings Monitor cache-only release: readiness report

Base (deployed): `553d2a18003162c85f3fb4e52955ac0cdd299b3e` (Scanner Health). Branch: `release/earnings-intelligence-cache-only`. **Not pushed; awaiting RELEASE GO.**
Package manifest SHA-256 (anchor for `-ManifestSha256`): `e543414d9779402f125f57b339e20057d74974dec4ee63dd93b179777d93c24d` (133 files).

## What it is
The Earnings Monitor update only: 30-day calendar (15-day model window), evidence ledger, prediction versions with reasons, look-ahead-safe grading, new alert rules, two scheduler lanes, budget planning (planning only), the dashboard views and a read-only cache probe. It ships **CACHE_ONLY**: the production approval stays `cache_only`, so it makes no provider request.
It does **not** contain: any change to `unusualwhales_service.py` or any `uw_*` module (separate release), the Smart Money Move engine installer or kit (separate approvals), the WebSocket ingest, `main.py`, or any model/scoring file.

## Changed files (36, plus the rebuilt frontend assets)
backend/earnings_monitor/{adapters,alerts,config,evidence(new),gate,logic,routes,scheduler,service,sim,smart_move,store}.py; backend/tests/{test_cache_compat(new),test_earnings_intelligence(new),test_earnings_monitor,test_earnings_monitor_complete,test_scanner_health}.py; frontend/src/{api/client.ts,pages/EarningsMonitorPanel.tsx}; frontend/tests/{earnings_monitor.test.tsx,mocks/client.ts}; deploy/earnings_monitor/{MANIFEST.sha256,verify_after_deploy.ps1, backend/earnings_monitor/*.py, frontend_dist/*}; docs/earnings_monitor/{INTELLIGENCE_UPDATE.md,RELEASE_READINESS.md}.

## PASS / FAIL
| Check | Result |
|---|---|
| Diff inside the cache-only allowlist; `unusualwhales_service.py`, `uw_*`, `main.py`, scoring and Smart Move engine files untouched | PASS (scripted audit) |
| No package module imports a provider at import time; every call goes through ProviderAccess | PASS |
| New provider call site vs deployed | one: `event_refresh`, reachable only in REFRESH mode with the 'event' purpose approved **and** `plan_driven_refresh=true` (default false). Inert in CACHE_ONLY. |
| Collection defaults inert (plan-driven refresh off, Finnhub verification 0, cap 1000) | PASS |
| Backend tests | 772 pass, 14 skipped; 1 old failure (`test_env_isolation[demo_trades]`, also fails on 38ca61d) |
| Frontend: tsc, 22 UI tests, production build | PASS |
| Schema 2 -> 3 on a store written by the DEPLOYED code (60 events, 240 predictions, 302 changes): all rows kept, 5 jobs ran without failure, no new predictions on an unchanged cache | PASS |
| Rollback: the previous (deployed) code opens and runs on the migrated store; Update rollback (manual and automatic) restores files exactly | PASS |
| Update refusals (wrong anchor, unexpected main.py, monitor not installed) change nothing | PASS |
| 29-day simulation with a crash, no provider call, no duplicate or stuck job | PASS |
| Nine model sources, Options Flow, Engine20 route | health checks in the Update script (rehearsed against a stand-in) and the full application test suite pass; **not proven against the live server until the post-deployment check below** |
| Cache probe against real production cache samples | **NOT VERIFIED**: no read access to the live caches (see risk) |
| Provider policy | matches the confirmed policy: no daily or per-minute limit; 3 concurrent REST requests across all machines; the 1,000 figure is the monitor's own configurable guard (`hard_daily_cap` in em_data\config.json), default unchanged |


## Correction since the first candidate: Smart Money Move on November 3 (root cause and fix)
**Finding (from the real Engine20 store and report):** the store holds 295 events dated 2026-11-03 and 524 dated 2026-11-04. At the Engine20 run (2026-11-03 08:29:08Z) the frozen engine marked every Nov 3 event `CLOSED` (the report was already inside its final 24 hours; predictions are allowed only 48 to 24 hours before) and every Nov 4 event `OPEN`. The authorised run collected evidence for, and scored, only 20 Nov 4 events (BUY 4, SELL 1, NO TRADE 13, NO VALID PREDICTION 2). So **no Nov 3 prediction was ever generated, and none could have been** under the frozen rules; the snapshot page lists exactly those 20 and has no date filter (it prints dates verbatim; times are labelled UTC). Nothing is hidden by a front-end filter and no timezone conversion shifts a date. Nothing was reused from Nov 4.
**Defect found while checking date mapping:** the monitor matched a Smart Money Move (and any model) result to an event by **symbol only**. A result for one report date could be attached to the same symbol's event on another date. Fixed: every result carries its report date and is attached only when it equals the event's date (a mismatch counts as no call and is counted, never reused).
**Display fix:** the Smart Money Move view and the nine-model grid group by market date and show `NO PREDICTION AVAILABLE` with the real reason (window closed before the run / not selected or not collected under the run / not yet in the window / no engine installed) and the origin `HISTORICAL SNAPSHOT`, `LIVE ENGINE` or `NOT GENERATED`. The engine's own "no valid prediction" is kept as its own result with its own reason.
**Replay with the real data** through the corrected monitor (819 events): Nov 3 -> 295 NO PREDICTION AVAILABLE (window closed before the run); Nov 4 -> 4 BUY, 1 SELL, 13 NO TRADE and 2 NO VALID PREDICTION from the historical snapshot, and 504 NO PREDICTION AVAILABLE (not selected or not collected). Scoring files of all eight formula models and the shared engines are byte-identical to the deployed base (38ca61d).

## Explicit deployment risk: live cache shapes
The readers were checked against the provider's OpenAPI field names (news, calendar) and the application's own proven flow parser, and run against representative fixtures, but never against the live process caches. If a field differs, the monitor skips that item (it never invents data) and the dashboard shows less evidence; it cannot change a prediction or break the scheduler. **Safe post-deployment check** (read-only, no request): `verify_after_deploy.ps1` now calls `/api/earnings-monitor/cache-probe` and `/health` and prints each reader's compatibility, missing field names, the 30-day calendar cache coverage and the first timed scan. Run it once after the restart and again 10 minutes later (caches warm).

## Exact deployment steps (after RELEASE GO and a push)
1. Download the archive of the release commit, verify `(Get-FileHash .\MANIFEST.sha256).Hash` equals the manifest above.
2. Baseline: compare installed `backend\earnings_monitor\*` with the deployed (553d2a18) manifest (all MATCH); list `_backup_*` folders; run `verify_after_deploy.ps1` (PASS, CACHE_ONLY, 0 requests).
3. `powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy_earnings_monitor.ps1 -Action Update -Package $PWD.Path -ManifestSha256 e543414d9779402f125f57b339e20057d74974dec4ee63dd93b179777d93c24d` (preflight, backup, replace, restart, health checks, automatic rollback on failure).
4. `.\verify_after_deploy.ps1` immediately and after 10 minutes.

## Rollback
`.\deploy_earnings_monitor.ps1 -Action Rollback -Package $PWD.Path -BackupDir <the _backup_monitor_update_* folder the Update printed>`. The migrated store stays valid for the previous code (verified). No data is deleted.

## Licensing: items that need the provider's written confirmation
Public pages say the API is for personal use and redistribution is not allowed, and say **nothing** about storage. Please obtain written confirmation for: (1) storing provider data (caches, snapshots, evidence items including short news headlines) and for how long; (2) deriving and storing model predictions and accuracy statistics from it; (3) showing provider-derived data to anyone other than the account holder (any other login user of the dashboard); (4) the Smart Money Move research store (multi-year price and earnings history); (5) using one token from several machines and applications; (6) WebSocket data use and storage; (7) publishing any figure derived from provider data. News headlines can carry third-party rights as well.

## Separate approvals still required
Unusual Whales concurrency layers (own release); Smart Money Move engine installation; Smart Money Move live evidence collection; any REFRESH approval or budget/cap/policy change; the alert channel; the WebSocket ingest; renewing the monitor approval (ends 2026-11-17 12:12:35Z).
