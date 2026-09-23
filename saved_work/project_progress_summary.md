# US-Stock Reader — Progress Summary

Last updated: 2026-09-10

---

## State

A working personal US market-intelligence application on live IBKR data.
16 routes, 49 API endpoints, 7 workspace/cache tables. Every navigation item,
tab, filter and action button reaches a real screen backed by a real endpoint.

The headline change this phase: **seed data can no longer become a signal.**

---

## The gating fix (most important change)

The Earnings screen previously showed *Beat Rate 100%, Avg Surprise +6.7%* and
scored Earnings History 13/15 — all computed from **one `source='TEST'` row**.
That is now impossible:

* `earnings_intelligence_service._statistics` computes beat rate, surprise and
  move statistics from **verified provider rows only**. With seed rows it
  returns `None` for every figure and a basis of `TEST_DATA`.
* The `earnings_history` scoring component is gated the same way — it reports
  `TEST_DATA` and is excluded from the score rather than contributing 13/15.
* NVDA consequently scores **38/100 WATCH** with `missing_components:
  [estimates, earnings_history]` — the honest answer, not 51.

A test asserts a seed row with a 400% "surprise" cannot reach `largest_move`.

---

## External provider architecture (new)

```
provider_config.py                 env-var keys, status vocabulary, HTTP
benzinga_earnings_service.py       calendar, actual results, history
alpha_vantage_estimates_service.py estimates + revision snapshots
earnings_intelligence_service.py   merged history, statistics, lifecycle
models_earnings.py                 earnings_calendar, estimate_revisions,
                                   provider_fetch_log
```

Rules enforced in code:

* Keys come from `backend/.env` only (`BENZINGA_API_KEY`,
  `ALPHA_VANTAGE_API_KEY`). See `.env.example`.
* **No key → `PROVIDER_NOT_CONFIGURED` and no network call at all.** Tests
  assert the HTTP layer is never reached without a key.
* No endpoint ever echoes a key back — a test asserts the secret does not
  appear in the status payload.
* Provider data is cached in PostgreSQL and gated by `provider_fetch_log`, so
  opening a page never calls a provider. Syncs are explicit buttons in
  Settings.

### Alpha Vantage scope, stated honestly

Alpha Vantage publishes *current* consensus only — not the value as it stood
7/30/60/90 days ago. The service therefore snapshots each run and builds the
revision trend from its own history, reporting `PARTIAL_DATA` with the
horizons it actually has. Missing windows are never back-filled with the
current value, which would manufacture a trend from one observation.

---

## Earnings lifecycle (new)

`SCHEDULED → PRE_READY → RESULT_DETECTED → PARTIAL_RESULT →
OFFICIAL_VERIFIED → POST_SCORE_READY → ENTRY_MONITORING → TRADE_READY/NO_TRADE`

EPS reported without revenue is `PARTIAL_RESULT`, surfaced as
**waiting for complete result** — never treated as a finished report.

Post-earnings moves are measured from real IBKR bars, matched to the reporting
time (BMO moves that session, AMC the next).

---

## Options provenance (new)

`live_options_analytics.get_data_basis()` states what produced every metric and
what cannot be produced. The Options screen renders it as a Data Provenance
panel.

Genuinely unavailable on IBKR, shown as **REQUIRES ADVANCED OPTIONS DATA**:

| Metric | Why |
|---|---|
| Gamma exposure (GEX) | Needs dealer inventory; no IBKR endpoint exposes it |
| Institutional aggressor flow | Needs exchange-side participant tagging |
| Unusual activity vs baseline | Needs historical per-contract option volume; TWS refuses option bars |
| Dark-pool prints | Not distinguishable in the IBKR tick stream |

Shown, with the basis stated on the card:

* **Call wall / put wall / max pain** — computed from *quoted* open interest.
  A standard calculation on real data, labelled "not a dealer-positioning
  model".
* **Sweeps / blocks** — classification of real `reqHistoricalTicks` prints by
  venue and timestamp, labelled "not an exchange-supplied flag".

If you would rather these were hidden entirely rather than labelled, say so —
it is a one-line change to move them into the unsupported set.

---

## Entitlement matrix (probed)

| Capability | Status |
|---|---|
| IBKR API, quotes, OHLCV, option chain, OI | **OK** |
| IV + greeks | **OK** — solved locally from real bid/ask |
| News headlines + article bodies | **OK** — 8 providers |
| Market scanner | **OK** — 673 scan codes, server-side |
| Streaming news bulletins | ENTITLEMENT_REQUIRED (IBKR 10276) |
| WSH earnings calendar | ENTITLEMENT_REQUIRED (empty metadata) |
| Benzinga | **OK** — 148 cached events, plan-limited symbol coverage |
| Alpha Vantage | RATE_LIMITED — `EARNINGS_ESTIMATES` needs a premium plan |
| SEC EDGAR | **OK** — 6h cache |

---

## Verification

| Check | Result |
|---|---|
| Backend suite | **188 passed** (~1.3s) |
| Frontend `tsc --noEmit` | PASS |
| `npm run build` | PASS |
| Route smoke test | **17/17** |
| Live quotes | NVDA 221.15 · AAPL 316.89 · MSFT 489.84 · SPY 759.18 · QQQ 708.33 |
| Charts | 126 bars each, EMA 20/50/200 |
| Option chains | NVDA + MSFT, 24 strikes, real OI, IV, greeks |
| Symbol search | live IBKR matches; unknown → SYMBOL_NOT_FOUND |
| Branding | no user-visible `TRDGO` string in frontend or backend |

Four visual passes at 1536×1024 fixed: blank history chart (field-name
mismatch), volume tiles reading `0.00M` for real 4K volume, empty earnings
header cell, and an uncapped flow table breaking the dense layout.

---

## Provider integration results (keys now configured)

Both keys are live in `backend/.env`. `.env.example` is back to blank
placeholders, so the template never carries a secret.

### Benzinga — WORKING, plan-limited

* **148 real earnings events cached**, 29 symbols, 2025-09-30 → 2026-12-17
* 116 rows carry actual EPS and revenue → lifecycle `OFFICIAL_VERIFIED`
* AAPL history verified: beat rate 100%, avg surprise **+5.74%**, 4 consecutive
  beats, revenue beat rate 100%

**Coverage is limited by the plan.** AAPL, MSFT, JPM, WMT resolve; NVDA, AMD,
META, TSLA, AMZN, GOOGL return zero rows. That is the subscription, not a bug —
a higher Benzinga tier would widen it.

Three real bugs found and fixed while integrating:

1. **Surprise was 100x too small.** Benzinga sends `eps_surprise_percent` as a
   fraction (0.0688 = +6.88%). The value is now recomputed from estimate and
   actual rather than trusted verbatim.
2. **Empty windows crashed the parse.** Benzinga answers "no events" with a
   bare `[]` rather than `{"earnings": []}`; that is now an empty result, not
   an error.
3. **Cache key overflowed the column.** A 12-ticker scope exceeded
   `provider_fetch_log.scope` (VARCHAR 64); long scopes are now hashed.

### Alpha Vantage — CONFIGURED, endpoint not available on this tier

`EARNINGS_ESTIMATES` is premium. The free key returns a rate-limit notice
instead of data, reported as `RATE_LIMITED`. The integration is complete and
will work on a premium plan without code changes.

**Security fix found here:** Alpha Vantage **echoes the API key back inside
its rate-limit message**, and that message was being written to
`provider_fetch_log.detail` and returned by the API. `provider_config.redact()`
now scrubs every configured key from all provider text before it is logged,
stored or returned. Four tests cover it.

---

## Verified live on 2026-09-10 (TWS reconnected)

| Check | Result |
|---|---|
| Backend suite | **188 passed** (~1.3s) |
| IBKR uplink | connected, clientId 17, port 7496 |
| Live quotes | AAPL 319.08 · MSFT 491.71 · NVDA 218.26 · SPY 759.25 · QQQ 711.30 |
| Chart ranges | 1D 78 · 5D 130 · 1M 22 · 6M 126 · 1Y 252 · 5Y 261 bars |
| Market overview | OK — SPY/QQQ/DIA/IWM |
| **Post-earnings moves** | **RESOLVED — 4/4 measured on AAPL** |
| Seed gating | still holds — NVDA reports TEST_DATA, no beat rate |

### Post-earnings moves — resolved

The item left unverified last phase now works. AAPL, from real IBKR daily bars:

| Report | Reaction session | Move |
|---|---|---|
| 2026-07-30 AMC | 2026-07-31 | **-7.35%** |
| 2026-04-30 AMC | 2026-05-01 | +3.24% |
| 2026-01-29 AMC | 2026-01-30 | +0.46% |
| 2025-10-30 AMC | 2025-10-31 | -0.38% |

avg 2.86 · median 1.85 · largest 7.35. The AMC offset is visibly correct: each
report lands on the *next* session.

---

## Two real bugs found during that verification

### 1. An IBKR outage poisoned the earnings cache for six hours

`get_history` is a composite: Benzinga supplies the quarters, IBKR supplies the
moves. When TWS was down the Benzinga half still succeeded, so a result
reporting **zero moves** was cached under the full 6h TTL and kept being served
long after TWS recovered. `get_quote` and `get_chart` were never affected -
they return without caching on an outage - so this was specific to the
composite.

`_attach_post_earnings_moves` now returns *why* it found nothing, separating
"the bar source failed" from "these reports predate the window". A result
degraded by an outage is held for `DEGRADED_TTL` (120s) instead of six hours,
and the payload carries `moves_status` / `moves_detail`.

### 2. Verified earnings history never reached the scorer

The worst of the two. The gate correctly checked the **Benzinga** history, then
scored `get_earnings_history(db, symbol)` - the **legacy `earnings_events`
table**, which is empty for AAPL and MSFT. So a verified 100% beat rate was
read from an empty row set, scored **0/15, and reported status `OK`**. A silent
zero is worse than an honest `UNAVAILABLE`: it dragged the composite down while
claiming the component was fine.

`earnings_intelligence_service.as_score_history()` now adapts the verified rows
into the shape the scorer expects, so the gate and the data agree.

| | before | after |
|---|---|---|
| AAPL earnings_history | 0/15, status OK | **13/15**, STRONG_BULLISH, confidence 70 |
| NVDA (seed only) | excluded, TEST_DATA | excluded, TEST_DATA (unchanged) |

The seed gate is intact - the adapter takes verified rows only, and a test
asserts a seed row with a 400% "surprise" still scores 0 at confidence 0.

---

## Two more bugs found while verifying options

### 3. SPY returned a one-strike option chain

`reqSecDefOptParams` returns one row per (exchange, trading class), and the
non-standard classes left behind by past corporate actions come back alongside
the standard one. `_chain_meta` took the **first** SMART row, which for SPY is
`2SPY` - an adjusted class listing a single strike. The most liquid options
underlying there is was rendering one row.

Selection now prefers the class named after the symbol, then the one listing
the most contracts.

| | before | after |
|---|---|---|
| SPY chain | 1 strike, 0 with OI | **24 strikes, 24 with OI and IV** |

ATM 759.0 prices at delta 0.5064 and IV 14.44 - both correct for at-the-money
SPY, so the greeks solver is behaving on real quotes.

### 4. A composite cached an outage (see also #1)

Covered above; listed here because it was found the same way - by checking a
figure rather than a status code.

---

## Options verified live

| Symbol | Result |
|---|---|
| MSFT | 24 strikes, 24/24 call OI + IV, delta 0.74 -> 0.18 across strikes |
| NVDA | 24 strikes, 22 with OI |
| SPY | 24 strikes, 24 with OI + IV (after fix #3) |
| **AAPL** | **blocked at TWS — see below** |

News (20 real headlines, Dow Jones/Briefing.com) and the scanner (server-side,
673 codes) both verified OK once the uplink steadied.

---

## Open issue: AAPL option definitions do not load from TWS

Every `reqContractDetails` for an **AAPL option** times out with no response -
all 24 expirations, and a single named contract too. The same call for MSFT
returns 180 contracts in 0.5s.

Proven not to be an app bug, by direct probe on a spare clientId:

| Request | Result |
|---|---|
| AAPL stock contract details | 0.1s |
| AAPL `reqSecDefOptParams` | 0.1s, 24 expirations, 124 strikes |
| AAPL option details (any expiry, SMART or CBOE) | **no response** |
| MSFT option details, identical call | 0.5s, 180 contracts |
| Deliberately malformed contract | errors instantly (socket is alive) |

Definition requests are served; AAPL option definitions are not. Everything
else for AAPL works - quote 321.93, news 20 items, chart, earnings history.

**A full TWS restart normally clears this.** The IBKR timeout and host/port
were deliberately left untouched: the condition is on the TWS side and tuning
around it would hide it rather than fix it.

TWS also emitted **1100 (IBKR<->TWS connectivity lost)** and **2105 (`ushmds`
farm broken)** partway through this session, then recovered. The client refused
to dispatch during that window and reported `PROVIDER_OFFLINE` with the exact
reason rather than serving anything - which is the intended behaviour.

---

## Earnings screen: why most tickers showed "No date"

Reported from the running UI - 8 of 10 tickers showed "No date", and the one
that showed a date was the wrong one. Three separate causes.

### 5. The earnings date came from the seed table only

`_earnings_row` queried the legacy `Company` / `earnings_events` tables and
never looked at the Benzinga `earnings_calendar` cache. So the only ticker
displaying a date on that screen was **NVDA, from a seed fixture**, while AAPL
and MSFT - which have real verified provider rows - showed "No date". Exactly
backwards, and a fixture was reading as a confirmed schedule.

Benzinga is now consulted first (cache-only, never a provider call). A seed row
is still shown but returns `TEST_DATA` / `DATABASE_SEED`, and the ticker card
renders it in amber as **SEED · After Close** so it cannot be mistaken for a
real date.

| | before | after |
|---|---|---|
| AAPL | No date | **After Close · 2026-10-29** (Benzinga) |
| MSFT | No date | **After Close · 2026-10-28** (Benzinga) |
| NVDA | "After Close" (looked real) | **SEED · After Close**, amber |

### 6. Benzinga plan covers a Dow-30 sample only

Confirmed by direct probe with an explicit ticker filter over a 400-day window:

| Request | Rows |
|---|---|
| AAPL | 4 |
| NVDA | **0** |
| TSLA, META, AMZN | **0** |

HTTP 200 with an empty list - entitlement, not an error and not our code. The
cache holds 148 events across 29 symbols, all Dow-30 constituents. TSLA, NVDA,
ORCL, ADBE, GME, AVGO, AMZN and META need a wider Benzinga plan (or a second
provider). Their cards correctly report `NOT_TRACKED` and explain it on hover.

### 7. A cold earnings overview took up to six minutes

`get_earnings_overview` fetches quote, chart, score, chain, IV history,
realized move, risk zones, earnings, estimates, revisions, history, lifecycle
and analysis **serially**, and `get_trdgo_score` loads the option chain again
on its own. With AAPL's options hanging at TWS that was the 180s timeout paid
**twice** - which is the spinner in the screenshot.

Failed chain loads are now held for `CHAIN_FAIL_TTL` (90s), so one outage is
paid once per page instead of once per call.

| | before | after |
|---|---|---|
| AAPL overview, cold | 365s | **186s** |
| NVDA overview, cold | 159s | **146s** |

**Still too slow, and not yet fixed.** The remaining ~145s is the option chain
assembly itself (~2 min of TWS round trips, by design). Making this feel fast
needs the overview split so the page renders quote/chart/score immediately and
loads options separately - a real change to the page's data flow, not a tuning
knob, so it is left for a decision rather than done quietly.

---

## Known gaps

1. **Benzinga coverage is plan-limited** — AAPL/MSFT/JPM/WMT resolve;
   NVDA/AMD/META/TSLA/AMZN/GOOGL return zero rows. A higher tier widens it.
2. **Alpha Vantage `EARNINGS_ESTIMATES` is premium** — the free key returns a
   rate-limit notice, so Estimate Revisions stays 0/25 until the plan changes.
3. Advanced options metrics need an OPRA-level provider (see table above).
4. **AAPL option definitions do not load from TWS** — a TWS-side condition,
   diagnosed above; every other symbol's chain works.

---

## Safety

No order placement anywhere; IBKR connects `readonly=True`. No endpoint returns
a credential. Score/confidence gating intact — a high score with low confidence
stays a WATCH, and missing components reduce confidence rather than defaulting
to neutral.

---

## Running it

```bash
docker start trdgo-postgres
cd backend  && python -m uvicorn main:app --host 127.0.0.1 --port 8000
cd frontend && npm run dev          # http://127.0.0.1:5173
```

Optional providers: copy `backend/.env.example` to `.env` and add keys.
Demo fixtures for visual work: append `?demo=1`.
