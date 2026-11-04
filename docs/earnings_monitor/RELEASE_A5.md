> **SUPERSEDED by RELEASE_A5_1.md. Commit `77a4ef31` has a known defect (the monitor could rebuild the stock universe through the provider screener). Do not install it.**

# Stage A.5 release candidate (local, NOT pushed, NOT deployed)

Base: Stage A.4 `ebf6b3cc` (production). Package: `deploy/earnings_monitor`, manifest SHA-256 anchor `49da449d595eb33a6d5f0ed504732e056833c64709a934e163d1d6f98c3b3268` (138 files). The anchor changes only if any package byte changes; the owner is given it out of band and the updater refuses a package that does not match.

## What is in it
1. Monitor code (5 changed modules: adapters, logic, routes, service, sim) and the rebuilt frontend (monitor panel, shared data-class badge, Options Flow label).
2. **Application component** (`app_component/plan.json` + `new/`): anchored edits to three existing application files and two new modules. It never replaces a whole application file.
   * `backend/api_routes.py` (3 edits): Options Flow answers gain `data_class`; new `GET /api/admin/provider-ledger` (admin only).
   * `backend/finnhub_service.py` (2 edits), `backend/finviz_service.py` (2 edits): request counter at each service's single HTTP door; return values unchanged.
   * new `backend/provider_ledger.py`, new `backend/signal_class.py`.
3. Updater `deploy_earnings_monitor.ps1`: backs up the application files with the rest, verifies the backup byte for byte before any change, writes the backup manifest first, applies the edits, health-checks (ledger endpoint 200 and refused without login, Options Flow carries `data_class`), rolls back automatically on any failure; new read-only action `AppCheck`.

Not touched: `main.py`, `.env`, the database, `approval.json`, `unusualwhales_service.py`, `auth_service.py`, `swr.py`, Engine20, the lease broker, the stock universe, any scheduling or collection setting. The ledger file lives in `backend\.provider_ledger\` (or `PROVIDER_LEDGER_DIR`) and is not removed by a rollback.

## Safety properties (all rehearsed in the sandbox, see A5_READINESS_AND_PROGRESS.md)
* A live file that no longer matches what the edits were written for (anchor not found exactly once, or partly patched) stops the Update **before any change**.
* Line endings (LF or CRLF) are preserved; a rollback restores the original bytes and removes the two new modules.
* Dependencies the new code relies on are checked first (`swr.held_at`, `market_clock`, UW client counters, `require_admin`); a missing one stops the Update.
* Unusual Whales counting: the ledger reads the client's own counter and banks it across restarts; it never records UW calls itself, so nothing is counted twice.

## Owner procedure (after approval only; do not run before the push is approved)
1. Read-only: `.\deploy_earnings_monitor.ps1 -Action AppCheck -Package <A.5 package> -ManifestSha256 <anchor>`. Expect `PATCHABLE` for all five lines. Anything else: stop and send the output (it means the live files differ from A.4).
2. `-Action Update -Package <A.5 package> -ManifestSha256 <anchor>` (the updater makes the backup, applies, restarts, checks, and rolls itself back on failure).
3. `state_verify.ps1` (read-only) and, optionally, `-Action RollbackCheck`.
4. Rollback if wanted: `-Action Rollback -Package <A.5 package>` (uses the recorded backup, no path needed). The recorded backup returns main.py, the monitor, the frontend and the three application files to A.4 byte for byte and removes the two new modules.

## Known limits
* The sandbox is a faithful copy of the A.4 code tree (commit `ebf6b3cc`), not the VPS itself. The `AppCheck` run on the VPS is what proves the live files match.
* A full production restart and live health check were not performed (no restart, no network).
