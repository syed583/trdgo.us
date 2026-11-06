# SNAPSHOTS stage result (production VPS, read-only), 2026-10-28

Verifier v3 (SHA-256 d398961a137e2d1e83f2054e52cbe3ddb0effbacc3b3fc638c701e1b0f987460). SELECT-only, READ ONLY transactions, no provider calls.
Output file on the VPS: `C:\audit-reports\readonly_verify\earn_snapshots.json`. Baseline FRESH copy SHA-256 1c90becef8ef447fa6bf73707b4f24445a216f552346b64df21279f77de9e10d.
Classes use PROVISIONAL windows in America/New_York (before open: before 06:00 / ambiguous 06:00-09:30 / after 09:30; after close: before 16:00 / after 16:00). No confirmed release timestamps exist in the database.

Table `model_predictions`: 71,450 rows; columns = id, model, symbol, earnings_date, report_timing, snapshot_date, predicted_at, signal, score, actual_move_pct, actual_direction, correct, evaluated_at. No input, coverage, tier or release-time column.
no_report_timing 9,814 = claude_pm retrospective 5,117 + ped retrospective 4,697. no_score 5,117 = claude_pm retrospective.

| model | rows | events | evaluated rows | pending rows | before | after | ambiguous | retrospective |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| claude_new (Claude) | 7,039 | 1,514 | 1,381 | 5,658 | 6,933 | 106 | 0 | 0 |
| claude_pro | 7,028 | 1,514 | 1,378 | 5,650 | 6,923 | 105 | 0 | 0 |
| gemini | 7,050 | 1,514 | 1,384 | 5,666 | 6,944 | 106 | 0 | 0 |
| grok | 7,035 | 1,514 | 1,381 | 5,654 | 6,929 | 106 | 0 | 0 |
| chatgpt | 7,042 | 1,514 | 1,381 | 5,661 | 6,936 | 106 | 0 | 0 |
| meta | 7,053 | 1,516 | 1,381 | 5,672 | 6,947 | 106 | 0 | 0 |
| kimi | 7,047 | 1,515 | 1,381 | 5,666 | 6,941 | 106 | 0 | 0 |
| ped | 7,318 | 1,530 | 2,180 | 5,138 | 2,621 | 0 | 0 | 4,697 |
| (retired) claude_new_pz | 3,734 | 1,399 | 1,028 | 2,706 | 3,629 | 105 | 0 | 0 |
| (retired) claude_p0 | 3,734 | 1,399 | 1,028 | 2,706 | 3,629 | 105 | 0 | 0 |
| (other) claude_pm | 7,370 | 1,588 | 1,459 | 5,911 | 2,231 | 22 | 0 | 5,117 |

Capture times seen (ET): 2026-10-20 15:58, 10-21 16:21, 10-22 04:54, then daily 00:05-00:21 ET (about 04:05-04:21 UTC), drifting about 3 minutes later each day.
Moves distribution over the 537 calendar names: 4 moves = 480, 3 = 31, 0 = 11, no trade cache = 15. None has 8.
Claude Pro cache keys: claudepro:eqboard 38,688 B, claudepro:one:LITE 3,098 B, claudepro:scan:7 1,292,557 B; no claudepro:scan:15. Largest stored value in swr_cache 1,450,133 B; none over the 1.5 MB cap.
