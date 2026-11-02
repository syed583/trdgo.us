# Phase 14 - batch #2 execution readiness (prepared 2026-11-02; NOTHING executed, no provider request sent, production untouched)

Replaces the allocation in `PHASE14_SUPERVISED_BATCH_PLAN_2026-11-02.md` (that file's safety analysis still applies).

## 1. Collector extension (offline, tested)
Two new job types in the EXISTING collector: `uw_earnings` (exact fiscal-quarter evidence, provider field `ending_fiscal_quarter`) and `uw_ohlc` (daily bars, `timeframe=1M`).
* Same path as every other job: `svc._call` -> governor -> `InfraFetcher` (attestation check, background priority, client retries off). Two endpoint branches were added inside `InfraFetcher.fetch`; no other client or HTTP code exists.
* Same limits: one request in flight, 2 s spacing, 401/403/429 stop, kill switch, and the governor's daily authorization (= `--authorize-requests`) as the hard cap on attempts including failures. Profile `batch2` = `pilot50` limits: 50 attempts maximum, retries 0, news page 0 only, 3 consecutive failures end the run.
* Raw bodies go to the append-only hashed payload store; each request appends one record (kind, symbol, retrieval time, hash, status) to `v2_evidence.jsonl`. Nothing is overwritten or relabelled, nothing is backdated, and no Snapshot A is created (cap 0).
* Safe de-duplication: an earnings payload is not re-requested within 7 days of an OK record; bars are requested once per event after the reaction session closed plus 30 minutes, and bars fetched earlier do not satisfy the task.
* Bar validation: each response is checked with the price-bar rules (positive finite prices, high/low consistent, trading day, not partial, no duplicate date). Failing rows are QUARANTINED (counted on the record, never used for an outcome); a response with no valid bar is stored as INVALID. The raw body is kept either way.
* Frozen scoring, grader, outcome definitions and NEWS_GUIDANCE were not touched. Tests: 10 new (`test_collection_ext.py`); whole suite 491 passed, shadow 100 passed / 5 skipped, UI 24 passed.

## 2. Revised allocation (batch2, at most 50 attempts, no retries)
Zero-request dry run on the saved calendar as of 21:30 UTC tonight: 115 events with an open window (Nov 4 before the open), calendar 5.3 h old = needs a refresh.
| Data type | Planned | Why |
|---|---|---|
| Fresh earnings calendar | about 4 (cap 14) | required first; refreshes only the dates whose window is open |
| Earnings payloads | 12 | fiscal quarter for the same earliest-cutoff events; without it a pair is impossible |
| Analyst estimates, Snapshot B | 14 | the same events, inside their 48 h to 24 h window |
| News (page 0) | 17 | the same events |
| Daily bars | 0 | no event with a recorded BMO/AMC session has a closed reaction session yet (Nov 3 closes tomorrow) |
| Snapshot A | 0 | never manufactured; a real Snapshot A needs a separate run about 10 days before each release |
| Total | about 47 of 50 | 3 attempts of headroom, retries disabled |

## 3. What the first batch can and cannot validate
Can: a fresh calendar; the exact fiscal quarter for about 12 events (checked against the event date); one real Snapshot B for 14 events; news for 17 events, all stored with retrieval times. These will be real, point-in-time evidence.
Cannot: any Snapshot A/B PAIR (no A exists for these events and one is never invented); any verified outcome (the reaction sessions have not closed); any accuracy figure. To get the first pairs, a later run must collect Snapshot A about 10 days ahead for events releasing around Nov 12-16 (needs a wider calendar and your approval), then B in each event's window; first verified outcomes need bars fetched after the Nov 3 / Nov 4 sessions close (a small bars batch).

## 4. Provider permissions and quota (Step 2): status
| Item | Finding | Status |
|---|---|---|
| Plan | Unusual Whales "Whale Bundle Advanced", monthly, USD 399, renewal date 1 Nov 2026 (owner's dashboard screenshots, 2026-10-23) | renewal after 1 Nov NOT verified |
| Daily request quota | "Unlimited"; support (2026-10-23): no daily cap, no per-minute cap, no fair-use ceiling | documented |
| Concurrency | 3 in flight per token across all machines; 429 `concurrent_limit_exceeded` beyond that; failed requests do not count | documented |
| Extra charges | none documented; the plan is a flat monthly fee | no overage terms seen; NOT confirmed in writing |
| Endpoints under this plan | the token already returned OK for earnings payloads (122 saved), analyst estimates (112 saved), daily bars (134 symbols) and news | CONFIRMED by past successful responses |
| 2-year lookback | dashboard shows 2 years; the 1M bar request is well inside it | documented |
| Permission to collect AND STORE responses for research | no terms of service, licence or data-use text exists in the repository or the notes | **BLOCKED: UNVERIFIED. Owner must confirm** the subscription terms allow storing and analysing raw responses (and that they are not redistributed) |

## 5. Readiness checklist (current status)
| # | Requirement | Status |
|---|---|---|
| 1 | Production cap = 2 applied and verified | **FAIL, owner action** (not applied; I do not touch production) |
| 2 | Authenticated production health | **NOT DONE, owner action** (read-only commands below) |
| 3 | Options Flow functional after the cap | **NOT DONE, owner action** |
| 4 | Provider permission and quota | **BLOCKED**: storage/use terms and renewal unverified (section 4) |
| 5 | Other-machine shared-token acknowledgement | **PENDING**, in the attestation (owner) |
| 6 | Market-closed window | **PENDING**: run after 21:00 UTC (16:00 ET) and before 11:00 UTC Nov 3, or set `market_hours_ok` knowingly |
| 7 | Run-specific attestation | **NOT GENERATED** (would claim checks that have not passed) |
| 8 | Zero-request dry run | PASS for planning (above); the gate correctly reads NOT VERIFIED and the kill switch ENGAGED |
| 9 | 50-attempt ceiling enforced, retries off | PASS (tests) |
| 10 | Other local consumers | PASS (none running, tasks disabled) |
| 11 | Kill switch released only for the run and re-engaged | PASS (tests); not disabled manually |

## 6. Owner commands (run ON THE SERVER; nothing here changes the app except the cap line)
Apply the cap (backs up .env, edits only the `UW_MAX_INFLIGHT` line):
```powershell
cd C:\apps\us-stock-reader\backend ; .\vps_uw_cap.ps1 -Status ; .\vps_uw_cap.ps1 -Set 2
powershell -NoProfile -ExecutionPolicy Bypass -File .\vps_uw_token_update.ps1 -Restart
```
Verify, read only, authenticated (unique variable names; the password is never printed). Use the service's local port (the one used for the pilot check):
```powershell
$smPw = ((Select-String -Path "C:\apps\us-stock-reader\backend\.env" -Pattern '^\s*ACCESS_PASSWORD\s*=(.*)$').Matches[0].Groups[1].Value).Trim()
$smHd = @{ Authorization = "Bearer $smPw" }
$smMx = Invoke-RestMethod -Uri "http://127.0.0.1:<PORT>/api/admin/flow-metrics" -Headers $smHd -TimeoutSec 20
"max_inflight = " + $smMx.max_inflight          # must print 2
$smOf = Invoke-RestMethod -Uri "http://127.0.0.1:<PORT>/api/options/flow/AAPL?limit=5" -Headers $smHd -TimeoutSec 30
"options flow status = " + $smOf.status ; "trades = " + @($smOf.trades).Count
Remove-Variable smPw,smHd,smMx,smOf
```
Required result: `max_inflight = 2`, Options Flow `status` OK (or NO_TRADES out of hours) and the page loads. If not, STOP and tell me.

## 7. Execution (only after every PENDING above is closed and you say GO)
1. Owner: copy `shared_key_attestation.PHASE14_TEMPLATE.json` to `C:\Users\initi\SmartMoveRun\shared_key_attestation.json`, fill `attested_by` and `attested_at` (UTC now). Valid 24 h.
2. Dry run (zero requests), new folders:
```powershell
New-Item -ItemType Directory C:\Users\initi\SmartMoveRun\batch2_store | Out-Null ; New-Item C:\Users\initi\SmartMoveRun\batch2_store\KILL | Out-Null
Copy-Item C:\Users\initi\SmartMoveRun\pilot_store_20261102\cal_obs.jsonl C:\Users\initi\SmartMoveRun\batch2_store\    # the saved calendar is the planning baseline; the pilot store itself is never written
python backend\smart_move_collection_runner.py --store-dir C:\Users\initi\SmartMoveRun\batch2_store --state-dir C:\Users\initi\SmartMoveRun\batch2_run --profile batch2 --shared-key-attestation C:\Users\initi\SmartMoveRun\shared_key_attestation.json
```
   Expect `shared_key.status = OWNER_ATTESTED`, ceiling 50, tasks earnings 12 / snapshot_b 14 / news 17.
3. Live (single command, on your GO):
```powershell
$env:SMART_MOVE_COLLECTION_LIVE = "1"
python backend\smart_move_collection_runner.py --store-dir C:\Users\initi\SmartMoveRun\batch2_store --state-dir C:\Users\initi\SmartMoveRun\batch2_run --mode live --arm --authorize-requests 50 --profile batch2 --shared-key-attestation C:\Users\initi\SmartMoveRun\shared_key_attestation.json --require-rotated-from e7fff6ea
```
4. Verify: `attempts.jsonl` (one line per attempt), `collection_state.json`, evidence timestamps; rebuild the quality report into a NEW folder.

## 8. Emergency stop, rollback, cleanup
* Stop now: create `C:\Users\initi\SmartMoveRun\batch2_store\KILL`, or Ctrl+C. The run halts between requests; a restart resumes without repeating a request. A 429 or authentication error ends the run and blocks the rest of the day.
* The kill file is re-engaged automatically after the run (even after an error). It is never disabled manually.
* After the run: delete the attestation file; on the server `.\vps_uw_cap.ps1 -Remove`, restart, re-run the section 6 verification (expect the default cap and Options Flow OK); delete the `.env.pre_cap_*` backups (they contain the token).
* Nothing in the batch writes to production, to the pilot store, or to any existing evidence file.
