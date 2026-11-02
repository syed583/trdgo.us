# One Unusual Whales token for localhost and production - rotation, concurrency and resume plan (2026-11-02)

Procedure only. Nothing here has been executed: no token regenerated, no provider request, no production access, no restart. Tokens are never typed into chat, a command line, Git or a log; only length, an 8-hex SHA-256 fingerprint and the last four characters are ever shown.

## 1. Facts (sources in brackets)

* The variable is `UNUSUAL_WHALES_API_KEY` [unusualwhales_service.py:81].
* **Two sources can supply the key and the second one wins in the web application:** the environment/`.env`, and the admin "provider key override" row (`provider.unusualwhales.api_key`) in the settings table, loaded at web-app start [main.py:301, unusualwhales_service.py:277-308]. The Smart Move collector never reads that row; it uses the process variable, else `backend/.env`.
* Production = Windows server, service `USStockReader`, folder `C:\apps\us-stock-reader\backend` (project notes). Localhost = this PC; its `backend/.env` holds the old token (fingerprint `e7fff6ea`) and so does `backend/.env.bak`.
* Localhost's `DATABASE_URL` names the production Supabase project, but `database.py` refuses it unless `APP_ENV=production` and redirects local runs to a development database. So a localhost web app reads a DEVELOPMENT override row (if any), never production's. Not inspected (no database connection was made).
* The plan has no daily or per-minute cap. The only limit is **three simultaneous in-flight requests per token across both environments** [Unusual Whales support, quoted in unusualwhales_service.py].
* Production could not be inspected from this machine. **Production credential state is UNVERIFIED** until the administrator runs `vps_uw_credential_audit.ps1` on the server.

## 2. Expected interruption

The moment the token is regenerated, the old token stops working everywhere: production (all Unusual Whales boards and feeds return authentication errors), localhost, Smart Move, and the stopped US Signals app. Production stays degraded until its `.env` AND any override row hold the new token and the service has been restarted. Do it outside US market hours (after 4 pm ET) and have the server login ready BEFORE regenerating.

## 3. Sequence (every step is a separate approval point)

1. **Before regenerating** - production admin runs `vps_uw_credential_audit.ps1` on the server and sends back the output (fingerprints, last4, override row yes/no, `UW_MAX_INFLIGHT`, `APP_ENV`).
2. Owner regenerates the token on the Unusual Whales site. The old token is now dead.
3. **Localhost first:** `. .\rotate_uw_token.ps1 -Session`, then `python backend\smart_move_secret_check.py --old-fingerprint e7fff6ea`. Required: `source = PROCESS_ENVIRONMENT`, `rotated_from_old = true`, `ready_for_connectivity_test = true`, no conflict note.
4. **Production:** on the server, `.\vps_uw_token_update.ps1 -UpdateEnv -SetMaxInflight 2` (hidden prompt; backs up `.env`; no restart). If the audit showed an override row: `.\vps_uw_token_update.ps1 -ClearOverride` (type YES) - otherwise the old override keeps winning.
5. Re-run `vps_uw_credential_audit.ps1`; compare with localhost: `python backend\smart_move_secret_check.py --old-fingerprint e7fff6ea --production-last4 <last4 shown on the server>` must give `matches_production_last4: true`. The fingerprints of `.env` and the override (if kept) must be identical.
6. **After the owner approves the restart:** `.\vps_uw_token_update.ps1 -Restart` (type YES). Only `USStockReader` is restarted; databases, predictions and evidence are untouched.
7. Verify production feeds from the admin page `/api/admin/provider/key` (masked last four) and the existing boards before anything new is enabled.
8. Delete the old-token copies: `rotate_uw_token.ps1 -RemoveOldBackups` (localhost `.env.bak`) and `.env.pre_rotation_*` on the server.

## 4. Recovery if production fails after the update

* Authentication errors: the token in `.env` or the override row is wrong or stale. Re-run the audit, enter the correct token again with `-UpdateEnv` (restoring the backup would bring back the REVOKED token and does not help), clear the override if it differs, restart.
* Only `UW_MAX_INFLIGHT` or other non-token settings broken: `-Rollback` restores the newest `.env.pre_rotation_*` (then re-apply the token with `-UpdateEnv`).
* Many 429 `concurrent_limit_exceeded`: something else is using the token. Stop every non-production collector (localhost), wait one minute, retry.
* Database circuit breaker or 15-connection errors are separate; the scripts open at most one short database connection.

## 5. Concurrency across the two environments

Local file locks and the in-process gate cannot see the other machine, so two independent limits do NOT add up safely: localhost 1 + production 3 (today's production default) = 4 > 3.

* **Policy A (default, recommended): one designated collector.** Production makes all live provider requests. Localhost uses saved data and dry runs only; any localhost live test happens in an agreed window with production's provider traffic paused. Enforced in code: a live Smart Move run is BLOCKED unless the owner attestation names `designated_collector` equal to the environment it runs in (`APP_ENV=production` = production, otherwise localhost).
* **Policy B (static partition): production `UW_MAX_INFLIGHT=2` + localhost 1 = 3.** Safe only if each environment truly enforces its cap; lowers production's peak from 3 to 2 slots. The attestation for a localhost collector must state `production_uw_max_inflight <= 2` or `production_provider_traffic_paused: true`.
* **Policy C (not built): a shared coordinator** (a small lease service both sides call before every request). Only needed if both must run freely; it is real engineering and a new production dependency.
* In every policy: Smart Move pilot `UW_MAX_INFLIGHT=1`, 2 s spacing, no retry, immediate stop on 429 or authentication error, no overlapping jobs (lock file), the 50-attempt ceiling and the kill switch.

Attestation file (owner-created; shows acknowledgement, not measurement):

```json
{"attested_by": "<name>", "attested_at": "<UTC ISO time>",
 "acknowledges_shared_token_three_slot_limit": true,
 "acknowledges_production_and_other_app_use_not_visible": true,
 "designated_collector": "production",
 "production_provider_traffic_paused": false, "production_uw_max_inflight": 2, "market_hours_ok": false}
```

## 6. Production resume plan (nothing started)

1. Token verified on both sides (step 5) and production restarted with the owner's approval.
2. Confirm existing production feeds before anything new: the quote, flow, options and earnings boards load, the admin page shows the new last four, no authentication errors or 429 in `journalctl`/service logs for 30 minutes.
3. Resume only the services that were authorized before the rotation. Do not deploy development changes: the Smart Move collector in this worktree is local, unpushed and not part of the production release.
4. Production becomes the designated live collector. Recurring Smart Move collection and the 50-request pilot each need their own written approval.
5. Localhost stays available for development, tests and dry runs (no live provider requests under Policy A).
