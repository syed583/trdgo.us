# Smart Move - news collection, analyst-snapshot schedule and news validation plan (2026-11-02)

Proposal only. Nothing here is scheduled or running. Every request would go through the existing governed path (authorization cap, 2 s spacing, hourly share, 401/403/429 stop, kill switch). No scoring, threshold, V1/V2 rule or stored prediction changes.

## 1. News collection (needs owner approval of count, schedule and budget)

* **Who:** stocks with an open 48-to-24-hour window under the existing eligibility rules (`RefreshService.eligible_events`).
* **When (per event):** INITIAL pass as soon as the window opens; FINAL pass between cutoff-6 h and cutoff-1 h. Nothing is requested in the last hour or after the cutoff (`smart_move_collection_plan.news_pass`). A failed record does not count as done, so it is retried at the next due time.
* **What:** `/api/news/headlines?ticker=SYM&limit=25`, page 0 first. A further page only while the page was full and its oldest headline is still inside the 7-day lookback (`news_next_page`); at most 3 pages per ticker per pass (page 1 verified live on MSFT).
* **Evidence rules:** de-duplicate on (publication time, source, headline); keep publication and retrieval times; headlines published at or after the cutoff and records retrieved after it are never pre-cutoff evidence (`pre_cutoff_headlines`, `accept_retrieval`); ticker collisions and unreliable sources are flagged by the existing news-x1 checks and stay UNVERIFIED.
* **States kept apart:** OK, filtered only, no pre-cutoff news, empty, failed, not attempted (`news_coverage`).

| Eligible events in the window | Floor (page 0, 2 passes) | Expected (about 15% need a 2nd page) | Ceiling (3 pages every pass) | Time at 2 s spacing (floor / ceiling) |
|---|---:|---:|---:|---|
| 116 (observed at the 1 Nov run start) | 232 | 267 | 696 | 8 / 23 min |
| 330 (largest listed single day, before eligibility filtering) | 660 | 759 | 1,980 | 22 / 66 min |

The actual count is known only when the calendar is refreshed on the collection day. Share of 15,000 requests/day: 1.5-13%. Hourly-share limits apply (about 100 per hour in live market hours), so large passes belong off-hours.

## 2. Analyst-estimate snapshots (needs owner approval)

* Ledger verified on a temporary copy: 92 rows for 94 stocks with an exact fiscal-quarter mapping (1 stock without quarter, 1 without the exact row); a second rebuild adds 0 rows; 0 duplicate keys; each stock has one snapshot (all 1 Nov 2026).
* Those quarters belong to events that already reported on 2-4 Nov, so they cannot support a revision for the next report (a different fiscal quarter). Earliest a second snapshot of the same quarter would be useful: 2026-11-08, which no longer matters for these events.
* Daily schedule (`snapshots_due`): one `earnings-estimates` request per tracked stock per UTC day, only when a mapped fiscal quarter exists; a snapshot already taken today is not repeated; nothing is reconstructed from current values.
* A revision is measurable once two snapshots of the same quarter are 7 to 60 days apart (`revision_eligibility`), so the first snapshot must be taken at least 7 days before the capture. A stock therefore has to be tracked from about 9 to 10 days before its report.
* Budget: 1 request per tracked stock per day. About 800 tracked stocks = 800/day (27 minutes at 2 s spacing); 1,200 = 1,200/day. These tracked counts are estimates (roughly 58 eligible events per release day over a 14-day horizon), to be replaced by the real calendar count.
* Cheaper option, for decision: two snapshots per event (one at about 10 days before, one inside the window) = about 2-3 requests per event instead of about 14.

## 3. News validation plan (no scoring until every step is complete and approved)

1. Export the stored headlines to the labelling sheet (`news_labelling_sheet.csv`, blank `human_label`).
2. Two independent reviewers label each row POSITIVE / NEGATIVE / NEUTRAL / NOT_RELEVANT / UNSURE for its likely effect on the next earnings reaction, without seeing the classifier label or any outcome.
3. Pre-declared gate (`validation_gate`): at least 200 labelled rows, at least 30 POSITIVE and 30 NEGATIVE, classifier agreement of at least 80% on directional rows, reviewer agreement measured and reported.
4. Collect more company-wire items first: the 133 stored unique headlines contain 0 explicit guidance changes and only 2 directional research candidates (neither earnings-related), so the sample is too thin to label meaningfully.
5. Even if the gate is met, scoring needs the owner's explicit approval and a separate, pre-registered rule; provider sentiment is never used.
