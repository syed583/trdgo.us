# Audit-only deployment — final execution checklist (PREPARED, NOT EXECUTED)

Nothing here has been run on production. Executing any step needs your explicit final approval; the VPS steps are run by you (I have no VPS access) from paste blocks I supply at each step, each protected by a SHA-256.

## 0. What exactly is deployed (correction to the request)
**Do not deploy `a95e7f4`.** It is the first audit-only commit; it creates the audit table *without* row-level security, which on Supabase makes the table reachable through the public Data API roles, and it lacks the trade-attribution check, the working stats log line and the corrected checks. The final hotfix is:

| | |
|---|---|
| Branch | `hotfix/audit-only-36094e8` (local; pushed only after your approval) |
| Commit | **`db6a72ca12f77d523c3e17ef6e3e6dfa365e9e12`** |
| Base (production) | `36094e89ff78c531bd6c8997208d31f02f1aafee` |
| Diff | 11 files, +1,124/−3; nothing in `requirements.txt`, `frontend`, `deploy` ⇒ **no pip install, no npm build, no frontend change** |
| Not touched | six strategy modules, weights, formulas, 55/45, entries/stops/targets/exits, `INTRADAY_WORKFLOW` (legacy), V2, shadow, gates |
Tests: hotfix build 656 passed, 12 skipped, 3 failed — the same 3 environment-only failures as the untouched `36094e8` (fresh checkout lacks the git-ignored `.env.dev`).

## 1. The three prerequisites you named — status today
| Prerequisite | Status |
|---|---|
| Fresh backup completed **and verified** | **NOT DONE / NOT VERIFIED.** No backup has been taken; I cannot take one. Step P5 below does it and `prod_gate_checks.py backup` (exit 0 required) verifies it |
| Migration connection mode suitable for DDL | **NOT VERIFIED.** The 12/12 run you cite used the earlier checker, whose session-mode test was wrong: it reported port **6543** (the Supabase *transaction* pooler) as session-like and advised `--port 6543`. The corrected checker (v3) must be run; it must exit 0 **and** advise a port other than 6543 (expected 5432). Never run the DDL through 6543 |
| Rollback ready | **PASS.** Rollback SQL rehearsed on PostgreSQL 17.5 (apply → rollback → re-apply, every `demo_trades` row byte-identical, Supabase-style roles emulated), code rollback = fast-forward undone with `git reset`, config rollback = delete one line |

### 1a. Actual VPS evidence, 2026-10-28 (supersedes the "NOT DONE / NOT VERIFIED" rows above)
| Gate | Result |
|---|---|
| Readiness on the VPS | 12/12; port **5432 is session mode** (use `--port 5432` for DDL; 6543 never); PostgreSQL 17.11 |
| Fresh backup | Taken and validated, SHA-256 sidecar matches (re-take <= 30 min before step B) |
| Scratch restore V4 (PostgreSQL 17.11 tools, isolated cluster 127.0.0.1:55443, user-reported) | **16/16 PASS**: `pg_restore` exit 0 with no errors, 55/55 tables, 1,010/1,010 `demo_trades` rows byte-identical to the dump, migration A, postcheck 10/10 (RLS, complete ACL, Data API roles), rollback, re-apply, idempotence; scratch cluster stopped and deleted; production fingerprint before/after unchanged |
| Traps found and fixed in the test (not in the product) | Docker holds ports 54320/54330 (bind refused); `COPY` prints timestamptz in the session time zone (compare in UTC); `pg_restore` stdout is CRLF/ANSI on Windows (compare bytes) |

## 2. Window and expected downtime
* Run it in the no-entry window **20:00 → 04:00 ET** (recorded in the runbook as 01:00 → 09:00 VPS time until 1 Nov): the paper tracker opens nothing then, the Strategy Trades page is idle.
* **Two service restarts** (code with audit OFF, then audit ON). Each: the API/frontend is unavailable **≈ 15–45 s** (my estimate — not measured on this VPS; Caddy answers 502 meanwhile) and the tracker misses at most one 60 s cycle. Total user-visible unavailability ≈ 1–2 minutes. Nothing is lost: positions are in the database and a tick is transactional (a restart mid-tick discards only that uncommitted tick).
* The migration itself needs **no restart and no lock on `demo_trades`** (it creates a new table); it takes seconds.
* Optionally do both restarts as one (code + env line together): saves ≈ 30 s, but loses the "code first, audit off" check. Recommended: two restarts.

## 3. Sequence (stop at the first failed check; every "must" is a hard gate)
Variables: `$R="C:\apps\us-stock-reader"; $B="36094e89ff78c531bd6c8997208d31f02f1aafee"; $H="db6a72ca12f77d523c3e17ef6e3e6dfa365e9e12"; $py="$R\backend\.venv\Scripts\python.exe"`

### P — pre-flight (before the window; read-only except the dump file)
* **P1 (laptop, needs your approval):** `git push origin hotfix/audit-only-36094e8`; then `git ls-remote origin hotfix/audit-only-36094e8` must print `$H`.
* **P2 (VPS):** `git -C $R status --porcelain` → prints nothing; `git -C $R rev-parse HEAD` → must equal `$B`.
* **P3 (VPS):** `git -C $R fetch --no-tags origin hotfix/audit-only-36094e8` (fetch changes no file); `git -C $R rev-parse FETCH_HEAD` → must equal `$H`; `git -C $R diff --name-only $B FETCH_HEAD` → exactly the 11 files; `git -C $R diff --stat $B FETCH_HEAD -- backend/requirements.txt frontend deploy` → empty.
* **P4:** the corrected `prod_gate_checks.py readiness` block → **exit code 0**, `MANDATORY … 0 FAIL … GO-eligible`, line `use --port 5432` (record it) and the `demo_trades` row count `N0`.
* **P5 backup:** verify `C:\audit-reports\take_predeploy_dump_277f30f.ps1` SHA-256 `465B19D1607A9B35E582AF4F52332EBDD6BD84F6DC81A6FBE2E674CED4D4E7EE`, run it → `DUMP VALIDATED`; the runbook's `GATE PASS` snippet → `GATE PASS` (dump ≥ 1 MB, SHA-256 matches its `.sha256`, ≤ 30 min old); then `& $py C:\audit-reports\phaseC\prod_gate_checks.py backup` → **exit 0** (rows readable from the dump, ids ⊆ live, row-count reconciliation). Record file name, size, SHA-256, time. Strongly recommended: restore it into a scratch PostgreSQL 17 and compare row counts.
* **P6:** you confirm the window; I confirm I will wait for your "go" at each of B, C, D.

### B — migration A (production database change; needs "go B")
```powershell
New-Item -ItemType Directory -Force C:\audit-reports\apply\migrations | Out-Null
cmd /c "git -C C:\apps\us-stock-reader show db6a72ca12f77d523c3e17ef6e3e6dfa365e9e12:backend/migrations/2026_10_27_A_audit_only.sql > C:\audit-reports\apply\migrations\2026_10_27_A_audit_only.sql"
cmd /c "git -C C:\apps\us-stock-reader show db6a72ca12f77d523c3e17ef6e3e6dfa365e9e12:backend/migrations/2026_10_27_A_audit_only_ROLLBACK.sql > C:\audit-reports\apply\migrations\2026_10_27_A_audit_only_ROLLBACK.sql"
# apply_sql.py is installed from a SHA-256-checked paste block (cd9ff7c6729ce6ce29f9bfb1b540ba33a6db845c72ec787590ae0fdf569356e1)
& $py C:\audit-reports\phaseC\apply_sql.py --root $R --sql-dir C:\audit-reports\apply\migrations --file 2026_10_27_A_audit_only.sql --expect-sha256 336960aa9c938196fdd4826120c7edbfd6462693a23d7ca2cdd421a3947eeddd --port 5432            # DRY RUN
& $py C:\audit-reports\phaseC\apply_sql.py ... same ... --apply                                                                                                                                         # one transaction, lock_timeout 3 s
& $py C:\audit-reports\phaseC\prod_gate_checks.py postcheck --expect-demo-rows <N0>                                                                                                                      # must exit 0
```
`postcheck` must show: exact 22 columns, index present, **row-level security on**, **no privilege for `anon`/`authenticated`**, `demo_trades` columns unchanged and rows ≥ N0. The SQL creates only `intraday_signal_audit` + one index and secures them; it changes no role, no credential, no privilege on any existing object.

### C — code, audit still OFF (needs "go C")
```powershell
git -C $R merge --ff-only FETCH_HEAD                   # "Fast-forward"; no merge commit
git -C $R rev-parse HEAD                               # must equal $H
& $py -m py_compile $R\backend\signal_verification.py $R\backend\demo_trade_service.py $R\backend\models_demo_trades.py $R\backend\tab_settings.py $R\backend\database.py
Restart-Service USStockReader                          # restart #1  (≈15–45 s)
for ($i=0; $i -lt 24; $i++) { $c = curl.exe -s -o nul -w "%{http_code}" http://127.0.0.1:8000/; if ($c -eq "401") { break }; Start-Sleep 5 }   # 401 = up, asking for the password
"HTTP $c"; Get-Service USStockReader | Select Status; Get-Content $R\logs\service.err.log -Tail 40 | Select-String "Traceback|Error"
```
Gate: HTTP 401, service Running, no Traceback. Smoke (2 minutes): log in, Strategy Trades page loads and shows the same trades and entry prices as before the restart; `demo_trades` count unchanged (`run_readonly_history.ps1` or the readiness count).

### D — activation (needs "go D")
```powershell
$envf = "$R\backend\.env"; $t = [IO.File]::ReadAllText($envf)
if (-not $t.EndsWith("`n")) { [IO.File]::AppendAllText($envf, "`r`n") }
[IO.File]::AppendAllText($envf, "INTRADAY_VERIFICATION_MODE=audit`r`n")
Select-String -Path $envf -Pattern '^INTRADAY_VERIFICATION_MODE=' | ForEach-Object { $_.Line }      # exactly ONE line, "…=audit"
Restart-Service USStockReader                           # restart #2
# same 401 wait loop and log check as in C
```
Only this one line is added. (`INTRADAY_WORKFLOW` has no environment switch and stays `legacy` in code; no other variable is touched. The duplicated `INTRADAY_MODEL` lines are left alone.)

### E — verification
* **Right after restart #2:** 401 on `/`, service Running, no Traceback; `postcheck` exit 0.
* **Expect no `SIGNAL_AUDIT_STATS` line overnight:** the tracker does not tick between 20:00 and 04:00 ET when nothing is open (this is not a failure).
* **First entry window, 04:00–04:15 ET:** (a) Strategy Trades shows new BUY/SELL trades for the usual models (compare per-model counts with the previous sessions in the history JSON); (b) `demo_trades` grows; (c) `SIGNAL_AUDIT_STATS` appears in `service.err.log` (`write_errors 0`, `verify_errors 0`, `cycles` increasing); (d) `cd $R\backend; & $py signal_verification_cli.py report <today>` shows audited signals; (e) trades the tracker opened carry `attribution_ok = true`.
* **After the close (16:00 ET):** the first six-model report: signals, verified scores, mismatches, missing evidence, staleness **NOT ASSESSED** (this build has no source timestamps), audit errors, attribution mismatches, Strategy Trades consistency — kept apart from data quality and profitability.

### F — rollback (any step; no trade data is touched)
| Trigger | Action |
|---|---|
| Service not Running 2 min after a restart; HTTP not 401; Traceback naming `signal_verification`, `_audit_signals` or `demo_trade_service` repeating | go to F2/F3 |
| Strategy Trades stops adding entries while the boards show qualifying signals; a trade changed/duplicated that the old logic would not | F2 + F3 immediately |
| `write_errors`/`verify_errors` growing with **no** trading symptom | F1 only (audit off); trading is unaffected |
* **F1 audit off:** remove the line (write without BOM): `$t=[IO.File]::ReadAllText($envf); [IO.File]::WriteAllText($envf, (($t -split "\r?\n") | Where-Object { $_ -notmatch '^INTRADAY_VERIFICATION_MODE=' }) -join "`r`n", (New-Object Text.UTF8Encoding $false))`; `Restart-Service USStockReader`.
* **F2 code:** `git -C $R status --porcelain` empty; `git -C $R merge-base --is-ancestor $B HEAD` ok; `git -C $R reset --hard $B`; restart; 401 check.
* **F3 table (optional — skip to keep the evidence):** `apply_sql.py … --file 2026_10_27_A_audit_only_ROLLBACK.sql --expect-sha256 c5c862a564982ca2fd884a77906a0d885abdd2d38e4fef421450f46b4f2e7a5a --port 5432 --apply` (drops only the audit table).
* **F4 last resort:** restore the P5 dump — not expected, since no existing table is modified. Never roll back to `683fce1` / `main`.

## 4. Known risks
* Backup, session-port check and restore proof are still open (section 1). The role advisory is accepted by you (no role/credential change).
* The first audit pass runs on the heaviest tick (≈170 simultaneous entries at 04:00 ET); the audit is bounded by a 5 s budget and runs after the trades are committed, so the worst case is fewer audited signals in that tick, never a missed trade.
* The restart interrupts the website ≈ 1–2 minutes in total; staleness cannot be assessed on this build; the audit says nothing about profitability.

## 5. Approval requested
Your explicit "go" separately for: P1 (push), B (migration), C (code + restart #1), D (activation + restart #2) — and your confirmation that you accept `db6a72ca…` instead of `a95e7f4`.
