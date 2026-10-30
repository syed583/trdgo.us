# Smart Move: real-data readiness (replay on saved data, offline)

No provider request, no VPS, no production database, no push; frozen scoring rules and both pre-registrations unchanged; sealed validation/test data not opened, and no outcome column was loaded anywhere in this stage (so nothing here can be an accuracy statement).

## 1. What saved data exists and what can be replayed

| Saved data (not in Git except indexes) | Content | Replayable | Point-in-time status |
|---|---|---|---|
| 62 candle files (50 symbols, SPY, 11 sector ETFs), 2023-10-30 to 2026-10-29 | UW daily candles, regular and extended rows | yes, hash-verified | PIT by date; retrieved 2026-10-30; adjustment of old closes not documented |
| `events_h1b.csv` (477 rows) | UW per-ticker earnings rows: date, label, `source` flag, SEC match, status | yes | event date, label and flag are as stored after the fact: hindsight |
| `sec_filings.json` | SEC 8-K item 2.02 filings back to 2014, acceptance times | yes | immutable, PIT; acceptance time is only an upper bound on release |
| AAPL V1–V8 raw payloads (15) | one symbol: estimates, flow, volatility context, ownership, headlines, analysts | format checks only | dated or snapshot |
| Finviz export rows, Finnhub calendar rows | **nothing saved** (only earlier probe scripts, outputs not kept) | no | not testable |
| UW calendar rows as the app saw them | not saved; the app overwrites `source` and hard-codes `date_confirmed` | no | not testable |
| 48 h-before calendar snapshots (date and timing as vendors showed them then) | **never saved** | no | the central gap |

## 2. Calendar row formats, flags, timing and independence (real data)
- **Unusual Whales rows (real, 477):** `source` is `company` (443) or `estimation` (34); labels `premarket` (223), `postmarket` (217), `unknown` (37). Upcoming rows (36): 34 are `estimation` with label `unknown` (dates January–February 2027), the 2 near ones (2026-10-30) are `company` with `premarket`. So the flag follows distance and an `unknown` label comes with `estimation`, which is consistent with the rule that estimation never confirms. We have only 2 near rows, so when the flag flips is not measured.
- **Label versus SEC filing window (independent origin), dev window:** 179 agree, 3 disagree, 14 not comparable (no or ambiguous filing). 98.4 % agreement on the comparable 182, with real disagreements, so the two origins are not identical; the label is not always right. 9 label conflicts exist over all years.
- **Finviz and Finnhub:** no saved rows, so their row format, their flags and their independence from Unusual Whales are **untested**. Source independence between the vendors cannot be established from saved data.
- **Real payload checks that pass:** the F7 adapter reads the real AAPL volatility-context payload (date, skew label, implied move) and the F5/F8 adapters read the real saved candle files (tests skip when the files are absent).
- **New finding:** the real volatility-context payload also carries `earnings_date` and `earnings_report_time` as of the requested date (AAPL, 2025-10-01 said 2025-10-30 postmarket, which matches the real event). This is a dated record of what the provider believed then, one request per event.

## 3. Replay of the frozen engines (dev period only, 186 events)
Capture at window start + 5 min, candles only up to the last completed session, earlier events only. Details: `shadow_impl/results/real_replay/real_data_replay_dev_only.md` and `.json`.

| Mode | Prior events from | Continuation (BUY / SELL / NO TRADE) | Contrarian | Coverage |
|---|---|---|---|---|
| A: frozen design as written | saved provider history | 0 / 0 / 186 | 0 / 0 / 186 | 183 events at 12/40, 3 at 2/40 |
| B: exploratory | saved SEC item 2.02 filings | 19 / 11 / 156 | 17 / 10 / 159 | 144 events at 27/40, 41 at 12/40, 1 at 17/40 |

- **A fails for a data reason, not a rule reason:** the saved provider history starts 2024-10 (older rows were dropped when it was saved), so no dev event has the 4 prior reactions F3 needs. Coverage stays at 12 of 40 and every event is NO TRADE (INSUFFICIENT_COVERAGE).
- **B (SEC-derived priors)** gives F3 for 144 events. It is a different evidence source from the design's provider history, with caveats: filing acceptance before 09:30 ET proves premarket, after 16:00 ET only "not contradicted", re-furnished 2.02 filings inside 45 days are collapsed, and non-earnings 2.02 filings could slip in. It needs your explicit approval as an accepted F3 source before it can count. The frozen rule text does not name the source, so this is a data-availability choice, not a rule change.
- **Missing everywhere:** F7 (never captured for these symbols) and the sector part of F8 (no mapping saved). Maximum available is 27 of 40 (0.675, above the 0.60 gate).
- **Reason codes (per variant, B):** BELOW_MARGIN 171, ILLIQUID_OR_UNKNOWN 84 (42 events under 20 M dollars), INSUFFICIENT_COVERAGE 84, RULE 57, SUSPECT_WINDOW 2, TIMING_CONTRADICTED 4 (2 events whose label a later filing contradicts).
- Both variants can agree on a side because F3 is variant-independent.
- **Evidence classes:** valid = dated price evidence and SEC filings; hindsight = the event's own date and label, the UW `source` flag, provider history rows; incomplete = F7, sector; never loaded = outcomes and provider move fields. This is a replay of how the engine behaves on dated price evidence, **not a shadow prediction**, because the calendar status at the time is unknowable.

## 4. Governor and warmer budget (offline simulation, fake clock)
250 events (220 core cap, 60 full over two days), `results/budget_simulation.json`.
- Warmer idle at its 300/h reservation: shadow worker used at most 100 requests per live hour; all 250 processed, 0 missed.
- Warmer at the observed 406/h: **0 shadow requests in live hours** (04:00–20:00 ET); the whole load ran in the night window, peak 1,087 requests in one off-hour; all 250 processed before their deadlines, 0 missed.
- Authorization 1,000/day: core calls untouched (1,004 core calls as in the larger runs), full-capture extras throttled from 780 to 494 (`DENIED_CORE_RESERVATION` 286 logged).
- Authorization 0: no request, 250 `MISSED_DEADLINE` entries.
- In every run the shared hourly limits and the per-day authorization held (checked per Eastern day: 697 and 1,087 requests on the two days against 1,700). The 4 simulation tests run in the suite.
- Not yet true to life: the warmer was a flat rate, not a measured series; the governor's live window ignores weekends.

## 5. Readiness verdict
| Layer | State |
|---|---|
| Pipeline, store, governor, scorer, grader | built and tested on synthetic data and on saved real candle formats |
| F5 and F8 market | validated on real saved candles |
| F3 | works only with a longer event history: SEC-derived (needs approval) or a re-fetched provider history (50 requests) |
| F7 | real-format validated on one AAPL payload; no multi-symbol evidence |
| Sector (F8) | no mapping |
| Calendar verification | logic tested; **no real Finviz or Finnhub row has ever been parsed**; no 48 h snapshot exists |
| Live shadow pilot | **not ready**: needs real calendar evidence first |

## 6. Remaining blockers
1. No real Finviz or Finnhub calendar rows, so VENDOR_CONFIRMED cannot be exercised on real data and vendor independence is unmeasured.
2. No historical 48 h calendar snapshots (hindsight on every replay).
3. F3 source decision (SEC-derived priors, or provider history requested again).
4. F7 and sector mapping not available in bulk.
5. Warmer usage not measured; recurring job, schema deployment, storage and kill-switch still unapproved.
6. The earnings-fix release remains a separate pending approval.

## 7. Minimal live-data verification proposal (only because saved data is insufficient; nothing sent)
Manifest `live_verification_manifest_PROPOSED_v2.json` (SHA-256 `cd10148166c1d927699ddbf4e90af862779d96a7e55172ef3f5b0fcf17f35da4`).
- **P1, minimum, at most 10 requests:** the calendar comparison for the nearest trading days (UW premarket and afterhours for 2 trading days, up to 2 pages each = 8; Finviz 1; Finnhub 1). Raw payloads stored, report built offline. Gives real Finviz and Finnhub formats, flags, timing agreement and independence evidence on about the nearest 86 events. Dates must be regenerated on the run day.
- **P2, recommended, 40 requests:** `GET /api/stock/{ticker}/volatility/context` for 40 development events chosen by a hash-ordered, outcome-blind rule, dated at the last session before each capture. It gives real F7 data on 40 symbols and a dated record of the next earnings date and time as known then, which measures how often the date known two days before equals the final one. This is the closest available substitute for the missing snapshots.
- **P3, optional, 50 requests:** provider earnings history for the 50 symbols, without dropping older rows (only if SEC-derived priors are not accepted).
- **P4, optional, 50 requests or zero:** sector mapping, or an owner-supplied file.
Totals: 10 minimum, 50 recommended, 150 if every option. Each at 2 s spacing, one retry, stop on 401/403/429, no per-symbol fallback.

## 8. Minimum next action to move toward a live shadow pilot
1. Your decision on the F3 source (accept SEC-derived priors for replay and pilot, or require provider history).
2. Approval of P1 (10 requests) so the vendor calendar rows and independence can be measured on real data; P2 (40 requests) is recommended in the same approval.
3. I then finish the offline calendar comparator for the real formats (no request) and report. Everything else (schema, job, storage, kill switch, warmer measurement) stays blocked until that result.
