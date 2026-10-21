# Phase 7 — Production Deployment Checklist & Rollback Plan (Institutional Intelligence)

Prepared, **not executed**. The institutional module is additive and independent;
nothing here touches the Earnings AI DB, models, or BUY/SELL signals.

## Pre-deploy gates (ALL must be green — hard gates)
- [ ] **G1** Disposable Postgres TEST validation passed
      (`migrations/institutional/pg_compat_check.py` → all PASS). *[blocked until TEST project exists]*
- [ ] **G2** Production institutional Supabase project provisioned (separate
      Frankfurt Pro project; see `PHASE7_STEP1_INSTITUTIONAL_DB.md`). *[needs approval + creation]*
- [ ] **G3** `migrations/institutional/001_institutional_schema.py --apply` run on
      the new prod project; schema + indexes present.
- [ ] **G4** Data migrated (Option A COPY or B re-ingest) and the integrity
      anchors reconcile (holdings 546,712; funds 8,964; securities 75; cusip_map
      70 HIGH/5 MEDIUM; SUM(shares)=281,587,229,390; GOOG≠GOOGL; AAPL bearish).
- [ ] **G5** `INSTITUTIONAL_DATABASE_URL` + `INSTITUTIONAL_ALLOWED_REFS` set
      server-side (secret store); frontend bundle carries no DB credential.
- [ ] **G6** Distinct production `SESSION_SECRET` set (not the Earnings AI secret).
- [ ] **G7** Connection budget confirmed (institutional ≤5; separate project =
      own 15-cap).
- [ ] **G8** Earnings AI regression unchanged — AI/signal files byte-identical to
      baseline; BUY/SELL spot check. *(verified in dev: no diff vs `main`.)*
- [ ] **G9** `cd frontend && tsc --noEmit` clean; full backend suite green.
- [ ] **G10** Institutional DB snapshot taken (rollback point).

## Deploy sequence
1. Merge `institutional-intelligence` into the deploy branch *(no earnings-DB migration)*.
2. Set the env vars (G5/G6) on the production host.
3. Apply migration 001 to the prod institutional DB (G3), load data (G4), verify (G4 anchors).
4. Build frontend; deploy backend + frontend.
5. Smoke test (below) before announcing/unhiding the nav entry.

## Post-deploy verification
- [ ] `GET /api/institutional-intel/health` → available, dev/prod source correct.
- [ ] Unauthenticated `/api/institutional-intel/overview` → **401**; authenticated → **200** real data.
- [ ] All six dashboard sections render; disclosures + mapping-confidence warnings visible.
- [ ] Point-in-time as-of between filing windows → latest-visible quarter (no look-ahead).
- [ ] **Earnings AI pages + BUY/SELL dashboards behave exactly as before** (compare to baseline).
- [ ] Institutional connection count within budget; no impact on earnings pooler.

## Rollback (fast, reversible, zero earnings impact)
- **Feature:** revert the merge/branch — removes the routes, page, and nav. No
  earnings-DB migration to undo.
- **Config:** unset `INSTITUTIONAL_DATABASE_URL` → institutional endpoints return
  `UNAVAILABLE`; the rest of the app is unaffected (fastest kill switch).
- **Data:** the institutional DB is independent — restore the G10 snapshot, or
  retain/drop the project. Earnings data and historical predictions are never
  touched.
- **Secret:** `SESSION_SECRET` change is reversible via rotation.

## Monitoring (production) — signals to wire
API health; DB connectivity; pool utilization; query latency; error rate; data
freshness (latest filing quarter); last ingest status; failed mappings;
data-quality warnings. **An institutional failure must not interrupt the Earnings
AI engine** (separate DB/engine guarantees this).

## Recovery runbook (summary)
- **Institutional DB outage:** endpoints return UNAVAILABLE (graceful); earnings
  unaffected; restore snapshot / unpause project.
- **Failed/incorrect ingest:** staging + data-quality gate blocks publish; roll
  back to pre-ingest snapshot.
- **Connection exhaustion:** separate pooler isolates earnings; lower pool if shared.
- **Auth failure:** existing session middleware; distinct secret prevents cross-env.
- **Rollback to previous institutional release:** revert branch + unset env var.
