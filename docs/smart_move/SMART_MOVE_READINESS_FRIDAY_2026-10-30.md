# Smart Move: what can be collected today (Friday 30 October 2026), and what is valid

Done with saved evidence and local checks only. No request was sent, none of the 60 approved requests is used, no window was opened, and the frozen scoring rules are unchanged. Raw output: `app/readiness_today.py` (rerunnable, offline).

## Short answer
- **Collecting today is technically possible, but it would not produce valid predictions, and part of it would be unusable even technically.**
- The frozen design (`prereg_score40_v1.json`, event_eligibility.window): *"capture and prediction between release minus 48 h and the cutoff; later predictions are out-of-window and excluded."* Capture is inside the rule, not only the prediction. Anything retrieved before a window opens is therefore not capture for that event.
- Today's data would also be partial: Friday's session is still running (11:37 ET). The frozen freshness rule needs the last completed session's bar, usable only after close + 45 minutes (16:45 ET).
- **Nothing is lost by waiting.** The market is closed all weekend, so the evidence a Saturday capture reads (Friday's completed session) is exactly what exists after Friday 16:45 ET. A Friday-evening download would be identical data, retrieved outside the window.
- First valid capture time: **Saturday 31 October 07:00 ET** (Monday before-open events); **17:00 ET** for Monday after-close events (including AAPL). The best single time for the approved run is about **17:05 ET Saturday**, when both groups are open.

## 1. Capture windows of the saved upcoming events (calendar saved Fri 11:14 ET, 0.4 h old)
| Event group | Events | Vendor-confirmed (ready) | Window opens (ET) | Window closes (ET) | State now |
|---|---|---|---|---|---|
| Mon 2 Nov, after the close | 110 | 99 | Sat 31 Oct 17:00 | Sun 1 Nov 16:00 | opens in 29 h |
| Mon 2 Nov, before the open | 28 | 22 | Sat 31 Oct 07:00 | Sun 1 Nov 06:00 | opens in 19 h |
| Mon 2 Nov, timing unknown | 78 | 0 | none (evidence-only) | | blocked |
| Fri 30 Oct (today), before the open | 54 | 46 | Wed 28 Oct 06:00 | Thu 29 Oct 06:00 | closed (cutoff passed) |
| Fri 30 Oct, after the close | 1 (+12 unknown) | 0 | closed | | closed |
Tuesday 3 November events are not in the saved calendar yet (the calendar was fetched two trading days ahead); their windows open Sunday 06:00 and 16:00 ET.
(Clock change: US daylight time ends Sunday 1 November 02:00, so window ends on Sunday are shown in EST.)

## 2. Why the system cannot collect usefully today
1. **Window rule (above):** the two groups that matter open in 19 and 29 hours; the events whose windows were open this week (today's reporters) closed their windows on Wednesday/Thursday and cannot be predicted any more.
2. **Session not complete:** the daily candles carry Friday's in-progress bar until the session ends; the live run this morning proved it (the stored candles are classed stale from Friday 16:45 ET on, because they were fetched during the session).
3. **F7 must be captured in the window** (frozen design: "the volatility context captured in the window"); the iv-rank row for Friday is stamped 22:35 UTC (18:35 ET), so even the data itself does not exist before this evening.
4. **Calendar confirmation must be fresh when the prediction is made** (6 h within 4 days). Anything fetched today is 14 to 28 hours old by the time the windows open.
5. The service is also locked by design: kill switch engaged and zero authorization, which the supervised run releases only inside its own process.

## 3. Do the providers return usable history when the market is closed?
Saved evidence says yes for the closed-market case we have seen, not yet for a weekend:
- UW candles requested at 06:34 ET Friday (pre-market) returned 2,258 rows ending Thursday 29 October, complete with pre-market and after-hours rows.
- The 40 volatility contexts (P2) and the first calendar (P1) came back at 09:01 to 09:07 ET, before the open, all complete.
- No request has been made on a Saturday, so a weekend response is untested. The code (and a weekend rehearsal test with a simulated provider on a Saturday night clock) handles the weekend session logic; the first weekend request will confirm the provider side.

## 4. Is Friday's completed session sufficient for Monday's predictions?
Yes, for the evidence the frozen engine scores: with Friday's close, F3 (125 quarters of provider history), F5 and F8 (market and sector) give **30 of 40 points (0.75, above the 0.60 gate)**; F7 adds the last 10 only if captured in the window. The weekend freshness class `LAST_SESSION_WEEKEND` is explicitly allowed by the pre-registration. Rehearsal: a Saturday-night simulated run reaches 40/40 for 10 names inside 60 requests.

## 5. What must be collected inside the window
Everything that feeds a prediction, by the frozen text: the calendar confirmation (fresh), the daily candles, the earnings history, the SPY and sector candles, the volatility context and the iv-rank. Of these, only the **calendar confirmation** and **F7** (context plus iv-rank) are *physically* time-sensitive; the rest equals Friday's close whenever it is fetched after 16:45 ET, but the rule still wants it captured in the window. I enforced this strictly in code today (evidence retrieved before a window opens is treated as missing with reason `RETRIEVED_BEFORE_WINDOW`; test added). One consequence: the AAPL data from this morning's first run is correctly shown as not valid for the real prediction.

## 6 and 7. Local eligibility and readiness (all 284 saved symbols)
| | Count | Why |
|---|---|---|
| **Ready** (vendor-confirmed, known timing, window ahead, sector mapped) | **121** (99 after-close + 22 before-open) | open Sat 07:00 / 17:00 ET |
| Blocked: single vendor | 117 | seen by one vendor only (mostly small names not in Finnhub or Unusual Whales) |
| Blocked: unknown timing | 90 | no BMO/AMC label from any vendor (overlaps the single-vendor group) |
| Blocked: window already closed | 46 | today's reporters, vendor-confirmed but too late |
| Blocked: date disagreement | 1 | vendors differ by one day |
| Ready but no sector | 0 | |
**Preview of the 10 names the run would pick at 17:05 ET Saturday** (largest market caps among ready names with an open window, if the calendar is unchanged; the real choice is recomputed after a fresh calendar): AAPL, PLTR, VRTX, MAR (before open, open since 07:00), WMB, FANG, O, ETR (BMO), IDXX (BMO), PEG (BMO).
Liquidity (adv20 ≥ 20 million dollars) is unknown until candles arrive; the engine applies it then.

## 8. Exact requests for the Saturday run (inside the approved 60; nothing is sent today)
- Calendar (Monday is the only trading day ahead): UW premarket 1, UW afterhours 2 (two pages), Finviz 1, Finnhub 1 = **5**.
- Shared candles: SPY + the sector ETFs of the selected names (XLK, XLV, XLY, XLE, XLRE, XLU) = **7**.
- Per name (10 names): daily candles, earnings history, volatility context, iv-rank = **40**.
- Total **52**, leaving 8 for retries; the governor counts every attempt and stops at 60.

## Decision
No Friday collection is valid, so there is nothing to authorize for today. Waiting costs nothing in evidence. If you want the run unattended at Saturday 17:05 ET I can arrange that on your word; otherwise tell me when you want to run it.
