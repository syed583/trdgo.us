# INTEGRITY stage result (production VPS, read-only), 2026-10-28 — PROVISIONAL

Verifier v4 (SHA-256 b277a7457fdf9c5db2b6b6b9701efd2186621467c38deb0980f1d59fa48e7a19), `--stage integrity`, SELECT-only in READ ONLY transactions, no provider calls. Output on the VPS: `C:\audit-reports\readonly_verify\earn_integrity.json`.
BMO/AMC windows (ET: before-open 06:00/09:30, after-close 16:00) are estimates, not release timestamps. Data span: first genuine capture 2026-10-20; reported events through 2026-10-27.

## Events and grading (identical for the 8 current models)
- Reported events: 402; with a genuine before-release snapshot: 380; only post-release: 22. PED: 448 reported, 82 genuine, 366 only retrospective.
- verified_last graded events: 70 of 380 (18.4%) — After Close 4 of 234 graded (230 unverifiable, 98.3%); Before Open 66 of 146 graded (80 unverifiable, 54.8%).
- After Close events evaluated before the reaction session closed: 234 of 234. Before Open: 0 of 146.
- App scoreboard (latest snapshot on/before earnings date): graded events 90 per model, of which 43 (47.8%) use a post-release snapshot.
- Actual direction baseline over the 70 graded events: up 30 (42.9%), down 28 (40.0%), flat (within 0.5%) 12 (17.1%). Always-BUY would be "correct" 30/70 (95% CI 32-55%).

## Verified accuracy, last valid pre-release snapshot (BUY+SELL calls; CI 95% Wilson)
| model | BUY | SELL | BUY+SELL | NEUTRAL graded |
|---|---|---|---|---|
| Claude | 12/28 43% (27-61) | 2/7 | 14/35 40% (26-56) | 35 |
| Claude Pro | 20/50 40% (28-54) | 0/3 | 20/53 38% (26-51) | 17 |
| Gemini | 12/34 35% (21-52) | 3/12 | 15/46 33% (21-47) | 24 |
| Grok | 23/58 40% (28-53) | 0/1 | 23/59 39% (28-52) | 11 |
| ChatGPT | 19/47 40% (28-55) | 0/2 | 19/49 39% (26-53) | 21 |
| Meta | 17/39 44% (29-59) | 1/5 | 18/44 41% (28-56) | 26 |
| Kimi | 14/42 33% (21-48) | 2/11 | 16/53 30% (20-44) | 17 |
| PED | 3/8 38% (14-69) | 0/0 | 3/8 | 11 |

First valid pre-release snapshot: Claude 18/48 38%, Claude Pro 19/53 36%, Gemini 19/54 35%, Grok 23/60 38%, ChatGPT 19/55 35%, Meta 17/46 37%, Kimi 16/50 32%, PED 3/5.

## Duplicates and repeats
Snapshots per event: avg 4.65 (max 9). Row-level graded BUY counts are 4.2-4.3x the event-level counts (Claude 121 rows vs 28 events).
Consecutive daily snapshots with identical score / identical signal / flipped signal: Claude 68.0% / 88.3% / 11.7%; Claude Pro 35.3 / 88.8 / 11.2; Gemini 67.4 / 88.2 / 11.8; Grok 50.2 / 89.7 / 10.3; ChatGPT 54.5 / 86.5 / 13.5; Meta 68.2 / 89.0 / 11.0; Kimi 42.7 / 86.8 / 13.2; PED 53.1 / 93.9 / 6.1.

## Post-release rows (973 total)
2026-10-20 15:xx ET Before Open 220 rows (22 symbols); 2026-10-21 16:xx ET After Close 549 rows (61 symbols) + Before Open 198 (22); 2026-10-27 12:xx ET Before Open 6 rows. 220+549+198+6 = 973.

## Other facts
- Table row counts: ped_scores 7,538; earnings_signal_snapshots 16,283; model_backtests 1,160 (own table with coverage_pct); score_snapshots 4,507.
- Date sanity: no snapshot dated after its event; none before 2026-10-13. claude_pm events reach 2027-04-12.
- `earnings_calendar` has reaction_date and post_earnings_move_percent (independent outcome source) and report_time/reporting_time labels, fetched_at/updated_at; no release timestamp column.
- Claude Pro scan: `claudepro:scan:7` 1,292,557 B exists; 15- and 21-day keys absent. ChatGPT 7/15/21-day: 944,428 / 1,247,130 / 1,254,338 B, so Claude Pro 15/21-day estimate is 1,706,839 / 1,716,704 B (> 1,500,000 cap).
- `local:`-prefixed scan keys (about 142 h old) exist in the production swr_cache.
