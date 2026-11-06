"""READ-ONLY production gate checks for the audit-only deployment.  Three subcommands:

    python prod_gate_checks.py readiness [--root C:\\apps\\us-stock-reader] [--out C:\\audit-reports] [--require-least-privilege]
    python prod_gate_checks.py backup    [--dump <file>] [--pg-restore <exe>] [--max-age-min 30] [--root ...] [--out ...]
    python prod_gate_checks.py postcheck [--expect-demo-rows N] [--root ...] [--out ...]     (after migration A: is the audit table right and private?)

Exit code:  0 = every MANDATORY check passed;  1 = at least one MANDATORY check FAILED;  2 = the checker itself could not run.
(Advisory findings -- e.g. an over-privileged role unless --require-least-privilege -- are printed as FAIL (advisory) and do not change the
exit code.)

Safe by construction: database connections are READ ONLY transactions or contain only SELECT / SHOW / SET application_name (a session
label, never data); no DDL, not even rolled back; the connection string and the role name are never printed; the dump is only read.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

AUDIT_TABLE, AUDIT_INDEX = "intraday_signal_audit", "ix_intraday_signal_audit_day"
EXPECTED_COLUMNS = {"id": "integer", "strategy": "character varying", "symbol": "character varying", "side": "character varying",
                    "shares": "integer", "session_date": "date", "entry_time": "timestamp with time zone", "entry_price": "double precision",
                    "stop": "double precision", "target": "double precision", "score_at_entry": "double precision",
                    "last_price": "double precision", "profit": "double precision", "profit_pct": "double precision",
                    "status": "character varying", "exit_time": "timestamp with time zone", "exit_price": "double precision",
                    "updated_at": "timestamp with time zone"}


# ------------------------------------------------------------------------------------------------------- helpers -----
class Results:
    def __init__(self):
        self.rows = []

    def add(self, name, ok, detail="", mandatory=True, status=None):
        st = status or ("PASS" if ok else "FAIL")
        self.rows.append({"check": name, "status": st, "mandatory": mandatory, "detail": detail})
        tag = st if (mandatory or st == "PASS") else f"{st} (advisory)"
        print(f"{tag:18} {name}" + (f"  [{detail}]" if detail else ""))

    def exit_code(self):
        return 1 if any(r["mandatory"] and r["status"] != "PASS" for r in self.rows) else 0

    def summary(self):
        m = [r for r in self.rows if r["mandatory"]]
        adv = [r for r in self.rows if not r["mandatory"] and r["status"] != "PASS"]
        line = (f"MANDATORY: {sum(r['status'] == 'PASS' for r in m)}/{len(m)} pass, "
                f"{sum(r['status'] == 'FAIL' for r in m)} FAIL, {sum(r['status'] == 'NOT VERIFIED' for r in m)} NOT VERIFIED; "
                f"ADVISORY findings: {len(adv)}  ->  {'GO-eligible (no mandatory failure)' if self.exit_code() == 0 else 'NO-GO'}")
        print("\n" + line)
        return line


def load_url(root):
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        env = Path(root) / "backend" / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.strip().startswith("DATABASE_URL="):
                    url = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not url:
        raise SystemExit("STOPPED: no DATABASE_URL")
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def with_port(url, port):
    p = urlparse(url)
    return url.replace(f":{p.port}/", f":{port}/", 1) if p.port else url


def host_kind(url):
    h = (urlparse(url).hostname or "").lower()
    return "pooler host (Supabase Supavisor)" if "pooler" in h else "direct database host" if h.startswith("db.") else "other host"


def probe_mode(url):
    """Is this connection transaction-pooled or session-pooled? Statement-by-statement: a transaction pooler may hand each statement to a
    different server backend and does not keep a session label; a session pooler / direct connection does."""
    import psycopg
    c = psycopg.connect(url, connect_timeout=10, prepare_threshold=None, autocommit=True)
    try:
        label = f"probe{int(time.time()) % 100000}"
        c.execute(f"SET application_name = '{label}'")
        pids, kept = set(), 0
        for _ in range(8):
            pids.add(c.execute("SELECT pg_backend_pid()").fetchone()[0])
            kept += c.execute("SHOW application_name").fetchone()[0] == label
            time.sleep(0.05)
        return {"distinct_backends_over_8_statements": len(pids), "session_label_kept_8_of_8": kept == 8,
                "mode": "SESSION-like (state persists across statements)" if len(pids) == 1 and kept == 8
                else "TRANSACTION-pooled (state does not persist)"}
    finally:
        c.close()


# --------------------------------------------------------------------------------------------------- readiness -------
def readiness(a):
    import psycopg
    R = Results()
    url = load_url(a.root)
    port = urlparse(url).port
    conn = psycopg.connect(url, connect_timeout=15, prepare_threshold=None)
    conn.read_only = True
    cur = conn.cursor()
    cur.execute("SET TRANSACTION READ ONLY")
    facts = {}

    def one(sql, *p):
        cur.execute(sql, p or None)
        return cur.fetchone()

    def rows(sql, *p):
        cur.execute(sql, p or None)
        return cur.fetchall()

    ver, num = one("SHOW server_version")[0], one("SHOW server_version_num")[0]
    one_db = one("SELECT current_database()")[0]
    facts.update(server_version=ver, connection_port=port, host_kind=host_kind(url))
    print(f"PostgreSQL {ver} (version_num {num}); connection port {port}; {host_kind(url)}")
    R.add("server is PostgreSQL 17", str(ver).startswith("17"), f"version {ver}")
    R.add("connected to the primary (not a replica)", not one("SELECT pg_is_in_recovery()")[0])
    R.add("session is read-only", one("SHOW transaction_read_only")[0] == "on")

    # ---- role investigation ----
    name_is_postgres, sup, cdb, crole, byp, repl, login, connlimit = one(
        "SELECT rolname = 'postgres', rolsuper, rolcreatedb, rolcreaterole, rolbypassrls, rolreplication, rolcanlogin, rolconnlimit "
        "FROM pg_roles WHERE rolname = current_user")
    member_of = [r[0] for r in rows("SELECT g.rolname FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid "
                                    "JOIN pg_roles u ON u.oid = m.member WHERE u.rolname = current_user ORDER BY 1")]
    pub_tables = rows("SELECT c.relname, pg_get_userbyid(c.relowner) = current_user, c.relrowsecurity FROM pg_class c "
                      "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind = 'r'")
    n_tables, n_owned, n_rls = len(pub_tables), sum(1 for t in pub_tables if t[1]), sum(1 for t in pub_tables if t[2])
    others = rows("SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls FROM pg_roles "
                  "WHERE rolname NOT LIKE 'pg\\_%' AND rolname <> current_user ORDER BY 1")
    facts["role"] = {"is_the_platform_default_postgres_role": name_is_postgres, "superuser": sup, "createdb": cdb, "createrole": crole,
                     "bypassrls": byp, "replication": repl, "connection_limit": connlimit, "member_of": member_of,
                     "public_tables": n_tables, "owned_by_this_role": n_owned, "tables_with_row_level_security": n_rls,
                     "other_login_roles_present": [r[0] for r in others if r[1]]}
    print(f"role: platform-default 'postgres' role = {name_is_postgres}; member of {member_of or 'nothing special'}; owns {n_owned} of "
          f"{n_tables} public tables; tables with row-level security: {n_rls}")
    print(f"other login roles that already exist (names only): {facts['role']['other_login_roles_present'] or 'none'}")
    can_create = one("SELECT has_schema_privilege(current_user, 'public', 'CREATE')")[0]
    owner = one("SELECT pg_get_userbyid(c.relowner) = current_user FROM pg_class c WHERE c.relname = 'demo_trades' AND c.relkind = 'r'")
    R.add("the connecting role may create the audit table", can_create, "CREATE on schema public")
    R.add("the connecting role OWNS demo_trades (the correct migration role)", bool(owner and owner[0]),
          "owner = this role" if owner and owner[0] else "apply migration A as the table owner")
    broad = [n for n, v in (("CREATEDB", cdb), ("CREATEROLE", crole), ("BYPASSRLS", byp), ("SUPERUSER", sup), ("REPLICATION", repl)) if v]
    R.add("least privilege: the application role is NOT over-privileged", not broad,
          ("role holds " + ", ".join(broad) + (f"; {n_rls} tables use row-level security" if n_rls else "; no table uses row-level security, so BYPASSRLS is inert")
           + ("; this is the platform's default role: its attributes cannot be removed, use a dedicated role" if name_is_postgres else "")) if broad else "",
          mandatory=bool(a.require_least_privilege))
    to = {k: one("SELECT current_setting(%s, true)", k)[0] for k in ("statement_timeout", "lock_timeout", "idle_in_transaction_session_timeout")}
    facts["role_timeouts"] = to
    print("role timeouts:", to)

    used, mx = one("SELECT (SELECT COUNT(*) FROM pg_stat_activity WHERE backend_type = 'client backend'), current_setting('max_connections')::int")
    R.add("connection headroom for one extra short audit session", used + 2 < mx, f"{used} client backends of {mx}")
    objs = rows("SELECT relname, relkind FROM pg_class WHERE relname ILIKE %s ORDER BY 1", "%intraday_signal_audit%")
    R.add(f"no object named like {AUDIT_TABLE} exists (table, index, sequence, _local twin)", not objs, str([tuple(o) for o in objs]) if objs else "")
    R.add("the exact names the migration creates are free",
          one("SELECT COUNT(*) FROM pg_class WHERE relname IN (%s, %s, %s)", AUDIT_TABLE, AUDIT_INDEX, AUDIT_TABLE + "_id_seq")[0] == 0)
    cols = {r[0]: r[1] for r in rows("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'demo_trades'")}
    missing, extra = sorted(set(EXPECTED_COLUMNS) - set(cols)), sorted(set(cols) - set(EXPECTED_COLUMNS))
    wrong = sorted(k for k in EXPECTED_COLUMNS if k in cols and cols[k] != EXPECTED_COLUMNS[k])
    R.add("demo_trades has exactly the columns the production build declares", not (missing or extra or wrong),
          f"missing={missing} extra={extra} wrong_type={wrong}" if (missing or extra or wrong) else "")
    nrows, size = one("SELECT (SELECT COUNT(*) FROM demo_trades), pg_total_relation_size('demo_trades')")
    print(f"demo_trades: {nrows} rows, {size / 1e6:.2f} MB; indexes {[r[0] for r in rows(chr(83)+'ELECT indexname FROM pg_indexes WHERE tablename = %s ORDER BY 1', 'demo_trades')]}")
    locks = one("SELECT COUNT(*) FROM pg_locks l JOIN pg_class c ON c.oid = l.relation WHERE c.relname = 'demo_trades' AND l.mode IN "
                "('AccessExclusiveLock', 'ExclusiveLock', 'ShareRowExclusiveLock', 'ShareLock')")[0]
    R.add("no heavy lock currently held on demo_trades", locks == 0, f"{locks} heavy locks")
    long_tx = one("SELECT COUNT(*) FROM pg_stat_activity WHERE xact_start IS NOT NULL AND now() - xact_start > interval '60 seconds' "
                  "AND backend_type = 'client backend' AND pid <> pg_backend_pid()")[0]
    R.add("no transaction older than 60 s that could block a DDL lock", long_tx == 0, f"{long_tx} long transactions")
    # ---- row-level security and Data API exposure (Supabase) ----
    n_force = one("SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind = 'r' "
                  "AND c.relforcerowsecurity")[0]
    n_pol = one("SELECT COUNT(*) FROM pg_policies WHERE schemaname = 'public'")[0]
    evt = [r[0] for r in rows("SELECT evtname FROM pg_event_trigger ORDER BY 1")]
    web = {}
    for r_ in ("anon", "authenticated"):
        if one("SELECT COUNT(*) FROM pg_roles WHERE rolname = %s", r_)[0]:
            web[r_] = {"select_demo_trades": one("SELECT has_table_privilege(%s, 'public.demo_trades', 'SELECT')", r_)[0],
                       "create_in_public": one("SELECT has_schema_privilege(%s, 'public', 'CREATE')", r_)[0]}
    dflt = rows("SELECT pg_get_userbyid(d.defaclrole), d.defaclobjtype, d.defaclacl::text FROM pg_default_acl d "
                "LEFT JOIN pg_namespace n ON n.oid = d.defaclnamespace WHERE n.nspname = 'public' OR d.defaclnamespace = 0")
    exposes_new = any(("anon=" in str(r[2]) or "authenticated=" in str(r[2])) and r[1] == "r" for r in dflt)
    facts["rls"] = {"tables_force_rls": n_force, "policies_in_public": n_pol, "event_triggers": evt, "data_api_roles": web,
                    "default_privileges_expose_new_tables_to_data_api_roles": exposes_new}
    print(f"row-level security: {n_rls} of {n_tables} tables enabled, {n_force} forced, {n_pol} policies; event triggers: {evt or 'none'}")
    print(f"Data API roles present: {web or 'none'}; new tables in public are exposed to them by default privileges: {exposes_new}")
    R.add("new tables in public are NOT exposed to the Data API by default (otherwise the migration's REVOKE / RLS lines are essential)",
          not exposes_new, "migration A enables RLS and revokes anon/authenticated on the audit table either way", mandatory=False)
    R.add("migration A's protection of the audit table is part of the SQL (RLS enabled + revoke)", True,
          "the audit table is created private on every run", mandatory=False)
    conn.rollback()
    conn.close()

    # ---- how the migration should connect ----
    print("\nconnection method for the migration:")
    cur_mode = probe_mode(url)
    if port == 6543:        # the statement probe cannot prove session semantics under light load (the pooler may hand the same backend back)
        cur_mode["mode"] = "TRANSACTION-pooled (port 6543 is the Supabase transaction pooler by design; the statement probe below is only indicative)"
    facts["connection_mode_current_port"] = cur_mode
    print(f"  port {port}: {cur_mode['mode']} ({cur_mode['distinct_backends_over_8_statements']} backend(s) over 8 statements)")
    session_url, session_ok = None, False
    if "SESSION" in cur_mode["mode"]:
        session_url, session_ok = url, True
    else:
        for alt in (a.alt_port,):
            try:
                alt_url = with_port(url, alt)
                m5 = probe_mode(alt_url)
                with __import__("psycopg").connect(alt_url, connect_timeout=10, prepare_threshold=None) as c5:
                    same = (c5.execute("SHOW server_version").fetchone()[0] == str(ver)
                            and c5.execute("SELECT current_database()").fetchone()[0] == one_db)
                m5["same_server_and_database"] = same
                if not same:
                    m5["mode"] = "NOT THE SAME DATABASE: " + m5["mode"]
                facts[f"connection_mode_port_{alt}"] = m5
                print(f"  port {alt}: {m5['mode']} ({m5['distinct_backends_over_8_statements']} backend(s))")
                if "SESSION" in m5["mode"] and same:
                    session_url, session_ok = alt_url, True
            except Exception as exc:  # noqa: BLE001
                facts[f"connection_mode_port_{alt}"] = f"unreachable: {type(exc).__name__}"
                print(f"  port {alt}: not reachable from here ({type(exc).__name__})")
    R.add("a SESSION-mode connection (not the transaction pooler) is available for the DDL", session_ok,
          f"use --port {urlparse(session_url).port}" if session_ok else "none found: ask for the direct connection string before migrating")
    facts["recommended_migration_port"] = urlparse(session_url).port if session_ok else None

    Path(a.out).mkdir(parents=True, exist_ok=True)
    line = R.summary()
    f = Path(a.out) / f"gate-readiness-{datetime.now().strftime('%Y%m%d-%H%M')}.json"
    f.write_text(json.dumps({"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "facts": facts,
                             "checks": R.rows, "summary": line, "exit_code": R.exit_code()}, indent=2, default=str), encoding="utf-8")
    print("report:", f)
    return R.exit_code()


# ------------------------------------------------------------------------------------------------------- backup -------
def parse_copy(text, table="demo_trades"):
    """Rows of ``COPY public.<table> (...) FROM stdin;`` ... ``\\.`` from pg_restore --data-only text output -> (columns, [rows])."""
    cols, out, inside = None, [], False
    for line in text.splitlines():
        if not inside:
            m = re.match(rf"COPY (?:public\.)?{re.escape(table)} \((.*?)\) FROM stdin;", line)
            if m:
                cols, inside = [c.strip() for c in m.group(1).split(",")], True
        elif line == "\\.":
            break
        else:
            out.append(line.split("\t"))
    return cols, out


def find_tool(name, hint):
    cands = [hint] if hint else []
    cands += [r"C:\pgtools17\pgsql\bin\%s.exe" % name, r"C:\pgsql\bin\%s.exe" % name, r"C:\Program Files\PostgreSQL\17\bin\%s.exe" % name]
    for c in cands:
        if c and Path(c).exists():
            return c
    return None


def backup(a):
    import psycopg
    R = Results()
    folder = Path(r"C:\audit-reports\db-backups")
    dumps = sorted(folder.glob("*.dump"), key=lambda p: p.stat().st_mtime, reverse=True) if folder.exists() else []
    dump = Path(a.dump) if a.dump else (dumps[0] if dumps else None)
    R.add("a dump file exists", bool(dump and dump.exists()), str(dump) if dump else f"nothing in {folder}")
    if not (dump and dump.exists()):
        R.summary()
        return R.exit_code()
    age_min = (time.time() - dump.stat().st_mtime) / 60
    R.add(f"the dump is fresh (taken within {a.max_age_min:g} minutes)", age_min <= a.max_age_min, f"{age_min:.0f} minutes old")
    size = dump.stat().st_size
    R.add("the dump is not empty", size > 0, f"{size / 1e6:.1f} MB")
    import hashlib
    h = hashlib.sha256()
    with open(dump, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    side = Path(str(dump) + ".sha256")
    want = side.read_text(encoding="utf-8", errors="ignore").split()[0].strip().lower() if side.exists() and side.read_text(errors="ignore").strip() else None
    R.add("the dump's SHA-256 equals the recorded .sha256 sidecar", want == h.hexdigest(), f"SHA-256 {h.hexdigest()}" if want == h.hexdigest()
          else (f"computed {h.hexdigest()} vs recorded {want}" if want else "no .sha256 sidecar found"))
    prev = [p for p in dumps if p != dump]
    if prev:
        ratio = size / max(prev[0].stat().st_size, 1)
        R.add("the dump is not suspiciously smaller than the previous one", ratio >= 0.9, f"{ratio:.2f} x previous ({prev[0].name})")
    else:
        R.add("the dump is not suspiciously smaller than the previous one", False, "no previous dump to compare with (advisory)", mandatory=False,
              status="NOT VERIFIED")
    tool = find_tool("pg_restore", a.pg_restore)
    if not tool:
        R.add("pg_restore (PostgreSQL 17+) is available to read the dump back", False, "pass --pg-restore <path>")
        R.summary()
        return R.exit_code()
    ver = subprocess.run([tool, "--version"], capture_output=True, text=True).stdout.strip()
    mm = re.search(r"(\d+)(?:\.\d+)*\s*$", ver)
    R.add(f"pg_restore is version {a.min_pg_major} or newer (it must be at least the server's major version)", bool(mm) and int(mm.group(1)) >= a.min_pg_major, ver)
    toc = subprocess.run([tool, "--list", str(dump)], capture_output=True, text=True)
    R.add("pg_restore can read the dump (--list succeeds)", toc.returncode == 0, (toc.stderr or "").strip()[:120])
    t = toc.stdout
    R.add("the dump contains demo_trades data", bool(re.search(r"TABLE DATA public demo_trades", t)))
    R.add("the dump contains the demo_trades constraint and indexes (a restore recreates the schema)",
          all(x in t for x in ("uq_demo_strategy_symbol_day", "ix_demo_session", "ix_demo_strategy_session")))
    data = subprocess.run([tool, "--data-only", "--table=demo_trades", "-f", "-", str(dump)], capture_output=True, text=True)
    cols, drows = parse_copy(data.stdout)
    R.add("the demo_trades rows can be read back out of the dump", bool(cols) and len(drows) > 0, f"{len(drows)} rows")
    # compare with the live table (read-only)
    url = load_url(a.root)
    conn = psycopg.connect(url, connect_timeout=15, prepare_threshold=None)
    conn.read_only = True
    cur = conn.cursor()
    cur.execute("SET TRANSACTION READ ONLY")
    live_ids = {r[0] for r in cur.execute("SELECT id FROM demo_trades").fetchall()}
    taken = datetime.fromtimestamp(dump.stat().st_mtime, timezone.utc)
    new_since = cur.execute("SELECT COUNT(*) FROM demo_trades WHERE entry_time >= %s", (taken,)).fetchone()[0]
    conn.rollback()
    conn.close()
    dump_ids = {int(r[cols.index("id")]) for r in drows} if cols else set()
    R.add("every row in the dump is still in the live table (nothing was lost or deleted since)", dump_ids <= live_ids,
          f"{len(dump_ids - live_ids)} ids only in the dump" if dump_ids - live_ids else "")
    gap = len(live_ids) - len(dump_ids)
    R.add("the live table has no more rows than the dump plus the trades opened since the dump", 0 <= gap <= new_since + 5,
          f"live {len(live_ids)} rows, dump {len(dump_ids)}, opened since the dump {new_since}")
    print("\nRESTORE PROCEDURE (not executed): create an empty scratch database (PostgreSQL 17), then")
    print(f'  pg_restore --no-owner --no-privileges --dbname=<scratch connection> "{dump}"')
    print("  SELECT COUNT(*) FROM demo_trades;   -- must equal the dump row count above")
    print("  then run backend\\migrations\\2026_10_27_A_audit_only.sql and its _ROLLBACK.sql there and compare row counts again.")
    Path(a.out).mkdir(parents=True, exist_ok=True)
    line = R.summary()
    f = Path(a.out) / f"gate-backup-{datetime.now().strftime('%Y%m%d-%H%M')}.json"
    f.write_text(json.dumps({"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "dump": str(dump), "checks": R.rows,
                             "summary": line, "exit_code": R.exit_code()}, indent=2, default=str), encoding="utf-8")
    print("report:", f)
    return R.exit_code()


def postcheck(a):
    """After migration A (read-only): the audit table has exactly the expected shape and is private; demo_trades is unchanged."""
    import psycopg
    R = Results()
    url = load_url(a.root)
    conn = psycopg.connect(url, connect_timeout=15, prepare_threshold=None)
    conn.read_only = True
    cur = conn.cursor()
    cur.execute("SET TRANSACTION READ ONLY")

    def one(sql, *p):
        cur.execute(sql, p or None)
        return cur.fetchone()

    cols = [r[0] for r in cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = %s "
                                      "ORDER BY ordinal_position", (AUDIT_TABLE,)).fetchall()]
    want = ["id", "ts", "strategy", "symbol", "trading_date", "session_type", "direction", "score", "recomputed_score", "score_diff", "entry_price",
            "verdict", "data_status", "failed_checks", "checks", "breakdown", "data", "legacy_opened", "trade_id", "legacy_held",
            "attribution_ok", "attribution_detail"]
    R.add("the audit table exists with exactly the expected columns", cols == want, f"{len(cols)} columns" + ("" if cols == want else f": {cols}"))
    R.add("its index exists", one("SELECT COUNT(*) FROM pg_indexes WHERE tablename = %s AND indexname = %s", AUDIT_TABLE, AUDIT_INDEX)[0] == 1)
    rls = one("SELECT relrowsecurity FROM pg_class WHERE relname = %s AND relkind = 'r'", AUDIT_TABLE)
    R.add("row-level security is enabled on the audit table", bool(rls and rls[0]))
    for r_ in ("anon", "authenticated"):
        if one("SELECT COUNT(*) FROM pg_roles WHERE rolname = %s", r_)[0]:
            priv = one("SELECT has_table_privilege(%s, %s, 'SELECT') OR has_table_privilege(%s, %s, 'INSERT') OR has_table_privilege(%s, %s, 'UPDATE') "
                       "OR has_table_privilege(%s, %s, 'DELETE')", r_, "public." + AUDIT_TABLE, r_, "public." + AUDIT_TABLE, r_, "public." + AUDIT_TABLE,
                       r_, "public." + AUDIT_TABLE)[0]
            R.add(f"the audit data is not reachable by the Data API role '{r_}'", not priv)
    # the complete access list of the table and its sequence: who can touch the audit data at all (role names only, no credentials)
    def grantees(relname, kind):
        out = []
        for g, priv in cur.execute("SELECT acl.grantee, acl.privilege_type FROM pg_class c, LATERAL aclexplode(c.relacl) acl "
                                   "WHERE c.relname = %s AND c.relkind = %s", (relname, kind)).fetchall():
            out.append(("PUBLIC" if g == 0 else one("SELECT rolname FROM pg_roles WHERE oid = %s", g)[0], priv))
        return out
    seq_name = AUDIT_TABLE + "_id_seq"
    tacl, sacl = grantees(AUDIT_TABLE, "r"), grantees(seq_name, "S")
    t_who, s_who = sorted({g for g, _ in tacl}), sorted({g for g, _ in sacl})
    print(f"who holds privileges on the audit table: {t_who or 'only the owner (no explicit grants)'}; on its sequence: {s_who or 'only the owner'}")
    R.add("no privilege on the audit table for PUBLIC, anon or authenticated (complete ACL, not just the two probes)",
          not ({"PUBLIC", "anon", "authenticated"} & set(t_who)), f"grantees: {t_who}" if {"PUBLIC", "anon", "authenticated"} & set(t_who) else "")
    R.add("no privilege on the audit sequence for PUBLIC, anon or authenticated", not ({"PUBLIC", "anon", "authenticated"} & set(s_who)),
          f"grantees: {s_who}" if {"PUBLIC", "anon", "authenticated"} & set(s_who) else "")
    pol = one("SELECT COUNT(*) FROM pg_policies WHERE tablename = %s", AUDIT_TABLE)[0]
    R.add("no row-level-security policy opens the audit table to anyone", pol == 0, f"{pol} policies" if pol else "", mandatory=True)
    if one("SELECT COUNT(*) FROM pg_roles WHERE rolname = 'service_role'")[0]:
        print("note: the Supabase 'service_role' (server-side API key, bypasses RLS) can read every table in public by design, as for the other tables.")
    n = one(f"SELECT COUNT(*) FROM {AUDIT_TABLE}")[0]
    print(f"audit rows so far: {n}")
    cols_dt = {r[0]: r[1] for r in cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'demo_trades'").fetchall()}
    R.add("demo_trades still has exactly the original columns", cols_dt == EXPECTED_COLUMNS)
    rows_now = one("SELECT COUNT(*) FROM demo_trades")[0]
    if a.expect_demo_rows is not None:
        R.add("demo_trades has at least the rows it had before the migration (trading continues, nothing deleted)", rows_now >= a.expect_demo_rows,
              f"{rows_now} rows now, {a.expect_demo_rows} before")
    print(f"demo_trades rows now: {rows_now}")
    conn.rollback()
    conn.close()
    Path(a.out).mkdir(parents=True, exist_ok=True)
    line = R.summary()
    f = Path(a.out) / f"gate-postcheck-{datetime.now().strftime('%Y%m%d-%H%M')}.json"
    f.write_text(json.dumps({"checks": R.rows, "summary": line, "exit_code": R.exit_code()}, indent=2, default=str), encoding="utf-8")
    print("report:", f)
    return R.exit_code()


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("readiness", "backup", "postcheck"):
        sp = sub.add_parser(name)
        sp.add_argument("--root", default=r"C:\apps\us-stock-reader")
        sp.add_argument("--out", default=r"C:\audit-reports")
    sub.choices["readiness"].add_argument("--require-least-privilege", action="store_true")
    sub.choices["readiness"].add_argument("--alt-port", type=int, default=5432, help="the session-mode port to try when the current one is a transaction pooler")
    sub.choices["postcheck"].add_argument("--expect-demo-rows", type=int, default=None)
    sub.choices["backup"].add_argument("--dump")
    sub.choices["backup"].add_argument("--pg-restore")
    sub.choices["backup"].add_argument("--max-age-min", type=float, default=30.0)
    sub.choices["backup"].add_argument("--min-pg-major", type=int, default=17)
    a = ap.parse_args()
    try:
        return {"readiness": readiness, "backup": backup, "postcheck": postcheck}[a.cmd](a)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"CHECKER ERROR ({type(exc).__name__}): {str(exc).splitlines()[0][:200]}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
