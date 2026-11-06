# Provider dry run result (production VPS, 2026-10-29)

`outcome_dryrun.py` SHA-256 d496e95b262b9308aa3d0b0876072f37360c9ef9226959866ab0e665a1695566. 22 GET requests (cap 25) of `/api/stock/{symbol}/ohlc/1d`, sequential, background priority; one SELECT in a READ ONLY transaction; output `outcome_dryrun.json` on the VPS. Sample: 22 completed events (470 completed events carry a timing label).

## Real candle shape
Daily candles carry several rows per date: dates with 1 / 2 / 3 rows = 53 / 536 / 261; market_time labels pr 291, po 779, r 838. Within a date the provider returns pr, po, r in that order, and dates newest first. Only the `r` (regular session) row is the official close.

## The legacy grader reads the wrong rows
The replica of the legacy index logic reproduced the stored legacy outcome exactly in 11 of the 12 stored values in the sample (all 8 Before Open, 3 of 4 After Close; OMC differs because it was stamped before the next day's bars existed). So the mechanism is proven:
- After Close: base = the first row of the report date (pre-market or after-hours row), reaction = the next ROW, which is another row of the SAME date. AMKR: 38.65 (pr) -> 38.3194 (po) = -0.86%, the stored value; the regular closes 36.81 -> 39.94 give +8.50%.
- Before Open: reaction = the first row of the report date (the pre-market row), not the regular close.

## Stored legacy outcome vs the regular-close reading (hand-checked from the raw rows for AMKR, BANR, OMC, QCRH, AAT)
| Event | Timing | Stored legacy | Regular close (corrected) | Class changes? |
|---|---|---:|---:|---|
| AMKR 10-26 | AMC | -0.86 | +8.50 | SELL -> BUY |
| BANR 10-21 | AMC | 0.00 | +3.01 | NEUTRAL -> BUY |
| OMC 10-22 | AMC | 0.00 | +0.15 | none |
| QCRH 10-20 | AMC | 0.00 | +0.08 | none |
| AB 10-27 | BMO | +0.03 | +0.03 | none |
| ABT 10-21 | BMO | -0.44 | -1.89 | NEUTRAL -> SELL |
| ALLY 10-20 | BMO | +0.32 | +1.01 | NEUTRAL -> BUY |
| AUB 10-20 | BMO | -0.05 | +1.19 | NEUTRAL -> BUY |
| ARGX 10-22 | BMO | -4.00 | -1.61 | none |
| BCPC 10-23 | BMO | +2.18 | +2.18 | none |
| CNOB 10-22 | BMO | +8.35 | +6.28 | none |
| EFX 10-20 | BMO | +5.63 | +11.33 | none |
Only 2 of the 12 stored values equal the corrected move (AB and BCPC); 5 of the 12 change the BUY/SELL/NEUTRAL class (AMKR, BANR, ABT, ALLY, AUB).

## Events with no stored outcome, graded with the corrected reading
After Close: AAT +1.93, BBNX -16.55 (legacy logic would say +9.97), CASH +3.44, CUBI +7.38, ESS -4.89. Before Open: ABG +12.74, ARCC -0.54, CAC -1.57, CTS +9.83. 21 of 22 sample events grade; WSO.B (share-class symbol) stays PENDING_PRICE.

## Phase 2A fetcher
The "last row per date wins" fetcher gave the same result as the regular-close selection in all 21 gradable events, because the provider happens to return the regular row last. The defect is latent (order-dependent); explicit regular-row selection is still recommended.

## Consequences
1. Every accuracy figure computed from the legacy outcome columns is invalid and must be recomputed from regular-close outcomes.
2. `earnings_trade_service._historical_moves` (the same row logic) feeds model inputs: historical earnings reaction and the implied-vs-realized move ratio, the Claude implied-move gate, and the cached earnings-move history. These model inputs are affected for all earnings models. Not changed here (scoring inputs).
3. The corrected grader works on real data: about 21/22 of sampled completed events grade.
