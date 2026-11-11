# TRDGO.US Earnings / Smart Money Move Engine20 — FINAL AUDIT (handoff of 2026-11-10, executed 2026-11-11)

Scope: the production engine (kit v6.1 as installed on the VPS, git `d47a2a3f`), the scheduled LIVE chain (v6.1 scripts,
wrapper v2), the Nov 10 unauthorised VPS edits, and the dashboard reader. Evidence = file:line citations in the two
verification reports (P0-01 safety chain; P0-02/P0-04 scanner; P0-03/P0-05 request path and findings) and the VPS
outputs pasted by the owner on 2026-11-11. No provider request was made by this work; no production task was changed
without the owner's explicit "OK" (restore of the v6.1 files; install of kit v6.3; repoint of the two LIVE tasks).

## 1. State found on 2026-11-11 morning
- Five protected files edited on the VPS on Nov 10 (runner, engine20, live, calendar_tiers, wrapper). The hash gates in
  the scheduled scripts refused every run from Nov 10 16:15 to Nov 11 09:30 VPS, exiting 0 ("success"). The scanner was
  silently down ~18 h. A LIVE pilot at `-Authorize 5 -DailyLimit 5` (10 requests, 0 predictions) had run at 15:00Z.
- Restored to kit v6.1 from the verified Nov 6 backup (hashes = kit table); edits preserved in `C:\backups\sm_edits_20261110`.
- 11:20 VPS LIVE pass then published 181 records (BUY 4, SELL 2, NO_TRADE 11, NVP 26, PENDING_REFRESH 138), calendar
  refreshed, but halted `CONSECUTIVE_FAILURES` at exactly 100 requests (own hourly rail counted as failures = the v6.2 defect).

## 2. Findings by handoff item
| Item | Verdict | Evidence / action |
|---|---|---|
| P0-01 LIVE script safety (14 checks) | 7 PASS, 4 PARTIAL, 3 FAIL → all FAIL/PARTIAL fixed in kit v6.3 + v6_3 scripts except hash-pinning of the scripts themselves and the Windows-alert channel | FAIL: refusal exit 0 + nothing persisted (fixed: exit 3 + BLOCKED record + last_gate_refusal.json); hourly-cap halt (fixed: v6.2 pause); lowered budget passes every gate (fixed: pinned 150/700 unless -PilotApproved). PARTIAL: defaults made a no-arg task fully live (fixed: mandatory); kill unlink outside try (fixed); machine-wide live flag not refused by the runner (fixed); tests gaps (added) |
| P0-02 next-day scanner | PASS with 3 reporting defects fixed (D1, D2, D4) and 1 schedule gap open (D3) | Window-driven (release−48h..−24h), zoneinfo America/New_York, holidays/early closes, change tracking, resume via freshness/dedup. Open: Friday-holiday week has no Monday-BMO pass inside the variant window (owner schedule decision) |
| P0-03 zero provider requests | ROOT-CAUSED, not a defect of the LIVE chain | Ceiling 5: calendar refresh needs 3 pages + 2 confirmations = 5 → 0 left for evidence; second run same day: DENIED_DAILY_CAP → exit 6. Governor denials never reach attempts.jsonl (diagnose from ledger.jsonl). A ceiling < ~40 cannot produce predictions. v6_3 scripts refuse such budgets |
| P0-04 calendar completeness | PASS | t0_complete reported in plan/universe/dashboard; E01 floor + E02 priority added with tests; deferred slots never treated as fresh |
| P0-05 F1–F7 (P1 audit findings) | F1, F2, F3, F4, F6, F7 FIXED (tests cited); F5 intentionally left; ADV20 gate, short-history rule intentionally frozen | Factor-F7 options capture (-WithF7) is OFF by scope decision, not formally decided → owner yes/no requested |
| E01 / E02 (ChatGPT snippets) | Re-implemented from scratch with tests (not copied) | `smart_move_calendar_tiers.plan_refresh`: urgent slots may use the hard ceiling minus confirmations; T1 starved while any T0 deferred |
| E03 missing env flag | Verified: process-only flag set by the script, cleared in finally; runner now also refuses a User/Machine-scope flag | |
| E04 pilot zero requests | Resolved (see P0-03) | |
| E05 t0_complete=false | Temporary deferral under tiny ceilings, not a planning defect; with 150/run the 11:20 pass completed T0 | |
| E06 scripts bypass controls | Fixed in v6.3 (see P0-01); remaining: scripts not self-hash-pinned | |
| E07 F1–F7 | See P0-05 | |
| E08 missing/short-history evidence | Intentional frozen classification (NVP, never neutral); PENDING_REFRESH now closed out as WINDOW_MISSED (D2) | |
| E09 historical point-in-time | NOT REBUILT in this pass (needs the ledger + outcomes on the VPS; research-only tooling exists: smart_move_accuracy / outcomes) | BLOCKED on data access, see pending approvals |
| E10 automatic next-day scanning | Verified (P0-02) | |
| E11 dashboard freshness | Known label defects (STALE threshold 150 min vs 6 h, DRY_RUN mode overwrite, title mojibake) remain in the app build; publisher now emits live_every_minutes=360 for the fix | |
| E12 release readiness | See TRDGO_EARNINGS_RELEASE_READINESS.md | |

## 3. Intraday (same day, same verification pass)
- A.10 build healthy; entries honour the 10:00–15:00 ET window and the 60 s quote rule; but every EOD close on Nov 9 and
  Nov 10 landed at 16:01 ET instead of 15:50 (board rebuild before exits). Fixed locally (`889e1332`): exits-first fast
  path from a direct quote at the cutoff, loop wakes at the cutoff, slow ticks logged. Not deployed.

## 4. What was installed on the VPS today (owner-approved, hash-gated)
Kit v6.3 (`stage_c_kit_v6_3.zip` sha `c52ce7a7…98cd3`, anchor `9ce563d3…aeea5`), backup
`C:\smlive\kit_v6_3_backups\kit_v6_3_backup_20261111_105521_606652`; scripts `C:\smlive\v6_3_*.ps1`; tasks
TRDGO-SmartMove-LIVE-6h and -MondayBMO repointed with `-Authorize 150 -DailyLimit 700`. Plan-only gates: all PASS.
