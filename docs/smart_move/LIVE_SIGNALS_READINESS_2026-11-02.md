# Live Earnings Signals - readiness assessment (2026-11-02, local only; nothing deployed, no provider request)

## 1. What already exists (audit of the real code)
* **Production scan models** (Pre-Earnings Direction, Claude Last, Grok, Meta; ChatGPT/Kimi/Gemini/Claude Pro variants; `/all-models/scan` shows one cell per model and counts BUY/SELL): they do NOT call providers themselves. They read the swr cache keys `earn:equity:{SYM}` / `earn:trade:default:{SYM}`, which production's own warmers fill from Unusual Whales and Finnhub. They therefore **never read the Phase 14 batch store**: Batch #2 cannot change their output. Signals: BUY / SELL / NEUTRAL / LOW_DATA / PENDING; scores 0-100 with coverage; **no calibrated confidence anywhere** (Claude's confidence is a coverage/agreement heuristic). Endpoints exist (`/pre-earnings-direction/scan`, `/claude-last/scan`, `/grok/scan`, `/meta/scan`, `/all-models/scan`, `/earnings/upcoming`) with pages in the production frontend. Daily history: `earnings_signal_snapshots`, `ped_scores`.
* **Smart Move frozen engine** (40-point, continuation and contrarian): reads the batch store. Frozen; unchanged.
* The only model that consumes Batch #2 evidence is the frozen Smart Move engine.

## 2. What was implemented locally (this commit)
`smart_move_batch_marker.py` (validation + explicit sealed completion marker, hash-verified), `smart_move_live_signals.py` (mapping, freshness labels, coverage, idempotent job, read-time status), `smart_move_live_signals_routes.py` (read-only API), page `/smart-move/signals`, 14 backend + 9 UI tests. No scoring, confidence or provider code was added. The job refuses to run without a valid sealed marker, re-validates the batch, appends each signal once, logs every refusal and failure, and shows missing data as NO TRADE with the reason.

## 3. Coverage on the real saved data today (783 events: 303 on Nov 3, 480 on Nov 4)
All 783 rows are NO TRADE with **no valid prediction**: 783 INSUFFICIENT_INPUTS, 0 BUY, 0 SELL, 0 complete, 0 partial. 236 events are vendor-confirmed (121 Nov 3, 115 Nov 4) and the other 547 are estimated or single-vendor or have unknown timing. For the 236, the engine has 0 of 40 points because factors F3 (earnings-reaction history), F5 (price/technical), F7 (volatility skew) and F8 (market) have no fresh evidence.

## 4. How many stocks Batch #2 can realistically support: **none for BUY/SELL**
The signed plan collects calendar (4), earnings payloads (12), Snapshot B (14) and news (17). None of those fill F3, F5, F7 or F8, which need 3-year daily bars, volatility context, and market/sector bars (about 4 requests per event plus shared ETF bars). Expect **0 BUY, 0 SELL and every event NO TRADE / no valid prediction from the frozen engine after Batch #2.** A 50-attempt batch could feed the engine for roughly 10 events at most if it were re-planned around those inputs; that needs an owner decision and different job types in the profile (the fetcher already supports `ohlc`, `earnings`, `vol_context`, `iv_rank`). Batch #2 does remain useful for the evidence-quality, fiscal-quarter and Snapshot B work.

## 5. Blockers and calibration
1. Batch #2 as planned cannot produce signals from the frozen engine (section 4).
2. No model has a calibrated probability; the page shows none.
3. The frozen engine has no validated accuracy (0 verified outcomes; claim threshold 100 decided events over 8 weeks). Production models are uncalibrated and unvalidated against outcomes.
4. The unanimity display rule (BUY/SELL only when every valid primary model agrees) is a design choice that needs owner approval.
5. Production scan rows can only reach this page through production-side code (the scans are in-process): not built, because it means editing and deploying production.
6. The router is registered only in the local Smart Move server.

## 6. Deploying safely (not done; each step needs approval)
1. Decide the data path: (a) re-plan a later batch to feed the engine, and/or (b) surface the existing production models. Approve the unanimity rule.
2. Production side: add a thin reader that calls the four existing scan functions in process (`pre_earnings_direction_service.scan`, `claude_last_service.scan`, `grok_service.scan`, `meta_service.scan`) and passes their rows to `process_batch(production_scans=...)` or a production-only variant; include each scan's own update time.
3. Copy the Smart Move modules into the production tree (they import only the standard library, the shadow-engine calendar and FastAPI), register the router in `api_routes.py`, set `SMART_MOVE_SIGNALS_DIR` and `SMART_MOVE_BATCH_RUN_DIR`, keep the page behind the existing authentication.
4. Run the offline suites and the UI tests on the production build; take a database and `.env` backup; deploy outside market hours; verify `max_inflight` and Options Flow unchanged; keep the previous release for rollback.
5. After a sealed batch (`python backend\smart_move_batch_marker.py` style seal by a named person) run the job once, check the page shows rows labelled with their true status, and only then announce it.

## 7. Verdict
**NOT READY for live BUY/SELL signals.** The pipeline, labelling and safeguards are ready and tested, but the planned Batch #2 evidence cannot generate any BUY or SELL from the existing frozen model, and the production models are not connected to this page. See the blockers above.
