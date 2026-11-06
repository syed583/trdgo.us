# Signal verification (audit only) — verification report

Branch `feature/intraday-session-workflow`, local only. Nothing pushed, deployed or restarted; no database touched (tests use throw-away SQLite). Shipped defaults: `INTRADAY_WORKFLOW["mode"] = "legacy"`, `INTRADAY_VERIFICATION["mode"] = "off"`. Strategy modules, parameters, weights, formulas, BUY ≥ 55 / SELL ≤ 45, entry/stop/target and exit logic are unchanged (guard tests; byte-identical to 277f30f).

## What was built (backend only)
`signal_verification.py` re-derives every actionable BUY/SELL **without calling the strategy's scoring code** and records the result. `INTRADAY_VERIFICATION["mode"]` has only `off` and `audit`; an unknown value (e.g. "enforce") is inert. **It never blocks, delays or changes a trade.** It runs after Legacy's transaction is committed, in its own session, inside a try/except; a crash in it cannot touch trading (tested).

Checks per signal: (1) the row has exactly the configured parameters with the configured weights (weights are read from the same tables the strategies read); (2) every bias is on the existing 5-step scale; (3) each contribution = weight × (0.5 + 0.5 × bias), missing = 50 %; (4) score = round(sum of contributions, 1), also = sum of the reported contributions; (5) signal = BUY ≥ 55 / SELL ≤ 45 / NEUTRAL (exact boundaries tested); (6) data: per-parameter availability and timestamps — missing, stale, not-implemented and pre-market-unavailable readings are **not counted as verified market evidence** (`DATA_OK` / `DATA_THIN` / `DATA_UNVERIFIED` is reported separately from score integrity); (7) an entry price exists and sits inside its own stop/target bracket.
Stored in `intraday_signal_audit` (score, recomputed score, difference, verdict, data status, every check, the per-parameter breakdown, coverage/ages, whether Legacy opened a trade and its id). Not served by any API and not shown in the UI. The Strategy Trades page already shows only BUY/SELL, entry and the trade-management fields; a test asserts it (and the blotter payload) contain no parameter scoring, so **no frontend change was needed**.
V2 shadow mode is separate: different switch, tables and code path; it can be on or off independently.

## Local test against the existing behaviour — what I could and could not do
Production signals/trades cannot be read from this laptop (standing rule), and no recorded historical boards exist, so I did **not** produce production pass/fail counts. What I did instead:

| Test | Result |
|---|---|
| The verifier vs the **six real scoring functions** (grok, kimi, meta, gemini `score_one`; chatgpt and claude_pro `_score_equity`), 400 random inputs each (about a quarter of sources missing) = 2,400 rows, **1,705 actionable BUY/SELL** (grok 325, kimi 312, meta 305, gemini 267, chatgpt 189, claude_pro 307) | **0 integrity failures** — the independent recomputation agrees with the production scoring code on every row, both directions |
| Tampering: changed weight, off-scale bias, wrong contribution, wrong total, missing / extra / duplicated parameter, score 54.9 / 55.0 / 45.0 / 45.1 / 50 vs signal, missing entry, entry outside bracket | each detected by the intended check |
| Data verdict: fresh → `DATA_OK`; stale sources or stale live engine → `DATA_THIN`; only a quarter of parameters with readings → `DATA_THIN` (eligible coverage < 70 %); no timestamps → `DATA_UNVERIFIED` | as designed. A clean score on thin data is flagged, never "verified" |
| Audit on vs off over a replay day | the Legacy book is **identical to the cent**; every signal audited; opened trades linked by id |
| A signal the verifier FAILS (tampered weight) | still trades exactly as before — audit does not block |
| Crashing verifier | trading unchanged, no audit rows |

Honest caveats: the random inputs exercise the code paths and score arithmetic, not real market data; per-parameter timestamps are only present on board rows when audit mode is on (the audit enables the existing evidence keys on the row), so data-quality verdicts for real traffic only exist after it runs.

## How to get the real numbers without changing anything
1. **Existing production trades (read-only, can be run today on the VPS, no deployment needed once this branch is on the server — or via the same SELECTs):** `python signal_verification_cli.py history 2026-10-20 2026-10-27` re-checks each stored paper trade: entry score vs the unchanged thresholds and its own side, entry/stop/target bracket, size 100. It cannot re-derive parameter contributions for history (they were never stored). Tested on a fixture: 8 trades → 2 pass / 6 fail with the right reasons.
2. **Live signals going forward:** set `INTRADAY_VERIFICATION["mode"] = "audit"` (needs your approval), run a session, then `python signal_verification_cli.py report <date>` gives pass/fail and reasons per strategy, plus how many of the failing/thin signals Legacy actually traded. Only then decide whether any enforcement is worth proposing — none exists or is proposed now.

## Changes beyond the module
`_row_map` carries extra read-only keys (params, raw levels, evidence ages); the gate adds `signal_meta`, `gate_ts`, `raw_levels` to the row when shadow or audit is on (no decision changes — same test as shadow); one new table `intraday_signal_audit` (additive; added to the reviewed migration SQL, `ISOLATED_TABLES`, `_ALL_MODELS`); two `release_check.py` rows (ships off with no enforcing mode; audit-only after Legacy's commit) plus mutation tests; the 277f30f rollback target still passes.

## Checks
Backend `pytest backend`: **1235 passed, 4 skipped**. `release_check.py` PASS (working tree and 277f30f). Frontend untouched (last `tsc`/build exit 0).

## Open points / decisions for you
* Approve the audit mode on the server for a few sessions (and the additive migration — one more table) to get real pass/fail counts; until then production counts are unknown.
* Whether the data-quality classification (`DATA_THIN`) should ever gate entries is a separate decision; thresholds it uses are the PROPOSED V2 ones (70 % eligible coverage, 0.55 agreement) and are unproven.
* The synthetic replay used earlier has board scores that are not the sum of their parameters; the verifier correctly flags such rows, so the replay cannot serve as a pass/fail benchmark. Real strategy output (above) can.
