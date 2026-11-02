# Phase 14 - data source readiness audit (2026-11-02)

Method: configuration names only (no values read or printed), the existing provider clients and collection jobs, and a zero-request dry run of the governed collector. **No provider request was sent.**

## Credentials present on this machine (names only)
`UNUSUAL_WHALES_API_KEY`, `FINNHUB_API_KEY`, `FINVIZ_API_TOKEN` (also `ANTHROPIC_API_KEY`, WhatsApp, database and session settings that are irrelevant here). No paid service was enabled or purchased.

## What each source is wired to provide (from the code, not from a live call)
| Need | Source wired in the code | Status |
|---|---|---|
| Earnings calendar (date, BMO/AMC) | Unusual Whales calendar, Finnhub earnings calendar, Finviz Elite calendar | wired; live use BLOCKED (below) |
| Fiscal-quarter period end | Unusual Whales `/api/earnings/{ticker}` (`ending_fiscal_quarter`) | wired; live use BLOCKED |
| Analyst EPS / revenue estimates (Snapshots A/B) | Unusual Whales `/api/companies/{t}/earnings-estimates` | wired; live use BLOCKED. The endpoint gives a CURRENT snapshot only: history cannot be fetched, it can only be accumulated forward. |
| Daily OHLCV bars | Unusual Whales OHLC (3Y daily, saved for 134 symbols to 2026-10-30) | wired; live use BLOCKED |
| Earnings-related news | Unusual Whales news headlines | wired; live use BLOCKED |
| Actual release timestamp | none of the three providers; SEC EDGAR acceptance time is the only candidate (documented in the release-time feasibility note) | NOT AVAILABLE from configured providers; release times stay nominal |
| Corporate actions | no provider feed; only the split-ratio quarantine heuristic | NOT AVAILABLE |
| Historical analyst-estimate snapshots | none (providers expose current values) | NOT AVAILABLE; Snapshot A/B must be collected going forward, 7 or more days apart |

Rate limits (from the project's own notes): the Unusual Whales token allows 3 simultaneous in-flight requests across every machine and process that uses it (production, this machine, another local application); there is no daily cap. Finnhub and Finviz limits and the licensing terms of all three providers cannot be verified offline and are NOT VERIFIED here.

## Why no collection was started
1. **Shared-key gate: BLOCKED.** The governed collector reports `SHARED KEY SAFETY NOT VERIFIED - no owner attestation file`. The attestation must be created by the owner (designated collector and the production concurrency cap); it was revoked after the pilot and I am not permitted to recreate it.
2. **Kill switch: ENGAGED.** The collector releases it itself only for an armed, attested run. I was told not to disable it.
3. Every live Smart Move fetch goes through one choke point (`InfraFetcher`) that refuses without a valid attestation. I did not bypass it and did not write a second fetch path.
4. The calendar is 44.7 hours old (STALE); a refresh is the first thing a run would need (about 4 requests).

## What the owner must do to unblock a first supervised batch
1. Decide the collector (localhost) and the production cap (`UW_MAX_INFLIGHT=2` per the pilot plan), apply it on the server, and create the attestation file with a fresh timestamp.
2. Approve a request ceiling for the first batch (the pilot profile used 50) and a time window outside US market hours.
3. Confirm that no other local consumer of the token (recorder, signals platform) is running.
Then the existing command in `docs/smart_move/deploy/PILOT_EXECUTION_PLAN_2026-11-02.md` section 5 applies. It does not need new code for calendar, news, estimates and bars.

## Data the first batch can and cannot produce
- Can: a fresh calendar, news pages, estimate Snapshot B for events inside their window, and (if a bars job is added to the profile) daily bars.
- Cannot: Snapshot A for events whose A window already passed (never manufactured); verified outcomes before each event's reaction session closes (the next unclosed sessions are Nov 3 and Nov 4 events, so the earliest verifiable outcomes need bars fetched after those closes); release timestamps; corporate-action feeds.
