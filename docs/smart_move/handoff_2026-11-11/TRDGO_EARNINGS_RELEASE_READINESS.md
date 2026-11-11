# TRDGO.US Earnings / Smart Money Move — RELEASE READINESS (2026-11-11)

## Final acceptance checklist
| Requirement | Status | Basis |
|---|---|---|
| Previous code changes verified | PASS | Changelog §A (calendar_tiers + wrapper diffs confirmed; runner/engine20/live diffs awaited) |
| Intentionally removed features preserved | PASS | REMOVED_FEATURES.md; nothing restored |
| Kill-switch enforcement verified | PASS | runner KILL present-by-design, released only inside try/finally (v6.3); wrapper KILL absent-by-design; scripts re-create on crash |
| LIVE scheduled-script safety verified | PASS (v6.3) | refusal exit 3 + persisted record; budget pinned; task args checked; machine-wide flag refused |
| Request ceilings verified | PASS | 150/run, 700/NY-day enforced in governor + wrapper + runner; 100/h live-hours rail now a pause |
| Shared API-key protection verified | PASS | attestation gate (24 h, designated collector, cap 2), UW_MAX_INFLIGHT=1 in the runner, machine slots |
| Automatic next-day earnings discovery verified | PASS | P0-02: window-driven, 4 passes/day each window seen ≥3 times |
| BMO/AMC handling verified | PASS | UW report_time + Finnhub hour + Finviz clock; Monday-BMO via the Friday variant |
| Calendar freshness verified | PASS | T0 5 h / T1 24 h per-slot; E01/E02 |
| T0/T1 priority verified | PASS | tests |
| F1–F7 evidence requirements verified | PASS (findings) / DECISION (factor F7 capture) | P0-05 |
| Evidence persistence verified | PASS | store append + sha + requests/evidence/ledger; dedup |
| Prediction timestamps verified | PASS | generation time, evidence times, ET; look-ahead refused (publisher tests) |
| Missing-data handling verified | PASS | NVP never neutral; PENDING_REFRESH closed out as WINDOW_MISSED (v6.4 candidate) |
| Earnings results verified | NOT TESTED | outcomes grading exists (smart_move_outcomes); not re-verified against live data in this pass |
| Historical outcome calculations verified | BLOCKED | needs the production ledger/outcomes (E09) |
| Dashboard integration verified | PASS (local) | reads correct files, no stale-as-fresh leak; label defects fixed on the app branch (STALE = cadence + grace, scanner mode, mojibake, ETF strip dash) — awaiting deploy |
| Scheduler reliability verified | PARTIAL | resume/dedup/lock PASS; Friday-holiday Monday-BMO gap (D3) and no alert channel |
| Full regression suite completed | PASS (offline) | TEST_RESULTS.md |
| Working TRDGO.US application preserved | PASS | /health, /safety, A.10 running; Smart Money Move scanning again |
| Controlled LIVE validation separately approved and completed | PARTIAL | kit v6.3 installed with approval; the 17:20 VPS pass is the first full validation of the pause behaviour (pending) |
| Production deployment separately approved and completed | PARTIAL | kit v6.3 + scripts: DONE; app hardening + 15:50 close: NOT deployed; kit v6.4 (D1/D2/D4): local only |

## Verdict
COMPLETED: safety chain repair, planner E01/E02, scanner verification, kit v6.3 install, incident recovery.
FIXED: 3 FAIL + 4 PARTIAL safety items; D1/D2/D4 (local); intraday 16:01 close (local).
TESTED: offline suites above; VPS gates/plan-only.
PENDING: 17:20 pass result; app deploy (hardening + 15:50 close + dashboard labels); kit v6.4 install (built, rehearsed 20/20, staging block ready).
BLOCKED: historical accuracy rebuild (data), Friday-holiday schedule (owner), factor-F7 capture (owner), alert channel (owner choice of email/webhook).
READY FOR CONTROLLED LIVE VALIDATION: yes for Smart Money Move under kit v6.3 (already scheduled); the app-side changes need their own deploy approval.
