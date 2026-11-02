# Smart Money Move - earnings signal data readiness audit (2026-11-02, read only)

Method: code and saved-store inspection plus an offline run of the frozen engine on the saved data. No provider request, no collection, no change to Batch #2, the kill switch, production or any frozen file. Only the Smart Money Move frozen engine (score40-v1) is considered; the production prediction models are out of scope.

## 1. What the frozen engine needs (read from `scorer.py` and `prereg_score40_v1.json`)
* 40 points in four factors, scored separately for two variants (continuation = primary, contrarian): **F3** earnings-reaction history 15 (mean reaction 8, up/down consistency 7; needs at least 4 earlier reactions), **F5** price/technical 10 (20-day trend, relative strength vs SPY, extension vs 50-session mean), **F7** volatility skew label 10 (optional; only from a prospective capture taken inside the event window), **F8** market/sector 5 (SPY 20-day return 2, sector ETF vs SPY 3).
* **BUY** = bull >= 14 and bear <= 4 and bull - bear >= 10; **SELL** = the mirror; anything else NO TRADE (CONFLICT, BELOW_MARGIN). No scaling to 100.
* **Gates that force NO TRADE (each a reason code):** capture outside the 48 h to 24 h window; announcement session not BMO/AMC; not tradable; **liquidity: 20-day average dollar volume must be known and >= 20,000,000, and it comes only from the stock's own price bars (so F5 is mandatory)**; stale evidence; **coverage = available points / 40 must be >= 0.60 (24 points)**.
* **So not all four categories are mandatory.** Mandatory: price history (F5, for the liquidity gate and 10 points), SPY bars (relative strength), and enough other points to reach 24: F3 + F5 = 25 passes; F5 + F7 + F8 = 25 also passes. F7 is optional. Without F3 and without F7 only 15 points are possible, so a valid signal needs F3 or F7 in addition.
* **Freshness rules (smart_move_live):** stock/SPY/ETF bars must be the V1 evidence kind `ohlc` with `timeframe=3Y`, last bar on or after the latest completed session and retrieved after that session closed; earnings payload = kind `earnings`, `limit=24`, not older than 7 days; vol context and IV rank = kinds `vol_context` / `iv_rank` for the expected session date, at most 24 h old, and for F7 captured inside the event window.

## 2. Existing data inventory (all nine saved evidence stores)
| Input | Status | Source and freshness |
|---|---|---|
| Earnings reaction history | **PARTIAL, unusable for Nov 3/4** | V1 `earnings` payloads for 122 symbols, retrieved 2026-10-31 (age 2 days, inside the 7-day rule), but only **1** symbol (ROKU, Nov 3) overlaps the 783 Nov 3/4 events; reaction history also needs fresh price bars (F3 uses them). |
| Price and technical (OHLCV, EMA, volume, trend) | **PARTIAL, all stale** | 3Y daily bars for 134 symbols (122 stocks + SPY + 11 sector SPDR ETFs), retrieved 2026-10-31, **last bar 2026-10-30**: 0 of 134 reach the expected session (2026-11-02). Only 1 symbol overlaps the Nov 3/4 events. QQQ: no saved bars (the engine does not use QQQ). |
| Volatility (IV rank, skew, vol context) | **MISSING for these events** | `iv_rank` and `vol_context` exist for 122 symbols from 2026-10-31 but 1 overlaps Nov 3/4, they are for another session date, and F7 accepts only a prospective in-window capture. No saved volatility history exists. |
| Market context (SPY, sector ETFs, trend) | **PARTIAL, stale** | SPY and 11 sector ETFs have 3Y bars with last bar 2026-10-30 (stale for any capture after the Nov 2 close). The sector of each event comes from the saved calendar (all 236 confirmed events have a sector). |
Nothing can be reconstructed from current snapshots: the Batch #2 job types (`uw_earnings`, `uw_ohlc` 1M) are different evidence kinds and a 1-month window, so **the frozen engine does not read them.**

## 3. November 3 and 4 coverage (783 saved events)
| | Nov 3 | Nov 4 | Total |
|---|---|---|---|
| Saved events | 303 | 480 | 783 |
| Vendor-confirmed with known BMO/AMC | 121 (all AMC) | 115 (all BMO) | 236 |
| Unverified (estimated, single vendor, unknown timing) | 182 | 365 | 547 |
| With sufficient existing data for a valid signal | 0 | 0 | 0 |
| Missing price history | 303 | 480 | 783 |
| Missing earnings-reaction history | 302 (ROKU has a saved payload but its bars are stale, so F3 is still unusable) | 480 | 782 |
| Missing volatility | 303 | 480 | 783 |
| Missing market information (fresh) | 303 | 480 | 783 |
| Currently eligible for a valid signal | 0 | 0 | 0 |
**Window reality:** Nov 3 confirmed events are all After Close; their prediction window closes at **21:00 UTC today**. After that no valid prediction can be made for them (OUT_OF_WINDOW); a prediction is never backdated. Only the 115 confirmed Nov 4 Before-Open events keep their window open tonight (to 11:00 UTC on Nov 3). The 235 Nov 4 After-Close events are not vendor-confirmed.

## 4. Current signal readiness (frozen engine run offline on the saved data)
**0 valid BUY, 0 valid SELL, 0 genuinely neutral NO TRADE, 783 blocked by missing data.** For the 236 confirmed events: 0 of 40 points available (F3, F5, F7, F8 all missing), reason codes ILLIQUID_OR_UNKNOWN and INSUFFICIENT_COVERAGE. A NO TRADE caused by missing data is reported as "no valid prediction", not as a neutral view. No confidence is shown.

## 5. Collection requirements (a future plan; the signed Batch #2 allocation is NOT changed)
Per stock: 1 request for the 3-year daily bars (`ohlc`, 3Y) and 1 for the earnings payload (`earnings`, limit 24) = F3 + F5 (25 points) plus F8-market (2). Shared and reusable across all stocks: SPY bars (1) and one bar request per distinct sector ETF (7 sectors for the top 19, 5 for the top 9). Optional F7: vol context + IV rank = 2 more per stock, captured inside the window. Nothing in the saved stores can be reused (stale or other events).
| Plan (requests incl. 4 for the calendar refresh) | Stocks that get complete inputs | Requests |
|---|---|---|
| A: F3 + F5 + F8 market | **22** | 45 + 4 = 49 of 50 |
| B: A + sector ETF (adds F8 sector 3 pts) | **19** | 46 + 4 = 50 of 50 |
| C: B + F7 | **9** | 42 + 4 = 46 of 50 |
These are upper bounds with 0 to 1 spare attempt and retries off; one failure removes a stock. "Complete inputs" does not mean a BUY or SELL: a valid signal also needs bull >= 14 with bear <= 4 (or the mirror), liquidity >= 20,000,000, at least 4 earlier reactions, and the capture inside the window; most stocks will still end NO TRADE (neutral) which is a genuine, valid outcome once the inputs are complete.
**Priority:** Nov 3: none can be served after 21:00 UTC today (window closed); Nov 4: the 115 confirmed Before-Open events, ranked by market cap as a selection heuristic only (the liquidity gate decides, from the bars). Candidates: `FUTURE_PLAN_priority_candidates_nov4_2026-11-02.json` (top 30; first names MCD, NVO, UBER, CVS, EMR, DDOG, GWW, COR, CVE, MLM). Run after the 16:00 ET close so bars reflect the Nov 2 session, then capture inside the window (before 11:00 UTC on Nov 3).
**Needs new code and owner approval:** the batch runner has no job that writes the engine's evidence kinds (`ohlc` 3Y, `earnings` 24, SPY and sector ETF bars). The Phase 14 `uw_earnings` / `uw_ohlc` (1M) jobs do not feed the engine and would need to be replaced or supplemented by jobs for the V1 kinds.

## 6. Dashboard requirements vs the current page
Requested: Symbol, Company, Earnings Date, Session, BUY/SELL/NO TRADE, Signal Reason, Data Completeness, Last Updated, Model Version, Smart Money Move only. The current page `/smart-move/signals` has Symbol, Company, Date, Session, Signal, Reason (detail row), Updated, Data status. **Gaps:** (1) a Data Completeness column (available points of 40 and the four-factor checklist, from the engine's own output); (2) a Model Version column (`score40-v1` plus the pre-registration hash prefix); (3) the Reason should appear in the table, not only the detail row; (4) the Confidence column should be removed (it is always "not calibrated"); (5) **the production-model adapter and the unanimity rule built in the last step must be removed**: Smart Money Move uses only its own two variants, with continuation as the primary signal and contrarian shown separately.

## 7. Final report
1. Inventory: section 2. 2. Missing inputs: price bars (fresh), earnings history for the events, volatility, fresh market bars. 3. Readiness: 0 valid signals. 4. Nov 3: 303 events, 121 confirmed (AMC), windows close 21:00 UTC today, 0 serviceable. 5. Nov 4: 480 events, 115 confirmed (BMO) serviceable tonight, 365 unverified. 6. Requests: 2 per stock + 1 SPY + sector ETFs (+ 2 per stock for F7) + 4 calendar. 7. Maximum within 50: 22 / 19 / 9 (section 5). 8. Future plan: section 5 (needs approval). 9. Blockers: Batch #2 allocation does not feed the engine; no job for the engine's evidence kinds; Nov 3 windows closing; nothing reusable; page columns; unapproved removal of the production-model code. 10. Next steps: owner chooses plan A, B or C and whether to run it after Batch #2 or instead of its remaining allocation; approve the new job kinds; approve page changes; run after the close; then score with the frozen engine and show results labelled by status.

## FINAL STATUS: **NOT READY - MISSING DATA**
