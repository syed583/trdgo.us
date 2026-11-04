# Stage A.5.1 release candidate (supersedes A.5 `77a4ef31`; installation is disabled until the owner approves it)

Base: Stage A.4 `ebf6b3cc` (production, with the Phase 2A `api_routes.py` build). Package: `deploy/earnings_monitor`, 138 files, manifest SHA-256 anchor
`51dd4c4c8b1dac655982acc2980b428e5e460f54a7e1653513e3d09e64e9b47f`.

## What A.5.1 corrects
A.5 (`77a4ef31`, do NOT install) had a defect in the monitor's new coverage-reason read: `ProdAdapters.warm_universe()` called `universe_service.symbols()`, which calls `get()` and so
`swr.serve(build)`. When the held 1,000-stock universe is missing or older than 24 hours that rebuilds it through the Unusual Whales screener (a handful of paged requests).
A.5.1 reads it with `universe_service.peek()`, which returns the held value (memory, then the shared database tier) and never builds.

Evidence: a regression test (`test_warm_universe_read_never_builds_the_universe_or_calls_a_provider`) and a full-cycle tripwire test run the production adapter through repeated scan,
evidence, recompute, health and alert cycles with a missing, a stale (30 h) and a fresh universe while every provider door, urllib, any non-local socket, `swr.serve/_refresh/_run` and
`universe_service.build` record and raise. A.5 fails them (5 touches per run); A.5.1 records 0. Scope: CACHE_ONLY, the deployed mode. In REFRESH mode providers are reachable only through
`ProviderAccess` with the owner's approval; this release does not enable it.

Everything else is identical to A.5 (the application component, updater, frontend). See RELEASE_A5.md for the component description and safety properties.

## Unusual Whales requests caused by the application's own health checks (read this before approving the installation)
The updater's health checks call the application's Options Flow route for AAPL (`/api/options/flow/AAPL?limit=5`). That route serves a cached answer only while it is younger than its
limit (4 seconds while the market is open, 10 minutes otherwise; the in-process copy is lost on a restart, the shared database copy obeys the same limit). Otherwise the application builds it
from Unusual Whales: up to 3 REST requests per build (market flow alerts, stock flow, tape rows).
* `Update` runs the health check before the change, after the restart, and once more for the new routes: roughly 3 requests outside market hours, up to about 9 during market hours, and
  about 3 more if an automatic rollback runs. This is an upper bound from reading the code, not a measurement.
* The manual `Rollback` mode restarts the service and runs the baseline health check once (up to 3 requests).
* `PreCheck`, `PostCheck` and the standalone `AppCheck` / `RollbackCheck` actions run no health check and make no provider request.
These requests are within the owner's plan (no daily cap, 3 concurrent) and are counted by the new provider ledger, but they are real API requests: the health checks are NOT provider-call-free.
Recommendation: run `Update` outside market hours.

## Owner procedure (read-only first)
`A5_RUN.ps1 -Mode PreCheck` (read only). `-Mode Update` is refused by the script until the owner approves the installation and the script's block is lifted for this commit.
