# Stage A.5 development progress and readiness (2026-11-04)

Production runs Stage A.4 (`ebf6b3cc`, CACHE_ONLY). Nothing below was pushed, deployed or sent to a provider.

## Test results (exact)
| Suite | Result |
|---|---|
| Backend, `feature/earnings-intelligence` @ 03423fe2 (`pytest`, `SESSION_SECRET=x`) | exit code 0; 833 collected: **821 passed, 0 failed, 0 errors, 12 skipped** (3 of the 12 skips are 'TRDGO-US-DEV institutional dataset not configured'; the reasons for the other 9 were not captured in this run) |
| Backend, A.5 package branch (monitor + intelligence + cache-compat tests) | exit code 0; 111 tests, 0 failed, 0 errors, 0 skipped |
| Frontend UI tests | 28 passed, 0 failed |
| TypeScript `tsc --noEmit` | clean |
| Frontend production build (A.5 branch) | built |
| Smart Money Move stub end-to-end (`e2e_stub.py`) | PASS: 48 stub requests, 0 real network connections |
| Provider-failure scenarios (`e2e_failures.py`: 429 stop, backoff, kill switch, no storm) | PASS |
| Stage B cross-machine lease over real HTTP, 6 processes (`b_lease_real.py`) | PASS: never more than 3 in flight, crash frees lease after TTL, wrong secret 401, broker down fails closed |
| A.4 -> A.5 update, read-only RollbackCheck, recorded rollback, crash recovery (sandbox) | PASS; DB, approval, `.env`, `main.py` byte-identical throughout; backup stays complete after rollback |
| Release audit A.5 vs A.4 | 0 FAIL |

## Smart Money Move against the real Engine20 store (read-only copy)
* 44 evidence records (26 news, 18 estimates), all status OK, no duplicate (kind, symbol), every payload hash re-verified (44/44).
* 50 logged requests, all unique ids: 48 Unusual Whales, 1 Finviz, 1 Finnhub; none newer than 2026-11-02 16:15Z.
* Replay of the Nov 3 run through the current monitor: 819 events; 18 stored calls shown as HISTORICAL_SNAPSHOT (4 BUY, 13 NO_TRADE, 1 SELL) plus 2 NO_VALID_PREDICTION; 295 Nov 3 events NOT_GENERATED (window closed before the run) and 504 Nov 4 events NOT_GENERATED (not selected or not collected). No signal was created for any event without evidence.

## Stage A.5 candidate
* Branch `release/stage-a5` (local, `wt_a2`), head `67e15ffd`; package manifest SHA-256 `d5b8f271c9cc1f2b080c0b49ebd55b2c41ff23512a567f3f021227dabe161017`, 134 files.
* Changes: data-class labels (LIVE/CACHED/STALE/HISTORICAL/UNAVAILABLE) in the monitor, a provider-ledger block on the health route and tab, coverage-gap reason codes, and the `days` echo fix. Monitor package only: 5 backend modules, the panel, one new shared component. Audit allowlist extended by `frontend/src/components/DataClass.tsx` (disclosed).
* **UPDATE 2026-11-04 (later):** the application-side files are now INCLUDED in the A.5 package as an anchored-edit component (see RELEASE_A5.md). Package manifest is now `49da449d595eb33a6d5f0ed504732e056833c64709a934e163d1d6f98c3b3268`, 138 files, branch head `783a3574`. The earlier manifest `d5b8f271...` is superseded.
* The old `days` echo bug is present in A.4 (echo-only, harmless); A.5 fixes it.

## API request estimates
* Current universe: unchanged (top-1,000 liquid). Warm load per day: UNKNOWN until the ledger has run a day.
* Expanding coverage: at least 61 requests per stock per refresh. 100 more stocks = 6,100 per day, 642 stocks = 39,162 per day, i.e. 1.1 to 3.6 hours of the 3-concurrent limit at 0.3 to 1.0 s per request (what-if, latency UNKNOWN). Cost UNKNOWN. Needs owner approval.
* Smart Money Move collection (simulation of the real governor, 363 events, synthetic evidence): per-run cap 150 and daily cap 300 reaches 32% of events; daily 400 reaches 54% on a 06-21h schedule or 85% on 24 h; 500/700 add nothing; a per-run cap of 50 halves coverage. Defaults today: 50 per run, 100 per day.

## Cannot be completed without the owner
* Scheduler registration dry run on this machine: the permission classifier blocked it; run `register_collection_task.ps1` without `-Create` yourself (it only prints). No task was created.
* Live collection, any B1/C/D activation, the monitor approval renewal (expires 2026-11-17T12:12:35Z).
* Provider quotas, prices and licence confirmation (see PROVIDER_REGISTER.md).
* Universe expansion (cost and licence decision).
* A.5 application component deployment (decision on touching `api_routes.py` and the Finnhub/Finviz modules in production).

## Proposed safe deployment sequence (each step needs explicit approval)
1. Push `release/stage-a5` (monitor package only) and run the A.4 -> A.5 Update with the existing Blocks (preflight, backup verified, Update, `state_verify.ps1`). Rollback is the recorded-backup Rollback.
2. Decide on the A.5 application component; if yes, deploy it as its own release with a ledger-only restart window and verify `/api/admin/provider-ledger`.
3. Stage B1 (UW lease broker) with the existing lease test as acceptance.
4. Stage C (frozen engine kit; SHA-256 checked) with `verify_kit_against_engine20.py`.
5. Stage D collector registered in DRY RUN, reviewed for a day, then the owner flips to LIVE with explicit caps.
