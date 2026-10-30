# Smart Move - provisional capacity 220 core + 30 full, calendar validation, selection and implementation sequence (2026-10-31)

Design only. **Provisional capacity design: 220 core events/day with up to 30 full-capture events/day. Not activated.** No provider request, no production database change, no migration, no recurring job, no push or deployment, no change to the existing models, no live signal. The continuation and contrarian variants and the frozen pre-registration `b25bd8d7...b87` are unchanged; the baseline validation and test data stay sealed.

## 1. Independent review of the VPS report
Limit first: **I only have the printed summary, not the JSON file** (the `top_10_days`, past-only grid and weekly distribution were not sent back), so the checks below are consistency and arithmetic checks on the printed numbers, plus inference where stated. A review of the JSON itself needs the small read-only command from my previous message.
| Check | Result |
|---|---|
| Event arithmetic | raw 1,610 - 1 confident merge = 1,609 deduplicated; past 888 + future 721 = 1,609; timing 701 + 820 + 88 = 1,609; the 878-symbol events 911 = 888 past + 23 future (timing 84 + 401 + 426 = 911); warmer-queue events 348 = 20 + 328 (5 + 175 + 168 = 348); 878 + 696 non-U878 symbols = the 1,574 symbols (the 698 extra events are all future) |
| Date shifts | 1 confident merge (FIS 3 -> 4 Nov) and 11 uncertain pairs kept separate (TRV, AXP, KO, PM, INTC, PG, VZ, BA, CAT, DAR, AAPL); gaps of 1 day (TRV, PM, PG, BA, CAT, DAR), 2 (VZ), 4 (AAPL) and 7 days (AXP, KO, INTC). All 11 are `CONCURRENT_PREDICTIONS`: two dates were being predicted at the same time, i.e. **the application's own sources disagreed about these events' dates**. The count is an upper bound by at most 11 events (0.7 %); for the 9 pairs dated on or before 30 Oct the real date can be settled after the fact from the outcome rows and the SEC 8-K 2.02 date |
| Peak request estimates | all printed values reproduce exactly with requests = min(n, cap) x 5 + min(n, 30) x 13 + 24 for the busiest day: cap 30: 564; 70: 764; 115: 989; 170: 1,264; 195: 1,389. **At 220 the printed 1,494 uses the observed peak (216 events), not the ceiling**: a day with 220 events would need 220 x 5 + 390 + 24 = **1,514** |
| Coverage figures | consistent with the peak: lost events at 195 = (1 - 0.978) x 1,609 = about 35, and for the 878-symbol events (1 - 0.977) x 911 = about 21 = exactly the 29 Oct excess over 195 (216 - 195 = 21), so in that set only the peak day exceeds 195; the extra 14 lost in the all-symbol view must come from a **future day with more than 195 events** (inference; not visible in the printed summary). At 220 coverage is 100 % because the observed peak is 216 |
| Weak points | future days are partial (events are seen only about 21 days ahead and 379 are projected), so the future part of the coverage figures is soft; 216 on 29 Oct is one observed peak, not a maximum; the per-date ceiling model ignores that a capture window is 24 h long (section 5) |

## 2. Calendar validation: confirmed, estimated, stale, moved, unknown
Evidence from the data: of 721 future events, 379 (53 %) are projected or unconfirmed: 364 not refreshed for 7 days, 26 with unknown timing, 24 first seen more than 45 days ahead, 2 in a date shift; 88 events (5.5 %) have unknown timing overall; the UW earnings rows already carry `source = company | estimation` (34 of 36 upcoming rows of the pilot sample were `estimation`).
**Date status per event (stored with every event version, with its evidence):**
| Status | Rule |
|---|---|
| CONFIRMED | the provider marks the date as company-announced (`source = company`) **and** a second independent source gives the same date (Finviz export date, already integrated), checked within the last 48 h |
| ESTIMATED | only an estimated date (`source = estimation`) or a single source; usable for scheduling, never for a "confirmed" label |
| MOVED | a refresh returned a different date: the old version is closed, a new event version opens (window recomputed); if the new window is still open the event is re-captured, otherwise it is MISSED / MOVED_TOO_LATE |
| STALE | no successful refresh within its freshness limit (D-10 to D-4: 24 h; D-4 to window open: 6 h; inside the window: at capture time) |
| UNKNOWN | no date, or two sources that conflict by more than one day, or the event is outside any source's calendar |
**Timing status:** CONFIRMED_TIMING (UW session label and the Finviz clock string agree: premarket/before 09:30 or after 16:00), SINGLE_SOURCE, CONFLICT (sources disagree), UNKNOWN (88 events today). Per-event, never per-symbol (sessions alternate between quarters). Conflicts and unknowns are captured as evidence but scored NO TRADE (`UNKNOWN_TIMING`, `TIMING_CONTRADICTED`) as already pre-registered.
**Uncertain pairs (11):** kept as two candidate events until resolved: after the date has passed, the outcome row, the SEC 8-K Item 2.02 filing date and the price reaction decide which was real; before that the later source-confirmed date wins for scheduling and the pair is reported. Nothing is merged silently.
**Metrics to publish daily:** share of events per status at window open (targets, to be tuned after one cycle: >= 90 % CONFIRMED date and >= 95 % CONFIRMED_TIMING by the start of the window; unknown timing <= 2 %), number of moves after prediction, number of conflicts, age of the oldest refresh inside the window.

## 3. Refreshing future dates and BMO/AMC timing before the 24-48 h window
* **Sources already available at no new vendor cost:** UW calendar endpoints (`earnings/premarket` and `earnings/afterhours`, one request per date and page; the application's warmer already reads the calendar via `uw_earnings_calendar.calendar`), UW per-ticker earnings rows (`source`, `report_time`), Finviz Elite export (date plus clock time for all tickers in one request), SEC 8-K Item 2.02 after the fact. Their request cost for bulk use is **unverified** (page size and limits unknown) and needs a small approved verification before activation.
* **Cadence (design):** a daily bulk refresh of the calendar from D-30 (one Finviz request plus the UW date endpoints for the next 14 days), a 6-hourly refresh of events whose window opens within 4 days, and a per-event confirmation immediately before capture (served from the bulk refresh when it is younger than 6 h, otherwise one per-ticker call).
* **Rules:** an event enters the capture queue only when its status is CONFIRMED or ESTIMATED with a refresh younger than the freshness limit; STALE events are re-read first; the window is computed from the latest confirmed version; moves after a prediction supersede it (never overwrite).
* **Staleness by horizon:** most of the 364 "not refreshed" events lie beyond the window (more than 2-3 days away) and do not matter yet; the next count run should report staleness by days-to-event buckets (0-3, 4-10, 11-21, over 21) and per-day BMO / AMC / unknown counts (script v3, read-only, to be approved separately).

## 4. Deterministic selection and priority when demand exceeds 220
Applied per capture slot, using only information available before capture (no outcome data):
1. *Eligibility (no request):* CONFIRMED / ESTIMATED date inside its window; illiquid names (cached 20-session dollar volume below 20 M USD) are recorded as `SKIPPED_ILLIQUID` (they would be NO TRADE anyway) and do not use core capacity.
2. *Priority order:* (a) earliest deadline first (release minus 24 h), so events that would otherwise expire go first; (b) date and timing status (CONFIRMED before ESTIMATED before UNKNOWN timing, which is evidence-only); (c) a fixed tie-break by `SHA-256(seed + symbol + event date)` ascending, with the seed written into the pre-registration so any selection can be reproduced.
3. *Core capacity:* the first 220 per capture day; the rest are written to the ledger as `MISSED / CAPACITY` with their rank, never silently dropped. Because each event has a 24 h window, spare capacity on the previous day is used first (earliest-deadline-first scheduling), so the effective need is below the per-date counts.
4. *Full capture (30/day):* chosen only among events already selected for core, with CONFIRMED date and timing and liquid, stratified 15 BMO + 15 AMC by the same hash order (a representative sample, not the largest names). The 100-point evidence is then collected for about a third of events (32-36 % at 30/day).
5. Recorded per day: demand, selected, missed by reason, the seed and the ordered list hash. Selection never uses prices after the cutoff or any outcome.

## 5. Capacity verification and recalculated provider request budget (220 core + 30 full)
* **Per day, worst case:** 220 x 5 + 30 x 13 + 24 = **1,514** requests (observed-peak basis: 1,494). **Retries:** bounded to one retry for transient failures (timeouts, 5xx); a 429, 401 or concurrency refusal stops the run; budgeting 10 % for retries adds 151. **Calendar refresh:** one Finviz request plus the UW date endpoints (unverified; allow 40). **Design ceiling: about 1,700 requests per day.**
* **Time:** one request in flight, 2 s spacing: 1,700 requests take about 57 minutes (about 63 with overhead), well inside a 24 h window; the 3-concurrent limit is shared with the live engine and another local application, so Smart Move stays at background priority with one request in flight.
* **Existing load:** the warmer used 10,448 requests on 28 Oct and 18,318 on the current provider day: **+9-16 % on the busiest day** (1,700 against 18.3 k and 10.4 k); averaged over a year, roughly 70-90 thousand requests against several million, about 1-2 %. The project notes record warmer hourly caps (400 per hour in live hours, 6,000 off hours); the pilot must either share the live-hours cap (1,700 requests would need over 4 hours) or run mostly off hours with its own governor.
* **Subscription restrictions found so far:** the earnings-estimates endpoint is included in the plan (verified); the response headers showed 1,000,000 requests per minute remaining and the warm state reports an unlimited plan, so no daily or per-minute cap applies; flow, tape and dark-pool endpoints are heavy (payload sizes measured in the verification) and the dark-pool full-day tape is excluded; a thin-name verification is still pending (deferred). Billing terms are not verified.
* **Storage:** extended capture 30 events/day x about 150-250 KB gzip, core events a few KB: well under 1 GB a year.
* **Result:** the provisional design is technically feasible within the provider's observed limits; it is not activated, and several inputs (calendar request cost, thin-name behaviour, per-day BMO / AMC distribution) are still unverified.

## 6. Architecture (revised, shadow only, separate worker)
Calendar service (multi-source refresh, status, versions) -> event registry (`smv.event_versions`) -> deterministic selector and earliest-deadline scheduler (fake-clock tested) -> request governor (budget, hourly caps, retries, 429 stop, kill switch) -> factor adapters F3, F5, F7, F8 (scored) and F1, F2, F4, F6 (capture-only, full tier only) -> payload store (atomic hashed files) and `smv.input_snapshots` (immutable) -> shadow scorer (the frozen 40-point rules, both variants, bull / bear separately, missing = no points) -> `smv.predictions` -> grader (reads `earnings_event_outcomes`) -> report generator, daily seal and integrity check, monitoring.

## 7. Implementation sequence and what can be built and tested locally (no production touch)
| # | Component | Local only? | How it is tested |
|---|---|---|---|
| 1 | calendar status / versioning logic (confirmed, estimated, moved, stale, unknown) | yes | fixtures built from the VPS summary shapes and the 11 date pairs |
| 2 | deterministic selector and earliest-deadline scheduler | yes | fake clock, weekend / holiday / DST, overflow days of 216-300 events, reproducible ordered-list hash |
| 3 | request governor (budget, retries, 429 stop, hourly caps, kill switch) | yes | fake provider with injected failures |
| 4 | schema and payload store | yes | local PostgreSQL scratch cluster, crash and corruption tests (design proof exists) |
| 5 | adapters F3, F5, F7, F8 | yes | recorded responses (verification and H1b files) |
| 6 | shadow scorer (frozen pre-registration, both variants) | yes | known-answer, leakage and missing-evidence tests in the style of the baseline |
| 7 | grader and report generator | yes | synthetic outcomes |
| 8 | freshness classification, suspect-window rule, cutoff calendar | yes | fake clock |
| 9 | end-to-end rehearsal on synthetic events | yes | local PostgreSQL + temporary folder |
| 10 | read-only count script v3 (staleness by horizon, per-day timing) | script local; run on the VPS (read-only) | synthetic PostgreSQL, then your approval |
| Needs production or the provider (not before approval) | calendar-source verification (a few requests), thin-name verification, ACL rehearsal on the VPS folder, backup and restore test on the VPS, applying the schema (migration approval), live capture, any flag |

## 8. Decisions needed
1. Approve building components 1-9 locally (tests only, nothing activated, no provider or production access)?
2. Approve preparing count script v3 (read-only, for you to run later)?
3. Keep 220 core + 30 full as the provisional design, with the 1,700 requests per day design ceiling?
4. Calendar-source verification (bulk UW date endpoints, Finviz time field) and the thin-name set: propose request lists after the counts of script v3 are in.
