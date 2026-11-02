# Smart Move supervised pilot - future execution plan (prepared 2026-11-02, NOT executed, NOT authorized)

No request to Unusual Whales has been made for this plan. Production is untouched. Localhost live collection is blocked by the shared-key gate until the owner attests otherwise.

## 1. Facts that shape the plan

* One token, one provider limit: three simultaneous in-flight requests, shared by production and localhost. Production's default is 3 (`UW_MAX_INFLIGHT` unset), so any request from another machine can be the 4th.
* Local slot locks (`uw_slots`) only coordinate processes that use the SAME lock folder. The folder defaults to the per-user temp folder, so a Windows service and an administrator's PowerShell can have DIFFERENT folders unless `UW_SLOT_DIR` is set to one fixed path for both. The slot code also fails open if the folder is unusable. (Tested: same folder = one shared pool of three; different folders = independent pools.)
* Smart Move code is local and unpushed; it is not part of the production release. A pilot on the production machine would first need an approved, tested deployment of the collector files.
* Every live Smart Move script, route and runner builds its client through `InfraFetcher`, which now refuses to be constructed without a valid owner attestation naming THIS environment as the designated collector.

## 2. Options (owner decision)

| Option | Where | How production is protected | Cost / impact |
|---|---|---|---|
| A | **Production machine**, weekend | Same machine, same fixed `UW_SLOT_DIR` for the service and the pilot; pilot requests are background priority and wait for a free slot | Needs an approved deployment of the collector files (not the app) and a one-time production restart to set `UW_SLOT_DIR`. Strongest protection; most preparation. |
| B (recommended for the first pilot) | **Localhost**, weekend (US market closed) | Production temporarily capped at `UW_MAX_INFLIGHT=2` so 1 + 2 = 3 can never be exceeded; restored to default afterwards | Two short production restarts (seconds each) and production runs at 2 slots during the window. Attestation: `designated_collector=localhost`, `production_uw_max_inflight=2`. |
| C | Localhost, weekend, production unchanged | Not protected: production can hold all three slots; a pilot request can be the 4th and cause a 429 for production, or production can cause a 429 that stops the pilot | Needs a deliberate owner decision to accept residual risk and a code change to allow it; the current gate refuses it. Not recommended. |

## 3. Pilot procedure (Option B), each step needs explicit approval

1. Choose a Saturday or Sunday; US market closed.
2. Owner: production `.env` gets `UW_MAX_INFLIGHT=2`; restart `USStockReader` (planned, seconds). Verify the Options Flow page loads.
3. Owner creates the attestation file (`designated_collector=localhost`, `production_uw_max_inflight=2`, fresh timestamp, both acknowledgements true).
4. Dry run first: `smart_move_collection_runner.py --profile pilot50` (zero requests): shows calendar state, task counts, shared-key status OWNER_ATTESTED, token fingerprint 68e6d2fd.
5. Live pilot (single command, see section 5). Hard limits: 50 provider attempts including retries, no retries, one request in flight (`UW_MAX_INFLIGHT=1`), 2 s spacing, calendar at most 14, Snapshot B at most 18, no Snapshot A, news page 0 only with the remainder. About 2 to 3 minutes.
6. Stop conditions (automatic): HTTP 429 or authentication error stops the run and blocks the rest of the day; ceiling reached; kill switch; any calendar refresh that cannot freshen the calendar.
7. Owner: restore production (remove `UW_MAX_INFLIGHT`), restart, verify Options Flow.
8. Verify evidence: `attempts.jsonl` (one line per attempt incl. failures), `collection_state.json` (job status, requests, done tasks), the saved evidence records (retrieval time on each; news with publication time), and the event-status report. Nothing is scored from it, no prediction is changed, and no accuracy is claimed.

## 4. How the kill switch and stop work

* The live run needs an existing kill-switch file, releases it only while it works, and re-engages it afterwards even if the run raises an error.
* To stop mid-run: create the kill-switch file (or press Ctrl+C); the run halts between requests. A restart resumes from the saved state without repeating any request.
* Production is never started, stopped or reconfigured by the runner.

## 5. Command (for later; do not run now)

```powershell
$env:SMART_MOVE_COLLECTION_LIVE = "1"
python backend\smart_move_collection_runner.py --store-dir <live store> --state-dir <state folder> --mode live --arm --authorize-requests 50 --profile pilot50 --shared-key-attestation C:\Users\initi\SmartMoveRun\shared_key_attestation.json --require-rotated-from e7fff6ea
```

## 6. Limits of this plan

* The 14-day calendar horizon is unverified; the pilot uses the 3-day calendar.
* Cross-machine concurrency is protected only by caps that each side must honour; nothing observes the other side's requests.
* No real post-earnings outcomes exist yet, so nothing here supports a prediction-accuracy claim.

## 7. Additions (Phase 11)

* The obsolete scheduled task `SmartMove_OneTime_20261031_1705ET` (it ran an old copy of `scheduled_run.py` from `SmartMoveRun\code`) was DISABLED on 2026-11-02 after confirming it was not running; its configuration was exported first (`SmartMoveRun	ask_backups\..._before_disable_*.xml`). Rollback: `Register-ScheduledTask -Xml (Get-Content <that file> -Raw) -TaskName SmartMove_OneTime_20261031_1705ET -Force`, then enable it. Nothing was run or deleted.
* **The ordinary localhost development backend is NOT covered by the Smart Move gate.** At start-up it launches background warmers (unless `DISABLE_WARMERS` is set) that call Unusual Whales with the shared token (one request at a time on a non-production machine). Do not run it during a pilot, or start it with `DISABLE_WARMERS=1`, because its requests would add to the shared three-request limit and the pilot's count would not include them.
* Local lock folders do not coordinate across machines or across Windows accounts: only a common `UW_SLOT_DIR` on the SAME machine does, and nothing coordinates localhost with production.

## 8. Correction (Phase 12E): what production actually runs

Sections 1 to 7 were written from the NEWER local code. Production runs commit `38ca61d` (deployed 2026-10-28), which does not contain `uw_slots.py`, `uw_priority.py`, `uw_budget.py`, `uw_health.py` or the `/api/uw/health` route (they arrived in `446d305c`, not deployed). Consequences:

* `https://tradgo.us/api/uw/health` shows the frontend's "Page not found" because the running backend has no such route; the host and proxy are correct.
* In the deployed build `UW_MAX_INFLIGHT` is read after `load_dotenv()` and builds ONE in-process `threading.BoundedSemaphore(MAX_INFLIGHT)`, never above 3. There is no machine-wide slot lock, no priority class cap, no adaptive brake and no slot-lock folder; `this_process_slots`, `effective_slots`, `machine_slot_dir`, `failopen` and `peak_in_flight` do not exist in production.
* The only runtime read-out is `max_inflight` in `GET /api/admin/flow-metrics` (admin role required).
* The only other direct caller of the provider in that build is the manual script `backend/data_health_check.py`, which is uncapped: never run it during the pilot.
