# Smart Move dashboard (local, read-only, saved evidence)

Open: **http://127.0.0.1:5191/smart-move**  (also in the sidebar under "Earnings & news" → Smart Move)

What it is: the TRDGO-US page for the frozen 40-point Smart Move shadow engine. It shows only outputs of that engine on SAVED evidence, in three separately labelled datasets (current calendar observation, historical replay, synthetic examples). It is not a live signal feed and makes no accuracy claim. No provider request, no database, no write endpoint.

## Start (two processes; first time needs the frontend packages)
Folder: the `feature/smart-move` checkout. In this session's worktree that is
`C:\Users\initi\AppData\Local\Temp\claude\C--Users-initi-Downloads-US-STOCKS\f48be4ee-7f76-4cb9-a237-d4759a147702\scratchpad\wt_smart`
(after merging the branch it is simply the repository root).

1. API (port 8791):
   ```
   cd backend
   python -m uvicorn smart_move_local_server:app --host 127.0.0.1 --port 8791
   ```
2. Page (port 5191, proxies /api and /auth to 8791):
   ```
   cd frontend
   npm install            # only if node_modules is missing (the worktree uses a junction to the main checkout's node_modules)
   npx vite --config vite.smart-move.config.ts
   ```
   Or run `docs/smart_move/app/start_local.ps1` to start both.
3. Open http://127.0.0.1:5191/smart-move . API docs: http://127.0.0.1:8791/docs . URL options: `?set=replay`, `?set=replay&exp=sec_moderate`, `?set=synthetic`, `?symbol=AAPL`.

The top-right "FEED OFFLINE", the "view-only account" strip and any 404 in the browser console come from the rest of the app shell, which has no backend on this local server; the Smart Move page does not depend on them. The `/auth/me` stub in `smart_move_local_server.py` returns a read-only, non-admin account and exists only for this local server.

## Tests
- API (12): `cd docs/smart_move/app && python -m pytest -q tests`
- Engine, pipeline, replay, calendar (92+): `cd docs/smart_move/shadow_impl && python -m pytest -q`
- End to end against the running app (18 checks, renders the real page in headless Chrome and compares with the API and the saved files): `python docs/smart_move/app/e2e_check.py`
- Frontend: `cd frontend && npx tsc -p tsconfig.app.json --noEmit` and `npx vite build`

## Evidence pack
`docs/smart_move/app_data/` (built by `app/build_app_data.py` from the saved P1/P2 evidence and the existing replay/synthetic results; SHA-256 of every file in `meta.json`, verified at load; the API refuses with 503 if a file is altered). To plug the router into the full TRDGO-US backend later: `from smart_move_routes import router` and `app.include_router(router)` (one line, not done here so nothing in production changes).

## Datasets and labels
| Dataset | What it is | Label on screen |
|---|---|---|
| Current calendar | the 271 symbols the three vendors returned on 2026-10-30 09:01–09:03 ET; calendar status by the vendor rules; engine run with the evidence that exists (no price history saved, so NO TRADE with reasons) | "Not a prediction" |
| Historical replay | 186 development-period events replayed from saved candles; **primary** = pre-registered provider history, F7 absent; SEC-derived F3 and the F7 sensitivity are separate, flagged EXPLORATORY / F7 UNVERIFIED | "Not a live prediction" |
| Synthetic examples | 28 invented events that exercise the engine mechanics | "Not real" |

## Live current evidence and governed refresh (added)
The default tab, **Live current**, scores events from the live store (`backend/.smart_move_live/`, git-ignored) with the same frozen engine, using only evidence the refresh service retrieved at or before the scoring time. It never uses the saved pack, the replay or the SEC-derived F3 as current evidence (SEC-F3 is shown as a separate, unavailable experimental line). F7 counts only when it was captured prospectively inside the event window; stale or missing evidence blocks BUY/SELL and the page lists the exact blocker.

**Everything is OFF by default** (`feed` shows DISABLED KILL SWITCH, authorization 0, nothing scheduled):
| Setting | Default | Meaning |
|---|---|---|
| `SMART_MOVE_REFRESH_ENABLED` | unset (kill switch engaged) | must be `1` to allow any request |
| `SMART_MOVE_AUTHORIZED_DAILY` | `0` | requests per day the owner has approved; the 1,700 planning ceiling bounds it but is not a grant |
| `SMART_MOVE_AUTO_REFRESH` | unset | `1` starts the background loop (every 5 min: calendar every 6 h, evidence when stale and a window is open) |
| `SMART_MOVE_MODE` | `LIVE` | `SIMULATION` attaches the fake provider (every row labelled SIMULATED) |
Controls: `POST /api/smart-move/refresh` (manual; max 50 requests, at most one per minute, same governor) and `POST /api/smart-move/refresh/kill` (engages the kill switch; releasing it is only possible locally by removing `KILL` from the live store or changing the settings). The governor enforces authorization, the daily ceiling, the shared 400/h live-hours cap with the warmer (300 reserved, the warmer's own counter is read when it runs in the same process), 2 s spacing, one retry, 429/401 stop for the day, and an append-only ledger (`requests.jsonl`, `ledger.jsonl`, `log.jsonl`).

Demonstration without any provider: `SMART_MOVE_MODE=SIMULATION SMART_MOVE_SIM_NOW=2026-11-03T11:05:00+00:00 python -m uvicorn smart_move_local_server:app --port 8792` and `SM_VITE_PORT=5192 SM_API_PORT=8792 npx vite --config vite.smart-move.config.ts`, then open http://127.0.0.1:5192/smart-move and press "Refresh now" (two presses a minute apart complete the collection). `SMART_MOVE_CALENDAR_INTERVAL_H=0.08` makes the calendar go stale after about five minutes to show the stale state.
Tests: `docs/smart_move/app/tests/test_live.py` (18) and `e2e_check.py --sim-page http://127.0.0.1:5192` (31 checks).

## First supervised live run (2026-10-30 15:13 UTC, owner-approved)
`app/supervised_first_run.py`: 13 read-only requests (authorization 20 for that process only, kill switch re-engaged afterwards): calendar (5 Unusual Whales, 1 Finviz, 1 Finnhub) and a one-symbol probe (SPY, XLK, AAPL candles; AAPL earnings history, volatility context, iv-rank). Records in `docs/smart_move/p1p2/first_live_run/` (raw payloads stay local, hashed). Findings fixed afterwards: the daily feed carries the in-progress bar (freshness now needs the last completed session's bar, retrieved after that session ended); iv-rank returns a week of per-row stamped rows (the latest is used); the earnings history carries 125 quarters including the upcoming one. AAPL scored on real evidence: 30/40 points (F3, F5, F8), NO TRADE because its capture window opens Sat 31 Oct 16:00 ET (OUT_OF_WINDOW) and F7 counts only when captured in the window.

## Supervised window collection (owner-approved: max 60 requests, 10 stocks, one run)
`app/supervised_window_run.py --store-dir <new folder> --precheck-store <earlier calendar store> [--execute]`. Prechecks from saved observations that an event's capture window is open (otherwise sends nothing and says when the next window opens), refreshes the calendar, selects the 10 largest-market-cap VENDOR_CONFIRMED events with an open window and a sector, collects their evidence (shared SPY and sector ETF candles, then candles, earnings history, volatility context, iv-rank per name), scores with the frozen engine, stores timestamped predictions, writes a report and re-engages the kill switch. The process authorization equals the cap (60), so retries count. Rehearsed with the simulated provider in `tests/test_window_run.py` (4 tests). First window opens Sat 31 Oct 07:00 ET (Monday before-open names); Monday after-close names from 17:00 ET.
