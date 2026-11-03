# Engine20 snapshot dashboard: production readiness (2026-11-03). NOTHING deployed, pushed or restarted; zero provider requests.

**Verdict: PARTIAL (ready to deploy as a small overlay once the owner approves; NOT ready to deploy this branch as it stands).**

## Why an overlay, not the branch
Production runs `38ca61d`. It contains **no** Smart Move file at all (`git ls-tree 38ca61d backend/ | grep smart_move` is empty). `feature/smart-move` is 116 commits and 151 non-doc files ahead of it, including unrelated earnings/warmer/db-cache/`api_routes.py` changes. Deploying the branch would ship all of that. So the release is the branch `release/smart-move-engine20` (commit `499390fd`), cut from `38ca61d` and containing only 8 files, 574 added lines, 0 removed:

| File | Change |
|---|---|
| `backend/smart_move_engine20_routes.py` | new, read-only API (2 GET routes) |
| `backend/smart_move_engine20_snapshot.py` | new, stdlib-only at import |
| `backend/main.py` | +3 lines: import and `app.include_router(...)` right after `app.include_router(api_router)` |
| `frontend/src/pages/SmartMoveEngine20Page.tsx`, `smart-move.css` | new |
| `frontend/src/App.tsx` (+2), `components/Sidebar.tsx` (+1), `api/client.ts` (+6) | lazy route, nav entry, 2 client calls |

Prepared package (SHA-256 values in MANIFEST.sha256 inside the folder): `C:\Users\initi\SmartMoveRun\release_engine20_20261103\` (`backend\` 2 files, `dist\` 111 files built from the overlay, `engine20_snapshots\`).

## Audit results
| # | Check | Result |
|---|---|---|
| 1 | Production frontend build (`tsc -p tsconfig.app.json --noEmit && vite build`) | PASS on both the branch and the overlay (overlay: built, 111 files; only the pre-existing `INEFFECTIVE_DYNAMIC_IMPORT` warning) |
| 2 | Routes registered and gated | PASS. Overlay app imported with a throwaway sqlite URL (no database touched, file deleted): both routes registered; unauthenticated GET and POST return **401**; neither path is in `PUBLIC_PATHS`/`PUBLIC_PREFIXES`. The existing `_gate` middleware covers every `/api` path |
| 3 | Snapshot loaded from `SMART_MOVE_ENGINE20_DIR`, hash checked on **every** read | PASS (tests: altered file after a first read is served as `SNAPSHOT_UNVERIFIED` with no rows) |
| 4 | No keys, Windows paths or private data in responses | PASS. Real snapshot responses scanned: no drive letters, `Users`, key/secret/token/password/bearer strings, no store paths. The `source` text names run folders only. Regression test added |
| 5 | NEWS_GUIDANCE | There is no NEWS_GUIDANCE file or hash: it is a factor name that the audits and the engine keep BLOCKED (0 points). `smart_move_news.py` has no diff against its last commit; the 135 news/guidance/v2/audit tests pass |
| 6 | Integration points | `backend/main.py` after `app.include_router(api_router)`; static frontend from `frontend\dist`; served behind Caddy -> python on 127.0.0.1:8000 |
| 8 | No interference with USStockReader, Options Flow or provider concurrency | By construction: the routes only read two local files, import no provider/UW code, change no gate, budget or `UW_MAX_INFLIGHT`. Not verified on the live server (needs the post-deploy health checks below) |
| 9 | Snapshot cannot look like a live signal | PASS. API always `is_live_signal:false`; page banner "historical snapshot, not a live signal", generation time on every row, window shown as open/closed at read time |
| 10 | Outcomes cannot be falsely graded | PASS. Only `GRADED` records (the only kind `record()` persists) are compared; pending/missing/implausible are never stored, corporate-action suspects are quarantined, NO TRADE and NO VALID PREDICTION are never scored; no store or no module = "not available" |

Tests: app 535 passed at the last full run (+3 new tests added since, passing individually; 135 news/guidance/audit tests re-run), shadow 100 passed (5 skipped), baseline 14 passed (1 skipped), UI 39 passed, `tsc` clean. Frozen files `scorer.py`, `grader.py`, `smart_move_outcomes.py`: no diff.

## Risks to know about
1. `smart-move.css` has a few global rules (`.mono`, `.ghost-btn.danger`, `.ghost-btn:disabled`, dark-theme overrides). They load with the page's chunk and could restyle those classes elsewhere. Look at 2-3 other pages after deploy.
2. Outcome tracking is NOT shipped in the overlay (it needs `smart_move_outcomes` and the `smvshadow` package, absent in production). The page says outcomes are unavailable. Shipping it is a separate decision.
3. The snapshot's prediction window closed at 11:00 UTC on 2026-11-03; the page will show it as closed and historical. It is a record, not today's signal.
4. The overlay was built against a `node_modules` borrowed from the branch worktree; rebuild on a clean install if you want a stricter check. I could not run the production backend tests locally (no `psycopg2` here).

## Environment variables (server `backend\.env`)
- `SMART_MOVE_ENGINE20_DIR=C:\apps\us-stock-reader\smart_move_data\engine20` (required; without it the page shows "No snapshot yet")
- `SMART_MOVE_OUTCOMES_DIR`: leave unset.
Nothing else changes: `UW_MAX_INFLIGHT=2`, `APP_ENV`, `ACCESS_PASSWORD` stay as they are.

## Deployment (owner runs on the VPS, PowerShell; every step is reversible)
```powershell
# 0 preflight (read-only): service up, cap 2, flow OK  (use the verification command already used before)
$stamp = Get-Date -Format yyyyMMdd_HHmmss; $app = "C:\apps\us-stock-reader"
# 1 backups
Copy-Item "$app\backend\main.py" "$app\backend\main.py.bak_engine20_$stamp"
Copy-Item -Recurse "$app\frontend\dist" "$app\frontend\dist_bak_engine20_$stamp"
Copy-Item "$app\backend\.env" "$app\backend\.env.bak_engine20_$stamp"
# 2 copy the package (after unpacking it on the server as C:\deploy\release_engine20_20261103) and compare the SHA-256 values in the manifest
Copy-Item C:\deploy\release_engine20_20261103\backend\*.py "$app\backend\"
New-Item -ItemType Directory -Force "$app\smart_move_data\engine20" | Out-Null
Copy-Item C:\deploy\release_engine20_20261103\engine20_snapshots\* "$app\smart_move_data\engine20\"
# 3 the three main.py lines, inserted once and checked
$m = Get-Content "$app\backend\main.py" -Raw
if ($m -notmatch "smart_move_engine20_routes") { $m = $m.Replace("app.include_router(api_router)`n", "app.include_router(api_router)`n`n# Smart Move Engine20 snapshot (read-only; session gate covers it)`nfrom smart_move_engine20_routes import router as _sm_engine20_router  # noqa: E402`napp.include_router(_sm_engine20_router)`n"); Set-Content "$app\backend\main.py" $m -NoNewline }
Select-String "$app\backend\main.py" -Pattern "smart_move_engine20_routes"      # must show the line
# 4 env var (owner edits .env: add the single line SMART_MOVE_ENGINE20_DIR=...)
# 5 frontend: swap dist
Rename-Item "$app\frontend\dist" "dist_old_engine20_$stamp"; Copy-Item -Recurse C:\deploy\release_engine20_20261103\dist "$app\frontend\dist"
# 6 restart (the only downtime)
nssm restart USStockReader
```
(If `main.py` uses CRLF line endings the `.Replace` needs `` `r`n``; the check line in step 3 shows whether it applied.)

**Expected downtime:** one service restart, about 10-30 seconds (the API is unavailable during it; the static `dist` swap itself needs no downtime). Restarting also restarts the in-process warmers.

## Health checks (after the restart)
1. `Get-Service USStockReader` Running; `/api/admin/flow-metrics` -> `max_inflight = 2`; Options Flow `status OK` (same commands as before).
2. Unauthenticated `GET /api/smart-move/engine20/latest` -> **401**.
3. Authenticated: `/api/smart-move/engine20/latest` -> `status OK`, `is_live_signal false`, counts BUY 4 / SELL 1 / NO_TRADE 13 / NO_VALID_PREDICTION 2, audit `PASS`.
4. Open `/smart-move/engine20` in the browser; open two other pages (e.g. Earnings, Signal History) to see nothing restyled.
5. Service log: no import error at start.

## Rollback (about 30 seconds plus one restart)
```powershell
Remove-Item "$app\frontend\dist" -Recurse; Rename-Item "$app\frontend\dist_old_engine20_$stamp" "dist"
Copy-Item "$app\backend\main.py.bak_engine20_$stamp" "$app\backend\main.py" -Force
Copy-Item "$app\backend\.env.bak_engine20_$stamp" "$app\backend\.env" -Force
Remove-Item "$app\backend\smart_move_engine20_routes.py","$app\backend\smart_move_engine20_snapshot.py"
nssm restart USStockReader     # then repeat health checks 1 and 2 (the new route must now be 404/401 as before)
```
Leave `smart_move_data\engine20` in place (harmless data).

## Owner approvals still needed
1. Approve deploying the overlay (not the branch) and the restart window.
2. Approve editing the server `.env` (one line).
3. Approve pushing `release/smart-move-engine20` (not pushed) or transferring the package by hand.
4. Decide whether to ship outcome tracking later (extra modules) and when to run the outcome collection (provider requests need their own authorization).
5. Decide whether a fresh Engine20 collection is wanted: the current snapshot is historical.

