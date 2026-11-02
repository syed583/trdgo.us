# Phase 14 - batch #2 final preflight report (prepared 2026-11-02 18:55 UTC)

**Status: READY FOR OWNER REVIEW - AWAITING EXPLICIT GO.** Nothing was executed: no provider request, no kill-switch release, no signed attestation, no change to production or to any existing evidence.

## 1. Owner-reported facts (recorded as REPORTED, not verified by Claude)
* Unusual Whales has authorized storing raw API responses, historical analysis and backtesting, and showing derived analytics to TRDGO.US users. **OWNER-REPORTED permission. Claude did not verify the licence** (the public terms read earlier did not state it; keep the written confirmation on file).
* Subscription active; production VPS (Windows) service `USStockReader` Running; `UW_MAX_INFLIGHT=2`; authenticated `max_inflight` = 2; Options Flow API status OK with 5 trades.

## 2. Requirement checklist
| # | Requirement | Result | Evidence |
|---|---|---|---|
| 1 | Collector code and tests verified | PASS | Backend 491 passed, shadow 100 passed / 5 skipped, UI 24 passed; 10 tests cover the new jobs, 88 cover the gate, ceiling and kill switch. Frozen hashes pinned by test. |
| 2 | Production concurrency capped at 2 | PASS (owner-reported) | Owner readings above. Not independently checked: I never touch production. |
| 3 | Collector concurrency 1 | PASS | Localhost has no `APP_ENV` and no `UW_MAX_INFLIGHT`, so `MAX_INFLIGHT` resolves to 1 (checked by importing the client with no network); the collector is sequential. 1 + 2 = 3. |
| 4 | Maximum 50 attempts | PASS | `--profile batch2` refuses more than 50; the governor's daily authorization = `--authorize-requests` and counts failures. A 51 request is blocked (tested). |
| 5 | Retries disabled | PASS | `batch2` builds the service with `retries=0`; the client's own 429 re-sends are switched off in `InfraFetcher`. |
| 6 | Kill switch preserved | PASS | `batch2_store\KILL` exists and was NOT touched. The runner releases it only inside an armed, attested run and re-engages it in a `finally` (tested). |
| 7 | No other consumer beyond the allocation | PARTIAL | Local: no other process running, other app's scheduled tasks all disabled (gate check at 18:55 UTC). Other machines: not visible by design; covered by the owner's signature. |
| 8 | Separate copy of the pilot database | PASS | `C:\Users\initi\SmartMoveRun\batch2_store` = byte-identical copy (53 files, KILL included); the pilot store's manifest is unchanged before/after (hashes saved in `batch2_store_source_manifest.json`). The live run writes only to the copy and to `batch2_run`. |
| 9 | Historical evidence never modified | PASS | Append-only stores; originals untouched (point 8). |
| 10 | No fabricated Snapshot A/B, outcomes or accuracy | PASS | Snapshot A cap 0; bars job needs a closed reaction session; no outcome or accuracy is computed by the run. |
| 11 | Frozen scorer, grader, outcome rules, NEWS_GUIDANCE | PASS | Unchanged; hash pins in tests; guidance stays disabled. |
| 12 | Market-closed window | PASS (planned) | See section 5. At 18:55 UTC the market is IN SESSION, so do not start now. |
| 13 | Attestation prepared for owner review | PASS (unsigned) | `batch2_attestation_DRAFT_FOR_OWNER_SIGNATURE.json`: `attested_by` and `attested_at` are blank, so the gate rejects it. Claude does not sign for the owner. |
| 14 | Zero-request dry run | PASS | On the copy, as of 21:30 UTC: mode DRY_RUN, provider_requests 0, ceiling 50. Gate reads "not verified" and the kill switch blocks, as it must until you sign. |
| 15 | Emergency stop and rollback | PASS (prepared) | Sections 6 and 7. |

## 3. Expected request allocation (from the dry run, 21:30 UTC)
115 events have an open window (Nov 4 before the open). Calendar 4 (refreshes `2026-11-04 premarket` and `afterhours`), earnings payloads 12, Snapshot B 14, news 17 (page 0), daily bars 0, Snapshot A 0. **About 47 attempts of 50**, spacing 2 s, one in flight, about 2 to 3 minutes. Known cost: none beyond the flat subscription.
Cannot come out of this batch: any Snapshot A/B pair, any verified outcome, any accuracy figure.

## 4. Remaining risks
1. **Licence:** owner-reported only; keep Unusual Whales' written confirmation.
2. **Other machines on the token** are invisible to the gate. One unseen consumer would turn a request into a 429; the run then stops (it never retries) and the rest of the day is blocked.
3. **Production restart already happened;** if production's cap is changed back before the run ends, 1 + 3 = 4 breaks the limit. Keep it at 2 until the run is finished.
4. **Release times stay nominal;** Snapshot A for future events needs a later, separately approved run.
5. The attestation is valid for 24 hours only; sign it shortly before the run.

## 5. Proposed execution window (UTC)
* Market closes 21:00 UTC (16:00 ET; DST ended 1 Nov, so ET = UTC-5).
* **Sign the attestation between 21:15 and 21:29 UTC. Start the live batch at 21:30 UTC on 2026-11-02. Latest start 22:30 UTC.** The target events (Nov 4 before the open) keep their window open until 11:00 UTC on Nov 3, so a later night start would still serve them, but a short run right after the close leaves the most margin before production traffic rises (from about 08:00 UTC).
* Do not start before 21:15 UTC.

## 6. Exact commands (run on the collector PC, from the project folder)
Dry run again just before the live batch (zero requests), after the owner has signed and copied the attestation:
```powershell
python backend\smart_move_collection_runner.py --store-dir C:\Users\initi\SmartMoveRun\batch2_store --state-dir C:\Users\initi\SmartMoveRun\batch2_run --profile batch2 --shared-key-attestation C:\Users\initi\SmartMoveRun\shared_key_attestation.json
```
It must show `OWNER_ATTESTED`, ceiling 50, tasks earnings 12 / snapshot_b 14 / news 17. If it shows anything else, do not continue.

**Supervised live batch (only after the owner's separate GO):**
```powershell
$env:SMART_MOVE_COLLECTION_LIVE = "1"
python backend\smart_move_collection_runner.py --store-dir C:\Users\initi\SmartMoveRun\batch2_store --state-dir C:\Users\initi\SmartMoveRun\batch2_run --mode live --arm --authorize-requests 50 --profile batch2 --shared-key-attestation C:\Users\initi\SmartMoveRun\shared_key_attestation.json --require-rotated-from e7fff6ea
```
**Emergency stop (any time):** press Ctrl+C in that window, or in a second window:
```powershell
New-Item -ItemType File -Force C:\Users\initi\SmartMoveRun\batch2_store\KILL | Out-Null
```
The run halts between requests; a restart resumes without repeating a request.

## 7. Rollback and cleanup
1. Confirm the kill file exists again: `Test-Path C:\Users\initi\SmartMoveRun\batch2_store\KILL` (the runner re-engages it; create it as above if False).
2. Revoke the attestation: `Remove-Item C:\Users\initi\SmartMoveRun\shared_key_attestation.json`
3. Restore production (on the server): `cd C:\apps\us-stock-reader\backend ; .\vps_uw_cap.ps1 -Remove ; Restart-Service USStockReader`, then verify `max_inflight = 3` and Options Flow OK (commands in the earlier cap instructions), then delete `.env.pre_cap_*`.
4. The pilot store is unchanged; if the run must be discarded, `batch2_store` and `batch2_run` can simply be left unused (never delete them without review).
5. After the run: rebuild the quality report into a NEW folder, check `attempts.jsonl` (one line per attempt) and the evidence timestamps.

## 8. Final state
READY FOR OWNER REVIEW - AWAITING EXPLICIT GO. To go: (a) sign and place the attestation between 21:15 and 21:29 UTC, (b) reply with an explicit GO. I will not run anything before that.
