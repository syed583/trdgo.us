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

## Verified against the real runner (smart_move_collection_runner.py, profile engine20)
* The wrapper's arguments (`--profile engine20 --store-dir --state-dir --select-top --with-f7 --mode --arm --authorize-requests --daily-limit --shared-key-attestation`) all exist. A dry run through the wrapper against a copy of the 2026-11-03 store sent 0 requests.
* The runner HARD-CODES the limits: at most 50 provider attempts per run (`PILOT_MAX`) and at most 100 per New York day (`DAILY_LIMIT_MAX`). `--authorize-requests`/`--ceiling` above 50 or `--daily-limit` above 100 are refused. A larger budget therefore needs a code change to the runner's constants (a safety guard, not a scoring formula), which is a separate approval, not a flag.
* Live mode also needs the environment variable `SMART_MOVE_COLLECTION_LIVE=1`; the wrapper never sets it.
* The runner chooses stocks by market capitalisation (`select_top`: vendor-confirmed, window open), not by closest cutoff. "Urgency first" would be a change to the runner.
* A stale calendar (older than 5 h) costs 4 requests of the same ceiling before any evidence is fetched; with a stale calendar nothing is selected (seen in the dry run).
* The runner releases its own kill file only for the run and re-engages it afterwards.

## Proposed higher-coverage policy (needs your approval, not applied; the first two rows need a runner change)
| Setting | Current (runner constants) | Proposed | Why |
|---|---|---|---|
| Schedule | none | hourly, every day including weekends (a Monday report's window falls on Saturday and Sunday), trading days | windows open at any hour |
| Per run | 50 | 150 | one run per hour; sequential, 1 in flight |
| Per New York day | 100 | 700 | the planner needed about 690 for a typical day; the provider has no daily cap |
| Concurrency | 1 | 1 (unchanged) | leaves two of the three provider slots to the application |
| Selection | top 20 by market cap | top 60; closest-cutoff-first would be a runner change | limited budget goes where windows close first |
| Hard stop | KILL file | KILL file, plus stop on the first 429 for the rest of the hour | respects the shared 3-slot limit |

Cost per event measured so far: about 2 requests (Snapshot B plus news), 4 with `--with-f7`; the 50-request pilot covered 26 events. At 100 a day that is about 50 events a day against 164 to 280 events a day reporting this week, so roughly 18 to 30% at best; 700 a day would cover about 350, i.e. everything eligible on the busiest days.

## Not decided here
* Whether the provider's terms allow storing and analysing the collected data for this use (owner reports permission; written confirmation still advisable).
* Whether more than one machine will call with the same token (the lease broker exists; not mounted).
