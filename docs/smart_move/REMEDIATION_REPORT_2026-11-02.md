# Smart Move V2 — remediation report (2026-11-02)

Scope: root causes of the PARTIAL verdict of the one-time V2 collection (95 stocks, 512 attempts). **No live request, outcome fetch or recurring job was made in this phase. V1 and every V2 weight, threshold and decision rule are unchanged.**
Data sources: the saved V2 collection store (`v2_runs/v2_20261101`), the V1 run, Windows event logs and read-only `powercfg` queries.

## 1. Fixed (code + regression tests)
| # | Defect | Fix | Tests |
|---|---|---|---|
| 1 | One calendar refresh at 10:38 UTC; 42 stocks were captured 6.3–9.4 h later, which the frozen 6 h rule treats as an unconfirmed event | `smart_move_calendar_guard.py`: age is computed from the wall clock before **every** request; refresh at > 5 h (inside the cap, through the governed service); a refresh that cannot run (budget, provider stop, failure) **stops the run** (`STOPPED_STALE_CALENDAR`); a response that arrives against a > 6 h calendar (e.g. a sleep inside the request) is stored as `REJECTED_STALE_CALENDAR`, never scored, and re-requested after a refresh. Sleep gaps are recorded. The scheduler's `COLLECT_V2` action uses the same guard. | `test_calendar_guard.py` (5): age at check time, refresh/no-op/refuse, 7 h pause mid-run, calendar down after the pause, gap watcher |
| 2 | Analyst-estimate input picked the **wrong period for 92 of 92 stocks** (nearest period on/after the save day = next quarter or fiscal year; the quarter being reported had already ended) | `smart_move_v2_snapshots.py`: exact `fiscal quarter` row whose date equals the event's `ending_fiscal_quarter` (from the saved earnings payload); no exact row = no snapshot. Append-only, idempotent snapshot ledger, point-in-time reads, readiness. `load_inputs(..., ending_quarter=)` uses it. | `test_v2_snapshots.py` (4) incl. a test documenting the old defect |
| 3 | Collector had no protection against a suspended PC | `smart_move_keep_awake.py` (per-process `SetThreadExecutionState`, no system setting changed), used by the collector; WakeToRun added to the (still disabled) Task Scheduler definition | `test_keep_awake.py` (3) |

Effect on results: none today. With one snapshot per stock the revision half of the analyst factor was unavailable under both mappings; the fix matters from the second snapshot on.

## 2. The 42 stale-calendar stocks (item 2) — original V1 evidence was valid
`stale_calendar_audit.py` (read-only, `audits_final/stale_calendar_audit_20261102.json`): for all 42, the V1 capture was inside the frozen window, the calendar observation was 0.5–1.1 h old at the V1 capture (limit 6 h), the status was VENDOR_CONFIRMED and no cited evidence was retrieved after the capture. **42 of 42 VALID_AT_CAPTURE.** The staleness exists only in the later V2 re-score (8.4 h at worst), where it correctly blocked V2. No frozen prediction was changed and no later data was used.

## 3. News sentiment schema (item 3) — stays BLOCKED
* Saved samples: 4 stocks, 56 headlines. `sentiment`: **neutral 56/56**. `is_major` true 38, false 18. `tags` empty on all. Sources: Blockchain News 28, Tradex 14, Business Wire 10, GlobeNewswire 2, PR Newswire 1, Unusual Whales 1. Tickers per headline: mean 1.3, max 5.
* The committed spec (`api_spec.yaml`) shows only examples (`positive`, `neutral`) and **does not define** the field: not who assigns it, nor whether it is about the headline or about a listed ticker.
* 56 identical values carry no information about the scale. The sources are largely aggregator-style sites. No guidance field exists at all.
* To unblock (needs separate approval): written definition from Unusual Whales support, then a larger labelled sample (about 25 headlines x 20 stocks = 20 requests) showing positive/negative values, then a review. Guidance would better come from SEC 8-K items, which is a different provider and a separate decision.

## 4. Options-flow missing data (item 4) — no provider or parser defect found
94 stocks with a flow response (1 more, OGS, failed after retries). Zero null fields in 5,863 rows; numeric strings parse correctly.
| Class | Stocks | Meaning |
|---|---|---|
| A. no alerts returned | 8 | genuinely absent |
| B. alerts exist, none in the last 5 days | 51 | genuinely absent **recent** flow (the endpoint returns the latest alerts of any age; for thin names the 100 rows span months or years) |
| C. recent alerts but < 3 pass the context rules | 25 | volume/OI < 1 (251 alerts), expiry before earnings (61), multi-leg (1) |
| D. 3+ pass but premium < $500k | 3 | thin |
| E. qualifies | 7 | |
Only 1 stock is a provider failure. **The binding rule is the 5-day lookback, not the premium threshold.** Information only (not applied, not a recommendation): stocks that would qualify with a 10-day lookback: 10; 30 days: 20; 90 days: 26; premium floor lowered to $250k with 5-day lookback: 9. Any change needs an out-of-sample test of whether longer-lookback flow predicts the post-earnings move; until then the thresholds stay.

## 5. Analyst-estimate history (item 5)
* The provider's own revision fields (`eps_estimate_revision_up/down_last_week`) were **null in all 2,913 quarter rows**, so revisions must come from our snapshots.
* The ledger is ready; the second snapshot is due **7 days after the first (from 8 Nov 2026)**, then the revision becomes measurable and V2 analyst coverage rises from 5 to up to 15 points. A recurring collection is **not** activated; it needs your authorization.

## 6. Windows sleep (item 6)
* Evidence (System log): Modern Standby entered 11:33→14:09 UTC and 14:53→16:58 UTC on 1 Nov, plus short cycles after 18:10. The collector was suspended in each. Hourly-limit resume itself worked.
* The machine is an Acer laptop (mobile), 28 % battery at the check. Power plan: **on AC, sleep after = never, wake timers enabled; on battery, sleep after 1 h**. So idle sleep on AC is already off; the observed suspensions are consistent with running on battery or a closed lid / manual Sleep. I could not read the sleep reason (admin rights) and changed no setting.
* Proposal (details in `ALWAYS_ON_SCHEDULING.md`): (1) the collector is already restart-safe, so prefer short scheduled ticks over one 10-hour process; (2) on this laptop: keep it on AC, set the lid action to "do nothing" yourself, register the tick task with WakeToRun; (3) for real unattended operation use an always-on host.

## 7. Findings that need your decision before any scoring change (nothing applied)
1. Flow lookback (5 d) — see section 4.
2. News/guidance — definition first, then sample, then review.
3. Whether early-filer 13F data (partial until 14 Nov) should count at all before the filing deadline.
4. The 60-point directional minimum stays unvalidated; no outcomes exist to validate it.

## 8. Readiness for the next collection
READY in code: calendar guard, stale-capture rejection, sleep-gap log, keep-awake, exact-period snapshots, resumable checkpoint, 700-style cap and hourly share. NOT ready until: you authorize a run; the machine is on AC with the lid open (or an always-on host is used); the 1 Nov renewal and quota are checked in the dashboard. Recommended next run: re-collect only the evidence needed (estimates snapshot #2 not before 8 Nov; flow within the 5-day window before earnings) with the guard on.

## 9. Tests
Smart Move suite: 183 passed, 1 skipped, 0 failed. Frontend `tsc -p tsconfig.app.json` clean and `npm run build` OK. V1 files and the V2 scoring file (`smart_move_v2.py`) were not modified in this phase.
