# Phase 6 — Dual Earnings Strategy Research (DESIGN ONLY)

**Read-only research & design.** No scoring/threshold/consensus/production/strategy code changed;
no deploy; Track A/B and Phase 5 preserved. Legend: **[FACT]** · **[SIM]** · **[ASSUMPTION]** · **[MISSING]**.

## REPORT A — Existing infrastructure & data availability
Reusable (no new infra needed to *study* Strategy A):
- `historical_backtest_service` — point-in-time partial-factor reconstruction + consensus trades.
- `forward_scoreboard_service` — genuine recorded predictions, cost model, execution engine.
- `risk_experiment_service` — **capital-constrained portfolio** (sizing, exposure, worst-loss),
  gap-risk score, **exit-before-earnings** path (= Strategy A), OOS split.
- `intraday_model` / `intraday_signal_v2` — **LIVE** 5-min engine (VWAP, EMA, ORB, RVOL, 0-2DTE flow).

Data availability (point-in-time):
| Need | Status |
|---|---|
| Daily OHLC ~10y | **[FACT] available** |
| Earnings dates + BMO/AMC bucket | **[FACT] available** (`earnings_history`) |
| Prior beats / reaction history | **[FACT] available** |
| Price/trend/EMA/RSI, sector/index vol | **[FACT] available** |
| Options flow / IV / unusual / disparity / dark pool / short interest **history** | **[MISSING]** (live-only ≤~15d) |
| **Actual earnings publication timestamp** | **[MISSING]** (only premarket/postmarket bucket) |
| **Historical intraday 5m/15m bars** | **[MISSING]** (UW intraday only ~days deep) |
| **Historical extended-hours prices** | **[MISSING]** |

## REPORT B — Strategy A: Pre-Earnings Momentum (studyable now; no edge found)
**Design:** enter T‑3/T‑2/T‑1 on the unchanged directional consensus; **exit at the last regular
close before the announcement** (handles timing by never holding into the premarket/postmarket
release). Signals usable point-in-time: price trend/relative strength, EMA/RSI, sector/market, prior
beats; options/estimate factors are **[MISSING]** historically (available live-forward only).

**Evidence [SIM], exit-before-earnings, per event (n=84/timing, 18mo, 25 symbols):**
| Timing | Avg net | Win% | PF | Sharpe/trade | 95% CI | Worst |
|---|--:|--:|--:|--:|--|--:|
| T‑1 | −0.12% | 28.6% | 0.81 | −0.06 | [−0.53, +0.29] | −6.4% |
| T‑2 | −0.27% | 44.0% | 0.77 | −0.08 | [−1.00, +0.46] | −13.6% |
| **T‑3** | **+0.04%** | 52.4% | 1.03 | **+0.01** | **[−0.82, +0.90]** | −10.8% |
| T‑3 BUY | +0.13% | 52.5% | 1.10 | +0.03 | [−0.74, +0.99] | −10.8% |

**[FACT] No demonstrated edge.** T‑3 is the only non-negative timing, but Sharpe≈0 and the 95% CI
spans zero (and large single-trade losses). SELL n=3–4 → noise. Pre-earnings drift ≈ random here.

## REPORT C — Strategy B: Post-Earnings Reaction (cannot backtest; forward-only)
**Hypotheses (4):** bullish continuation, bearish continuation, bullish reversal after selloff,
bearish reversal after rally — triggered **after** results are public.
**Signals:** EPS/revenue surprise vs consensus, guidance/outlook change, reaction-vs-surprise,
post-release gap, RVOL/unusual activity, VWAP position, EMA 9/21/50, 5m market structure, 15m trend,
breakout/breakdown + failed-breakout reversals, sector/index confirmation, options flow (only if
timestamp-verified).

**[MISSING] Strategy B CANNOT be historically backtested** — it needs **(a) the actual release
timestamp** (we only have a premarket/postmarket bucket) and **(b) historical intraday 5m/15m +
extended-hours prices** (not retained). **Do not manufacture a backtest.** Required instead: a
**genuine forward-recording pipeline** (below). Any claim of profitability without it is invalid.

## REPORT D — 5m / 15m confirmation research
The **live** `intraday_model` already computes VWAP, EMA, ORB, RVOL and 5-min structure — reusable
for a *forward* Strategy-B recorder, **not** for history (no stored intraday bars). Design: 5-min
entry trigger confirmed by 15-min trend; if the two timeframes **conflict → WAIT** (test whether WAIT
improves risk-adjusted outcome — do not assume agreement = profit). Add liquidity-sweep / failed-
breakout detection. All of this must be **forward-recorded** against verified release timestamps.

## REPORT E — Risk management & execution assumptions
Reuse the Phase 5 capital-constrained portfolio: **5% / 10% sizing** (5% cut DD from −7.3%→−3.7%),
**no leverage** (gross ≤ 100%), deterministic capacity rejection, max-concurrent and (to add) max-
sector exposure + daily loss limit. **ATR-based stops / volatility-adjusted sizing** are specified
as parameters; **stops must be modeled with gap-through risk** (a stop does not guarantee the fill —
especially around earnings). No martingale / averaging-down. Costs: spread+slippage+commission+borrow
as in Track A/B. Strategy B stops need intraday data → forward-only.

## REPORT F — Validation plan
- **Strategy A:** partial-factor historical (available now) **+** forward recording. Chronological
  **60/20/20 train/val/test by event date**, event-level grouping, embargo; thresholds fit on train,
  reported once on test; event-level CIs. **Current verdict: samples too small (≤84 events/timing,
  SELL ≤4) for a statistically supported conclusion.**
- **Strategy B:** **forward-only** — build a recorder that captures, per earnings event: the verified
  release timestamp, pre/post surprise, and 1/5/15-min bars from the release; grade reaction outcomes
  going forward. **No historical simulation.** Distinguish **full-factor forward observations** from
  **partial-factor historical simulations** at all times.

## REPORT G — Strategy A vs Strategy B
| Evaluation | **A — Pre-Earnings Momentum** | **B — Post-Earnings Reaction** |
|---|---|---|
| Entry | T‑3/T‑2/T‑1 before earnings | after release |
| Exit | before announcement | intraday / defined window |
| Hypothesis | pre-earnings drift | post-earnings reaction/continuation/reversal |
| Gap exposure | **avoided** | **entered after the gap is known** |
| Data | daily + available factors (**have**) | timestamped release + intraday (**MISSING**) |
| Backtestable now? | **Yes** (partial-factor) | **No** (forward-only) |
| Current evidence | **studyable; no edge found** (Sharpe≈0, CI spans 0) | **none yet** (needs forward data) |

**Which deserves research:** **Strategy B is conceptually the stronger candidate** — it trades the
*actual* reaction (a well-documented phenomenon) and, by entering *after* the gap, sidesteps the exact
risk that sank the hold-through strategy. **But it has zero historical evidence and requires building a
forward data pipeline first.** Strategy A is testable now but, on honest partial-factor + OOS evidence,
**shows no edge** — so it is the lower-priority path unless richer factors/forward data change that.

## REPORT H — Missing data, limitations, development requirements
**Missing:** actual release timestamps; historical intraday 5m/15m; historical extended-hours prices;
historical options/IV/flow/dark-pool/short-interest; estimate-dispersion history. **Survivorship
bias** present (current tickers only). **Small samples** (≤84 events/timing; SELL ≤5 independent).
**To pursue Strategy B** (development, on approval only): a forward recorder for (1) verified
earnings release timestamps, (2) intraday 1/5/15-min capture from release, (3) surprise/guidance
snapshots, (4) graded reaction outcomes — then accrue weeks–months before any claim.

## Recommendation
Pursue **Strategy B as a forward-recorded research strategy** (build the data pipeline first — no
backtest claims), and keep **Strategy A as a low-priority partial-factor study** (no edge demonstrated).
Do not optimize either toward a predetermined profitable outcome. **Do not implement until approved.**
