# Phase 2A outcome-data blocker: investigation (interim, before the read-only production diagnostic runs)

No provider calls, no production writes, no schema change, no scoring change. Findings below come from the code and from the earlier read-only production output; the production diagnostic (`outcome_diag.py`, SHA-256 37f1f56cfc1ad9ba5eb0800bc6237eb613a78e0c8baae9930e8f1f1b6d1ace34) is staged but NOT yet run.

## 1. Exact source of prior-session and reaction-session closes
- Both the legacy grader (`earnings_trade_service._historical_moves`) and the new grader (`earnings_outcomes.default_bars_fetcher`) read the provider's daily candle endpoint `/api/stock/{ticker}/ohlc/1d` through `unusualwhales_service.candles` (limit up to 2,500 rows).
- The responses are cached only in process memory (`live_market_service.cache`, TTL 6 h for daily candles, 5,000 entries). **No raw daily bars are stored in the database**, so there is nothing stored to read for a recent event without a provider request.
- Stored derived outcome data that does exist: the legacy outcome columns of `model_predictions`; the cached earnings-move history inside `earn:trade:default:{SYM}` (`historical_moves`, refreshed only while the name is on the earnings calendar); `earnings_calendar.post_earnings_move_percent` (62 rows since 13 Oct, none with a value); `score_snapshots.price` (a few watched names).

## 2. Cause of the "0 usable outcomes" in the preview
| Candidate cause | Evidence |
|---|---|
| Absent market data | Not shown. The preview never looked at price data; it had no price source to read |
| Wrong symbol mapping | None seen (diagnostic reports symbols with non-alphabetic characters and events with no cache) |
| Missing calendar moves | **Yes, the direct cause**: 62 calendar rows, all with a NULL move (`no_move: 62`) |
| Date mismatches | 0 (`reaction_date_mismatch: 0`) |
| Reaction window | 243 events correctly withheld (68 Before Open + 175 After Close) |
| The preview's own restriction | **Yes**: it could only use the calendar table, and made no provider calls by design |
So "0 usable" means "the preview's only outcome source is empty", not "prices are missing". In the preview output the label `missing_price` meant "past the reaction close and not in the calendar table".

## 3. A defect found in the new grader's price fetcher (not yet in production)
The provider's daily feed can send several rows per date (pre-market `pr`, regular `r`, post-market `po`); `ticker_overview_service` documents this and keeps the regular row. `default_bars_fetcher` kept the LAST row per date. Synthetic payload in the documented shape (After Close report; regular closes 100.0 then 106.0; after-hours 108.0 on the earnings): correct move +6.0%; the fetcher as written computes -1.48% (wrong sign, base contaminated by the after-hours row); the legacy `_historical_moves` computes +0.5%. If production returns multiple rows per date, the legacy outcomes and every model input derived from `_historical_moves` (historical reaction, implied-vs-realized) are affected too. Whether production returns multi-row dates cannot be seen without a provider response.

Smallest safe fix (prototype tested locally, NOT applied): `regular_session_closes()` - use the regular row (`market_time == 'r'`), fall back to the highest-volume row only when no row on that date carries `market_time`, and leave a date out (event stays pending) when only pre-/post-market rows exist. 62-line patch and 8 tests in `docs/audit_evidence_phase2a/proposed/`; with it the earnings, scoreboard and scoring-pin tests (58) pass. The legacy function and anything feeding model inputs are NOT touched.

## 4. Never permanently failed
Statuses the new grader can assign: GRADED, PENDING_REACTION, PENDING_PRICE, PENDING_TIMING (no terminal failure status exists). `retry_due` returns True again after at most 7 days for every non-graded event, forever (tested: `test_retry_schedule_backs_off_but_never_stops`, `test_missing_price_is_pending_price_never_unverifiable`, `test_missing_timing_label_and_fetch_failure_do_not_crash_or_terminate`). The scoreboard shows such events as "outcome missing", not as failures.

## 5. The two populations
- "380 missing-price reported events" = every reported event with a scored-model snapshot that is past its reaction close (Before Open 169 + After Close 163) or has no timing label (48), whatever the model.
- "309 per main model" = the subset of that union for which that model has an eligible pre-release snapshot. The remaining 71 events have no eligible snapshot for it. The diagnostic prints the exact split (post-release only, other models only, retrospective only).

## 6. Interim readiness: FAIL
Blockers: (a) grading not yet validated against real prices; (b) the fetcher defect above must be fixed before any grading run; (c) the diagnostic result (stored-outcome coverage and the sample audit) is pending.
