# Phase 7 — Production-Readiness Plan (Institutional Intelligence)

**Status:** PLAN ONLY — read-only preparation. Nothing in this document has been
executed. No cloud resources created, no costs incurred, no secrets / env /
database settings / production code changed, no VPS or production Supabase
access, and the `institutional-intelligence` branch (commit `bc28d31`) has not
been pushed, merged, or deployed.

**Scope:** make the development-complete SEC 13F Institutional Intelligence
module production-ready **without affecting the Earnings AI**. All 10 AI models,
BUY/SELL signals, scoring formulas, weights, thresholds, options-flow and
technical calculations, and historical predictions remain **unchanged** — this
plan adds a separate, informational module alongside them.

---

## 0. Validation status — completed vs outstanding

### Completed in Phase 5 / 6 / 6.1 (evidence on hand)
- Institutional module built and isolated: own guarded engine
  (`institutional_dashboard_service._engine`) that refuses the production ref
  `ddetojbocaxhcfxtcopc`; reads TRDGO-US-DEV only.
- 8 read-only endpoints under `/api/institutional-intel/*`, auto-gated by the
  existing session middleware (verified 401 unauth / 200 authenticated).
- Frontend dashboard — all **6 sections verified** against real dev data
  (Overview, Stock Analyzer incl. point-in-time, Activity Rankings, Manager
  Explorer, Ownership History, CUSIP Mapping); desktop + mobile; BULLISH/
  BEARISH/NEUTRAL styled distinctly from BUY/SELL.
- Data: Q1+Q2 2026, 8,964 managers, 546,712 holdings, 75 securities; CUSIP map
  70 HIGH / 5 MEDIUM; AAPL reconciled to raw SQL; point-in-time as-of verified
  (no look-ahead); GOOG vs GOOGL confirmed separate share classes.
- Test isolation: `tests/conftest.py` fail-closed guard; **full suite 546/546
  passed, 0 failed** on the default local SQLite test DB.
- AI preservation: `earnings_equity_service`, `claude_last_service`,
  `grok_service`, `all_models_service`, `directional_model` **byte-identical to
  `main`**; no AI file modified.

### Outstanding (requires Phase 7 execution, each gated by approval)
- **Postgres-dialect validation** of the institutional module and DB-backed
  tests (suite currently green on SQLite, not Postgres).
- **Production institutional database** provisioning (separate project).
- **Distinct production `SESSION_SECRET`** for environment separation (6.1
  found a shared-secret auth gap).
- **Connection-pool budgeting** and an institutional-engine `connect_timeout`
  for production.
- **Quarterly ingest + CUSIP re-mapping SOP** and data-quality gates.
- **Deploy / verify / rollback runbook** execution.

---

## Deliverable 1 — Dedicated production institutional Supabase database

**Recommendation: a SEPARATE production Supabase project**, independent of the
Earnings AI production project (`ddetojbocaxhcfxtcopc`). Rationale: an ingest
fault, a schema change, or a connection spike in the institutional module can
then never touch earnings data, predictions, or the 15-connection budget of the
BUY/SELL engine. The module already uses its own engine and URL, so no shared
code changes are required.

**Topology**
```
Earnings AI (unchanged)      Institutional Intelligence (new, separate)
  prod project ddeto...        prod project <new-ref>
  DATABASE_URL                 INSTITUTIONAL_DATABASE_URL  (server-side only)
  database.py engine           institutional_dashboard_service._engine
  own 15-conn pooler           own 15-conn pooler
```

**Setup runbook (to execute only on approval)**
1. Create a new Supabase project in the same region as prod (Frankfurt) —
   *approval + cost checkpoint: confirm plan/tier before creating.*
2. Capture its project ref and pooler host; record the ref in the institutional
   engine's **allow-list** (mirror of the existing dev/prod guard) so the engine
   accepts the new prod ref and still refuses `ddetojbocaxhcfxtcopc`.
3. Create the institutional schema there (tables: `institutional_holdings`,
   `institutional_funds`, `institutional_securities`, `institutional_cusip_map`,
   `institutional_ingest`) + the additive performance indexes.
4. Set `INSTITUTIONAL_DATABASE_URL` in the production host's secret store only —
   never in a committed file or the frontend bundle.
5. Ingest the validated dataset (see Deliverable 5) and run the read-only
   dashboard checks before exposing the nav entry.

**Rollback:** the institutional DB is independent — it can be retained or dropped
with zero effect on the earnings DB or predictions.

---

## Deliverable 2 — PostgreSQL compatibility validation (no Docker, no new software)

**Constraint honored:** do **not** run the full suite against TRDGO-US-DEV; use a
**truly isolated, disposable PostgreSQL environment that cannot modify existing
institutional data.**

**Two-part approach**

**(a) Static dialect audit — available now, zero infrastructure, read-only.**
Grep the institutional models/queries for Postgres-specific constructs and
confirm support on the target version:
- `FILTER (WHERE …)` aggregates (used throughout the dashboard service),
- `FULL OUTER JOIN … USING (…)`, `DISTINCT ON`, window/`GROUP BY` pivots,
- any `JSONB`, array ops, `ON CONFLICT`, `now()`/server defaults in the models,
- `SET statement_timeout`, `sslmode`, `connect_timeout` connection options.
Output: a compatibility matrix noting anything that passes on SQLite but needs
Postgres confirmation (e.g., `FILTER` is Postgres-native but SQLite-emulated).

**(b) Disposable Postgres test project — proposed for approval (not created now).**
Because the task forbids Docker, new software, and touching TRDGO-US-DEV, the
isolated option is a **separate, disposable Supabase TEST project** (free tier,
its own ref), used only for dialect validation and then deleted:
- *Approval + cost checkpoint before creation* (free tier → no cost; still a
  cloud resource, so it waits for your go-ahead).
- Point `TEST_DATABASE_URL` at this throwaway project; the conftest guard still
  refuses the prod ref and never touches dev/prod.
- Create ONLY the institutional schema there, run the institutional + DB-backed
  tests, record pass/fail, then **delete the project**. It cannot modify
  existing institutional data because it is a different database entirely.
- *Alternative if no test project is desired:* ship with the static audit (a)
  only and treat full Postgres validation as a documented residual risk.

**Explicitly excluded:** running the suite against TRDGO-US-DEV; creating a test
schema inside dev/prod; installing Docker or a local Postgres.

---

## Deliverable 3 — Authentication hardening & separate SESSION_SECRET

**Finding (Phase 6.1):** the isolated dev backend accepted a session cookie it
never issued (a view-only user appeared), i.e. it shared a signing secret with
another backend — most likely the on-disk persisted `session_secret` fallback in
`auth_service`. No data leak (institutional data stayed dev-only), but cookies
must not be valid across environments in production.

**Plan (production-sensitive — each step gated by approval; nothing changed now)**
1. **Distinct `SESSION_SECRET` per environment.** Production institutional/front
   surface gets its own secret; ensure it does not fall back to the shared
   persisted secret file. *Approval checkpoint: this is a secret change — you
   apply it in the host secret store; I only document the exact variable.*
2. **Keep endpoints behind the existing session middleware** (already true) — no
   weakening of authentication.
3. **Access tier decision:** institutional reads are GET and currently allowed to
   view-only accounts. Confirm that is intended, or gate behind `full_access`.
4. **Dev tools excluded from prod:** `dev_backend.py` and
   `institutional_preview.py` are development-only entry points and must not be
   on the production serving path (prod serves from `main.app`).
5. **No credentials to the browser:** `INSTITUTIONAL_DATABASE_URL` and secrets
   stay server-side.

**Risk if skipped:** a cookie minted in one environment is honored in another.
**Rollback:** secret rotation is reversible; no schema or code dependency.

---

## Deliverable 4 — Connection-pool capacity & timeout recommendations

**Budget facts**
- Supabase pooler cap: **15 connections** per project.
- Earnings `database.py` engine: capped below 15 (existing config) — unchanged.
- Institutional engine: `pool_size=3, max_overflow=2` → **≤5** connections.

**Recommendations**
- **Preferred:** institutional on its **own project** (Deliverable 1) → it has
  its own 15-cap; the earnings budget is wholly unaffected. No tuning needed.
- **If it must share the earnings project (not recommended):** lower the
  institutional engine to `pool_size=2, max_overflow=1` (≤3) and verify the
  combined total stays well under 15 under load.
- **Add `connect_timeout` to the institutional production engine** (dev already
  uses `connect_timeout=8`) so a stalled connect fails fast rather than hanging
  a worker (the root cause of the Phase 6.1 test hang).
- **Do NOT** change the earnings `database.py` engine (no connect-timeout there
  today) without a separate, explicit approval — it is production-sensitive.

**Verification:** read-only connection-count check against the institutional DB
under representative load before launch.

---

## Deliverable 5 — SEC 13F data governance, limitations & quarterly ingestion

**Limitations to keep disclosed in-product (already shown in the UI banner/notes)**
- Delayed **quarterly** disclosure, filed up to **45 days** after quarter-end —
  **not live order flow**.
- **No 13F-A amendment handling** — source ingestion lacks submission form type;
  disclosed, never faked.
- **Day-granularity** filing (acceptance) dates — not intraday precision.
- **Current ticker ≠ historical identity** — CUSIP→ticker is the current map.
- **Bounded universe** (75 securities in dev) — production scope is a product
  decision.
- CUSIP mapping confidence: **HIGH exact / MEDIUM flagged** (not auto-promoted).
- Institutional **BULLISH/BEARISH/NEUTRAL** is ownership-change only and is kept
  **separate from BUY/SELL** — never combined, never fed into an AI score.

**Quarterly ingestion SOP (proposed)**
1. Snapshot/backup the institutional DB (rollback point).
2. Download the quarter's SEC 13F dataset; ingest into the institutional DB only.
3. Re-run CUSIP→ticker mapping (OpenFIGI; respect rate limits); re-review MEDIUM.
4. **Data-quality gate** before go-live: row counts, per-quarter filing-date
   spread, mapping-confidence counts, AAPL-style reconciliation spot check.
5. Publish only after the gate passes; otherwise roll back to the snapshot.

**Governance:** define the production universe (curated vs full) and the ingest
cadence/owner; document the OpenFIGI dependency and its limits.

---

## Deliverable 6 — Production deployment, verification & rollback runbook

**Deploy (additive, reversible — execute only on approval)**
1. Confirm pre-deploy checklist (below) is fully green.
2. Merge `institutional-intelligence` (commit `bc28d31`). It adds new API paths
   and a new frontend page/nav; `database.py`, `main.py`, and all AI code are
   untouched — no earnings-DB migration.
3. Set `INSTITUTIONAL_DATABASE_URL` (prod institutional project) server-side.
4. Build the frontend; deploy backend + frontend.
5. Smoke-test: `/api/institutional-intel/health` (dev/prod source), 401 unauth,
   200 authenticated, all 6 sections render real data, disclosure visible.

**Pre-deploy checklist**
- [ ] Deliverable 2 Postgres validation green (or residual risk accepted).
- [ ] Deliverable 1 production institutional DB provisioned + schema + data.
- [ ] Deliverable 3 distinct `SESSION_SECRET` set; dev tools off the prod path.
- [ ] Deliverable 4 connection budget confirmed; institutional `connect_timeout`.
- [ ] Earnings AI regression unchanged (AI files byte-identical; BUY/SELL spot
      check) — **hard gate**.
- [ ] Institutional DB backup/snapshot taken.

**Verification after deploy**
- Earnings AI pages and BUY/SELL signals behave exactly as before (compare to a
  saved baseline).
- Institutional dashboard reads correctly and is clearly informational.

**Rollback**
- Revert the merge/branch — **no earnings-DB migration to undo.**
- The institutional DB is independent: retain or drop it with zero effect on
  earnings data or historical predictions.
- Session-secret change is reversible via rotation.

---

## Master implementation sequence & approval checkpoints

1. **[CP-1]** Approve Deliverable 2(a) static dialect audit (read-only, no infra).
2. **[CP-2 — cost/resource]** Approve a disposable Supabase TEST project for
   Postgres validation (Deliverable 2b), then delete it.
3. **[CP-3 — cost/resource]** Approve the production institutional Supabase
   project (Deliverable 1).
4. **[CP-4 — secret change]** Approve distinct production `SESSION_SECRET`
   (Deliverable 3).
5. **[CP-5]** Approve connection/timeout settings (Deliverable 4).
6. **[CP-6]** Approve the quarterly ingest SOP + first production ingest
   (Deliverable 5).
7. **[CP-7 — deploy]** Approve merge + deploy with the pre-deploy checklist green
   (Deliverable 6).

Each checkpoint is independent; none proceeds without your explicit approval.

## Risk register (summary)
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| SQLite-only test pass hides a Postgres dialect bug | Med | Med | Deliverable 2 validation before deploy |
| Shared session secret honors cross-env cookies | Med | Med | Deliverable 3 distinct secret |
| Connection-cap exhaustion affects earnings | Low (separate DB) | High | Separate project; pool budget |
| Bad quarterly ingest | Med | Med | Snapshot + data-quality gate + rollback |
| Scope creep into AI code | Low | High | Additive-only; AI files byte-identical; hard gate |

## AI preservation guarantee
No step in this plan modifies the 10 AI models, BUY/SELL calculations, scoring
formulas, weights, thresholds, options-flow/technical logic, or historical
predictions. The institutional module remains separate and informational, on its
own database and engine.

---

*End of Phase 7 plan. Nothing executed. Awaiting explicit approval per checkpoint.*
