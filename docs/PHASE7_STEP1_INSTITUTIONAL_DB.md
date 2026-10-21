# Phase 7 — Step 1: Institutional Production Database (Preparation Report)

**Status:** PREPARATION + READ-ONLY VERIFICATION ONLY. No Supabase project
created, no charges incurred, no production Supabase or VPS accessed, no existing
database/code/env/secret changed, nothing pushed or deployed. The only live
action taken was a **read-only inspection of the TRDGO-US-DEV** database to size
the plan accurately. All 10 AI models, BUY/SELL signals, scoring formulas,
thresholds, and historical predictions are untouched.

---

## 1. Current institutional schema & connection configuration (verified read-only)

**Dev project:** TRDGO-US-DEV (`qzmsozpyypjdxzfprhoz`), **PostgreSQL 17.11**, region Frankfurt (`aws-1-eu-central-1` pooler).

**Connection config (code, unchanged):** `institutional_dashboard_service._engine`
— its own engine from `INSTITUTIONAL_DATABASE_URL`, `sslmode=require`,
`connect_timeout=8`, `pool_size=3, max_overflow=2` (≤5 connections). Refuses the
production ref `ddetojbocaxhcfxtcopc`. The Earnings AI uses a separate engine
(`database.py`); the two never share a connection.

**Tables, sizes, row counts (measured):**

| Table | Rows | Total size | Heap | Indexes |
|-------|-----:|-----------:|-----:|--------:|
| institutional_holdings | 546,712 | 143 MB | 59 MB | 84 MB |
| institutional_funds | 8,964 | 1.6 MB | 736 kB | 848 kB |
| institutional_securities | 75 | 136 kB | 16 kB | 120 kB |
| institutional_cusip_map | 75 | 80 kB | 24 kB | 56 kB |
| institutional_ingest | 4 | 80 kB | 8 kB | 72 kB |
| institutional_activity | 0 | 40 kB | 0 | 40 kB |
| **TOTAL institutional_*** | | **157 MB** | | |

- `institutional_activity` is an **empty cache table used by the pre-existing
  earnings-side module** (`compute_institutional_activity`); the dashboard does
  not depend on it. It can be recreated empty in production (no data transfer).
- The dashboard uses 5 tables: holdings, funds, securities, cusip_map, ingest.

**Data baseline (integrity anchors):**
- Q1 2026: 267,645 rows · 7,572 managers · 75 securities · filings 2026-03-31…05-29
- Q2 2026: 279,067 rows · 7,703 managers · 75 securities · filings 2026-07-01…08-31
- CUSIP map: 70 HIGH / 5 MEDIUM, 75/75 with ticker.
- **Transfer checksums:** `SUM(shares)=281,587,229,390`; `SUM(fund_id)=2,723,683,112`;
  funds=8,964; securities=75; cusip_map-with-ticker=75.

> **Note:** this is a **bounded 75-security development dataset** (built from real
> SEC 13F Q1/Q2 2026 datasets, limited to 75 names for development). Whether
> production uses this bounded set or a full/curated universe is an **open
> question** (see §9) that changes storage, cost, and ingest effort.

---

## 2. Recommended production project

A **separate Supabase project**, region **Frankfurt (eu-central-1)** to match the
Earnings AI prod project's latency/residency, **PostgreSQL 17.x** (same major as
dev, so no cross-version migration risk). Completely independent — own ref, own
pooler, own 15-connection budget, own backups. The Earnings AI prod project is
**not touched**.

---

## 3. Sizing, cost, storage, connections, backups (estimates — confirm current Supabase pricing before CP-3)

| Dimension | Estimate | Basis |
|-----------|----------|-------|
| Database size (launch) | ~160 MB | measured 157 MB on dev |
| Growth | ~70 MB per new quarter | ~280k holdings rows + indexes/quarter |
| 1-year size (keep all quarters) | ~430 MB | 160 + ~4×70; **well under 8 GB** |
| Plan | **Supabase Pro ≈ $25/mo** | includes 8 GB DB, daily backups (7-day), no auto-pause, pooler |
| Compute | Pro default (Micro/Small) sufficient | read-mostly, 5-min cached; add-on only if dedicated compute wanted (~$10–15/mo) |
| Connections | institutional engine needs ≤5 | Pro pooler (Supavisor) handles far more; session-mode ~15 cap, transaction-mode scales high |
| Backups | daily (Pro) sufficient | data is re-ingestable from SEC; optional PITR add-on if desired |

- **Free tier is NOT recommended for production** (500 MB, auto-pauses after
  inactivity, limited connections). 157 MB fits, but pausing breaks a live app.
- **Estimated recurring cost: ~$25/month** (Pro, default compute), up to ~$35–40
  with a dedicated compute or PITR add-on. **These are estimates; confirm on
  Supabase's current pricing page before creating anything.** No cost is incurred
  by this report.

---

## 4. Schema migration & data transfer procedure (two options; execute only on approval)

Both avoid the earlier blocker (local `pg_dump` 16.15 vs server 17.x mismatch)
and need **no new software** — they use the venv's existing `psycopg`/SQLAlchemy.

**Option A — exact COPY from dev (recommended for launch; fastest, byte-faithful):**
1. Recreate the schema on the new prod project from the app's own model
   definitions (`models_institutional` + `create_all`) or extracted DDL, then
   create all indexes (list captured in §1).
2. Stream each table dev → new-prod via `psycopg` COPY (holdings, funds,
   securities, cusip_map, ingest). 546k rows is small (~seconds–minutes).
3. Re-create `institutional_activity` empty.
4. Verify with the §1 checksums (see §5).

**Option B — re-ingest from SEC source (cleaner provenance; re-runs mapping):**
1. Recreate schema on the new prod project.
2. Re-run the ingest pipeline (`institutional_service.ingest_dataset`) against the
   two SEC 13F datasets, then re-run OpenFIGI CUSIP→ticker mapping (respect rate
   limits; re-review MEDIUM).
3. Verify counts match the dev baseline (§1).

Recommendation: **Option A** for the bounded dev dataset at launch (exact +
checksum-verified); **Option B** if a fresh or full universe is chosen (§9).

---

## 5. Data-integrity checks (holdings, managers, securities, CUSIP mappings)

Run after transfer, new-prod vs dev-baseline — **all must match**:
- `COUNT(*)` per table: holdings 546,712 · funds 8,964 · securities 75 ·
  cusip_map 75 · ingest 4.
- Per-quarter: Q1 267,645 / 7,572 mgrs · Q2 279,067 / 7,703 mgrs; filing-date
  ranges identical.
- Checksums: `SUM(shares)=281,587,229,390`, `SUM(fund_id)=2,723,683,112`.
- CUSIP mapping: 70 HIGH / 5 MEDIUM; 75/75 with ticker; GOOG≠GOOGL (distinct
  CUSIP + shareClassFIGI); spot-check AAPL reconciliation (inc 2,548 / red 2,883
  → bearish).
- Index parity: every index in §1 present on the new project.
- Dashboard smoke test against the new DB (read-only): all 6 sections render.

---

## 6. No Earnings AI modification — confirmation

- Step 1 touches only a **new, separate** project and **reads** dev.
- The Earnings AI production DB, `database.py`, the 10 models, BUY/SELL logic,
  scoring formulas, thresholds, and historical predictions are **not read, not
  written, not modified**.
- The institutional engine is physically separate and refuses the prod ref.

---

## 7. Environment-variable configuration (to apply on the production host, by you)

- `INSTITUTIONAL_DATABASE_URL` = the new project's **pooler** connection string
  (`sslmode=require`), stored **server-side only** (host secret store) — never in
  a committed file or the frontend bundle.
- Add the new project's ref to the institutional engine **allow-list** (mirrors
  the existing dev/prod guard) so it accepts the new prod ref and still refuses
  `ddetojbocaxhcfxtcopc`. *(small, additive code change — separate approval.)*
- Optionally `OPENFIGI_API_KEY` for faster re-mapping (unauthenticated works).
- **Nothing is set now.** This report only specifies the variables.

## 8. Rollback procedure (Step 1)

- Step 1 is fully reversible and decoupled: until `INSTITUTIONAL_DATABASE_URL`
  points at the new project, institutional endpoints return `UNAVAILABLE`.
- To roll back: delete (or abandon) the new project and leave the env var unset.
- **Zero effect** on the Earnings AI DB, models, signals, or predictions — they
  were never touched.

## 9. Credentials & permissions needed (no secrets displayed or copied)

- **New Supabase project DB password** — created when the project is made; stored
  only in the host secret store. *Not shown here.*
- **Recommended least-privilege roles:** a read-only app role (`SELECT` on the
  institutional tables) for the dashboard; a separate ingest role
  (`INSERT/UPDATE`) used only by the quarterly pipeline. Avoid using the default
  `postgres` superuser for the app.
- **Pooler connection string** for `INSTITUTIONAL_DATABASE_URL` (contains the
  password) — configured server-side, never echoed.
- **OpenFIGI** — optional key, no secret required for the free rate.
- **No Earnings AI credentials** are needed or touched.

---

## 10. Implementation checklist with approval checkpoints

| # | Step | Gate |
|---|------|------|
| 1 | Approve universe + retention + migration option (A/B) (see questions) | **you** |
| 2 | **[CP-3 cost/resource]** Create the new Frankfurt Supabase project (Pro) | **you create it** |
| 3 | Recreate schema + indexes on the new project | on approval |
| 4 | Transfer data (Option A COPY or B re-ingest) | on approval |
| 5 | Run §5 integrity checks — all must pass | hard gate |
| 6 | Configure `INSTITUTIONAL_DATABASE_URL` + engine allow-list (server-side) | **you set secret** |
| 7 | Read-only dashboard smoke test against new DB | hard gate |
| 8 | Hand off to Step 2+ (Postgres validation, auth, deploy) | — |

No step proceeds without your explicit approval. Resource creation (CP-3) and the
secret configuration (step 6) are actions **you** take; I prepare the exact
scripts/commands and verify.

---

## Unresolved questions (need your decision before CP-3)

1. **Production universe:** migrate the bounded 75-security dev dataset as-is, or
   re-ingest a full/curated universe? (drives storage, cost, ingest effort)
2. **Quarter retention:** keep all quarters (growing ~70 MB/qtr) or a rolling
   2-quarter window (~160 MB steady)?
3. **Compute tier:** default Pro compute, or a dedicated compute add-on?
4. **Backups:** daily (Pro default) sufficient, or add PITR?
5. **Migration method:** Option A (exact COPY from dev) or Option B (re-ingest
   from SEC source)?
6. **Region/plan confirmation:** Frankfurt + Pro, and confirm current Supabase
   pricing (estimates in §3 are not live-verified).
7. **DB roles:** dedicated least-privilege app/ingest roles (recommended) vs
   default user?

---

*End of Step 1 preparation report. No resources created, no costs incurred.
STOP — awaiting approval before creating any resources.*
