# Task 1 (20 stocks) and Task 2 (Batch #2): preflight (2026-11-02 20:17 UTC; NOTHING executed, zero provider requests)

The two tasks are kept apart: separate store, separate state folder, separate attestation, separate 50-attempt ceiling, never run at the same time (two collectors plus production at 2 would be 4 in flight; the token allows 3).

## REPORT A - the 20 stocks (Task 1)
**Selection (vendor-confirmed, Before Open, Nov 4 2026, prediction window open until 11:00 UTC Nov 3, top 20 by market cap; market cap is a selection heuristic only, the engine's liquidity gate decides):**
MCD, NVO, UBER, CVS, EMR, DDOG, GWW, COR, CVE, MLM, RPRX, SN, KHC, MDLN, UTHR, Q, GFS, CDW, ZBH, CRL (all BMO).

**New tooling (offline, tested: 11 tests; 89 related safety tests pass):** `smart_move_engine20.py` and runner profile `engine20`. It reuses the engine's own planner, so the evidence kinds are exactly what the frozen scorer reads (`ohlc` 3Y for the stock, SPY and sector ETFs; `earnings` limit 24), skips anything already fresh, goes only through the governor and `InfraFetcher`, appends raw responses to the store, and reports with the frozen engine's own output. No scoring, no new client.

**Request budget (exact, from the zero-request dry runs on `engine20_store`, a full copy of the pilot store):**
| Start time | Calendar refresh | SPY | Stock bars | Earnings history | Sector ETFs | Total | Not funded |
|---|---|---|---|---|---|---|---|
| Before the close (calendar still under 5 h old, until about 21:14 UTC) | 0 | 1 | 20 | 20 | 7 | **48 of 50** (spare 2) | none |
| After the close (from 21:45 UTC; calendar older than 5 h) | 4 | 1 | 20 | 20 | 5 | **50 of 50** (spare 0) | XLB and XLP: the sector sub-signal for MLM and KHC-type stocks will be missing |
Volatility context and IV rank (F7) would add 40 requests and cannot fit in 50: not planned. Retries 0, concurrency 1, spacing 2 s, three failures in a row end the run.

**What collecting before the close gives, and what it does not.** The engine requires the bars to reflect the latest completed session and to be retrieved after that session was complete (plus its 45-minute buffer). Before 21:00 UTC the latest completed session is Oct 30, so evidence collected now is valid for a capture made now, inside the window, but it does NOT use incomplete Nov 2 candles and it goes stale as soon as the Nov 2 session completes. After 21:00 UTC the same evidence becomes STALE and the stocks show **PENDING FINAL REFRESH**; the refresh costs another run (after 21:45 UTC). So the choice for the owner is: (a) collect and score before 21:00 UTC on Oct 30 data, or (b) collect after 21:45 UTC on Nov 2 close data (both inside the window). Option (b) is the only one that serves Nov 2 completed-session data and fits next to Batch #2's window, but it needs the 4 calendar attempts.

**Not yet done (all need your GO):** collection, scoring. Results today: data collected 0, API attempts consumed 0, BUY 0, SELL 0, genuine NO TRADE 0, awaiting data: 20 of 20 (shown as no valid prediction / pending). Earnings dates, previous earnings, historical reactions and EPS surprises come from the single `earnings` payload per stock (the saved sample shows 125 rows with reaction and EPS fields); volume and liquidity from the bars; technical inputs are computed by the engine from the bars; volatility is not collected in this plan; FINVIZ supplies only earnings date and time here, no history.

**To run Task 1 (all steps are yours; nothing runs without them):**
1. Production check and cap as before (owner-reported 2 in flight); no other token user.
2. Decide before-close or after-close and sign a SEPARATE attestation: draft `C:\Users\initi\SmartMoveRun\engine20_attestation_DRAFT.json` -> your signed copy at `C:\Users\initi\SmartMoveRun\engine20_attestation.json` (set `market_hours_ok` to true only for a run before 21:00 UTC). Same signing script as before, with these two paths.
3. Zero-request dry run:
```powershell
python backend\smart_move_collection_runner.py --store-dir C:\Users\initi\SmartMoveRun\engine20_store --state-dir C:\Users\initi\SmartMoveRun\engine20_run --profile engine20 --select-date 2026-11-04 --select-session BMO --select-top 20 --shared-key-attestation C:\Users\initi\SmartMoveRun\engine20_attestation.json
```
4. Live, only after your explicit GO:
```powershell
$env:SMART_MOVE_COLLECTION_LIVE = "1"
python backend\smart_move_collection_runner.py --store-dir C:\Users\initi\SmartMoveRun\engine20_store --state-dir C:\Users\initi\SmartMoveRun\engine20_run --mode live --arm --authorize-requests 50 --profile engine20 --select-date 2026-11-04 --select-session BMO --select-top 20 --shared-key-attestation C:\Users\initi\SmartMoveRun\engine20_attestation.json --require-rotated-from e7fff6ea
```
Emergency stop: Ctrl+C or `New-Item -ItemType File -Force C:\Users\initi\SmartMoveRun\engine20_store\KILL`. The kill file is re-engaged automatically. The report (`engine20_report.json`) lists, per stock: BUY / SELL / NO TRADE (genuine) / NO VALID PREDICTION / PENDING FINAL REFRESH, reason, coverage out of 40, liquidity, freshness, window validity, model version `score40-v1`.

## REPORT B - Batch #2 (Task 2), unchanged
| Item | Status |
|---|---|
| Original signed allocation | calendar about 4, earnings payloads 12 (v2 kind), Snapshot B 14, news 17, bars 0, Snapshot A 0: about 47 of 50; jobs unchanged |
| Approved execution window | start 21:30 UTC (01:30 Dubai), latest 22:30 UTC (02:30 Dubai), after the 21:00 UTC close |
| Authorization | attestation signed by Syed at 2026-11-02T19:27:36+00:00, valid to 2026-11-03T19:27:36+00:00; recognised by the gate (OWNER_ATTESTED after the close); **explicit GO still required**; I will not run it automatically |
| Remaining API budget | 50 of 50 (no attempt used; no `attempts.jsonl`) |
| Kill switch | engaged (`batch2_store\KILL`) |
| Execution readiness | technically ready, waiting for the close and your GO |
| Blockers | the close; your GO; production cap and no-other-consumer confirmations as signed. Batch #2 and Task 1 must run one after the other, not together. |
Suggested order after the close: Batch #2 at 21:30 UTC (about 3 minutes), then Task 1 at or after 21:45 UTC (about 3 minutes), each with its own GO, its own store and its own attestation.
