# Stage B: shared Unusual Whales concurrency coordination

Replaces `backend\unusualwhales_service.py` and adds `uw_slots.py`, `uw_lease_broker.py`, `uw_lease_broker_routes.py` (the broker router is NOT mounted). The new code is inert until configured.

Provider facts (support): no daily limit, no per-minute limit, three simultaneous REST requests per token across all machines, WebSocket excluded. The monitor's 1,000/day figure is an internal guard, not a provider limit, and this release does not touch it.

Configuration (all optional, all reversible):
* `UW_MACHINE_SLOTS=1` and `UW_SLOT_DIR=<folder>`: every process on this machine shares three slot files. `-ConfigureSlots` writes these two lines to `backend\.env` (Rollback removes exactly those lines).
* `UW_SLOT_WAIT` (seconds, default 30): how long a request waits for a slot before it is answered RATE_LIMITED without being sent.
* `UW_RETRY_AFTER_CAP` (seconds, default 30): the provider's Retry-After is honoured up to this.
* `UW_BROKER_URL`, `UW_BROKER_SECRET`, `UW_BROKER_FAIL_OPEN`: cross-machine leases. Needs the router mounted on ONE host (a later, separate decision); do not set them otherwise.

Install: `install_uw_concurrency.ps1 -Action Preflight|Install|Rollback` (see the header). The service keeps running the old code until it is restarted; `-Restart` restarts it, re-checks health (max_inflight unchanged, Options Flow, Engine20 snapshot route, monitor login) and rolls back by itself on failure.
