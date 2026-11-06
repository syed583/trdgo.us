# Phase 2A production preview (read-only) — result and deployment-readiness (2026-10-29)

Tool `phase2a_preview.py` SHA-256 45400c7395d5df349bf6192e15ae5df0f65fa715067b425527ab842083fa07b4 (35,620 bytes) ran on the VPS: two SELECTs in READ ONLY transactions, no provider calls, network blocked during computation, wrote only `phase2a_preview.json`. (A later local fix to one summary line gives SHA 36b46be9...; see section 4; not re-run.)

## 1. Old versus corrected scoreboard (production data)
The tool's BEFORE column reproduces the earlier production scoreboard exactly (Claude 90 graded, BUY 43/20, SELL 9/2; every model matches), and AFTER-A reproduces the earlier verified_last figures exactly (Claude BUY 28/12 ... PED 8/3): the corrected selection logic is reading the real data correctly.

| Model | OLD BUY n/ok | NEW BUY n/ok acc (95% CI) | OLD SELL | NEW SELL | OLD BUY+SELL | NEW BUY+SELL (CI) |
|---|---|---|---|---|---|---|
| Claude | 43/20 | 28/12 43% (27-61) | 9/2 | 7/2 | 22/52 42% | 14/35 40% (26-56) |
| Claude Pro | 64/29 | 50/20 40% (28-54) | 5/0 | 3/0 | 29/69 42% | 20/53 38% (26-51) |
| Gemini | 43/17 | 34/12 35% (21-52) | 17/3 | 12/3 | 20/60 33% | 15/46 33% (21-47) |
| Grok | 78/35 | 58/23 40% (28-53) | 1/0 | 1/0 | 35/79 44% | 23/59 39% (28-52) |
| ChatGPT | 64/29 | 47/19 40% (28-55) | 2/0 | 2/0 | 29/66 44% | 19/49 39% (26-53) |
| Meta | 52/24 | 39/17 44% (29-59) | 6/0 | 5/1 | 24/58 41% | 18/44 41% (28-56) |
| Kimi | 50/21 | 42/14 33% (21-48) | 13/2 | 11/2 | 23/63 37% | 16/53 30% (20-44) |
| PED | 21/11 | 8/3 38% (14-69) | 0/0 | 0/0 | 11/21 52% | 3/8 38% (14-69) |
Legacy graded events 90 per model (PED 135); corrected 70 (PED 19). Baseline (from the earlier integrity run): up 42.9%, down 40.0%, flat 17.1% over 70 events.

## 2. Counts per model (corrected selection)
Eligible events 1,492-1,494 (PED 622); excluded events: post-release only 22 per model (PED 0), retrospective only 908 for PED; BEFORE_RELEASE rows ~6,925-6,947 per model (PED 2,621) for the same ~1,492 events (about 4.65 snapshots per event, counted once); post-release rows 105-106 per model; ambiguous 0; PED retrospective rows 4,697.

## 3. What could not be shown
`earnings_calendar` holds only 62 rows from 2026-10-13, none with a post-earnings move: the calendar table is NOT a usable outcome source, so AFTER-B graded = 0 for every model (that column says "no outcome source", not "no prices"). Grading with real prices therefore remains NOT VERIFIED on production data.
Readiness by timing (events dated up to today ET; 943 more not reported yet): Before Open 237 reported (68 reaction window still open, 169 past the close needing prices; legacy graded 87, legacy stamped unverifiable 82); After Close 338 reported (175 window still open, 163 past the close needing prices; legacy graded 4, legacy stamped unverifiable 230); timing unknown 48.

## 4. Defect found in the preview tool (not in the product code)
The "legacy stamps before reaction close" line used the earliest stamped row of an event, which could be a label-less backfill row of another model, masking the genuine After Close stamp (it printed After Close 9 / unknown 446). Fixed locally (genuine labelled captures of the scored models only; test added). The grading-by-timing legacy columns in the same output (After Close: legacy graded 4, stamped unverifiable 230) are correct and agree with the earlier integrity run (234 of 234 After Close events evaluated before their reaction close).

## 5. Deployment readiness
| Gate | Result |
|---|---|
| Corrected selection reproduces the production figures | PASS |
| One prediction per stock / event / model (no duplicate counting) | PASS (0 violations) |
| Post-release, ambiguous and retrospective rows excluded | PASS |
| Premature grading prevented (unit tests, DST, holidays, early close; production windows withhold 68 BMO + 175 AMC events now) | PASS |
| Scoring models unchanged (30 SHA pins) | PASS |
| Backend tests 747 pass (3 existing environment failures identical on the base) | PASS |
| PostgreSQL dialect (dev, throwaway schema) | PASS |
| Migration is additive, rollback is code-only | PASS |
| Claude Pro cache fix | PASS in tests; production effect only checkable after deployment |
| Grading validated against real prices on production | **NOT VERIFIED (blocking)** |
Overall: **FAIL / NOT READY** until the grader is shown to reproduce real outcomes on a small real sample (proposed: a read-only provider dry run of about 15 events: compare the corrected move with legacy graded Before Open outcomes, and show that After Close events graded after their reaction close produce plausible moves). Recommended before deployment: ship with the legacy-column sync switched off by default (the scoreboard reads only `earnings_event_outcomes`).
