# Read-only production metrics capture (no provider requests)

Sign in as an admin on the production site, then copy these into the table. Nothing here calls Unusual Whales.

| Metric | Where | Value |
|---|---|---|
| Average latency (ms) | `/api/admin/flow-metrics` -> `avg_latency_ms` | |
| p95 latency (ms) | `/api/admin/flow-metrics` -> `p95_latency_ms` | |
| p99 latency (ms) | same, after the new build only | UNKNOWN until deployed |
| Average queue wait (ms) | `avg_wait_ms` | |
| p95 / p99 queue wait | `p95_wait_ms` / `p99_wait_ms` (new build) | UNKNOWN until deployed |
| HTTP 429 since the process started | `errors_by` -> `"429"` | |
| Concurrency 429s | `concurrency.concurrent_429` (new build) | UNKNOWN until deployed |
| HTTP 404 since the process started | `no_data` | |
| 404s by endpoint and ticker | `no_data_top` (new build) | UNKNOWN until deployed |
| Timeouts | `timeouts` (new build) | UNKNOWN until deployed |
| Blocked requests / paused | `blocked`, `blocked_for_seconds` | |
| Peak simultaneous requests | `concurrency.peak_in_flight` (new build) | UNKNOWN until deployed |
| Token fingerprint | `/api/admin/provider/key` -> `fingerprint` | |
| Provider-side 24 h 429 / 404 | Unusual Whales dashboard "Request health" | 3,617 / 1,997 (2026-10-23) |
| Provider-side busiest endpoints | dashboard "Your busiest endpoints" | quote, unusual, options-volume, greek-exposure, ohlc |

After the new build is live, `/api/uw/health` summarises all of this as OK / DEGRADED / CRITICAL with reasons.

## Release facts to collect (read-only, when access is available)

| Fact | How | Value |
|---|---|---|
| Production commit | on the server: `git -C C:\apps\us-stock-reader rev-parse HEAD` and `git status --porcelain` | UNKNOWN |
| Is it `main` / `origin/main`? | `git rev-parse main origin/main` ON THE SERVER, compare | UNKNOWN |
| Running build's safety state | `GET /safety` on the server (404 = predates the safety work) | UNKNOWN |
| Database engine (production) | `/safety` -> `database_dialect`, or `python backend\entry_lock.py status` -> `dialect` | PostgreSQL (Supabase, transaction pooler) per configuration; confirm on the server |
| Open positions | SQL in docs/release_and_rollback.md section 0 | UNKNOWN |
| Latency / queue / 429 / 404 | the table above | UNKNOWN until admin access |
