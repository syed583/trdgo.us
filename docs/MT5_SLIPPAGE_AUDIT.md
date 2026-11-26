# MT5 mirror audit - why the user's account makes less than the software

Account 250094 (user ..7036, DEMO), five trading days 2026-11-19 to 2026-11-25.
Source: `deploy/mt5_user_audit.py`, run read-only on the server on 2026-11-26 (four SELECTs, no broker
call). Every dollar figure is at the USER's size, 10 shares a trade. The software's own book is sized by
notional, so its raw dollar total is not comparable.

## The answer in one table

| Step | Dollars | Whose |
|---|---:|---|
| Software result, all 575 signals | +1,953.50 | |
| Signals never sent | -1,748.70 | OURS (see below) |
| Software result on the 109 signals that were filled | +204.80 | |
| 15 filled trades with no recorded close | -299.30 | OURS (actual result unknown) |
| Software result on the 94 trades filled and closed | -94.50 | |
| Entry price worse than the software's | -36.20 | split below |
| Exit price worse than the software's | -111.70 | split below |
| **MT5 result recorded in our log (price only)** | **-242.40** | |

The table reconciles exactly (-94.50 - 36.20 - 111.70 = -242.40). Broker commission and swap are not in our
data; the broker's statement will differ by those.

## The gap, by cause: $2,195.90 between +1,953.50 and -242.40

| Cause | Dollars | Share | Market or ours |
|---|---:|---:|---|
| Position ceiling full of phantom positions (414 signals) | 1,792.70 | 81.6% | OURS |
| Gateway licence expired (37 signals, 25 Nov) | 4.70 | 0.2% | OURS (no alert was raised) |
| Daily loss limit (15 signals) | -48.70 | -2.2% | RULE working; it saved $48.70 |
| 15 filled trades never recorded as closed | 299.30 | 13.6% | OURS; real outcome unknown |
| Fill against the broker's own quote at send | 93.00 | 4.2% | MARKET slippage (90.50 entry, 2.50 exit) |
| Broker quote against the software's price | 54.90 | 2.5% | OUR delay + feed difference (-54.30 entry, +109.20 exit) |
| **Total** | **2,195.90** | 100% | |

So about $93 of a $2,196 gap is market slippage. Roughly 95% is our software.

## By day

| Day | Signals | Filled | Software $ (all signals) | MT5 $ recorded | Log-open positions carried in |
|---|---:|---:|---:|---:|---:|
| 2026-11-19 | 111 | 38 | +744.00 | -197.80 | 5 of 20 |
| 2026-11-20 | 118 | 36 | +1,774.40 | +154.70 | 12 of 20 |
| 2026-11-23 | 101 | 19 | -172.30 | -102.90 | 17 of 20 |
| 2026-11-24 | 121 | 12 | +94.10 | -46.10 | 17 of 20 |
| 2026-11-25 | 124 | 4 | -486.70 | -50.30 | 18 of 20 |

The per-day split BY CAUSE is in the detail file on the server (`C:\SmartMoveRun\mt5_user_audit_250094.csv`,
one row per signal, columns `day`, `class`, `sw_pnl_at_user_size`); it was not printed by the run above.

## Cause 1 - phantom positions fill the ceiling (ours)

- The ceiling (`max_positions`, 20) counts every FILLED row in our order log that has no CLOSED row
  (`user_mt5_store.open_tickets`). It does not ask the broker.
- Every order is sent with a stop-loss and a take-profit attached (`user_copy_service._open_for`:
  `sl=ev.get("stop"), tp=ev.get("target")`). The broker can therefore close a position by itself.
- When the software later exits, `mt5_bridge.close_position` first reads the broker's open positions. If
  the ticket is not in that list it returns `ok` with `already_closed` and NO price.
- `user_copy_service.close_row` treats "ok but no price" as CLOSE_UNCONFIRMED and deliberately leaves the
  row open "so the sweep retries it". That rule was written on 2026-11-16 (F.1) after closes aimed at the
  WRONG account were recorded as closed; since F.1 every user call carries that user's own verified
  session, so "not in this account's open list" now does mean "not open in this account".
- Nothing ever turns such a row into CLOSED. The end-of-day sweep runs every 30 s from 15:45 to 15:58 ET
  and retries every log-open row each time, which is where the 1,703 CLOSE_UNCONFIRMED rows on 20 trades
  come from (20 rows x about 26 ticks a day).
- Result: 20 of 20 slots held by positions that do not exist. Fills fell 38, 36, 19, 12, 4.

Was the position really closed at the broker in those cases? By the code path, yes: "no price returned"
can only be produced when the broker's own open-position list was read successfully and the ticket was not
in it. HOW each was closed (stop, target, stop-out, by hand) is NOT known from our data; the broker's deal
history says, and `deploy/mt5_phantom_list.py --broker` reads it once the gateway works.

## Cause 2 - the gateway licence expired (ours: no alert)

From 2026-11-25 15:47 UTC every call for this account failed with
`CloudTrial failed(mt5rest...): Trial expired. Please purchase full version. https://mtapi.online`.
37 entries were skipped and 502 close attempts failed (CLOSE_FAIL) on 20 positions. Two PFE positions are
reported by the owner as really open in MT5; those are positions we could not close after the expiry.
The service shows this only as a per-user "last error". Nobody was alerted.

## Cause 3 - late closes (ours)

12 of 94 closes were logged more than 60 s after the software's exit (worst 108 s); the median is 4.6 s.
Exits cost $109.20 against the software's price, almost all of it here. What the code does:

- one worker thread handles every event for every user, one at a time (`user_copy_service._loop`);
- a close makes three gateway calls in sequence (quote, open positions, close), each with a 12 s timeout,
  and passes the one rate gate shared with the operator's own book (`broker_rate`);
- the late ones come in groups at the same instant - three QQQ shorts at 73-74 s, two ADBE longs at
  107-108 s - which is what a queue looks like.

Which of queue, rate gate or a slow gateway call caused each one is NOT established: the order log has no
per-step timing. The fix adds it.

## Cause 4 - market slippage (not ours)

Fill against the broker's own quote taken just before sending: $90.50 on entries, $2.50 on exits, $93.00
in all, about $0.85 a trade on 109 fills. No order was rejected by the broker.

## What is not known

- The real result of the 15 filled trades with no recorded close, and of the 20 phantoms.
- Commission and swap.
- Whether other users' accounts have the same phantoms (`deploy/mt5_phantom_list.py`, no flags, answers
  this read-only).
- Whether the gateway expiry applies to every account or only to some.
