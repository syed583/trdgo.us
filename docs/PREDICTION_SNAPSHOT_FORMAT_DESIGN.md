# Prediction snapshot format — design for genuine, auditable earnings-model history

**Status: DESIGN ONLY. Nothing here is implemented, migrated or deployed.** It replaces nothing: `model_predictions` stays as it is and its rows are labelled legacy / not independently verifiable.

## Why the current table cannot support a verified track record
`model_predictions` stores model, symbol, earnings date, timing label, snapshot date, capture time, signal and score. It does not store input values, providers, input timestamps, biases, weights, points, coverage, tier, gate state, the model/config version, or the actual release time. A stored score therefore cannot be re-derived, and "before release" can only be estimated from a label. Backfilled rows carry date-only stamps.

## Principles
1. **Append-only.** A snapshot is written once and never updated. Outcomes live in a separate table.
2. **Point-in-time.** Every input records the provider's own as-of time and when we fetched it, so the score can be recomputed from what was known then.
3. **Reproducible.** Inputs plus the versioned code and config re-produce the stored score exactly.
4. **Honest about gaps.** Missing, stale, fallback-substituted and heuristic inputs are recorded as such, never as neutral.
5. **Verified is a computed status**, not a claim: a snapshot is "verified pre-release" only when the release timestamp is confirmed and the capture precedes it by a safety margin.

## Tables (additive)

### `earnings_events` (one row per company earnings event)
`event_id` (uuid), `symbol`, `company_id` / CIK, `fiscal_period` (e.g. FY2026Q3), `scheduled_date`, `timing` (BMO/AMC/UNKNOWN),
`release_ts_utc` (nullable until known), `release_ts_source` (e.g. SEC filing acceptance time, company press-release time, provider), `release_confirmed` (bool),
`first_seen_at`, `last_changed_at`; a child `earnings_event_history` keeps every schedule change.
Rule: if `release_ts_utc` is unknown, no snapshot of that event can ever be marked verified.

### `input_snapshots` (the raw evidence, one row per event per capture run per input)
`run_id`, `event_id`, `input_key` (e.g. `eps_estimates`, `options_flow`, `price_candles`, `short_interest`, `dark_pool_levels`, `spy_15d_return`, `vix_regime`, `historical_moves`),
`provider`, `dataset` (internal dataset id, not a URL), `provider_asof_ts`, `fetched_at`, `cache_age_s`, `freshness_class`
(live / delayed / cached_real / historical / derived / missing / fallback_substituted), `fallback_used` + `fallback_from`,
`value_json` (the numeric inputs actually used, with units), `row_count`, `payload_sha256` (hash of the full raw response kept in cold storage).
Stored once per event per run and shared by all eight models, so the eight models score the same evidence.

### `model_scores` (one row per event per run per model)
`snapshot_id` (uuid), `run_id`, `event_id`, `model_id`, `model_version` (git commit SHA of the scorer), `config_hash` (hash of weights, cutoffs, gates, rescale rules),
`captured_at_utc`, `final_score`, `raw_score`, `final_signal`, `tier`, `raw_signal`, `gate_state` (e.g. implied-move gate), `coverage_pct`, `agreement_pct`, `low_data` (bool),
`minutes_before_release` (computed at write; null if release unknown), `pre_release_status` (VERIFIED_BEFORE / BEFORE_UNCONFIRMED / AMBIGUOUS / AFTER / RETROSPECTIVE),
`row_hash` and `prev_hash` (hash chain within a run).

### `model_score_parameters` (one row per parameter per model score)
`snapshot_id`, `parameter_key`, `label`, `input_keys[]` (links to `input_snapshots`), `raw_bias`, `snapped_bias`, `base_weight`, `rescaled_weight`, `points_earned`, `excluded` + `excluded_reason`, `substitution_note`.
Sum of `points_earned` must equal `final_score` (checked at write; a mismatch rejects the snapshot).

### `event_outcomes` (separate, written only after the reaction session closes)
`event_id`, `base_close_date`, `base_close`, `reaction_close_date`, `reaction_close`, `move_pct`, `direction_band` (flat band used), `grading_method_version`, `graded_at`, `status` (GRADED / PENDING_REACTION / UNVERIFIABLE + reason). A row is created only when the reaction close exists; UNVERIFIABLE can be re-attempted, never permanent by default.

## Capture rules
- Scheduled runs at fixed ET times (for example 08:00 and 20:00 ET) plus one forced run T-30 minutes before known release times; each run gets a `run_id` and records the exact UTC time.
- Many runs per event are allowed. For statistics, the **canonical** prediction is the last snapshot with `pre_release_status = VERIFIED_BEFORE` (or the earliest, for lead-time analysis). The definition is fixed in code and in the report header.
- Scores are computed from the `input_snapshots` of that run, never from a cache that may be older than the run; the snapshot records the cache age so staleness is visible.
- Post-release or unconfirmed-timing snapshots are stored and labelled, but excluded from verified accuracy.

## Integrity controls
- Database role used by the app has INSERT/SELECT only on the four snapshot tables (no UPDATE/DELETE); a trigger rejects updates.
- Hash chain per run and a daily digest written to a second location (object storage or a git-tracked digest file), so later tampering is detectable.
- A nightly verifier re-scores a random sample from stored inputs with the recorded version and compares to the stored score and points.
- Outcome grading uses only `event_outcomes`; it never edits a snapshot.

## Reporting rules this enables
Accuracy is reported per model, per signal (BUY, SELL, NEUTRAL separately), one prediction per event, with graded n, unverifiable n, pending n, a 95% interval, and the base rate of up/down moves. Retrospective, ambiguous and post-release snapshots are shown in separate lines, never merged.

## Size and cost (estimate)
About 540 events × ~17 inputs per run ≈ 9,000 input rows per run; model scores 540 × 8 ≈ 4,300 rows per run; parameters ≈ 55,000 rows per run. At two runs a day, about 130,000 small rows a day (tens of MB a day before compression); inputs are shared across the eight models, which keeps it about 8× smaller than storing inputs per model.

## Rollout (when approved)
1. Create tables and a shadow writer that records snapshots without changing any score or screen.
2. Run the nightly verifier for two weeks; fix any re-score mismatches.
3. Fill `release_ts_utc` from filings/press releases for upcoming events.
4. Start reporting verified accuracy from the first verified snapshot forward. Old rows stay labelled legacy.

## Acceptance tests
- Every stored score recomputes from its stored inputs exactly (all eight models).
- Parameter points sum to the final score for 100% of snapshots.
- No snapshot is marked verified without a confirmed release timestamp and a capture before it.
- Re-running capture twice in a run does not duplicate or alter existing rows.
- Grading an AMC event before its reaction close leaves it PENDING_REACTION, not UNVERIFIABLE.
