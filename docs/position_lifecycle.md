# Paper-position lifecycle across midnight, weekends and holidays

Scope: the paper blotter (`demo_trades`, `demo_trade_service`) and the live engine's signal log (`intraday_sig_v2`,
`intraday_signal_v2`). Paper only: there is no broker or order code anywhere.

## The rules (owner's requirements, 2026-10-24)

1. An open position keeps being tracked across midnight and across trading dates until a RULE closes it.
2. Nothing is closed merely because the date changed.
3. Stop, target, trailing stop (live engine), score exit and timed exit are unchanged.
4. After a restart every still-open position is recovered, whatever date it was opened.
5. The entry lock blocks NEW entries only; monitoring, exits and risk-reducing updates are never blocked.
6. A close happens once: no second close event, no second P&L, no second recovery.

## What changed

| Where | Before | Now |
|---|---|---|
| Paper tracker `tick` | selected `session_date = today`: a position open at midnight was never touched again | selects EVERY `OPEN` position |
| Paper timed exit | "a trade from a previous date closes on the next tick" (never reached) | fires only at/after the 16:00 ET close of a TRADING day, at the first close after the entry (see below) |
| Paper updates | wrote price / P&L / status without checking the row was still open | every write is `UPDATE ... WHERE id = :id AND status = 'OPEN'`: a trade another process already closed is never re-closed, overwritten or refreshed |
| New paper entries | one per strategy + symbol + day | additionally never a second position while one is still open from an earlier date |
| Live engine `_open_row` | `session_date = this session` | the open signal of the symbol, any session |
| Live engine close | `UPDATE ... WHERE id = :id` then a change-log row | `... AND is_open = TRUE`, change-log row only if that update closed it (one row per close, even when two scans race) |
| Live engine EOD (15:55 ET) | any weekday | trading days only (weekends and exchange holidays never close anything) |
| Live engine `scan_once` | the universe | the universe plus any symbol with an open signal (a symbol that left the universe is still managed) |
| Calendar | none | `market_calendar.py`: NYSE full-day holidays computed with weekend observance |

### The timed exit, precisely

The first 16:00 ET close of a trading day that comes after the entry, and only when the clock is at or after 16:00 ET
on a trading day:

| Opened | Closes at |
|---|---|
| Mon 05:00 or 10:00 (pre-market / regular) | Mon 16:00 |
| Mon 17:00 (after-hours) | Tue 16:00 |
| Fri 10:00, service down at 16:00 | the first tick at/after Mon 16:00 (Saturday and Sunday have no close) |
| Wed before Thanksgiving | Fri 16:00 (Thursday is a holiday) |
| Mon 10:00, service restarted Tue 09:45 | NOT closed at restart: monitored all day, exits at Tue 16:00 (or earlier by stop / target / score) |

The close is the session's REAL close: 16:00 ET, or 13:00 ET on an early-close day (see Calendar below).

## Restart recovery

There is nothing to rebuild: the positions are rows. The first tick after a start selects every `OPEN` row once,
refreshes it from the boards / quote, and applies the rules. A position with no RELIABLE price is left untouched
(nothing is refreshed, nothing closed; see Fill accounting below).

## Entry lock

`entry_lock` blocks INSERTs only (code check + database triggers). Exits and updates are UPDATEs; the tick's exits are
committed separately from any entry attempt (the entry check never raises), so a locked or failing gate cannot cost an
exit. A rollback lock (`lock --for-rollback`) refuses while ANY position is open, whatever its date.

## Reporting: what carrying positions across dates changes

* **Attribution is unchanged: a trade belongs to the session it was OPENED in**, as every closed trade already did.
  `blotter(date)` filters on `session_date`; the live-engine `log_rows` / `summary` / `changes` filter on the signal's
  `session_date`.
* **A past day's numbers can change after the day:** Monday's blotter total and the engine's Monday `summary` (closed,
  wins, average P&L, exit reasons) now also include a Monday position that closes on Tuesday. Before this change those
  positions were never closed and never counted.
* **Today's blotter** lists positions still open from earlier dates separately (`carried_open`, `carried_open_count`);
  they are NOT in today's `rows`, `total_profit`, `open` or `wins`, so nothing is counted twice.
* **Today's engine log** shows carried signals first, flagged `carried`; `summary.carried_open` counts them.
* Nothing else in the application reads these two tables by date (checked: the blotter route, the engine log route and
  the tracker loop are the only consumers).

## Tests

`tests/test_position_lifecycle.py` (SQLite; calendar, the timed-exit table, midnight / weekend / holiday, restart
recovery, no duplicate entry, missing price, database failure and retry, competing close, stale-snapshot protection,
entry lock with triggers, rollback with a carried position, blotter and log reporting, live-engine carry / EOD /
race / out-of-universe) and, opt-in on PostgreSQL 17, `tests/test_entry_lock_postgres.py`
(`TRDGO_PG_TRIGGER_TEST=1`).

## Fill accounting (`paper_fills.py`): what price a close is booked at

The strategy's exit CONDITIONS (stop, target, score, timed) and the BUY/SELL scoring are unchanged. Only the simulated
execution price differs, and it is separate from any signal.

* STOP / TARGET with a closed period between the previous observation and now (overnight 20:00-04:00 ET, weekend,
  holiday): booked at the FIRST RELIABLE price seen after the gap, either side of the level (a long stop at 99 with the
  open at 94 fills at 94; a target at 102 with the open at 105 fills at 105). Same for SHORT, mirrored.
* STOP / TARGET inside a continuous session: booked at the level, as before (no tick data to do better).
* SCORE_EXIT / TIMED_EXIT: the observed reliable price, as before.
* Reliable price = a non-WAIT board price no older than `PAPER_EXECUTION.max_price_age_s` (300 s) or a live quote.
  The last known price and the entry price are NOT reliable.
* No reliable price: the position stays open and nothing is refreshed. If a due close stays unpriced for
  `max_unpriced_trading_days` (5) trading days it is closed as `NO_PRICE` with NO exit price and NO P&L, so it can never
  enter a total, a win rate or a realized figure. No fill is ever invented.
* `fill_model = "level"` restores the old behaviour for comparison. The dollars a booking differs from its level are
  derived (`gap_effect`), so there is no schema change.

## Calendar

NYSE full-day holidays (weekend observance) and 13:00 ET early closes (day after Thanksgiving, Dec 24, July 3 when
July 4 is Tue-Fri). The live engine's end-of-day exit is five minutes before the close (15:55, or 12:55 early).
One-off closures that cannot be computed are added by the operator: `MARKET_EXTRA_CLOSURES` /
`MARKET_EXTRA_EARLY_CLOSES` (comma-separated ISO dates). A day the calendar thinks is open but the exchange shut is
safe anyway: no reliable price arrives, so nothing is closed or invented.

## Instruments

Stocks and ETFs share one session (extended 04:00-20:00 ET). Cash indexes cannot be traded and futures follow a Globex
calendar that is NOT modelled, so `intraday_15m.entry_check` refuses anything that is not a stock / ETF
(`UNSUPPORTED_INSTRUMENT`) and the paper tracker never opens one. Futures ANALYSIS is untouched; futures paper
execution stays disabled.

## Reporting additions

The blotter keeps attribution by ENTRY session and adds, per date: `realized_on_date` / `closed_on_date` (positions
closed that day whatever day they opened), `gap_effect_total`, and per row `exit_basis` (`GAP` / `LEVEL`) and
`exit_date`. `NO_PRICE` closes carry no profit and are excluded from totals and win rates. A past day's entry-date
numbers can still change when a carried trade closes later; `realized_on_date` is the figure that never does.
No historical-performance or backtest module reads these tables.

## Duplicate and stale live-signal rows (`intraday_sig_v2`)

Found on production 2026-10-26: 17 open rows, 16 of them 4-10 days old, and AMD / AMZN / NVDA / QCOM each open twice
(different sessions, sometimes opposite sides). Cause: the old engine looked only for an open row of the CURRENT session,
so a row left open overnight was never managed again and a new row for the same symbol could be opened next to it.

Rules now (`tab_settings.INTRADAY_LIFECYCLE`, `max_carry_trading_days = 1`):

* **Every open row of a symbol inside the carry window (today and the previous trading day) is managed** on every scan,
  each by its own side, levels and entry (BUY and SELL symmetric), with the same guarded single close and one change-log
  row per close. The board shows the newest open one.
* **A second open row is never created by this build**: the scan holds a per-symbol PostgreSQL advisory lock
  (transaction level, pooler-safe) and re-checks for an open row right before the INSERT.
* **A row older than the window is STALE.** It is never managed, never closed automatically (no price a week later is a
  fair exit, and booking one would rewrite past sessions' results), never rescored, and it does NOT block a new signal for
  its symbol. It is reported: `summary()["stale_open"]`, `log_rows()[..]["stale"]`, `entry_lock.status()["stale_unresolved"]`.
  The entry lock does not count stale rows as positions to protect (nothing can manage them), so they do not block a
  cut-over.
* **Expiring stale rows is an operator action, never automatic**:
  `python backend\intraday_signal_v2.py stale` (list) and `python backend\intraday_signal_v2.py expire-stale` (dry run) /
  `... expire-stale --apply`. An expired row is KEPT: entry, side, levels, timestamps and score are untouched; it only becomes
  closed with `exit_reason = EXPIRED`, no exit price and no P&L, so it is in no total and no win rate. Idempotent.
