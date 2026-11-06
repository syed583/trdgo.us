# Intraday workflow v2 (local, branch feature/intraday-session-workflow, NOT deployed)

Switch: `tab_settings.INTRADAY_WORKFLOW["mode"]` — default `"legacy"` (production behaviour unchanged). `"v2"` activates everything below.

## What changed
- `market_calendar.py`: PREMARKET 04:00–09:30, REGULAR 09:30–16:00 (13:00 early close), holidays/weekends/DST; entries only before the close.
- `intraday_execution.py` (pure rules): per-parameter classification (AVAILABLE / NEUTRAL / MISSING / STALE / UNIMPLEMENTED / NOT_APPLICABLE / UNAVAILABLE_PREMARKET), eligible-parameter coverage, per-source freshness (`WAIT - STALE DATA`, `WAIT - INSUFFICIENT DATA`), quote and liquidity validation, closed-candle 5m/15m confirmation, persistence (distinct evidence only), risk limits.
- `intraday_workflow.py` (orchestrator): session-keyed signal events + evidence snapshots, entry stages with rejection codes, exit management, MARKET_CLOSE at the official close (never fabricates a price: reliable quote within grace, else final regular 5m candle, else PENDING_RECON + data exception).
- Models: new nullable columns on `demo_trades`, tables `intraday_snapshots`, `intraday_signal_events`, `intraday_data_exceptions`; unique per strategy/symbol/day/**session** and one OPEN per strategy/symbol. Migration is additive and idempotent; history untouched.
- Scoring, weights, BUY>=55 / SELL<=45, strategy modules: unchanged (guarded by tests, `git diff` guard vs 277f30f).

## Proposed thresholds (INTRADAY_EXEC) — PROPOSED, not approved for production
Values live in `tab_settings.INTRADAY_EXEC`; none are activated on production without approval.

## Tests
1161 passed, 4 skipped (baseline 1094). New: workflow scenarios A–T, regular-session, rule and migration tests.

## Not done / caveats
- No comparison report with real P&L: it needs recorded v2 outcomes; no accuracy claim is made.
- Release 277f30f review: its 15-minute confirmation refuses pre-market and needs >=2 closed 15m candles (no entries before 10:00); v2 uses 5m-only confirmation pre-10:00 and 15m afterwards. Its carry-over of open positions contradicts "no overnight carry"; v2 closes at the bell.
- Nothing pushed, deployed, or touched on production.
