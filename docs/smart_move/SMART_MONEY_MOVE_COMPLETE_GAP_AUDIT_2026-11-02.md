# Smart Money Move - complete data and gap audit (2026-11-02 20:05 UTC; read only)

No provider request was made; Batch #2, its allocation, the kill switch, concurrency, retries and the 50-attempt ceiling are untouched; only the frozen Smart Money Move engine (score40-v1) is considered. Stock-by-stock detail for all 783 events: `SMART_MONEY_MOVE_STOCK_BY_STOCK_GAPS_2026-11-02.csv` (one row per event, sorted by date, confirmed first, then priority rank).

## 1. Inputs the engine reads (unchanged from the previous audit)
F3 earnings-reaction history (15 pts), F5 price/technical (10, mandatory for the liquidity gate), F7 volatility skew label (10, optional, prospective in-window capture only), F8 SPY / sector ETF (5). BUY needs bull >= 14, bear <= 4, margin >= 10; SELL the mirror; coverage >= 24 of 40 points; liquidity (20-day dollar volume) >= 20,000,000 from the stock's own bars; capture inside the 48 h to 24 h window; session BMO or AMC. Evidence kinds read: `ohlc` 3Y (stock, SPY, sector ETF), `earnings` limit 24, `vol_context`, `iv_rank`.

## 2. Existing data (all nine saved evidence stores, all cached provider responses)
| Input | Status | Provider and last update |
|---|---|---|
| Earnings date and BMO/AMC session (the 783 events) | AVAILABLE for 236 (vendor-confirmed), PARTIAL for 547 (estimated, single vendor, or unknown timing) | Unusual Whales calendar, Finnhub calendar, Finviz calendar; saved 2026-11-02 (pilot) |
| Historical earnings reactions / previous earnings dates / EPS surprise history | MISSING for 782 events, AVAILABLE for 1 (ROKU, retrieved 2026-10-31) | Unusual Whales `/api/earnings/{ticker}`; the saved sample payload has 125 rows per stock with `post_earnings_move_1d/3d/1w/2w`, `pre_earnings_move_*`, `actual_eps`, `street_mean_est`, `expected_move`, straddle results. |
| 3-year daily OHLCV, volume, liquidity, EMA/trend inputs | MISSING for 782, STALE for 1 (ROKU, last bar 2026-10-30) | Unusual Whales `/api/stock/{ticker}/ohlc/1d`; the saved bars hold open/high/low/close/volume. EMA and trend are computed from them by the engine; no indicator is stored. |
| Implied volatility, IV rank | MISSING for 782, STALE for 1 | Unusual Whales `/api/stock/{ticker}/volatility/context` (iv30, iv_rank, iv_percentile, iv_rv_ratio, skew label, term structure) and `/iv-rank` |
| Options volatility skew | MISSING for 782, STALE for 1 | same context payload (provider-defined skew label); the engine's F7 accepts only a prospective capture inside the event window |
| SPY | STALE | UW OHLC, 3Y, last bar 2026-10-30 (retrieved 2026-10-31) |
| Sector ETFs (11 SPDRs) | STALE | UW OHLC, last bar 2026-10-30. The saved calendar gives a sector for only 265 of the 783 events (518 have no sector mapping, so their F8 sector sub-signal is MISSING by the frozen rule). |
| Data freshness | all provider evidence older than the latest completed session | nothing is reusable for a post-close capture on Nov 2 |

## 3. Unusual Whales vs FINVIZ vs both
| Missing input | Unusual Whales | FINVIZ | Unavailable from both |
|---|---|---|---|
| Earnings reaction and EPS surprise history | YES: `/api/earnings/{ticker}` (implemented in the fetcher as kind `earnings`) | no | |
| 3Y daily OHLCV, volume, liquidity | YES: `/api/stock/{t}/ohlc/1d?timeframe=3Y` (implemented, kind `ohlc`) | **Not implemented and not verified.** The integration reads only the earnings-date column (`export.ashx`, columns Ticker and Earnings Date). Finviz screener columns are current snapshots, not point-in-time history; a historical-candle export was NOT confirmed (my documentation search found nothing). Do not assume it. | |
| IV, IV rank, skew label | YES: `/volatility/context` and `/iv-rank` (implemented) | no | |
| SPY and sector ETF bars | YES (same OHLC endpoint, implemented) | not verified | |
| Earnings date and BMO/AMC | YES (UW calendar `premarket`/`afterhours`) | YES: earnings date with a clock time (implemented; reported 95 of the 783 events) | |
| Point-in-time IMPLIED volatility or skew for past events | not supported (current values only) | not supported | **YES, unavailable: cannot be reconstructed.** F7 only works from a prospective in-window capture. |
Subscription permissions: the Unusual Whales endpoints above have already returned data under this token (earnings, OHLC, vol context, IV rank: saved Oct 31). FINVIZ is configured with an Elite export token; what else it permits is not documented in the repository and not verified: owner confirmation needed. An additional constraint: the frozen engine computes F3 from OHLC (not from the provider's `post_earnings_move` fields), so those fields are available but unused.

## 4. Stock-by-stock summary (783 events audited)
| | Count |
|---|---|
| Total earnings stocks audited | **783** (Nov 3: 303, Nov 4: 480) |
| Complete data | **0** |
| Partial data | **1** (ROKU: payload fresh, bars stale) |
| Missing data | **782** |
| Fresh price-history coverage | 0 |
| Earnings-reaction coverage (fresh) | 1 (ROKU), but F3 also needs fresh bars, so 0 usable |
| Volatility coverage (in-window) | 0 |
| Market-data coverage (fresh SPY and ETFs) | 0 |
| Vendor-confirmed with known session | 236 (Nov 3 After Close 121; Nov 4 Before Open 115) |
| Confirmed AND prediction window open at 21:45 UTC tonight | **115** (all Nov 4 Before Open; windows close 11:00 UTC Nov 3) |
| Confirmed with window already CLOSED | 121 (all Nov 3 After Close; cutoff 21:00 UTC today) |
| Unconfirmed / unknown session | 547 (190 events have no session at all) |
Signal readiness today (frozen engine, offline): **BUY 0, SELL 0, genuine NO TRADE 0.** NO VALID PREDICTION: 783 = 441 event not confirmed or session unknown, 227 window closed (never backdated), 115 missing data (the confirmed, open-window Nov 4 events). Every row has 0 of 40 coverage points and an unknown liquidity gate.

## 5. Requests required (UW, per endpoint above)
| Scope | F3 + F5 (2 per stock) | + shared | + F7 (2 per stock) |
|---|---|---|---|
| The 115 confirmed Nov 4 Before-Open events (the only serviceable set tonight) | 230 | SPY 1 + up to 11 sector ETFs = 12 -> **242** | +230 -> 472 |
| All 236 confirmed events | 472 | 484 | 956 |
| All 783 events | 1,566 | 1,578 | 3,142 |
**Within the 50-attempt ceiling** (4 attempts for the calendar refresh, retries off): plan A (F3 + F5 + F8 market) 22 stocks (49 attempts), plan B (adds the sector ETF) 19 stocks (50), plan C (adds F7) 9 stocks (46). Reusable: nothing from the saved stores; shared: SPY and the sector ETFs serve every stock. The 115 stocks cannot all be served in one 50-attempt batch (about 242 requests, i.e. 5 batches of 50); the Nov 4 window closes at 11:00 UTC on Nov 3, so the first batch is the only one that can still produce in-window predictions for them.
**Cannot be collected:** valid predictions for the 121 Nov 3 After-Close events (window closed); any point-in-time volatility for past events; F3/F5 for events with an unknown session (no window can be defined).

## 6. Priority and ordering (collection jobs prepared only; nothing executed)
1. Nov 4 Before Open, confirmed, window open: rank by market cap as a selection heuristic only (the liquidity gate decides from the bars); ranks are in the CSV (`priority_rank_nov4_open_window`, 115 rows).
2. Nov 3: none serviceable after 21:00 UTC today.
3. Shared requests first (SPY, then the sector ETFs of the chosen stocks), then per stock `ohlc` 3Y and `earnings` 24, run after the 16:00 ET close (21:00 UTC; 01:00 Dubai) so bars reflect the Nov 2 session, then the frozen engine captures inside each event's window.
The collector does not yet have jobs that write these evidence kinds; they are described, not built, and need owner approval. The signed Batch #2 allocation (calendar 4, earnings payloads 12 in a different kind, Snapshot B 14, news 17) is unchanged and does not feed the engine.

## 7. Final report answers
* **What data do we have?** Calendar data for 783 events (236 confirmed); stale Oct 30 price, earnings, volatility, SPY and sector-ETF data for other stocks; nothing usable for these events.
* **What is missing?** Fresh 3Y bars, earnings history, in-window volatility, SPY and sector bars for every one of the 783 events.
* **Can Unusual Whales or FINVIZ provide it?** Unusual Whales can provide every engine input (the endpoints are implemented and worked before). FINVIZ today provides only earnings dates and times; its use for history is unverified and snapshot-only. Past in-window implied-volatility evidence cannot be obtained from either.
* **How many requests?** 242 for the 115 serviceable events (about 5 batches of 50), 1,578 for all 783; at most 22 stocks inside one 50-attempt batch.
* **Can Smart Money Move generate valid signals?** Not now: 0. After a correctly planned post-close collection, up to about 22 Nov 4 stocks could receive complete inputs and the frozen engine would then return a genuine BUY, SELL or NO TRADE for them; most will be NO TRADE. The remaining 761 events cannot receive a valid prediction in this cycle.
* **After the market close:** (1) the owner approves a collection plan for the engine's evidence kinds and the new job types (separate from Batch #2); (2) signed attestation and GO as before; (3) fetch SPY, ETFs and the top Nov 4 stocks after 21:00 UTC and before 11:00 UTC on Nov 3; (4) run the frozen engine inside each window and show the results labelled by status.

## FINAL STATUS: **NOT READY - MISSING DATA**
