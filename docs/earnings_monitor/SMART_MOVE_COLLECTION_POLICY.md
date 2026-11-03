# Smart Money Move collection: scheduling and proposed governor policy (PROPOSAL, NOT ACTIVE)

Nothing here is switched on. The wrapper `deploy/smart_move_collector/run_engine20_collection.ps1` is dry-run by default; the scheduled task is created only by the owner; going live needs a separate approval.

## Two different kinds of limit
| Kind | Value | Who sets it |
|---|---|---|
| Provider limit | no daily or per-minute cap; at most 3 simultaneous REST requests per token across all machines; resets 8 pm ET | the provider |
| Internal governor | per run (`--authorize-requests`), per New York day (`--daily-limit`), select-top | the owner; changes are a separate approval |

The runner is sequential (1 in flight), so it uses one of the three provider slots; the application's own warmers and the other local app share the rest.

## How windows are used
The frozen engine (score40-v1) predicts 48 to 24 hours before the report. The monitor reads the engine's own rows (`window` OPEN/CLOSED, `window_cutoff_utc`):
* it alerts (`SMART_MOVE_WINDOW`) when the first open window closes within `sm_window_alert_h` (6 h);
* the hourly collector asks the runner to plan the events whose window is open, most urgent first;
* when an event leaves OPEN with no result, the monitor records `SMART_MOVE_WINDOW_MISSED` once, with the reason (`EVIDENCE_NOT_REFRESHED_BEFORE_CUTOFF`, `EVIDENCE_MISSING_OR_INSUFFICIENT`, `NO_RESULT_BEFORE_CLOSE`) and sends one digest per date.
* the wrapper log (`collection_runs.jsonl`) records every trigger, including each skip and why.

## Current policy (what Engine20 was approved for)
50 requests per run, 100 per day, top 20 events. The planner's 2026-11-03 picture: 363 eligible events, about 690 requests needed, so 100/day covers about 14% of the need per day. Most windows will therefore be missed at this setting; the missed-window record will show it.

## Proposed higher-coverage policy (needs your approval, not applied)
| Setting | Current | Proposed | Why |
|---|---|---|---|
| Schedule | none | hourly 06:00 to 21:30 ET, trading days | windows open at any hour |
| Per run | 50 | 150 | one run per hour fits in the hour at 1 in flight |
| Per New York day | 100 | 700 | covers the planner's ~690 for a typical day; the provider has no daily cap |
| Concurrency | 1 | 1 (unchanged) | leaves two slots for the application |
| Select top | 20 | 60, urgency first (closest cutoff) | most urgent first, so a short budget is spent where windows close first |
| Hard stop | KILL file | KILL file, plus stop on first 429 for the rest of the hour | respects the shared 3-slot limit |

Reasoning: the binding constraint is the shared 3-concurrent limit, not volume. At 1 in flight and about 1 s per request, 700 requests is about 12 minutes of an 11-hour day. Raising the daily figure does not touch the provider limit, but it does need your sign-off and the licensing questions below answered first.

## Not decided here
* Whether the provider's terms allow storing and analysing the collected data for this use (owner reports permission; written confirmation still advisable).
* Whether more than one machine will call with the same token (the lease broker exists; not mounted).
