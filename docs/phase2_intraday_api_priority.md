# Phase 2 — Intraday API priority, shared budget, fresh data for the six AI boards

Branch `intraday-data-safety` (Phase 1: `a1a236b`). **Not deployed.**

## What it does

| Piece | File | Role |
|---|---|---|
| Priority classes + fair gate | `uw_priority.py` | 1 live engine · 2 essential data & the six boards · 3 dashboards · 4 warmer. Bounded queues, per-class max wait, per-class in-flight caps, aging (nothing essential starves), labels travel into worker threads. |
| Shared budget controller | `uw_budget.py` | One accounting for every request: per module / per priority group, failed calls, 429s, timeouts, busy refusals. Verifies the quota; decides who may spend. |
| Request reuse | `unusualwhales_service.get`, `uw_freshness.py` | Identical requests from any module share one provider call (window by kind of data); an identical request already in flight is waited for, not repeated. |
| Fresh intraday feed | `intraday_feed.py` | Refreshes ONLY the time-sensitive signals, once per symbol for all six models, with the same fetchers/signal functions as the directional score. Budget-paced, interesting names first. |
| Per-type freshness + safety gate | `intraday_safety.py` | Each input is judged by its own limit; critical groups (options flow, daily-bar price action, live price) must be fresh or the name is WAIT / LOW DATA. |
| Warmer | `local_warmer.py` | Runs at priority 4 only when the controller permits; a build during which any request was refused is discarded. |
| Dashboard health | `intraday_safety.health_block`, `GrokIntradayPage.tsx` | LIVE / DELAYED / STALE / MISSING, Dubai-time stamps, API health, missing factors, timeframe, entry eligibility. |

## Admission rules (per request)

`remaining = allowance − used`.

* P1 live: allowed while `remaining > 0`, **and** below its HARD limits (`live_hard_cap` per provider day, `live_hourly_cap` per clock hour). Past a limit the boards say `WAIT / API UNAVAILABLE`.
* P2 essential: `remaining > emergency_reserve` and its own `essential_budget`.
* P3 dashboard: must leave `reserve + intraday budget not yet spent`; own `other_budget`; 429 cool-down.
* P4 warmer: as P3, plus **quota VERIFIED**, `warmer_budget`, `warmer_hourly_cap`, not manually paused.

"Used" comes from, in order: provider daily headers → a dashboard reading you record + calls since → local count only (UNVERIFIED: the warmer stays paused, live trading is never blocked).

## Configuration (`tab_settings.UW_BUDGET`) — numbers are settings, not measurements

| Key | Default | Meaning |
|---|---|---|
| `daily_limit` | 1000000 | a runaway ceiling (the plan has unlimited daily requests) |
| `emergency_reserve` | 1000 | last calls belong to the live engine only |
| `intraday_budget` | 9000 | P1+P2 together |
| `essential_budget` | 4500 | P2 alone (the rest of intraday is the live engine's) |
| `feed_hourly_cap` | 700 | the boards' shared feed per clock hour |
| `live_hard_cap` | 5500 | P1 hard limit per provider day = (`intraday_budget` - `essential_budget`) + `emergency_reserve` |
| `live_hourly_cap` | 1100 | P1 per clock hour (a runaway scan loop cannot burn the day in one hour) |
| `candles15_hourly_cap` | 160 | the 15-minute candle refresh per clock hour |
| `other_budget` | 2000 | P3 |
| `warmer_budget` / `warmer_hourly_cap` | 3000 / 400 | P4 |
| `reset_hour_et` | 20 | provider day resets 8 pm US Eastern |

Freshness by data type: `tab_settings.INTRADAY_FRESHNESS` (live, max seconds), signal→type map `SIGNAL_DATA_TYPE`, request reuse windows `UW_MEMO_TTL_S`.

## Operating it

```
python uw_budget_cli.py usage 8123      # record today's count from the Unusual Whales dashboard
python uw_budget_cli.py pause-warmer    # / resume-warmer
python uw_budget_cli.py status
GET /api/uw/budget                       # live: usage per module/group, 429s, queues, reuse, warmer gate
GET /api/warm/progress                   # warmer progress + why it is paused
```

The running service picks up CLI readings within 30 s. A reading belongs to ONE provider day; after the 8 pm ET reset the quota is UNVERIFIED again and the warmer pauses until a new reading (or provider headers) arrives.

## Rollback

All changes live on the branch. To revert on the server: `git checkout <previous commit>` (or `git revert` the Phase 2 commit) and restart the service. No data migration; the only new persisted keys are cache entries (`uwbudget:*`, `intrafeed:*`), safe to ignore.

## 15-minute candle confirmation (development, not deployed)

`intraday_15m.py` reads the provider's native `/ohlc/15m` candles (one request per symbol per CLOSED candle), validates every candle (finite positive prices, high/low consistent with open/close, volume present, 15-minute aligned, regular hours, no duplicates, no hole in today's newest six) and reads direction from three votes on CLOSED candles only: price vs session VWAP, EMA9 vs EMA21, RSI14 (>=52 bullish, <=48 bearish). Two agreeing votes with none against decide a direction.

* It never changes a score. A BUY / SELL stands only when the 15-minute reading is CONFIRMED; CONFLICT, MIXED, STALE (newest closed candle ended more than 20 minutes ago), MISSING, INVALID and WARMUP (before the first 09:45 ET close) all turn it into `WAIT`.
* It gates the six boards (`intraday_safety.gate`) and a NEW entry of the live engine (`intraday_signal_v2._maybe_open`). Exits are untouched. It applies in the regular session only.
* `tab_settings.INTRADAY_15M` holds the thresholds; `require_confirmation: False` shows the reading without gating (not recommended).
* Cost: at most 26 requests per symbol per session (~990 for 38 names).

## Tiered live scanning

`intraday_tiers.py` ranks the names by how far they lean from neutral and gives each a rescore period: open positions and pending confirmations are "hot" (3 min); the budget then buys as many "warm" names (5 min, inside the 6-minute engine-score limit, so RELIABLE) as it can, strongest first; the rest are "cold" (15 min, stretched to at most 30 when the budget cannot afford 15). All names stay on the boards; a name whose live score ages past its limit shows WAIT. The split is computed from the live engine's remaining hard cap and its measured cost per rescore. The sizing is visible in the board payload under `tiers`.

## Feed split: each kind of data on its own schedule (development, not deployed)

`intraday_feed.compute_fast` now treats four groups separately, each with its own timestamp (`group_ts` in the stored record) and the limit of its own data type (`INTRADAY_FRESHNESS`):

| Group | Signals | Refetched |
|---|---|---|
| flow (options_flow) | options flow, unusual activity, volume/put-call, flow by expiry | every cycle (~every 7 min per name) |
| price (price_action_daily) | EMA trend, RSI, price action | every cycle (daily history is static; the last candle is the live quote) |
| positioning (options_positioning) | key levels, OI positioning, implied volatility, expected move, disparity | when the held copy reaches 70% of its 30-minute live limit (~21 min) |
| daily_oi | daily OI change | when the held copy reaches 70% of 6 h |

* A signal is judged by the age of ITS data: `age = now - group_ts[group]`. A slow part that cannot be refetched keeps its old timestamp and ages out (dropped when past its maximum); it is never re-stamped as fresh.
* `uw.cap_cache_age(seconds)` (context-scoped) stops the client's caches serving anything older than the allowance used in the stamp (flow/price 60 s, slow 120 s), so the timestamp is true. The 1-hour IV-history cache is dropped when older than the positioning limit.
* A critical group (options flow; price action) with no fresh member still makes the name WAIT. Scores and weights are untouched. `INTRADAY_FEED["split"] = False` restores the single-bundle behaviour.

## Request consolidation (development, not deployed)

* One stock's unusual tape is asked for with limits 60 (Disparity), 100 (live engine) and 200 (flow panels). `unusual_activity()` now fetches ONE widest request (limit 200) and cuts it to size, so the engine and the feed share it inside the existing 60-second tape window (no freshness window was widened). The request memo also serves a narrower `limit` from a wider answer for `/api/option-activity/unusual` and `/api/stock/*/options-volume` (rows are the same ordered list, cut shorter).
* `cap_cache_age()` no longer applies to daily candles (`/ohlc/1d`, `/ohlc/1w`). Helpers called inside the feed's capped block (`get_quote` -> 5-day bars, realized move) were refetching a year of daily history every cycle. The live candle still comes from the quote, which stays capped.
* Measured on one identical mocked workload (9 names, one hour: engine every 5 min, feed every 7 min, 15-minute candles each closed candle): 1,214 -> 1,043 requests (-14%).

## The provider's real limit: THREE simultaneous requests per token (development, not deployed)

Unusual Whales support (2026-10-23): no per-minute cap, no daily cap, no fair-use ceiling on this plan. The only limit is **3 requests in flight at once per token, across every machine and process** (more -> 429 `concurrent_limit_exceeded`). The old client allowed 8 in flight and paused the whole app 5 s on any 429, which is where the ~3,600 daily 429s came from.

* **Cap of 3.** `MAX_INFLIGHT` is 3 in production, **1 on any other machine** (a dev server shares production's three); `UW_MAX_INFLIGHT` overrides, never above 3.
* **Per process:** the priority gate (3 slots; dashboards + warmer together at most 1 slot while the market is open, 2 outside it; live and essential may use all 3).
* **Per machine:** `uw_slots.py` - one OS lock file per slot, so the service, the CLI, scripts and a dev server on one machine share the same three. The OS frees a lock when its holder dies. If the lock directory is unusable it fails open to the in-process cap.
* **Across machines:** cannot be enforced exactly - the only shared store is the production database, which a development machine must never touch. Protection instead: the 1-slot default off production, and an **adaptive brake**: every `concurrent_limit_exceeded` takes a slot away from the gate for a minute (never below one), so a second machine's usage is absorbed. A shared lease in the production DB remains an option for several PRODUCTION processes/servers.
* **429 handling:** `concurrent_limit_exceeded` is retried with jittered exponential backoff (0.4 s base, doubling, capped at 4 s, `Retry-After` as a floor) OUTSIDE the slot, with no app-wide pause; priority 1-2 get 4 tries, dashboards 3, the warmer 2. A genuine rate 429 keeps the old one-retry-then-pause behaviour. The shared budget cools dashboards 15 s / warmer 60 s after a concurrency 429 (2 min / 15 min after a rate 429).
* **Market hours:** while the US session is open dashboards are limited to `other_session_hourly_cap` (6,000) an hour and the warmer to `warmer_hourly_cap` (400); outside it the warmer may use `warmer_offsession_hourly_cap` (6,000).
* **404s:** `/api/admin/flow-metrics` now lists the busiest 404s by endpoint and ticker (`no_data_top`) and the concurrency picture (`concurrency`). 404s and 429s do not count against the plan.
* **Budgets are safety rails, not quotas** (`tab_settings.UW_BUDGET`): `daily_limit` 1,000,000; `live_hard_cap` 30,000; `essential_budget` 15,000; `other_budget` 100,000; `warmer_budget` 60,000. Measured need for all 38 names is ~22,000 a day.

## No daily quota: what protects the API instead (development, not deployed)

`UW_BUDGET["plan_unlimited"]` (default True) retires the daily-quota logic: the quota state is "UNLIMITED", the warmer needs no dashboard reading, `uw_budget_cli.py usage` is informational. Set it False for a plan that does have a cap and the old verification returns. What replaces it:

* **Concurrency:** 3 in flight per process and per machine (`uw_slots`); the adaptive brake lowers the gate on `concurrent_limit_exceeded`.
* **Priority scheduling and bounded queues:** priorities 1-4, queue limits and per-class maximum waits.
* **Adaptive throttling:** background work (dashboards, warmer) stands aside (`YIELD_TO_LIVE_QUEUE`) while live / essential requests wait more than `yield_wait_s` (2 s, p95 over the last minute).
* **Timeouts:** 10 s on the wire for live / essential, 20 s for background.
* **Retry-After and backoff:** `Retry-After` is a floor; jittered exponential backoff outside the slot.
* **Runaway protection:** pools are rails (daily 1,000,000; live 30,000; essential 15,000; other 100,000; warmer 60,000), hourly caps, `module_hourly_runaway` (12,000 an hour per dashboard / warmer module) and the market-hours caps.
* **Squeezed production:** if production is limited to 2 or fewer connections because the token is shared (`UW_MAX_INFLIGHT=2`), background work gets NO connection while the market is open and all but one outside it.
* **Health:** `GET /api/uw/health` -> OK / DEGRADED / CRITICAL with reasons, latency avg / p95 / p99, queue waits, 429s (and concurrency 429s), 404s, timeouts, blocked state. It makes no provider request.

## Other changes in this round

* Intraday candle cache: 5-minute candles are held 90 s (was 300 s), 15-minute 20 s, so a "live" score can no longer rest on five-minute-old bars. The live engine also judges the newest 5-minute candle by its own start time.
* Request memo: a wider `limit` already held answers a narrower one for `/api/option-activity/unusual` (the engine and the feed asked for limits 100, 60 and 200).
* Removed the unused `INTRADAY_SAFETY["max_signal_age_s"]` and `intraday_safety.max_signal_age` (nothing referenced them; per-signal limits live in `INTRADAY_FRESHNESS`).

## Not built

* Multi-timeframe SCORING. The 15-minute candles confirm; they do not add points to any model.
