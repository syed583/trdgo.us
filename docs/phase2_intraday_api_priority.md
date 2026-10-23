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

* P1 live: allowed while `remaining > 0`.
* P2 essential: `remaining > emergency_reserve` and its own `essential_budget`.
* P3 dashboard: must leave `reserve + intraday budget not yet spent`; own `other_budget`; 429 cool-down.
* P4 warmer: as P3, plus **quota VERIFIED**, `warmer_budget`, `warmer_hourly_cap`, not manually paused.

"Used" comes from, in order: provider daily headers → a dashboard reading you record + calls since → local count only (UNVERIFIED: the warmer stays paused, live trading is never blocked).

## Configuration (`tab_settings.UW_BUDGET`) — numbers are settings, not measurements

| Key | Default | Meaning |
|---|---|---|
| `daily_limit` | 15000 | the plan allowance (verify on the dashboard) |
| `emergency_reserve` | 1000 | last calls belong to the live engine only |
| `intraday_budget` | 9000 | P1+P2 together |
| `essential_budget` | 4500 | P2 alone (the rest of intraday is the live engine's) |
| `feed_hourly_cap` | 700 | the boards' shared feed per clock hour |
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

## Not built

* There is **no 15-minute candle analysis engine** in TRDGO-US (only 5-minute bars, daily bars and time rules). Building one is a separate feature.
