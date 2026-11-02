# Phase 14 - supervised batch #2, preparation (2026-11-02, NOT executed, no provider request sent)

## 1. Preflight verdict: **FAIL (not ready). Do not run.**
| Check | Result |
|---|---|
| Collector safety controls (shared-key gate, InfraFetcher choke point, kill switch, ceiling) | PASS. 88 safety tests pass offline; the ceiling counts governor attempts including retries; pilot profile disables retries and paging. |
| Other local consumers of the token | PASS. No other application process is running; the other application's scheduled tasks are all disabled (checked by the gate, 18:08 UTC). |
| Other machines / production using the token | NOT VISIBLE from here (by design). Covered only by the owner's attestation. |
| Global provider limit = 3 in flight | KNOWN from the provider/support note encoded in the gate. Cannot be re-read without spending a request. |
| Production concurrency partition (local 1 + production at most 2) | **FAIL.** Production was restored to its default cap of 3 after the pilot, so 1 + 3 = 4 would be unsafe. Needs the owner to apply `UW_MAX_INFLIGHT=2` and restart (section 4). |
| Production USStockReader healthy | PARTIAL. The public host answers (HTTP 401 "Authentication required" on `/api/health`, i.e. the service is up) but a health reading needs an authenticated call, which I will not make. Owner: open Options Flow after the restart. |
| Provider licensing, permitted use, account quota | **UNVERIFIED.** Cannot be established offline. Owner to confirm that the Unusual Whales plan permits this research collection and that no quota applies (project notes say no daily cap). |
| Market session | **FAIL for now.** 18:08 UTC = in session. Run only after the 16:00 ET close (21:00 UTC) or at a weekend; otherwise the attestation must set `market_hours_ok`. |
| Run-specific attestation | **NOT GENERATED.** It would assert production cap 2 and an owner acknowledgement, which are not true yet. A template is provided (section 5); it is invalid until the owner fills it in. |
| Kill-switch release limited to this run | PASS by design: the runner needs the kill file to exist, releases it only while it works and re-engages it in a `finally`, even on error (tested). |
| 50-request ceiling enforced | PASS (profile `pilot50`: hard maximum 50, `--authorize-requests` must be 50 or less, also the daily cap). |
| Data types the collector can fetch | Calendar, news, Snapshot A/B estimates. **Earnings payloads (exact fiscal quarter) and daily bars are NOT supported by this runner** (they exist only in the older V1 live path, which also goes through the gate). |

## 2. Request allocation (50 attempts, no retries)
| Data type | Requests | Note |
|---|---|---|
| Fresh earnings calendar | up to 4 (estimate: 2 trading days x 2 vendors) | needed first; the saved calendar is 45 h old |
| Analyst estimates, Snapshot B | up to 18 | events inside their 48 h to 24 h window, earliest cutoff first |
| Company news (page 0 only) | up to 28 | for the same events |
| Snapshot A | 0 | never manufactured |
| Earnings payloads (fiscal quarter) | 0 | needs a new job kind (approval needed, section 7) |
| Daily price bars | 0 | same; also nothing is due until the Nov 3 / Nov 4 sessions close |
| Total | at most 50 | ceiling includes retries; retries are disabled |

Which events get Snapshot B depends on the fresh calendar. A run between 21:00 UTC tonight and 11:00 UTC tomorrow reaches events reporting on Nov 4 before the open (cutoff 11:00 UTC Nov 3). Nov 3 after-close events close their window at 21:00 UTC today and cannot be served.
Expected provider usage: 50 requests, one in flight, 2 s spacing, about 2 to 3 minutes. Known cost: none known (flat plan, no per-request fee is documented); not verifiable offline.

## 3. Shared-token concurrency plan
Token limit 3 in flight. Collector: 1 (UW_MAX_INFLIGHT=1, sequential). Production: capped at 2, which leaves production Options Flow two slots. 1 + 2 = 3, never more. The other local application stays stopped. Any 429 stops the run and blocks the rest of the day.

## 4. Required VPS changes (owner, on the server)
1. `vps_uw_cap.ps1 -Status`, then `-Set 2` (backs up .env, touches only the `UW_MAX_INFLIGHT` line).
2. Restart `USStockReader` (`vps_uw_token_update.ps1 -Restart`), then check Options Flow loads.
3. After the run: `vps_uw_cap.ps1 -Remove`, restart, check Options Flow again. Delete the `.env.pre_cap_*` backups (they contain the token).

## 5. Exact supervised procedure (after the owner says GO and sections 4 steps 1-2 are done)
1. Owner: copy `shared_key_attestation.PHASE14_TEMPLATE.json` to `C:\Users\initi\SmartMoveRun\shared_key_attestation.json`, fill in `attested_by` and `attested_at` (UTC, now), keep `designated_collector=localhost`, `production_uw_max_inflight=2`, and set `market_hours_ok` only if running in session. Valid for 24 h.
2. Dry run (zero requests): `python backend\smart_move_collection_runner.py --store-dir <NEW store> --state-dir <NEW state> --profile pilot50` and check `shared_key.status = OWNER_ATTESTED`, ceiling 50.
3. Live (one command, only on the owner's GO):
```powershell
$env:SMART_MOVE_COLLECTION_LIVE = "1"
python backend\smart_move_collection_runner.py --store-dir C:\Users\initi\SmartMoveRun\batch2_store --state-dir C:\Users\initi\SmartMoveRun\batch2_run --mode live --arm --authorize-requests 50 --profile pilot50 --shared-key-attestation C:\Users\initi\SmartMoveRun\shared_key_attestation.json --require-rotated-from e7fff6ea
```
Use NEW store and state folders (never the pilot's). The live store needs its own KILL file first (`New-Item <store>\KILL`).
4. Verify: `attempts.jsonl`, `collection_state.json`, saved evidence timestamps; rebuild the quality report into a NEW folder.

## 6. Rollback and kill switch
- Stop mid-run: create `<store>\KILL` (or Ctrl+C). The run halts between requests and resumes later without repeating a request.
- The kill file is re-engaged automatically after the run, even after an error. Nothing disables it permanently.
- Revoke the attestation after the run by deleting the attestation file (as after the pilot). Remove the production cap and restart (section 4.3).
- Nothing in the batch writes to production or to existing evidence folders.

## 7. Open decisions for the owner
1. GO / no-GO on this plan, and who attests (name) and when.
2. Confirmation of provider licensing and quota.
3. Earnings payloads and daily bars: approve a small offline extension of the runner (new job kinds, same gate and ceiling), or collect them in a later batch. Without earnings payloads the 946 events with an unknown fiscal quarter cannot be paired.
