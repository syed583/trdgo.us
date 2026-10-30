# Smart Move: P1 and P2 verification results

Run on 2026-10-30 between 13:01 and 13:08 UTC (09:01–09:08 ET). **50 read-only requests in total** (P1 10, P2 40), nothing else, no recurring job, no production change, no VPS action, no push. Frozen scoring rules and both pre-registrations unchanged. No outcome was used in any selection or scoring, and the sealed validation/test data were not opened. Files: `docs/smart_move/p1p2/` (manifests with hashes, per-request results, reports; the raw responses are in a local folder, not in Git, each with its SHA-256).

## 0. Pre-flight and what went wrong
- Checked before sending: all three credentials present (values never shown), manifest SHA-256 verified, hard cap, GET only, 2 s spacing, stop on 401/403/429, no retries. Rate limits seen in the responses: Unusual Whales `x-uw-req-per-minute-remaining` 1,000,000 (no practical limit), Finnhub 60 per minute (59 left), Finviz Elite is a subscription export with no per-request price known to me. The daily limit of the Unusual Whales plan is unlimited per the earlier notes; nothing was billed per call that I can see.
- **My runner made two mistakes in the first P1 attempt, costing half of its 10 requests.** (1) I used page 1 of the Unusual Whales calendar; pages start at 0, so three calls returned empty second pages and one returned the 4 rows beyond the first 100. (2) The Finviz call hit a 301 and my client refused every redirect. I stopped at the first error, froze a short continuation manifest (5 requests, total still 10, hash `0760a76a…cdf4`), and finished P1 inside the approved 10. The Finviz redirect rule follows only https://*.finviz.com (it went to `elite.finviz.com`).
- P2 first stopped on a hash check: the earlier manifest file had been saved with Windows line endings, so its bytes did not match its hash. The 40 requests are identical (the hash-ordered, outcome-blind selection was recomputed and matches); the P2 section was frozen as its own file (SHA-256 `8d31e0e7…93cd`).

## 1. P1 calendar verification report
**Requests (10):** Unusual Whales calendar for 2026-11-02 (premarket 25 rows, afterhours 104 rows over two pages) and for 2026-10-30 (afterhours 0 rows at 09:03 ET; premarket for 10-30 was not fetched); one Finviz export (100 tickers, the largest market caps among the Unusual Whales rows); one Finnhub calendar (258 rows, 55 dated 10-30 and 203 dated 11-02). Today's (10-30) coverage is therefore incomplete; the analysis is on 2026-11-02 (3 days ahead, 129 Unusual Whales rows).

**Real row formats**
| Vendor | Format |
|---|---|
| Unusual Whales | JSON `data` list; keys include `symbol`, `report_date`, `report_time` (`premarket` / `postmarket`), `source`, `marketcap`, `sector`, `has_options`, `expected_move_perc`. **`source` = `company` on all 129 rows.** |
| Finviz Elite | CSV, two columns `Ticker`, `Earnings Date` as `M/D/YYYY h:mm:ss AM/PM`. **Only two distinct clock values appear: `4:30 PM` (77) and `8:30 AM` (23).** |
| Finnhub | JSON `earningsCalendar` list: `symbol`, `date`, `hour` (`bmo` 66, `amc` 102, empty 90), `quarter`, `year`, estimates, actuals. |

**Agreement (2026-11-02)**
- Unusual Whales vs Finnhub: 116 symbols present in both, **116 same date, 116 same side, 0 conflicts**; 13 Unusual Whales symbols (10 %) are not in the Finnhub list.
- Unusual Whales vs Finviz (the 100 requested tickers): 97 same date and side; 3 Finviz rows carry a **past date** (RYAAY 2026-07-20, GRAB 2026-08-04, UDR 2026-10-21), i.e. stale vendor data, not a rival claim about the upcoming date. The calendar logic now sets such rows aside (recorded, never confirming, never a conflict; test added).
- Finviz vs Finnhub: 91 same date and side, 8 Finviz tickers absent from the Finnhub window, 1 stale date.
- **Timing conflicts: none** on any same-date pair. Finnhub's empty `hour` (90 rows) occurs only for names Unusual Whales does not list.
- Would-be status for the 129 Unusual Whales symbols at the retrieval moment (all observations seconds old, limit 6 h): **122 VENDOR_CONFIRMED, 7 ESTIMATED (SINGLE_VENDOR, 0 conflicts)**. In production this only holds if each vendor is refreshed within 6 h of use.

**Independence limitations (what these data cannot show)**
1. Perfect agreement in every comparable pair cannot separate independent confirmation from a shared upstream; the only disagreements were stale Finviz rows, which say nothing about independence. Independence stays **unproven**.
2. Finviz's clock is a side marker, not an announcement time: two values only, 4:30 PM for after-close and 8:30 AM for before-open, consistent with a label turned into a time. It adds no precision and counts as the same BMO/AMC claim.
3. All 129 near-term Unusual Whales rows are `company`; we saw no `estimation` row at 3 days, so where the flag flips was not measured. (Earlier saved rows showed `estimation` for dates months ahead.)
4. One snapshot: no date moves can be detected and no 48 h-before history exists.
5. Coverage differs by vendor: Finnhub lacks 10 % of the Unusual Whales names, Finviz was queried for only 100 tickers, and Unusual Whales returned nothing for today's after-close at 09:03 ET.

## 2. P2 volatility-context coverage
- **40 of 40 requests returned HTTP 200 with a complete payload** (requested dates 459 to 735 days back; events dated 2024-10-28 to 2025-07-31). No missing field. Skew label: put_rich 19, call_rich 12, flat 9. IV rank 15.5 to 92.6. Events: 25 after-close, 15 before-open.
- The F7 adapter accepted all 40 (`DATED_NOT_STAMPED`).

**Point-in-time integrity (a date parameter alone proves nothing, so it was tested)**
| Test | Result | Reading |
|---|---|---|
| Payload `date` equals the requested date | 40 of 40 | necessary only |
| Realized volatility `rv` vs the 21-log-return volatility of the saved closes | **exact for the window ending on the requested date (median error 0.0000, 40 of 40 within 2 %)**; windows shifted by 1 session deviate (median 1.3 to 1.5 %), by 3 sessions 4 to 8 %, by 10 sessions 38 % | the realized-vol part is computed as of that date, uses no later bar, and confirms our saved closes are the ones used then |
| Expiry arithmetic and weekday | `dte` = expiry minus date in 40 of 40; all expiries are Fridays | consistent |
| `market.vix` | null with `missing_periscope = true` in all 40 | suggests the record is reconstructed, not a stored live snapshot |
| Next-earnings date and time in the payload | equal to the final event in 40 of 40 | **cannot discriminate** as-of knowledge from hindsight: no event in the sample moved |
| Skew label, IV rank, IV percentile, implied move | no timestamp, no independent record | **not verifiable as of the date** |

Conclusion: the realized-vol field is as-of; the option-derived fields that F7 uses (the skew label) are **dated but not verified point-in-time**. They are therefore kept out of the primary replay and shown only as a labelled sensitivity. The dated next-earnings record also cannot yet be used as evidence that the calendar was right two days ahead.

## 3. Updated development replay (186 dev events; valid evidence only) and the separate SEC-F3 experiment
Details: `shadow_impl/results/real_replay2/replay2.md`. Capture at window start + 5 min, candles up to the last completed session, earlier events only, no outcomes. Evidence classes: candles PIT by date (now also matched exactly by the provider's own dated `rv`); event date and label as stored after the fact (hindsight); F7 and sector missing in the primary replay.

**SEC-derived prior events (EXPERIMENTAL, kept apart from the pre-registered provider source).** Ambiguous events are excluded and never used: `CLUSTER` (another 2.02 filing within 45 days) 196, `DURING_MARKET` (accepted 09:30–16:00) 57, `PROVIDER_MISMATCH` (a provider row within 3 days disagrees on date or side) 5. Usable: `BMO_CERTAIN` 687 (accepted before the open), `AMC_RECONCILED` 82 (after the close and the provider history has the same date and side), `AMC_UNCONFIRMED` 590 (after the close, nothing to reconcile with; an earlier release is possible). Filing acceptance time is never treated as the announcement time.

| Experiment (F7 absent) | Continuation BUY / SELL / NO TRADE | Contrarian | Coverage |
|---|---|---|---|
| A. provider history (pre-registered source) | 0 / 0 / 186 | 0 / 0 / 186 | 183 at 12/40, 3 at 2/40 |
| B-strict: SEC BMO-certain + AMC-reconciled | 8 / 9 / 169 | 9 / 9 / 168 | 65 at 27/40, 118 at 12/40, 2 at 17, 1 at 2 |
| B-moderate: adds AMC-unconfirmed | 19 / 9 / 158 | 14 / 9 / 163 | 124 at 27/40, 59 at 12/40, 2 at 17, 1 at 2 |

Reason codes per variant. A: INSUFFICIENT_COVERAGE 372, ILLIQUID_OR_UNKNOWN 88, SUSPECT_WINDOW 6, TIMING_CONTRADICTED 4. B-strict: INSUFFICIENT_COVERAGE 242, ILLIQUID_OR_UNKNOWN 88, BELOW_MARGIN 79, RULE 35, SUSPECT_WINDOW 6, TIMING_CONTRADICTED 4. B-moderate: BELOW_MARGIN 149, INSUFFICIENT_COVERAGE 124, ILLIQUID_OR_UNKNOWN 88, RULE 51, SUSPECT_WINDOW 6, TIMING_CONTRADICTED 4.

**F7 sensitivity, 40 P2 events, labelled "dated, point-in-time unverified"**

| Experiment | Continuation BUY / SELL / NO TRADE | Contrarian | Coverage |
|---|---|---|---|
| A + F7 | 0 / 0 / 40 | 0 / 0 / 40 | all 40 at 22/40 (below the 0.60 gate) |
| B-strict + F7 | 1 / 2 / 37 | 2 / 1 / 37 | 14 at 37/40, 26 at 22/40 |
| B-moderate + F7 | 5 / 3 / 32 | 4 / 3 / 33 | 35 at 37/40, 5 at 22/40 |

F7 lifts coverage from 27 to 37 where F3 exists; the engine only reaches a decision with F3 (or the sensitivity), never on F5/F8 alone. The decisions here describe the engine's behaviour on dated evidence; they are not shadow predictions and say nothing about accuracy.

## 4. Remaining blockers for a prospective shadow pilot
1. **Independence and the near-term flag are unmeasured over time:** one snapshot, 100 % agreement, no moved event, no `estimation` row near the date. Needs a few days of repeated snapshots (prospective, raw stored) before VENDOR_CONFIRMED can be trusted in practice.
2. **Unusual Whales calendar gaps:** nothing for today's after-close at 09:03 ET, and 129 names for 11-02 against 203 at Finnhub; coverage of the real universe (the 721 future events) is unmeasured.
3. **F3 source:** the pre-registered provider history is too short for the development window; the SEC-derived source is experimental and, in its strict form, covers only 67 of 186 events. A prospective pilot needs provider history stored from the start, or an approved alternative.
4. **F7 point-in-time:** option-derived fields cannot be verified as of a past date; prospective capture (stamped at retrieval) is the only clean source.
5. Sector mapping for F8 (the calendar rows now carry a `sector`, which can seed a mapping at no extra request).
6. Warmer usage not measured; no recurring job, production schema, storage location or kill-switch approved.
7. Replay remains hindsight on the event's own date and label.
8. The earnings-fix release remains a separate pending approval.

All 50 approved requests are used. Nothing further will be sent without a new approval.
