# Smart Move: local shadow pipeline and calendar verification (report)

Local only, synthetic fixtures, throw-away PostgreSQL on 127.0.0.1 (deleted afterwards). No provider request, no VPS action, no production write or migration, no push. Frozen pre-registrations unchanged (baseline `9316348b…`, score40 `b25bd8d7…`). No existing production model or file was changed.

## 1. What was built (under `docs/smart_move/shadow_impl/`)
- `smvshadow/calendar_confirm.py`: vendor-level calendar verification, isolated from production code.
  - Statuses: UNKNOWN, STALE, MOVED, ESTIMATED, **VENDOR_CONFIRMED** (eligible, shadow only), **COMPANY_VERIFIED** (separate, needs direct company evidence with its hash).
  - VENDOR_CONFIRMED needs all of: Unusual Whales `source = company` taken from the raw row; a second, different vendor with the same date and the same BMO/AMC timing; both observations inside the freshness limit (6 h within 4 days, otherwise 24 h, never over 48 h); no contradicting observation; a documented dependency record for the vendor pair. It is labelled "vendor agreement, not direct company confirmation" in every output.
  - `date_confirmed=True` is never read. A row without `source` has `vendor_flag=None` and cannot confirm anything. The app's own hard-coded field is ignored by construction, and the production code is untouched.
  - Append-only, hash-chained `ObservationLog` (idempotent, tamper-evident, no update/delete method); `calendar_observed_at` and `prediction_snapshot_at` are separate fields and only the first is used for freshness.
  - Dependency registry: Unusual Whales/Finviz and Unusual Whales/Finnhub are recorded as `UNPROVEN_INDEPENDENCE` (upstream not documented in the material we hold; a shared upstream is possible). A pair without a record cannot confirm. Two Unusual Whales feeds count as one vendor; identical payload hashes count as one.
  - Every excluded event carries at least one NO TRADE reason code.
- `schema/smv_schema_v2_calendar.sql`: append-only `calendar_observations` (raw flag stored verbatim) and `calendar_evaluations` (a CHECK refuses an excluded event with no reason). Local scratch only.
- `smvshadow/pipeline.py`: calendar evaluation -> eligibility -> deterministic selection -> governor -> capture -> frozen 40-point scorer (both variants) -> NO TRADE records -> append-only persistence.
- `smvshadow/demand.py`: peak-demand model (section 4). `synthetic_season.py`: 28-event synthetic season (section 3).

## 2. Tests
**84 pass** against the scratch PostgreSQL (81 pass and 3 skip without it). New this stage: 20 calendar tests, 5 pipeline tests (one on PostgreSQL), 4 demand tests. Calendar tests cover: raw-flag preservation and the ignored `date_confirmed`; company flag missing or `estimation`; single vendor; two feeds of one vendor; shared payload; vendor disagreement (1 day, over 1 day); label vs clock contradiction; clock inside trading hours; date without time; stale and partially stale observations; prediction-snapshot age versus calendar age (both directions); moved events, with and without a prediction; undocumented and documented (including shared-upstream) dependency; company evidence matching, contradicting on date, contradicting on timing; log append-only, idempotent, tamper-evident. The database tests show UPDATE, DELETE and TRUNCATE refused and an excluded event without a reason refused.

## 3. Synthetic BUY / SELL / NO TRADE results (invented data; no accuracy meaning)
`results/synthetic_season_results.md` and `.json` (deterministic). 28 events: 18 VENDOR_CONFIRMED, 6 ESTIMATED, 2 STALE, 2 UNKNOWN.

| Variant | BUY | SELL | NO TRADE |
|---|---|---|---|
| continuation | 4 | 1 | 23 |
| contrarian | 4 | 2 | 22 |

Reason codes across both variants: BELOW_MARGIN 24, RULE 11, STALE_CALENDAR 4, TIMING_CONTRADICTED 4, SINGLE_VENDOR 4, SOURCE_ESTIMATION 4, NO_OBSERVATION 4, CONFLICT 1. All 10 non-eligible events produced no request and a NO TRADE record. 308 requests for 18 events at a 1,700 authorization.
Reading note: F3 (the stock's own reaction history) is not variant dependent in the frozen rule, so when it dominates both variants can agree (S16 is BUY in both). The variants differ only through F5 and F8.

## 4. Peak provider demand (220 core + 30 full, 10 % retry margin, ceiling 1,700; planning only)
Capture 1,514/day. Calendar refresh through Unusual Whales costs 2 requests per trading day and side, up to 2 pages.

| Calendar refresh option | UW calendar requests/day | Total | With 10 % margin | Over 1,700 | Full events that still fit |
|---|---|---|---|---|---|
| Finviz + Finnhub only (no UW) | 0 | 1,514 | 1,666 | none | 30 |
| UW once a day (flag check, 15 trading days) | 60 | 1,574 | 1,732 | 32 | 27 |
| UW every 6 h for near events | 96 | 1,610 | 1,771 | 71 | 25 |

So with a worst-case margin, a UW-based refresh breaks the ceiling on the peak day unless full captures drop (27 or 25) or the refresh uses the other vendors. Nothing here assumes authorization above 1,700.
Warmer and hourly caps: the warmer was seen at about 406 calls/hour in live hours (04:00–20:00 ET), so it can use the whole 400/h live cap. Then the shadow worker has 0 live requests/hour and 5,000/hour off-hours, limited to 1,800/hour by 2 s spacing. Capture needs about 1,732 requests, i.e. roughly 1 hour of night capacity (8 h available, 14,400). Every capture window is 24 h long and contains a night, so the capture is feasible, but only if it runs in the 20:00–04:00 ET window when the warmer is at its cap. The runner waits for the next hour when the live allowance is zero. The daily account limit is unlimited on the current plan, so the binding limits are the 1,700 ceiling, the hourly caps, 3 concurrent and 2 s spacing. These warmer rates come from earlier notes, not from a new measurement.

## 5. Remaining blockers for production (all open)
1. No live data has ever flowed through the calendar code: payload shapes of Finviz and Finnhub rows are parsed from code and docs only; Unusual Whales row shape is from the API spec. One supervised, approved dry run is needed.
2. Vendor independence stays UNPROVEN; the policy you set accepts that, but the report labels it.
3. The calendar refresh option and the full-capture count must be chosen (section 4).
4. Real warmer usage must be measured (the governor accepts a usage function; none is wired).
5. The governor's live window ignores weekdays (conservative); sector ETF mapping per symbol, liquidity (adv20) source for pre-capture filtering, and release-time evidence are not solved.
6. Production schema, payload storage location, backups, the recurring job and a kill-switch procedure need their own approvals. Nothing was applied to any production database.
7. Calendar observation capture code (storing raw UW/Finviz/Finnhub payloads) does not exist yet for production; only the offline model does.
8. The earnings-fix release `669217f3` is independent and still awaits its own approval.
