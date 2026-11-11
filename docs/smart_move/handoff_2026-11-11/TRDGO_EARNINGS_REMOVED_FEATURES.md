# INTENTIONALLY REMOVED OR SUPERSEDED — NOT RESTORED (2026-11-11)

Each entry: what, evidence of the decision, and confirmation that this work did not revive it.

| Feature | Evidence | Not restored |
|---|---|---|
| Smart Money TAB (smart_money_model.py, page, route, sidebar) | removed 2026-10-15 in b9da488a; SMART_MOVE_PILOT_AND_SCHEDULING_DESIGN_2026-10-30.md:127 | ✔ |
| Production-model adapter + unanimity rule and Confidence column on the signals page | SMART_MONEY_MOVE_SIGNAL_DATA_READINESS_2026-11-02.md:50,53 | ✔ |
| SEC-derived F3 in the primary model | kept exploratory only (smart_move_live.score_all `exploratory.sec_f3`); P1_P2_VERIFICATION_RESULTS_2026-10-31.md:76 | ✔ |
| "Verified release time" as a model input | superseded by SEC acceptance time as supporting evidence (ERRATA 2026-10-30) | ✔ |
| `sm_` table prefix | superseded by `smv_` (ERRATA) | ✔ |
| Sentiment, full-day dark-pool tape, provider "current" metadata in historical rows | dropped from the plan, SHADOW_PILOT_DESIGN_2026-10-30.md:13,25 | ✔ |
| Factors F1/F2/F4/F6 as scored inputs | capture-only, never scored (same doc :49,67) | ✔ |
| Releases 499390fd, package release_engine20_20261103 | superseded "do not use" (PHASE16 readiness :97) | ✔ |
| Supervised-test reminder for Nov 5 02:15 | retired, not an authorisation (P1_REPORT :4) | ✔ |
| Limits 50/100 and "no unattended LIVE" | superseded by the owner: 150/700, LIVE every 6 h + standing attestation (2026-11-05) | ✔ (kept at 150/700) |
| Nov 10 VPS edits (wrapper kill-path inversion; direct edits of 4 engine files) | unauthorised; restored to kit v6.1 with the owner's OK; preserved in `C:\backups\sm_edits_20261110` | ✔ (E01/E02 re-implemented with tests instead) |
| Finding F5 (Monday-BMO slot refreshed as T0) | "Not a defect; possible later refinement" (P1 deployment audit :13) | left as is |
| ADV20 ≥ 20M gate, short-history (≥4 reactions) rule, scorer thresholds | frozen scorer sha 62e09aa0…; P1_REPORT :19 | untouched |
| Intraday: v1 Trdgo Stock board, ChatGPT Intraday, Claude Pro Intraday, swing modules | removed in the A.10 cleanup (owner-approved 2026-11-06/07) | ✔ |
| Legacy earnings providers (free providers), IBKR | removed in the Oct 2026 security pass | ✔ |
| H1A/H1B research exclusions | research-only (H1B docs) | n/a |

No feature was restored on the basis of a historical report alone. Where the handoff asked for something already removed (restoring the Nov 10 edits as-is; copying their snippets verbatim), the current approved behaviour was kept and the requirement was re-implemented through the hash-gated kit path.
