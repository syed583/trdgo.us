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
