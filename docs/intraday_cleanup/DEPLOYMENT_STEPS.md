# Intraday cleanup & Strategy Trades upgrade — deployment steps (VPS)

Branch: `feature/intraday-cleanup-tracker` (based on local `3c16d447`). **Do not run any step without the owner's explicit OK.**
Smart Money Move (`C:\smlive`, its ledgers, tasks, wrapper, budget) is not touched by any step below.

## 0. What this deploy changes on the server (read before approving)

* **This is the first deployment of the whole undeployed intraday stack** that production `38ca61d` never had:
  `uw_priority / uw_budget / uw_slots / uw_freshness / uw_health`, `intraday_safety`, `intraday_15m`, `intraday_feed`,
  `intraday_tiers`, `market_calendar`, `entry_lock`, `paper_fills`, workflow v2 (shipped OFF). The Smart Move runner
  imports `unusualwhales_service` (raw `uw.get`) from this backend: that function's behaviour is unchanged, but the
  client now sits behind the priority gate / budget rails (`tab_settings.UW_BUDGET`) — the runner's requests are
  admitted as before (it tags itself; the rails are far above its 700/day). Verify after deploy with one DRY_RUN
  (`C:\smlive\...step4` script) before the next scheduled LIVE run.
* Removed: the v1 "Trdgo Stock" board + its 45 s scan loop, ChatGPT / Claude Pro intraday boards, `/api/ai-trade/*`,
  `/api/chatgpt-intraday/*`, `/api/claude-pro-intraday/*`.
* Paper tracker: entries 10:00–15:00 ET only, fresh-quote entries, $10k notional sizing, breakeven trail, 15:50 ET close,
  observed fills, prior-day leftovers closed as CLEANUP. **The 29 positions currently OPEN on production will be closed by
  the first tick** (today's at the next rule / 15:50; earlier days' as CLEANUP at the current price).
* Schema: six nullable columns are ADDED to `demo_trades` by `ensure_schema` (`ALTER TABLE ... ADD COLUMN`, needs DDL
  rights on the pooler role — check `backend/.env` role; falls back to a logged error and no entries if refused).
* The entry path requires `APP_ENV=production` on the VPS for the 15-minute candle feed to run (otherwise every
  BUY/SELL is WAIT and no trade opens). The VPS has it (credential audit 2026-11-02).
* `release_check.py` now REQUIRES the pure-intraday rules: `277f30f` / `38ca61d` / `36094e8` are **no longer valid
  rollback targets by its rules** — a rollback is a rollback to the old carried-position tracker and must be a conscious
  decision (see step 6).

## 1. Pre-deploy (local, done)
- backend tests green, frontend `npm run typecheck` + `npm run build` green, `python release_check.py` PASS on the tree.
- Push the branch (needs owner OK): `git push -u origin feature/intraday-cleanup-tracker`.

## 2. Backup (VPS, read-only except the backup folder)
```
$ts = Get-Date -Format yyyyMMdd_HHmmss
Copy-Item C:\apps\us-stock-reader\backend\.env C:\audit-reports\backups\env_pre_intraday_cleanup_$ts.env
git -C C:\apps\us-stock-reader rev-parse HEAD > C:\audit-reports\backups\prev_commit_$ts.txt
Compress-Archive C:\apps\us-stock-reader\frontend\dist C:\audit-reports\backups\dist_pre_intraday_cleanup_$ts.zip
```
Database: take a Supabase snapshot / `pg_dump demo_trades` (the only table altered) — owner's existing procedure.

## 3. Deploy (VPS) — outside US market hours, with the paper book FLAT or accepting the first-tick cleanup
```
Stop-Service USStockReader
git -C C:\apps\us-stock-reader fetch origin
git -C C:\apps\us-stock-reader checkout feature/intraday-cleanup-tracker
git -C C:\apps\us-stock-reader pull --ff-only
C:\apps\us-stock-reader\backend\.venv\Scripts\python.exe -m pip install -r C:\apps\us-stock-reader\backend\requirements.txt
cd C:\apps\us-stock-reader\frontend; npm ci; npm run build
C:\apps\us-stock-reader\backend\.venv\Scripts\python.exe C:\apps\us-stock-reader\backend\release_check.py   # must print PASS
Start-Service USStockReader
```

## 4. Post-check (VPS, read-only)
```
Invoke-RestMethod http://localhost:8000/api/health
Invoke-RestMethod http://localhost:8000/api/market/etf-strip
Invoke-RestMethod "http://localhost:8000/api/strategy-trades?strategy=all"   # status OK, rules block present
Invoke-RestMethod http://localhost:8000/api/uw/health
Get-Content C:\apps\us-stock-reader\backend\logs\tracker.log -Tail 20
Get-Content C:\apps\us-stock-reader\backend\logs\uw.log -Tail 20
```
Then: Smart Move DRY_RUN (`C:\smlive\v6_1_step4_run_live.ps1` in DRY_RUN) and confirm `requests` behave as before.

## 5. First trading day watch
- 10:00 ET: first entries (expect `ENTRY` lines in tracker.log with quote age ≤ 60 s and shares = floor(10000/price)).
- 15:50 ET: every OPEN row → EOD_CLOSE; nothing OPEN after 15:51.
- `/api/uw/budget`: per-module counts; the `ai_boards` module should be the largest intraday consumer; no `intraday_engine`
  LIVE-cap refusals.

## 6. Rollback
`git checkout <prev_commit from step 2>`, restore `dist` from the ZIP, `Start-Service`. The six new columns stay
(nullable; the old build ignores them). Any trade opened by the new tracker that is still OPEN would be carried by the old
build — close the book first (step 3 condition) or accept it. Note the old build does not pass the new `release_check`.
