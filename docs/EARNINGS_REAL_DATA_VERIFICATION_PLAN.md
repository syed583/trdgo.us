# Earnings models — read-only real-data verification plan (production VPS)

**Status: written, NOT run.** Nothing here has touched the VPS. Each step needs your approval and is delivered as one SHA-checked paste block, as before.

Tool: `backend/tools/verify_earnings_readonly.py` (offline self-test passed: the cache stage only reads cached values; an uncached symbol is reported as `NO_CACHED_EQUITY`).

## Guarantees
- No INSERT/UPDATE/DELETE, no schema change, no model/weight/threshold edit, no restart, no deploy.
- The DB stage opens `SET TRANSACTION READ ONLY`, so the database itself refuses any write.
- The cache stage never calls `swr.serve`, `scan()`, `capture()`, `grade()` or `backfill_*`; market-wide inputs come from the cache only, so no provider call is made.
- The raw stage is GET-only, at background priority (yields to live trading), limited to 12 symbols, and should run after 20:00 ET (01:00 VPS) so it cannot compete with live requests.
- Output goes to `C:\audit-reports\` only. Secrets are never printed; use unique variable names in the PowerShell session (the old session still holds `$b` with the DB URL).

## Steps (each one a separate approval)
| # | Stage | Provider calls | Produces |
|---|---|---|---|
| 0 | Pick 10-12 symbols from the live earnings calendar (read from cache) covering: upcoming, reported today/recently, beat/miss, positive-news-negative-reaction, missing options data, thin estimates | none | symbol list with reasons |
| 1 | `--stage cache` | none | per symbol and model: score, signal, tier, coverage, every parameter's weight, bias, points, rescaled max, plus the age in hours of the cached equity and trade entries |
| 2 | `--stage db` | none | prediction counts per model, evaluated vs pending, per-signal evaluated/correct counts, first/last snapshot, rows predicted on a different day than `snapshot_date` |
| 3 | `--stage raw` (after 20:00 ET) | about 25 per symbol, sequential | per dataset: row count, the provider's own latest timestamp, and the bias the production helper derives now |
| 4 | Compare (done here, offline) | none | cached vs raw bias per parameter; stale or missing parameters; points recomputed independently from the raw bias |

## What each parameter is checked against
| Parameter family | Check | Pass rule |
|---|---|---|
| EPS / revenue / guidance estimates | latest estimate row date, fiscal-quarter vs fiscal-year row choice, revision counts | cached bias equals the bias recomputed from raw rows; latest row is not older than the last reported quarter |
| Historical reaction / hit-rate / implied-vs-historical | number of moves used, which end of the list is used | move count and window match the label (last 8), or the difference is quantified |
| Price action / EMA / RSI / levels | latest daily candle date | candle date equals the last session |
| Options flow / OI / unusual / disparity | option-chain tile time and call/put OI | present and same session |
| Dark pool | level rows and spot used | bias equals recompute |
| Insider / funding (SEC) | filing dates in window | inside the 90-day window |
| Short interest | `as_of` date | within one publication cycle |
| Peers | which peers reported in the last 75 days | recompute equals cached |
| Sector & market | SPY quote time, SPY 15-day return, VIX source (IBKR or VIXY fallback) | source is labelled and fresh |
| Final points | independent recompute with the model's weights | equals production within 0.1 |

Freshness classes reported for each parameter: live, delayed, cached-real (with age), historical, derived, missing, fallback-substituted (EPS surprise, peer recommendations, VIXY).

## Decisions I need from you
1. Approve step 0 and 1 first (no provider calls). I will give the exact command and its SHA.
2. Approve step 3 only if you want fresh provider reads; it must run after 20:00 ET.
3. Confirm nothing here should run during market hours.
