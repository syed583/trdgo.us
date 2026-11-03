# Earnings Monitor: what it is, how to turn it on, how to stop it

The monitor ships OFF. Installing the release adds a read-only API, a sub-tab in the Earnings workspace (Earnings Trade landing -> "Earnings Monitor") and a dormant scheduler. Nothing is scheduled and no provider request is made until the owner does ALL of the following.

## Switching it on (owner, separately authorised)
1. `EARNINGS_MONITOR_ENABLED=1` in the server `backend\.env`, then restart USStockReader (the scheduler thread only starts when this is set at boot).
2. An approval file `em_data\approval.json` (folder `EARNINGS_MONITOR_DIR`, default `<app>\em_data`). The gate re-reads it every tick, so deleting it, letting it expire, or creating `em_data\KILL` stops the next job at once.

Start with cache-only (no provider request of any kind):
```json
{"approved_by": "<name>", "approved_at": "<UTC now>", "expires_at": "<at most 30 days later>", "mode": "cache_only", "scheduling_approved": true}
```
Refresh mode (budgeted provider requests, one at a time, behind a circuit breaker). Each purpose is approved separately:
```json
{"approved_by": "<name>", "approved_at": "<UTC now>", "expires_at": "<at most 30 days later>", "mode": "refresh", "scheduling_approved": true,
 "provider_authorization": true, "kill_switch_acknowledged": true, "max_inflight": 1,
 "refresh_calendar": true, "refresh_event_evidence": true, "verify_outcomes": true,
 "expected_events_per_day": 20, "daily_request_budget": 450}
```
The budget must cover the worst-case estimate for the approved purposes (calendar 80/day; event evidence 15 per event per day, ASSUMED and to be measured; outcomes 2 per event). 20 events/day: 420. Hard cap 1000.

## Smart Move score40-v1
The monitor never scores. A Smart Move call appears when (a) the frozen engine and a private evidence store are installed (`SMART_MOVE_ENGINE_ROOT`, `SMART_MOVE_EVIDENCE_STORE`) and the engine finds fresh evidence, or (b) a verified snapshot exists (`SMART_MOVE_ENGINE20_DIR`). Collecting the evidence is the separately attested collector (profile engine20, 50-attempt cap); the monitor only records what it would need (`smart_move:plan`) and pauses its own provider calls while `em_data\collector.active` exists.

## Stopping and rolling back
- Stop scanning now: create `em_data\KILL` (or delete `approval.json`).
- Remove the release: `deploy_earnings_monitor.ps1 -Action Rollback -BackupDir <the _backup_earnings_monitor_* folder>`; the history in `em_data\monitor.sqlite` is kept.
