# Intraday V2 shadow-mode readiness report

Branch `feature/intraday-session-workflow`, local only. Shipped default is still `INTRADAY_WORKFLOW["mode"] = "legacy"`; the opening rule is still **disabled**. Nothing pushed, deployed, restarted or activated; no database (dev or production) was touched — every DDL/migration test ran on throw-away SQLite engines.

## 1. What shadow mode is

`INTRADAY_WORKFLOW["mode"] = "shadow"` (opt-in, needs your approval to switch on anywhere):

* **Legacy stays the only workflow that opens paper trades.** `demo_trade_service.tick()` runs exactly as before and commits. Only afterwards, in separate sessions, are the Legacy decisions logged and V2 run.
* **V2 judges the same boards** (same rows, same data timestamps) and writes **only** to its own tables: `intraday_shadow_trades` (a virtual book: entries, exits, `MARKET_CLOSE`, `PENDING_RECON`), `intraday_signal_events`, `intraday_snapshots`, `intraday_data_exceptions` (kind `SHADOW_CLOSE_PRICE_MISSING`) and `intraday_shadow_log`. It never reads or writes `demo_trades`, the blotter, or any broker; no real-money path exists.
* **Proof Legacy is unaffected:** the replay (3 simulated trading days, identical snapshots) produces a Legacy book in shadow mode that is **identical to the cent** to the Legacy-only book (156 trades, same prices, exits, P&L) — a test now asserts it (`test_shadow_replay_leaves_the_legacy_book_untouched…`). The released gate (`intraday_safety.gate`) decides the same in shadow mode; the only difference is three extra, read-only keys on the row (`signal_meta`, `gate_ts`, `raw_levels`) so V2 can see the strategy's own evidence and levels on rows Legacy withholds. Strategy modules, parameters, weights, formulas and BUY ≥ 55 / SELL ≤ 45 are byte-identical to 277f30f (guard test).

## 2. What is recorded (requirement 3)

`intraday_shadow_log` — one row per **change of decision** for each engine / strategy / symbol / session / direction:
`engine` (LEGACY | V2), `strategy`, `symbol`, `trading_date`, `session_type` (PREMARKET / REGULAR / POSTMARKET / CLOSED), `direction`, `score`, `decision` (OPENED | WOULD_OPEN | REJECTED), `code` (V2 `WAIT - …` code, or `LEGACY: <the gate that refused>`), `reasons` (JSON), `data_ts` (JSON: decision time, board build time, price age, engine age, every per-signal source age aged to the decision, phase, quote age for V2), `snapshot_id`, `trade_id`, `window_state` (opening-rule state). V2 additionally keeps the per-parameter evidence snapshot (reading, contribution, category, coverage, agreement) and the event history.
`intraday_shadow.report(...)` / `python intraday_shadow_cli.py report <date>` compares both engines per strategy (signals, entries, rejections by code, overlap both/legacy-only/v2-only, and the two books' realised P&L / win rate). Both are read-only. `python intraday_shadow_cli.py plan` prints the migration dry-run.
Replay result (3 simulated days): 5,555 log rows — Legacy: 156 OPENED (= every trade) + 1,278 REJECTED; V2: 250 WOULD_OPEN + 3,871 REJECTED.

## 3. Pre-market entries and carry (requirement 4) — PASS
Tests: a shadow pre-market position is the same OPEN row at 09:29:30, 09:31, 12:00 and 15:59:30 and closes `MARKET_CLOSE` at 16:00:10 (`test_shadow_premarket_position_carries_into_regular_hours_and_closes_at_the_bell`); in V2 mode the same carry holds (`test_premarket_position_is_still_open_when_regular_trading_begins…`). The regular-session signal for the same name is a separate event.

## 4. Configurable opening rule (requirements 5 and 6) — built, DISABLED
`INTRADAY_EXEC["opening_rule"] = {"enabled": False, "no_entry_start": "09:30", "no_entry_end": "10:00", "require_both_after_end": True}`.
* Disabled (shipped): behaviour is exactly the tested V2 default.
* Enabled: inside `[09:30, 10:00)` ET a new V2 entry is refused with `WAIT - OPENING WINDOW`; **open positions keep being managed** (stops, targets, score exits, close) — tested: a pre-market position stopped out at 09:41 while a valid new signal is refused. From 10:00 **both** the 5-minute and the 15-minute readings must exist, be fresh and agree; a 15-minute warm-up, a missing or an invalid reading refuses (fail-closed — "subject to actual data availability" means no data, no entry). Never applies to the pre-market. Tests: states, window block, both-required with `ok` / `warmup` / `missing`, and that the default still tolerates a 15-minute warm-up.
* Replay sensitivity (simulated): enabling it removed 5 of 250 V2 entries (245), blocking 2 final events as `OPENING WINDOW` and the rest through the stricter confirmation; drawdown unchanged. Direction only — synthetic prices.
* Dependence on data: V2 needs ≥ 22 closed 5-minute candles of history (prior session counts) for its EMA/RSI, the existing 15-minute rule's depth. Without prior-session candles the 5-minute reading is INVALID until 11:20 ET (see the comparison report §6).

## 5. Official close and PENDING_RECON (requirement 7) — PASS
Normal day and the 13:00 early close: positions are `MARKET_CLOSE` at the first price at/after the bell (15:59:50 still open, 16:00:05 closed; 12:59:30 / 13:00:05 on 2026-11-27), no new entry after the bell; no price → `PENDING_RECON` with no price/P&L and an exception row; `PENDING_RECON` is not exposure, is never managed, is reconciled at its own trade date's final regular 5-minute close, and never merges into a new entry. The shadow tracker keeps ticking after hours while the virtual book has anything open or pending (`has_open_trades`). Replay: shadow book flat at the end of every day; 12 `MARKET_CLOSE` exits. Legacy's own `TIMED_EXIT` closes its book at the bell (8 in the replay) and no Legacy position was carried overnight in the emulation (Legacy's release gate refuses non-regular-session entries).
Note: during the 5-minute grace after the bell a position with no fresh price stays `OPEN` (no entries are possible then).

## 6. Review of the limits (requirement 8) — values are PROPOSED, none activated
Source of every value: `tab_settings.INTRADAY_EXEC` (`status: "PROPOSED - not approved for production"`). There is **no real-data evidence yet** for the PROPOSED rows; shadow mode is how the evidence is collected (spread, coverage, time-to-confirmation, and the outcome of accepted vs rejected signals are all stored).

| Setting | Proposed | Justification | Honest limitation | Decision you need to make |
|---|---|---|---|---|
| Signal persistence | 2 distinct observations ≥ 120 s apart | Removes one-tick flickers; the live engine rescans about every 180 s (`INTRADAY_FRESHNESS["engine_score"]`) and an unchanged snapshot never counts, so the real minimum is about one rebuild interval | Not tuned on data | Keep, or require 3 for pre-market (thinner data). Shadow measures time-to-confirm and flip rate |
| Max spread | 0.10 % pre-market, 0.20 % regular | Pre-market quotes are wider and stale faster, so the stricter pre-market limit is deliberate; regular 0.20 % is generous for large caps | No spread history exists. 0.10 % may reject many pre-market names (replay: 29 invalid-price blocks, mostly the planted wide-spread name) | Approve after the first shadow week shows the real spread distribution |
| Quote age | 120 s pre / 300 s regular | Existing price-volume freshness limits | — | None (existing) |
| Price discrepancy quote vs board | 0.50 % | Stops filling on a quote that disagrees with the price the signal used | Arbitrary | Approve or drop |
| Min price / avg volume | $10 / 1,000,000 | Existing live-engine floors (`LIQ_MIN_PRICE`, `LIQ_MIN_AVGVOL`) | — | None (existing) |
| Open positions | 60 total / 15 per strategy | Placeholder risk budget. Context: each board covers about 38 names, and the production book held **172** open paper positions at the Phase B5 preflight, so a 60 cap would have blocked roughly two thirds at the peak | Policy, not a measurement | Decide whether paper trading should be capped at all; alternative: no count cap, exposure caps only |
| Exposure | $1M total, $250k per strategy, $60k per symbol (gross), 3 strategies per symbol-side | 100 shares × about $150 ≈ $15k per position; $250k is about 16 positions per strategy | Arbitrary | Same decision as above |
| Daily loss | $5k per strategy / $15k total (entries stop, exits continue) | Circuit breaker; a stop is about 0.5 × ATR on 100 shares (tens of dollars), so these bind only in a disorder day | Replay drawdown was $387 (synthetic), so the limits never bound | Keep as a breaker, or set from your risk appetite |
| Close grace | 300 s | Gives a stale feed time to deliver the closing price before the fallback | Operational choice | Keep |
| Opening rule | disabled | See §4 | — | Enable only with approval; recommend enabling the 09:30–10:00 no-entry window first in shadow mode to measure what it removes |
Recommendation: approve none of the PROPOSED numbers for activation now; run shadow mode for at least five trading sessions, read `intraday_shadow_cli report`, then fix the numbers from the observed distributions.

## 7. Database migration review and release_check (requirement 9)
Findings and what changed:
1. **The ORM model of `demo_trades` now names eight nullable evidence columns, so even Legacy mode reads them.** Deploying the code without the columns would break the Legacy tracker. Fix: `ensure_schema()` always runs `migrate_additive()` (nullable `ADD COLUMN IF NOT EXISTS` + `CREATE TABLE IF NOT EXISTS` for the five new tables — metadata-only, no row rewritten, no lock beyond a catalog change). If the application's DB role cannot run DDL the migration fails silently and the tracker would break, so the **recommended deployment is to run the reviewed SQL first** (`backend/migrations/2026_10_27_intraday_v2_additive.sql`, generated from the models, idempotent, not executed here) and confirm `intraday_shadow_cli plan` shows nothing to do.
2. **The behaviour-changing part is separate and V2-only:** dropping the old `uq_demo_strategy_symbol_day` constraint and creating `uq_demo_trades_strategy_symbol_day_session` (one position per strategy/symbol/day **and entry session**; old rows count as `''`) and `uq_demo_trades_one_open` (at most one OPEN per strategy/symbol). It runs only when `mode == "v2"`; a dry-run (`migration_plan`) reports blockers; historical OPEN duplicates make the second index skip and report — nothing is deleted or forced. Rollback SQL is in the migration file.
3. **Development and production share one Supabase database; dev isolation renames tables to `_local` twins by text rewrite.** An index name that does not contain its table name would collide with production's. All new/changed index names now contain their table name (a test enforces it) and the five new tables are in `ISOLATED_TABLES`. Existing production index names (`ix_demo_session`, …) are unchanged.
4. Event key collision when switching shadow → v2: events are keyed by trading date, so activate **between sessions** (after the close, before the next pre-market); a same-day switch would see shadow `TRADED` events and skip those candidates.
5. Connection use: the shadow logging and V2 run sequentially, each in its own short session, after Legacy's commit (never two at once; well inside the 15-connection ceiling).
6. A harness-found hazard fixed: reading the real 15-minute gate for a refusal reason touches the shared cache; with one connection (SQLite) this rolled back Legacy trades. Logging now happens after Legacy's commit and decides before it writes. Regression tests added.
`release_check.py`: new rows (shipped mode is legacy; opening rule ships disabled; session helpers; execution rules; entry-lock honoured; `_market_close`; `PENDING_RECON` never an invented price and not exposure; shadow routes to the shadow book only; Legacy commits before the shadow evaluation; additive migration in every mode; unique-index migration only when V2 is active). They apply **only to a build that contains `intraday_workflow.py`**, so the released 277f30f stays a valid rollback target (test: 277f30f passes). 8 mutation tests prove each new row is detected.

## 8. Checks (requirement 10)
| Check | Result |
|---|---|
| Backend suite (`pytest backend`) | **1206 passed, 4 skipped** (was 1172) |
| New: shadow tests (23), lifecycle/replay tests, release-check tests | all pass |
| Replay, 5 engine runs, identical snapshots | Legacy book in shadow mode identical to Legacy-only; V2 shadow book = V2 standalone (250 entries, $8,233 **simulated**) |
| `release_check.py` (working tree and `277f30f`) | PASS / PASS |
| Frontend `tsc --noEmit`, `npm run typecheck`, `npm run build` | exit 0 / 0 / 0 (frontend unchanged in this phase; build written to a scratch folder) |
| `INTRADAY_WORKFLOW["mode"]` | `legacy` |
Simulated figures prove plumbing only; they are not performance evidence.

## 9. Remaining risks
* No real-data validation yet; thresholds are proposals; Legacy's gates are emulated in the replay (the real shadow decision log uses the real gate).
* The migration was never run against a real Postgres: SQLite tests cover logic, the generated SQL is reviewed but untested on Postgres.
* Provider history depth (≥ 22 closed 5-minute candles) and bid/ask availability are assumptions; V2 fails closed if absent.
* Shadow mode adds one extra board read per tick (cache reads) and writes about 1–2k log rows a day; table growth should be reviewed after a week.
* `PENDING_RECON` rows with no retrievable close need an operator decision.
* Switching to `v2` later is a separate approval with its own checklist (migration part 2, between sessions, entry lock engaged, release_check updated).

## 10. Proposed next steps — all need your approval
1. Review/approve the additive migration SQL (stage 1, DB change). 2. Deploy this branch in `legacy` mode (no behaviour change), verify. 3. Switch to `shadow`, run ≥ 5 sessions, review `intraday_shadow_cli report`. 4. Decide the PROPOSED values and the opening rule from that data. 5. Only then consider `v2`.
