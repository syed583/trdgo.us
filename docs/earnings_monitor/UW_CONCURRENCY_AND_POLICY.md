# Unusual Whales: concurrency architecture, error audit, licensing and a proposed internal request policy

Prepared 2026-11-03. Nothing here is deployed; the new layers are off unless switched on by environment.

## 1. What the provider has confirmed (support, 2026-10-23 and again 2026-11-03)
No daily limit, no per-minute limit, no stated fair-use ceiling. The only limit is **three simultaneous REST requests per token across every machine and process that uses it**. WebSocket messages do not count. Failed requests (404/429) do not count towards it.

## 2. Who can call the token today (audit)
Sources: `docs/uw_token_caller_inventory.md` (2026-10-24), the isolation decision of 2026-10-26, and this session's code search.

| Consumer | Status | In flight |
|---|---|---|
| TRDGO-US production service (page requests, warmers, loops, Options Flow, **the Earnings Monitor**, which runs in the same process) | the only consumer on the VPS (2026-10-26 audit: no other env file, process, task or container holds the token) | one shared in-process cap of 3 (`UW_MAX_INFLIGHT`) |
| us-signals-platform (recorder with 4 workers, engine, scripts) | **stopped**; it held the same token value | was 4 + 1 |
| TRDGO-US on the development machine | default 1 in flight (`UW_MAX_INFLIGHT` off production) | 1 |
| Smart Money Move collector runs (Engine20) | a separate process on the development machine with its own client | up to 3 inside its own process |
| Manual scripts (`data_health_check.py`, audits, probes) | occasional | 1 each |

**Gap found:** the in-process cap of 3 does not cover a second process or machine. Production plus a development server, a collector run or a script can together exceed three. That is the remaining source of `concurrent_limit_exceeded`.

## 3. The coordination architecture (built, tested, OFF by default)
Acquisition order for every REST request: **in-process semaphore -> machine slot -> broker lease -> request**; release in the reverse order, so waiters cannot deadlock.

| Layer | Covers | How | Switch |
|---|---|---|---|
| in-process (existing) | threads in one process | `BoundedSemaphore(3)`, background work limited to one slot and yielding to live requests | always on |
| machine slots (`uw_slots.py`) | every process on one machine | three slot files held with OS file locks; the kernel frees a slot if its holder dies; jittered polling; waits up to `UW_SLOT_WAIT` s (30) | `UW_MACHINE_SLOTS=1` |
| lease broker (`uw_lease_broker.py`) | every machine | at most three leases, each with a time-to-live; secret header; the client **fails closed** if the broker is unreachable (`UW_BROKER_FAIL_OPEN=1` is an explicit owner choice) | `UW_BROKER_URL` + `UW_BROKER_SECRET` |

A refused slot is answered as a clean `RATE_LIMITED` result and **no request is sent**. The router (`uw_lease_broker_routes.py`) is not mounted anywhere. Where the broker runs (one host every machine can reach, most naturally the production VPS) and its secret are owner decisions.

Verified by tests: nine real processes contend for three slots and never more than three hold at once (and three do); a killed holder frees its slots; ten requests from "two machines" through the broker peak at three; the lease table expires dead clients; the broker refuses a missing or wrong secret; a down broker fails closed.

Stated limits: the broker's table is in memory, so a broker restart forgets its leases for up to one TTL (45 s); a lease shorter than a slow request would let it outlive its lease (the TTL exceeds the client timeout); machines that never ask the broker are not covered.

## 4. Retries and backoff
Existing, kept: full-jitter exponential backoff (base 0.35 s, cap 4 s), bounded retries (foreground 2, background 3), a concurrency 429 does not pause the application, a real rate 429 pauses with the provider's `Retry-After`.
Changed: `Retry-After` was capped at 5 s inside the retry loop; it is now honoured up to `UW_RETRY_AFTER_CAP` (default 30 s). Added: one structured warning per (code, endpoint) per 30 s (code, endpoint, attempt, Retry-After, configured in-flight) so a 429 storm is visible without flooding the log.

## 5. The 404 and 429 errors
* **404:** every Unusual Whales path the code calls (79 distinct patterns) was checked against the committed OpenAPI spec; all match. The 25 patterns that do not match are this application's own routes plus one dynamically built prefix (`/api/market/`). So the 404s are not wrong paths: they are the provider saying "no data" for a symbol and endpoint (thin tickers: dividends, insiders, etc.). They are handled as `NO_DATA`, never retried, counted per endpoint (`errors_by_path`), and the provider does not count them.
* **429:** the October measurement (3,617 in 24 h) traced to an in-process cap of 8 plus a global pause on any 429 and a second application on the same token. The cap is now 3, the global pause is gone for concurrency 429s, and the second application is stopped. What remains possible is the cross-process overlap in section 2, which section 3 closes once enabled.
* **Not available to me:** current production counts. They are in the application's own counters (errors by status and by endpoint); after deployment the Scanner Health usage and the admin Workflow page show them.

## 6. Streaming (WebSocket)
There is **no** Unusual Whales WebSocket client in either codebase (the other application's WebSocket serves its own dashboard). The provider documents `wss://api.unusualwhales.com/socket?token=...`, channels `news` and `flow-alerts` among others, join messages, **no replay or resume**, and **one machine per token** (several machines streaming one token drop each other's connections). `uw_stream.py` is a ready ingest for `news` and `flow-alerts` into a bounded local spool that the monitor already merges as evidence (news stays unverified text and never changes a score). It needs the `websockets` package (not installed on the server), the owner's approval, and exactly one streaming host. It is not started anywhere.

## 7. Licensing (independently checked, public pages only)
* The public API page: the API is "strictly for personal use"; redistribution is not allowed and breach can end the account.
* **Storing, caching or archiving the data is not mentioned** on the public pages; derived data and display are not addressed either; the Terms page could not be read in full here (it renders in the browser).
* An enterprise route exists with its own written agreement.
So the permission to store data for research is **not verifiable from public text**. The owner reported permission on 2026-11-02; get it **in writing from support** (storage, derived analytics, and use of the stored snapshots in Smart Money Move research). Until then keep the stored data private (as it is), do not display raw provider data to other users, and do not publish it.

## 8. The 1,000-requests-a-day figure and a proposed policy
**Origin:** it is the Earnings Monitor's own `HARD_DAILY_CAP`, written by me as a runaway-loop guard and a conservative allowance for the unknown fair-use ceiling. It is **not** a provider limit, and it covered only the monitor's own requests (the application's other traffic was never inside it). Nothing changes until you approve.

**Proposed internal policy (replaces a single daily number with layered guards):**

| Guard | Proposal |
|---|---|
| Provider concurrency | at most 3 in flight across all processes and machines (section 3); the monitor holds at most 1 and yields to live trading |
| Per-purpose ceilings (monitor) | calendar 400, event evidence 3,000, outcomes 400, news and flow 1,500 per day (about 5,300 in total) |
| Runaway guard | hard stop at 8,000 monitor requests/day, and an alert at 80% of any ceiling |
| Anomaly breaker | pause the monitor and alert if its requests in an hour exceed 3x its trailing 7-day hourly median, or if concurrency 429s exceed 20 in 10 minutes |
| Per-run ceiling | at most 200 event refreshes per scan, spaced by the shared gate |
| Priority | live trading and pages, then the monitor, then the cache warmer |
| Always | the kill switch and the owner's expiring approval (<= 30 days) |

For the measured profiles (lean about 850/day, standard about 1,800/day) these ceilings leave headroom of 3x and more. This needs your decision; the code keeps the current cap until you change it.
