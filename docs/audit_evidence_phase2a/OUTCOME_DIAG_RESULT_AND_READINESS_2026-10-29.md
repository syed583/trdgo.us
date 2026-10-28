# Outcome-data diagnostic: production result and readiness (2026-10-29) — FAIL

Tool `outcome_diag_v2.py` SHA-256 2b4a669b5f86cf0695798d3996e4c3097c652a9c442ed4962b5a9f2d4f841535 (read-only; 5 SELECTs in READ ONLY transactions; no provider calls). First run with v1 crashed on a float epoch timestamp (fixed; nothing written).

## Populations
- Reported events with a scored-model snapshot: 623 (events not yet reported: 943). Past their reaction close under the new rules: Before Open 169, After Close 163; reaction window still open: Before Open 68, After Close 175; timing label missing: 48.
- "380" = 169 + 163 + 48 (every model). "309" = the part of that union where claude_new (and Claude Pro, Gemini, Grok, ChatGPT, Meta, Kimi — identical 309) has an eligible pre-release snapshot; Pre-Earnings Direction 51.
- The other 71 = 22 events where claude_new has only post-release snapshots + 49 events held only by other models (no claude_new snapshot).

## Stored outcome sources for the 380 completed events
| | Before Open | After Close | timing unknown | total |
|---|---:|---:|---:|---:|
| completed events | 169 | 163 | 48 | 380 |
| legacy outcome stored | 87 | 4 | 44 | 135 |
| cached earnings-move history has the event | 0 | 0 | 0 | 0 |
| calendar move stored | 0 | 0 | 0 | 0 |
| no stored outcome (needs a price fetch) | 82 | 159 | 4 | 245 |
Legacy vs cached moves: 0 events with both (the cached histories were built before these events). Calendar table: 62 rows since 13 Oct, 0 with a move, 0 with eps_actual. Every completed event's symbol has a trade cache (0 without). Share-class symbols present: BIO.B, GEF.B, TAP.A, WSO.B (mapping to the provider unverified).
Price-like columns exist in several tables, but `price_bars` is a legacy ORM table nothing in the code writes; stored prices are only a few `score_snapshots` points (not closes).

## Sample audit findings
- Before Open, legacy graded: AB 10-27 +0.03%, ABT 10-21 -0.44%, ALLY 10-20 +0.32% (all inside the 0.5% flat band).
- Before Open, legacy stamped with no move (82 of 169): ABG, ACU, AIT (10-27) stamped 10-28 04:22 UTC, i.e. after the close, without a move: the legacy grader needs the provider's earnings history to carry the report and its actual EPS, and records nothing if it does not (cause not visible in stored data).
- After Close, stamped before the reaction close: ABCB, ADC, AGYS stamped 4-16 hours before their reaction session closed (expected ready times shown in the JSON).
- After Close, legacy graded although stamped early: AMKR -0.86%, BANR 0.00%, OMC 0.00%. They were stamped 04:06-08:56 UTC the morning after the report, before the reaction session could have closed, and two are exactly 0.00%: these four stored outcomes cannot be real reactions.

## Readiness: FAIL
Blockers: (1) there is no validated outcome source: 245 of 380 completed events have nothing stored and need prices; the 135 stored outcomes are legacy-computed and unvalidated (the 4 After Close ones are invalid); (2) the new fetcher must use regular-session closes (fix proposed, not applied); (3) the real shape of the provider's daily candles (rows per date, market_time labels) has not been observed, so the legacy and new grading cannot be validated; (4) share-class symbols unverified.
Events gradable under the new rules once prices are fetched: 332 timed events (169 Before Open + 163 After Close), 243 more within a day; verified-correct events today: 0.
