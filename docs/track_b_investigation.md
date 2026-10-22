# Track B Investigation — Earnings Engine Performance, BUY Bias, SELL & T‑3

Status: **investigation only — no scoring weights, thresholds, deploy, or live trading changed.**
All results are **SIMULATED · PARTIAL‑FACTOR** (only ~30/100 of the model is reconstructable
point‑in‑time). Legend: **[FACT]** verified · **[ASSUMPTION]** · **[SIM]** simulated · **[MISSING]**.

Code: `backend/historical_backtest_service.py`, `backend/forward_scoreboard_service.py`.
Tests: `backend/tests/test_historical_backtest.py` (+ `test_forward_scoreboard.py`) — 26 PASS.

## REPORT A — Portfolio performance reconciliation
The "T‑3 is +0.13% per trade but −34% portfolio" paradox has two causes, both now quantified:

1. **All‑model mean ≠ consensus.** T‑3 **all‑model** avg net **+0.13%** [SIM] *includes the profitable
   SELLs and every model's vote*. The portfolio trades the **majority‑vote consensus**, which is
   BUY‑dominated: T‑3 **consensus‑only** avg net = **−0.27%** (n84, win 50%, PF 0.92).
2. **Sizing artifact.** The old portfolio was **fully invested, equal‑weight, daily‑compounded** —
   effectively 100% in whatever few names were open, which amplifies. Recomputed with **realistic
   fixed‑fractional sizing (10%/trade, ≤10 concurrent, cash otherwise):**

   | Timing | Fully‑invested (old) | **Realistic (10%/trade)** |
   |---|---|---|
   | T‑1 | −36.6% / DD −60.2% | **−3.4% / DD −11.1%** |
   | T‑2 | −41.5% / DD −47.7% | **−6.5% / DD −9.0%** |
   | T‑3 | −34.4% / DD −39.5% | **−2.5% / DD −7.4%** |
   | All | −31.7% / DD −41.9% | **−11.9% / DD −25.1%** |

   **The realistic numbers are the honest read.** Both are preserved on the page.
   Verified: `test_fixed_frac_less_extreme_than_fully_invested`, `test_fixed_frac_compounds_fixed_fraction`.

## REPORT B — SELL profitability investigation
SELL looks great (prediction-level avg +3.01%, PF 4.15) but is **not statistically reliable** [FACT].
Event-level (consensus, dedup to independent events):
- 33 SELL predictions = **only 5 independent events** (consensus SELL = 10 rows / 5 events).
- **2 profitable events, 3 LOSING events** — the positive average is driven by 2 winners:
  ORCL 2026‑06‑10 **+12.58% (Services)**, TSLA 2026‑07‑22 **+9.27%**; losers TSLA −0.02%, TSLA −1.86%,
  INTC −3.54% (all Manufacturing).
- Per timing (tiny n): SELL T‑1 +6.92% (n3), T‑2 +2.30% (n3), T‑3 +1.35% (n4).
- **Event-level mean +3.29%, SD 6.42, SE 2.87 → rough 95% CI [−2.46%, +9.03%] — spans zero, n=5.**
- **Conclusion: no credible SELL edge.** Majority of SELL *events* lost money; the positive mean is two
  outliers (one a Services name), and the confidence interval includes zero and large losses.

## REPORT C — Structural BUY bias root cause
[FACT] Directional score distribution over 38,652 live predictions is **right‑shifted** (peak 55–64):
- **score < 45 (SELL zone): 10.0%** vs **score ≥ 55 (BUY zone): 65.3%**.
- Per model (deduped): BUY 9,467 / SELL 1,432 / NEUTRAL 4,052; grok 86% BUY, ped NEUTRAL‑heavy.

Cause (evidence‑based, **not changed**): the engine is **centered at 50 and normalized by covered
weight**, and **missing factors are excluded (not scored 0)** so they never drag a score below 45;
with bullish base rates for most reporters, scores rarely reach the SELL zone. It is a
**threshold + centering + missing‑factor‑exclusion** effect, not a sign/mapping bug (SELLs do occur).

## REPORT D — T‑3 / T‑2 / T‑1 comparison (same event population)
Consensus, signed by side, decomposed into pre‑announcement drift vs the announcement gap [SIM]:

| Timing | Pre‑announcement move | Announcement gap | Prediction win% | Realistic DD |
|---|---|---|---|---|
| T‑1 | −0.02% | −0.28% | 42% | −11.1% |
| T‑2 | −0.17% | −0.45% | 48% | −9.0% |
| T‑3 | **+0.14%** | −0.28% | 52% | **−7.4%** |

- **The announcement gap is the loss driver** (negative at every timing) for a BUY‑biased book.
- **T‑3's only edge is positive pre‑announcement drift**, partly offsetting the gap → least bad.
- **Do not declare a winner:** n ≈ 84 events per timing, one universe, partial factors.

## REPORT E — Historical data quality audit
- **[FACT] Point‑in‑time & look‑ahead:** entry uses only data on/before T‑k; verified by tests.
- **[LIMITATION] Survivorship bias PRESENT:** UW `earnings_history` returns current tickers only, so
  delisted/acquired names in the window are absent — results skew to survivors.
- **[ASSUMPTION] Earnings date:** UW `report_date`; **publication timestamp UNVERIFIED** (assumed
  AMC 16:00 / BMO 08:00 ET; no actual announcement timestamp exists in the feed).
- **[SIM] Execution:** exit = first executable **daily** price after the announcement (next‑session
  open / BMO gap‑open). **No historical extended‑hours prices** → the exact after‑hours fill cannot
  be modeled. Costs are assumptions.
- **[MISSING] ~70% of the model is absent:** options flow, IV, unusual activity, disparity, dark
  pool, short interest, guidance, estimate revisions. **Label everything SIMULATED · PARTIAL‑FACTOR.**

## REPORT F — Proposed scoring improvements (NOT implemented)
Each: problem → evidence → proposed correction → benefit → risk → validation → comparison.

1. **SELL threshold calibration.** *Problem:* only 10% of scores reach ≤45, so SELLs are rare.
   *Evidence:* Report C histogram (median ~57). *Correction:* consider a distribution‑aware SELL
   cutoff (e.g. a lower percentile) **as a separate experimental profile**, not the live one.
   *Benefit:* more balanced BUY/SELL. *Risk:* more false SELLs; may just trade noise. *Validation:*
   chronological out‑of‑sample (below). *Compare:* against the unchanged live model on held‑out events.
2. **Missing‑factor normalization.** *Problem:* excluding missing weight biases scores upward.
   *Evidence:* Reports A/C; coverage is low historically. *Correction:* test a "shrink‑to‑neutral"
   (pull the score toward 50 proportional to missing weight) **in a profile.** *Benefit:* fewer
   over‑confident calls on thin data. *Risk:* fewer actionable signals. *Validation:* OOS. *Compare:*
   calibration (predicted vs realized) old vs new.
3. **Bearish‑factor recognition.** *Problem:* bearish evidence rarely flips the call. *Evidence:*
   Report C. *Correction:* audit per‑factor bias sign ranges for asymmetry (experiment only).
   *Benefit:* genuine SELLs. *Risk:* overfitting. *Validation:* OOS. *Compare:* SELL precision.
4. **Consensus confidence gate.** *Problem:* conflicting votes (22% of events) still trade. *Correction:*
   require a vote margin / min agreement before taking a consensus position. *Benefit:* skips low‑
   conviction events. *Risk:* fewer trades. *Validation:* OOS win‑rate by margin bucket.
5. **Data‑coverage requirement.** *Problem:* thin‑coverage names trade anyway. *Correction:* block
   high‑confidence calls below a coverage floor (already wired in Data Health — extend to the call).
   *Benefit:* avoids guessing. *Risk:* fewer signals. *Validation:* performance by coverage bucket.
6. **Earnings‑gap risk filter.** *Problem:* the gap is the loss driver (Report D). *Correction:* a
   separate gap‑risk score (historical gap dispersion, implied move) that **sizes down or skips**
   high‑gap‑risk events — beside the direction score, never inside it. *Benefit:* cuts the biggest
   loss source. *Risk:* skips winners too. *Validation:* OOS return/DD with vs without the filter.

**Chronological out‑of‑sample validation method (for any change):**
split events by time (e.g. train = oldest 60%, validation = next 20%, test = newest 20%); tune only on
train/validation; report the **test** slice once; never tune thresholds on the same sample used to
discover the problem; compare every variant to the **unchanged live model** on identical held‑out
events, measuring win rate, net return, profit factor, realistic‑portfolio DD, and calibration.

## REPORT G — Automated test evidence
`pytest tests/test_historical_backtest.py tests/test_forward_scoreboard.py` → **26 passed**. New Track B
tests: partial‑factor reconstruction, look‑ahead‑safe beats, cost/sign, no duplicate‑event drawdown
inflation, fixed‑fractional compounding, concurrency cap, and realistic‑vs‑fully‑invested sizing.

## Bottom line
The engine loses money in this partial‑factor simulation mainly because it is **long into almost every
event** and **the earnings gap is negative** for that long bias; costs are minor and the −34% was a
**sizing artifact** (realistic sizing ≈ −2% to −12%). The **SELL and T‑3 "advantages" are not reliable**
(5 correlated SELL events; T‑3 only marginally better via pre‑drift). The BUY bias is a **structural
score‑distribution effect**. Improvements are proposed but **not implemented**, pending explicit
approval and proper out‑of‑sample validation.
