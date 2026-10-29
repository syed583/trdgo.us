# Phase 2B analysis — `_historical_moves` feeds wrong prices into the earnings models (read-only analysis, nothing changed)

Code: `backend/earnings_trade_service.py:26-71` (`_historical_moves`), output consumed as `trade["historical_moves"]`.

## The defect (same mechanism proven for the old grader in the 2026-10-29 provider dry run)
The provider's daily candles carry 1–3 rows per date (`pr` pre-market, `po` post-market, `r` regular; returned pr, po, r within a date). `_historical_moves` fetches `candles(symbol,"1d",limit=2500)`, sorts by date (stable) and takes rows by position without looking at `market_time`:
* **Before Open report:** base = `closes[idx-1]` (last row of the prior date = the regular close, correct by luck of ordering); reaction = `closes[idx]` = the **pre-market** row of the report date, not the regular close.
* **After Close report:** base = `closes[idx]` = the **pre-market** row of the report date; reaction = `closes[idx+1]` = the **post-market row of the same date**, not the next session's regular close.
Result: AMC reactions are measured pre-market → after-hours of the report day (≈ 0 or a few tenths of a percent) and BMO reactions use the pre-market print. Both systematically understate the true move.

## Size of the error (12 hand-checked events from the dry run; the same mechanism, so indicative only)
| | Stored/legacy | Regular-close corrected |
|---|---|---|
| mean absolute move | 1.82 % | 3.10 % (legacy ≈ 41 % too small) |
| AMC (4 events) | −0.86, 0.00, 0.00, 0.00 | +8.50, +3.01, +0.15, +0.08 |
| events whose direction class changes | 5 of 12 | |
`_historical_moves` itself has not been replayed against real candles yet (needs ~1 candle request per symbol).

## Which models read it (code search; `chatgpt_service` has no reference)
* `claude_last_service` (Claude New): `hist_reaction` param (share of up vs down moves, ±0.2 % dead zone), `implied_vs_realized`, and `_implied_move_eval` = implied move ÷ average |historical move| → the **IM ratio bias and the trade gate** (`tab_settings.V3_IM_POINTS`: ratio < 0.9 → +1 … > 1.5 → −1; gate half size > 1.1, no-trade > 1.5).
* `claude_pro_service`, `meta_service` (IM ratio from the same moves), `grok_service` (implied-vs-realized), `kimi_service` (hist_reaction), `gemini_service` (hit-rate), `pre_earnings_direction_service` (hist_bias).
Also used by `earnings_signal_history_service`, `earnings_backtest_service`, `risk_experiment_service`, `get_summary` ("historical post-earnings move averages x %").

## Direction of the bias (hypothesis — not yet measured)
Because the average |move| is understated, the IM ratio (implied ÷ historical) is inflated, which pushes names into the "> 1.5 → bias −1.0 / no-trade" buckets and removes dead-zone events from the up/down count. That would tilt scores toward SELL/NEUTRAL, which is consistent with the replay of the 99 events (e.g. Grok 36 BUY / 82 SELL, Gemini 13 BUY / 24 SELL) but does not prove it. Measuring it needs a before/after replay.

## Proposed fix (design only; needs your approval)
Reuse the Phase 2A code that is already tested and live: `earnings_outcomes.regular_session_closes` (only `market_time == 'r'`, fail-closed) and `reaction_window`/market calendar for BMO vs AMC. Per historical report date: BMO = regular close of the prior session → regular close of the report day; AMC = regular close of the report day → regular close of the next session; skip (do not guess) when a regular close is missing. Same output shape (`date`, `label`, `move_pct`) so no model code changes.

## What changes when it ships (why it is a separate, approved phase)
* Every model's historical-reaction, IM-ratio and hit-rate inputs change ⇒ scores, signals and gates change for many names; predictions captured before and after are not comparable. Record a `method_version` for the moves (and in the capture) so the scoreboard can separate them.
* Caches (`earn:*`, `swr` entries, Signal History) hold computed values for hours; they refresh on their own TTLs or need a controlled flush.
* The scoring formulas and weights stay untouched — only the input data becomes correct.

## Suggested rollout
1. Read-only replay tool (like `outcome_replay`): fetch candles for the symbols in the 99-event sample (~100 capped provider GETs, approved count), compute old vs new moves, re-score the eight models offline with both, and report how many signals, gates and IM-ratio buckets change per model.
2. Local fix + tests (fixtures with real pr/po/r shapes: BMO, AMC, missing `r`, holiday, split).
3. Shadow period: compute both, store both, keep serving the old one; compare for several days.
4. Switch with a flag (`HISTORICAL_MOVES_METHOD=regular_close`), rollback by flag.

## Decisions needed from you
a) Approve step 1 (read-only replay; provider budget e.g. ≤ 100 candle requests). b) Shadow-then-switch vs direct switch. c) Whether scores before/after the switch should be reported separately on the scoreboard (recommended).

---
# MEASURED RESULT of the read-only replay (production VPS, 2026-10-29, moves_replay.py SHA-256 55cc499b89d2b350b6abfd5a6d3e2f57d2a633c4ed0dbb4422dcee177c60c3c2)
100 provider GETs (2 per symbol), 50 symbols (sample sha 8e7ceced…f43b), sequential, no errors, `stopped: None`; no database access. Each symbol returned 4 reported quarters, so 200 events were compared (111 Before Open, 89 After Close). The legacy numbers are the output of the unchanged production `_historical_moves`; the corrected ones use the regular-session close of the same report dates.

| | Legacy (as the models see it) | Regular-close corrected |
|---|---|---|
| mean absolute move, all 200 events | 2.64 % | 4.31 % (legacy 39 % too small) |
| Before Open (111) | 3.77 % | 4.66 % |
| After Close (89) | 1.24 % | 3.88 % (legacy 68 % too small; **0 of 89 match**) |
Events: 51 of 200 (26 %) have the **opposite sign**; 97 of 200 (49 %) change BUY/SELL/NEUTRAL class (±0.5 % flat band); in 93 (47 %) the legacy move is less than half the true one; only 20 match within 0.05 (all Before Open).
Per symbol: corrected ÷ legacy mean |move| has median 1.44 (range 0.70 – 194; the maximum is a name whose legacy average is near zero).

## Effect on the model inputs
* `hist_reaction` bias (share of up vs down moves): changes by ≥ 0.25 for **32 of 50 symbols (64 %)**.
* Implied-move ratio (implied ÷ mean |historical move|, `V3_IM_POINTS`): for an implied move equal to 0.8×, 1.0× or 1.2× of the corrected average, the legacy input gives a **lower (more bearish) bias for 35 of 47 symbols (74 %)**, a higher one for 0 / 4 / 5, and the same for 12 / 8 / 7. At 1.5× both saturate (−1) for most names, so little differs (legacy higher 8, same 39).
So the earlier hypothesis is confirmed in direction: understated historical moves inflate the ratio and push the implied-move score and gate toward the bearish / no-trade side for about three quarters of the names, and flip the reaction bias for about two thirds. This measures input differences only; the effect on each model's final BUY/SELL/NEUTRAL still needs a re-score (their other inputs are not in this sample).

## Limits
50 symbols from the Phase 2A sample (not the whole universe); 4 quarters per symbol (all the provider returned), so "last 8" equals "all"; report timing taken from the provider's own labels, same as the legacy function; no split adjustments made.
