# Legacy vs V2 comparison report — intraday paper-trading workflow

Branch `feature/intraday-session-workflow` (local, not pushed). `INTRADAY_WORKFLOW["mode"]` is still `"legacy"`. Nothing deployed, restarted, or activated; no production or VPS contact.
Reproduce: `backend/.venv/Scripts/python.exe backend/tests/legacy_v2_compare.py out.json` (about 3 minutes) and `pytest backend/tests` (1172 passed, 4 skipped).

## 0. Read this first: what the evidence is and is not

* **No recorded historical market-data snapshots exist** in the repository or on the dev database (the dev tables are empty twins; production was not touched). So the replay uses a **seeded, deterministic, SIMULATED** market: 12 symbols (including a low-volume, a wide-spread and a sub-$10 name), 3 trading days (Tue 2026-10-27, Wed 10-28, **Fri 2026-11-27 = 13:00 early close**), 5-minute ticks 04:00–20:00. Both engines read the *same* boards, quotes, 5m bars and 15m readings at every tick.
* Scores come from a synthetic momentum rule mapped through the **real, unchanged** cutoffs (BUY ≥ 55, SELL ≤ 45). Six strategies, parameters, weights and formulas are untouched (the strategy modules are byte-identical to 277f30f; a test guards it).
* "Legacy" = the released `tick()` of 277f30f. Its freshness gate and 15-minute gate read live caches, so they are **emulated** from the same snapshot (stale/thin rows → WAIT; regular session only; newest closed 15m candle fresh and agreeing). This is an emulation, not the live code path.
* **Every P&L, win-rate and drawdown figure below is SIMULATED. It is not evidence of real performance and must not be quoted as such.** Reliable historical evidence for item 9 does not exist yet.

## 1. TypeScript check — PASS
`cd frontend && npx tsc --noEmit` → exit 0, no errors.

## 2–4. Replay results (3 simulated days, identical snapshots)

Units differ and are not interchangeable: signals = distinct strategy/symbol/day/direction BUY/SELL appearances during an entry window (same count for both engines: **312**, 52 per strategy). V2 rejections are counted per signal event (strategy/symbol/session/direction) by its final reason; legacy rejections are counted per candidate by the gate that refused it (legacy cannot say more).

| | Legacy (emulated 277f30f) | V2 |
|---|---|---|
| Trades opened | 156 (all REGULAR) | 250 (135 PRE-MARKET, 115 REGULAR) |
| Still open / PENDING_RECON at end | 0 / 0 | 0 / 0 |
| Exits | SCORE 114, TARGET 34, TIMED 8 | SCORE 165, TARGET 72, MARKET_CLOSE 12, STOP 1 |
| Simulated net P&L / win rate / max drawdown | $4,595 / 64.1% / $309 | $8,233 / 61.6% / $387 |

Per strategy (trades opened; V2 rejection reasons by event):

| Strategy | Signals | Legacy trades | V2 trades | V2 rejections |
|---|---|---|---|---|
| chatgpt | 52 | 26 | 53 | unconfirmed 7, duplicate 39 |
| grok | 52 | 26 | 34 | liquidity 20, price 9, duplicate 28, unconfirmed 4, stale 2, limit 1, insufficient 3 |
| meta | 52 | 27 | 40 | liquidity 12, price 9, duplicate 30, unconfirmed 7, limit 1, insufficient 2 |
| gemini | 52 | 25 | 49 | duplicate 39, unconfirmed 7, limit 4, stale 2 |
| claude_pro | 52 | 26 | 31 | duplicate 23, liquidity 21, price 11, unconfirmed 9, limit 4, insufficient 2 |
| kimi | 52 | 26 | 43 | liquidity 11, duplicate 34, unconfirmed 10, limit 3, stale 1, insufficient 1 |

V2 blocks, all strategies (events): **stale data 5, insufficient data 8, missing confirmation 44, low liquidity 64, invalid price/spread 29, duplicate entry 193, position limit 13.**
* "Position limit" here is the proposed cap of 3 strategies holding the same symbol on the same side (checked in a one-day run), not an account-wide cap. A tightened-limit sensitivity run (`max_open_total 12`, `per_strategy 3`) raised position-limit blocks to 32 and cut simulated drawdown to $261; it exists only to prove the risk layer bites.
* Duplicate entries are mostly a held position seeing the same signal again — correct blocking, not lost opportunities.
* V2 opens more trades than legacy mainly because it permits pre-market entries (legacy never does) and re-enters in the regular session after an exit; the two are not like-for-like and the comparison says nothing about quality.

## 5. Pre-market carry and separate regular-session signals — PASS
`test_premarket_position_is_still_open_when_regular_trading_begins…`: position opened 07:15 ET is the same OPEN row at 09:29:30, 09:30:30 and 09:45; the regular-session BUY for the same name is stored as its own REGULAR event (different id and snapshot), rejected as WAIT - DUPLICATE ENTRY while the first position is open, and `trade_id` stays on the PREMARKET event only. A later genuinely new regular signal (after the first position exited) opens a second row with `entry_session=REGULAR` (`test_a_new_regular_session_signal_may_open_a_second_position…`).

## 6. Regular-session opening period (5-minute only) — exact behaviour and risks
Probe (clean uptrend, BUY, minute by minute; `opening_probe`):

| Time (ET) | With prior-session history | Without prior history |
|---|---|---|
| 09:31–09:39 | 5m WARMUP (needs 2 closed candles today; first closes 09:35, second 09:40) → refused | refused |
| 09:40–09:59 | 5m OK, 15m WARMUP → **5-minute reading alone decides**; entry possible | 5m INVALID (needs 22 closed candles) → refused |
| from 10:00 | 5m OK and 15m OK, **both must agree** | refused until 11:20 (22 closed candles today), 15m refused until ≥22 15m candles exist |

Rules in force: closed candles only (the forming candle never counts); three votes (price vs today's VWAP, EMA9 vs EMA21, RSI14 ≥52 / ≤48) with ≥2 of 3 needed; a missing, stale or invalid 15m reading after warm-up refuses; a 15m reading that disagrees refuses.
Risks (nothing changed, for your decision):
1. 09:40–10:00 relies on one timeframe and a VWAP built from two candles — noisy, opening-auction volatility; legacy refused all entries before 10:00. Option: a configurable no-entry window until 10:00 or a stricter 5m rule.
2. Correctness depends on the provider returning ≥22 closed 5m candles of history (prior session). If it does not, V2 refuses (fail-closed) for up to ~1h50 — safe but silent unless the event/reason is read.
3. The 15m warm-up depth was initially 15 candles in V2; found during this work and **aligned to the existing 15-minute rule's `min_bars` = 22** (stricter, no new numbers).
4. Pre-market has no candle confirmation by design (no regular candles exist); persistence (two distinct observations ≥ 2 min apart), coverage, a two-sided quote and the tighter spread limit apply instead.

## 7. Official close, including early-close days — PASS
* Normal day: 15:59:00 and 15:59:50 still OPEN; 16:00:05 → `MARKET_CLOSE` at the observed price with the assumption recorded (`test_everything_is_closed_or_non_tradable_at_the_official_close`).
* Early close 2026-11-27: OPEN at 12:59:30; `MARKET_CLOSE` at 13:00:05; no new entry or event at 13:10+ (`test_early_close_day…`). Across the 3 replay days 12 positions were closed at MARKET_CLOSE and none were left open at the end.
* No price at all: after the 5-minute grace the row becomes `PENDING_RECON`, exit price and P&L stay NULL, one `CLOSE_PRICE_MISSING` exception is recorded.
* Caveat: during the 5-minute grace after the bell a position without a fresh price stays `OPEN` (but no entries are possible and the tick wakes at the bell). Not a carry risk, but it is not instantaneous.

## 8. PENDING_RECON — PASS
* Not exposure: excluded from open positions used by the risk layer.
* Never managed: next session at 09:45 with a price that would trigger stop/score exit it is untouched (status, price and P&L unchanged).
* Not carried: reconciliation books it at the **final regular 5-minute close of its own trade date** (101.5 in the test), never at today's price, and resolves the exception.
* Does not block or merge into a new legitimate entry: a new signal opens a separate OPEN row (the one-OPEN-per-strategy/symbol index excludes PENDING_RECON).
* Open design point: a PENDING_RECON row with no close data ever found stays PENDING_RECON indefinitely (flagged by the exception table) — it needs an operator decision; nothing auto-resolves it or invents a price.

## 9. Realised P&L, win rate, drawdown, frequency — UNAVAILABLE (reliable evidence); SIMULATED only
The simulated figures in the table above exist solely to prove the plumbing. They use synthetic prices, fixed 100 shares, the existing stop/target/score-exit rules, no commissions and no slippage beyond the bid/ask fill rule. Reliable historical P&L comparison needs recorded boards/quotes/candles (the V2 snapshot tables will provide this going forward) or the production paper-trade history, which this task was not allowed to read.

## 10. Proposed V2 thresholds — values, basis, evidence (all remain PROPOSED, none approved)
| Setting | Value | Basis | Evidence |
|---|---|---|---|
| Per-source freshness | existing `INTRADAY_FRESHNESS` | existing constants | unchanged |
| Min eligible coverage | 70 % | existing `MIN_COVERAGE_PCT` | unchanged constant, now judged on eligible weight |
| Min agreement | 0.55 | existing `V3_CONFIDENCE` | unchanged |
| Persistence | 2 distinct observations, ≥120 s apart | PROPOSED | design choice; no data |
| Quote max age | 120 s pre / 300 s regular | existing price-volume limits | existing |
| Max spread | 0.10 % pre / 0.20 % regular | PROPOSED | no spread history available; the pre-market tightness is deliberate |
| Price discrepancy quote vs board | 0.50 % | PROPOSED | no data |
| Min price / min avg volume | $10 / 1,000,000 | existing live-engine floors | existing |
| Candle votes / RSI 52-48 / EMA 9-21 | existing `INTRADAY_15M` | existing | unchanged; warm-up depth 22 now aligned |
| Position caps | 60 total / 15 per strategy; exposure $1M / $250k / $60k per symbol; 3 strategies per symbol-side | PROPOSED | arbitrary placeholders |
| Daily loss limits | $5k per strategy / $15k total | PROPOSED | arbitrary placeholders |
| Close grace | 300 s | PROPOSED | operational choice |
Recommendation: approve no PROPOSED number until real data has been analysed (recorded V2 snapshots from a non-production book, or the production paper-trade history, which this task did not read). Only the PROPOSED rows need your decision; the others are existing constants.

## 11. PASS / FAIL summary
| # | Item | Result |
|---|---|---|
| 1 | `tsc` | PASS (0 errors) |
| 2 | Deterministic harness, identical snapshots | PASS (simulated data — no real snapshots exist) |
| 3 | Signals / entries / rejections per strategy | PASS |
| 4 | Block counts by cause | PASS |
| 5 | Pre-market carry; separate regular events | PASS |
| 6 | Opening-period behaviour and risks | PASS (investigated; risks above) |
| 7 | Close incl. early close | PASS |
| 8 | PENDING_RECON containment | PASS |
| 9 | Real P&L comparison | NOT POSSIBLE — simulated only |
| 10 | Threshold review | DONE — nothing activated |
| 11 | Full suite | PASS: 1172 passed, 4 skipped |

## Defects the harness found and fixed (V2 code only; strategies untouched)
1. **Grok, Kimi, Meta, Gemini would have been blocked all regular session** (WAIT - INSUFFICIENT DATA): their registry only had the equity path and V2 looked up a `live` path. Now `registry_for()` falls back to the equity registry. Regression test added.
2. **Claude Pro / ChatGPT live-engine rows could never gather two observations**: the live engine's own rebuild time was not part of the evidence fingerprint. It now is (an unchanged engine snapshot is still not new evidence). Regression test added.
3. 15m/5m reading warm-up depth aligned to the existing 22-candle rule (see §6).

## Remaining risks
* No real-data validation; thresholds unproven; legacy gates emulated.
* Provider history depth and quote bid/ask availability are assumptions of the quote and candle checks (fail-closed if absent).
* V2 requires the schema migration (additive) and an updated `release_check.py` invariant before any deployment — not done, not deployed.
* The workflow tick reads six boards each cycle; load on the shared Unusual Whales budget in live mode is unmeasured.
* The 04:00 ET open on a cold start: persistence needs two distinct observations, so the first pre-market entries cannot occur before ~04:03.
* PENDING_RECON rows without any retrievable close need operator handling.
