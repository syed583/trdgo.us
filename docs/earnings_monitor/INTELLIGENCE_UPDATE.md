# Earnings Intelligence update (follows release c0b37a0c)

Everything here ships **OFF / cache-only**. No provider request is made by anything described below unless the owner later signs a refresh approval.

## What changed

| Area | Change |
|---|---|
| Calendar | Rolling **30 days** (was 15). The eight models still score the next **15 days** (their scan caches are keyed `:15`); later events are shown as **OUTSIDE MODEL WINDOW**, never as neutral. The cache-only calendar reads Unusual Whales' per-date responses the application already holds (`uwcal:*`, not older than 12 h) and compares them with Finviz **from its cache only** (`fviz:earn`). A date that was never fetched is a gap, not an invented event. |
| Events | `last_verified` (when two independent vendors were last compared), `verification` CONFIRMED / SINGLE_VENDOR / CONFLICT / UNVERIFIED. A CONFLICT blocks predictions. |
| Scheduler | Two lanes with their own cross-process locks: **heavy** (calendar, eligibility, evidence, news/flow, earnings-day) and **light** (alert delivery, outcome verification, Smart Move planning). A long scan can no longer hold up alerts or outcomes; two scans never overlap. |
| Evidence ledger | New table `evidence_items`. Each item shows how far it got: *collected* (stored) or *verified* (a provider-structured field), which models' own code reads that kind (*relevant*), and which models' latest row carries it (*used*). **News and filings never change a score.** Licensed analyst-revision feeds and transcripts are not connected; none is inferred. |
| Re-scoring | A changed model input (a bias moving by at least 0.05) makes **that event's** models re-read their own cached call. Each stored prediction version carries a `reason`; the change record carries `previous_prediction_id`, `new_prediction_id` and the reason. Nothing is overwritten. |
| Alerts | Added NEW_EARNINGS, MODEL_DISAGREEMENT, NEWS_OR_GUIDANCE, UNUSUAL_OPTIONS_FLOW, OUTCOME_VERIFIED to the existing rules (cooldowns, dedupe, flapping, hourly cap, digests kept). The first calendar population and any scan that adds more than 25 events at once raise **no** "new earnings" alerts. Delivery stays the local outbox file; no external notification exists. |
| Dashboard | Inside the existing Earnings sub-tab: Upcoming Earnings (30 days) · All 9 Models · Smart Money Move · Prediction Changes (previous vs latest, with reason) · News & Options Activity · Model Consensus · Historical Accuracy (BUY and SELL accuracy separately, average real move, verified outcomes, agreement vs outcome, unverifiable count) · Scanner Health. |
| Schema | v3, applied **in place** when the existing store is opened (columns added, nothing dropped or rewritten). |

## Measured provider request cost (Phase 7)

Method: the two lowest-level HTTP functions (`unusualwhales_service.get`, `finnhub_service._get`) were replaced by counters, caches cleared, and the real code paths run offline (`audit_requests.py`). The stubs return **empty OK payloads**, so anything that only fans out when data is present (one request per listed expiry, etc.) is **under-counted**: treat these as lower bounds.

| Path | Requests, cold cache | Second run |
|---|---|---|
| Calendar refresh, 14 days | 22 | 0 |
| Calendar refresh, 30 days | **46** (23 weekdays × premarket + afterhours) | 0 |
| Event evidence refresh (`get_analysis` + `get_summary`) | 141, of which **80 are the 40-day calendar dates shared with the calendar cache** → **61** per event | 33 |
| Outcome verification (`_historical_moves`) | 1 | 0 |
| News headlines, one symbol | 1 | 0 |
| Options tape, one symbol | 1 | 0 |

The previous estimate (**420 requests/day for 20 events**) assumed 15 requests per event refresh and a 20-request calendar. The measured figures give, for 20 reporting events a day, one refresh each, four calendar refreshes and one outcome check each:
`4 × 46 + 20 × 61 + 20 × 2 = 1,444` requests/day, which is **above the monitor's own hard cap of 1,000**. The gate therefore refuses an approval whose `expected_events_per_day` cannot fit (at most ~12 events/day fit under the cap). Options for the owner, none taken automatically: approve a smaller expected number of events, raise the cap deliberately, or refresh only the next-session events. When a budget runs out, every stock that was not refreshed is **listed with the reason** (Scanner Health → `refresh_skipped`), in a stated order (Before-Open reporters first, then alphabetical). Nothing is dropped silently.

Shared with the existing warmers: the same Unusual Whales token, the same global gate and the 3-concurrent-request limit. The monitor takes **one** in-flight slot and waits up to 60 s for it.

## Smart Money Move (score40-v1)

Predictions come only from the frozen engine's own result: `SMART_MOVE_ENGINE_ROOT` + `SMART_MOVE_EVIDENCE_STORE` (engine on a private evidence store, read-only) or a verified Engine20 snapshot. The monitor never scores it and never starts its collection. Without either, the honest label is PENDING or NO VALID PREDICTION.

The frozen-engine installer and its private kit are **not part of this release**: installing the Smart Money Move engine, and collecting its live evidence, are separate approvals. Until then the honest label is NO PREDICTION AVAILABLE (with the reason) or NO VALID PREDICTION, and results from the Engine20 snapshot are labelled HISTORICAL SNAPSHOT.


## Rollback

`deploy_earnings_monitor.ps1 -Action Rollback -BackupDir <the backup the Update printed>` restores the previous code and frontend. The v3 store columns are additive and harmless to the previous code.

## Added after the first review (still local, still off)

| Area | Behaviour |
|---|---|
| Budget plan (LEAN baseline) | `REFRESH_PLAN` (hourly, no request) ranks the events that need evidence (soonest first, Before-Open first, then the event with the fewest model calls, then alphabetical) and funds only what fits: `daily cap - max(planning reserve, what the rest of the application already used today) - calendar reserve - outcome reserve`, divided by 61 per event. Every event that does not fit is listed with the reason (Scanner Health). `EVENT_REFRESH` runs the funded events, a few per job, one at a time through the shared gate, and only when a REFRESH approval allows the `event` purpose (`plan_driven_refresh=False` limits refreshes to the earnings day). |
| Shared cap | The application's own Unusual Whales counter (warmers, Options Flow, monitor) is read, read-only. The existing warmer has its own daily limit of 13,000, so on a normal day the app total can already exceed the monitor's 1,000: in that case the plan funds **zero** refreshes and says so. |
| Look-ahead | Grading uses only prediction versions that were both built and stored before the START of the session's reporting window (BMO: 05:30 ET, AMC: 15:00 ET on the report day). Later versions are shown but never grade. |
| Approval expiry | One alert a day within 3 days of the end; Scanner Health shows the days left. |
| Smart Money Move origin | Each row says HISTORICAL SNAPSHOT or LIVE ENGINE. |
| Dashboard | Upcoming shows "n of 9 models with a fresh call" and evidence freshness; Accuracy shows verified reactions with the cutoff and the events still waiting; Health shows daily usage, the approval end date and the refresh plan. |
