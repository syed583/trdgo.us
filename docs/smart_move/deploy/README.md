# Smart Move scheduling files (not activated)

* `SmartMove_Tick.task.xml` - Windows Task Scheduler, every 30 min, `Enabled=false`.
* `smartmove-tick.service` / `.timer` - VPS (systemd), not installed.

The tick decides from the clock (DST-proof): refresh a stale calendar, collect open windows (closing soonest first), fetch due outcomes, raise missed-window alerts. By default it **plans only** and appends a line to `tick_log.jsonl`.
Live execution requires all of: `--execute`, env `SMART_MOVE_LIVE_COLLECTION=1`, and the file `<run-root>\LIVE_COLLECTION_ENABLED`. Remove any one to stop it. Budgets: 1700/day planning ceiling, ~100/h shared hourly cap, 2 s spacing, stop on 401/429, kill switch re-engaged after each batch.
Known limit: the execution path (full_universe_run in a new folder, seeded from the previous run's store) has been tested with mocks only, never live.
