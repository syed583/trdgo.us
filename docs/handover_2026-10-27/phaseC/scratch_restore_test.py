"""Restore a production dump into a THROW-AWAY local PostgreSQL cluster and rehearse migration A and its rollback there.

NEVER touches production: it does not read DATABASE_URL or backend\\.env, connects only to 127.0.0.1 on a scratch port, and creates / deletes
only its own work folder. Needs a PostgreSQL 17 binary folder (initdb, pg_ctl, pg_restore, psql) -- e.g. C:\\pgtools17\\pgsql\\bin.

    python scratch_restore_test.py --dump C:\\audit-reports\\db-backups\\<file>_predeploy.dump --pg-bin C:\\pgtools17\\pgsql\\bin ^
        --sql-dir C:\\audit-reports\\apply\\migrations --gate C:\\audit-reports\\phaseC\\prod_gate_checks.py
Options: --port 54320 (scratch port), --work C:\\audit-reports\\scratch_restore, --keep (do not delete the scratch cluster), --expect-tables N.
Exit code 0 = every check passed, 1 = a check failed, 2 = the test itself could not run.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

MIG_A = ("2026_10_27_A_audit_only.sql", "336960aa9c938196fdd4826120c7edbfd6462693a23d7ca2cdd421a3947eeddd")
MIG_A_ROLLBACK = ("2026_10_27_A_audit_only_ROLLBACK.sql", "c5c862a564982ca2fd884a77906a0d885abdd2d38e4fef421450f46b4f2e7a5a")
ROLES = ["anon", "authenticated", "authenticator", "service_role", "supabase_admin", "supabase_auth_admin", "supabase_storage_admin",
         "supabase_read_only_user", "supabase_privileged_role", "supabase_etl_admin", "supabase_replication_admin", "pgbouncer", "dashboard_user"]
results = []


def check(name, ok, detail=""):
    results.append({"check": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    return ok


def run(cmd, **kw):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)


def ctl(cmd):
    """pg_ctl start / stop: the server it spawns inherits the pipes, so they must NOT be captured (capturing would wait for the server to exit)."""
    p = subprocess.run([str(c) for c in cmd], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return p.returncode


def lf_sha(path):
    return hashlib.sha256(Path(path).read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8")).hexdigest()


def copy_hash(lines):
    return hashlib.md5("\n".join(sorted(lines)).encode("utf-8")).hexdigest()


def copy_lines(conn, cols):
    """The table's rows in COPY text format (the same format pg_restore prints), one string per row."""
    with conn.cursor().copy(f"COPY public.demo_trades ({', '.join(cols)}) TO STDOUT") as cp:
        blob = b"".join(bytes(x) for x in cp).decode("utf-8")
    return [l for l in blob.split("\n") if l != ""]


def dump_rows(text):
    cols, rows, inside = None, [], False
    for line in text.splitlines():
        if not inside:
            m = re.match(r"COPY (?:public\.)?demo_trades \((.*?)\) FROM stdin;", line)
            if m:
                cols, inside = [c.strip() for c in m.group(1).split(",")], True
        elif line == "\\.":
            break
        else:
            rows.append(line)
    return cols, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", required=True)
    ap.add_argument("--pg-bin", required=True)
    ap.add_argument("--sql-dir", required=True)
    ap.add_argument("--gate", required=True, help="path of prod_gate_checks.py (its postcheck is run against the scratch copy)")
    ap.add_argument("--port", type=int, default=54320)
    ap.add_argument("--work", default=r"C:\audit-reports\scratch_restore")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--expect-tables", type=int, default=None)
    a = ap.parse_args()
    import psycopg
    b = Path(a.pg_bin)
    tool = {n: b / f"{n}.exe" if (b / f"{n}.exe").exists() else b / n for n in ("initdb", "pg_ctl", "pg_restore", "psql")}
    if not all(Path(t).exists() for t in tool.values()):
        print("STOPPED: initdb / pg_ctl / pg_restore / psql not all found in", b)
        return 2
    dump = Path(a.dump)
    work = Path(a.work).resolve()
    if "scratch" not in work.name.lower():
        print("STOPPED: the work folder name must contain 'scratch' (safety)")
        return 2
    for name, sha in (MIG_A, MIG_A_ROLLBACK):
        if not (Path(a.sql_dir) / name).exists() or lf_sha(Path(a.sql_dir) / name) != sha:
            print(f"STOPPED: {name} is missing or does not have the reviewed SHA-256 {sha}")
            return 2
    with socket.socket() as s_:
        if s_.connect_ex(("127.0.0.1", a.port)) == 0:
            print(f"STOPPED: port {a.port} is already in use")
            return 2
    print("pg_restore:", run([tool["pg_restore"], "--version"]).stdout.strip(), "| dump:", dump.name, f"({dump.stat().st_size / 1e6:.1f} MB)")
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    data = work / "pgdata"
    started = False
    try:
        r = run([tool["initdb"], "-D", data, "-U", "scratch", "-A", "trust", "--encoding=UTF8", "--no-sync"])
        if not check("scratch cluster created (initdb)", r.returncode == 0, (r.stderr or r.stdout)[-160:] if r.returncode else ""):
            return 1
        rc = ctl([tool["pg_ctl"], "start", "-w", "-t", "90", "-D", data, "-l", work / "server.log", "-o",
                  f"-p {a.port} -c listen_addresses=127.0.0.1 -c fsync=off -c synchronous_commit=off"])
        started = rc == 0
        if not check("scratch cluster started on 127.0.0.1 only", started, f"port {a.port}" if started else f"pg_ctl exit {rc}; see {work / 'server.log'}"):
            return 1
        base = f"host=127.0.0.1 port={a.port} user=scratch connect_timeout=10"
        with psycopg.connect(base + " dbname=postgres", autocommit=True) as c:
            c.execute("CREATE DATABASE restore_check")
        dsn = base + " dbname=restore_check"
        with psycopg.connect(dsn, autocommit=True) as c:
            for rname in ROLES:
                c.execute(f"CREATE ROLE {rname} NOLOGIN" + (" BYPASSRLS" if rname in ("service_role", "supabase_admin") else ""))
            # emulate Supabase: every NEW table / sequence in public is granted to the Data API roles by default
            c.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon, authenticated, service_role")
            c.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO anon, authenticated, service_role")
        # ---- restore ----
        t0 = time.time()
        r = run([tool["pg_restore"], "--no-owner", "--no-privileges", "-h", "127.0.0.1", "-p", a.port, "-U", "scratch", "-d", "restore_check", dump])
        errs = [l for l in (r.stderr or "").splitlines() if l.strip()]
        print(f"pg_restore finished in {time.time() - t0:.0f}s, exit {r.returncode}, {len(errs)} message lines")
        for l in errs[:8]:
            print("   |", l[:170])
        with psycopg.connect(dsn) as c:
            has = c.execute("SELECT to_regclass('public.demo_trades') IS NOT NULL").fetchone()[0]
            check("the dump restores into a scratch database and demo_trades exists", has, f"pg_restore exit {r.returncode}, {len(errs)} message lines")
            if not has:
                return 1
            ntab = c.execute("SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind = 'r'").fetchone()[0]
            nrls = c.execute("SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity").fetchone()[0]
            npol = c.execute("SELECT COUNT(*) FROM pg_policies WHERE schemaname = 'public'").fetchone()[0]
            print(f"restored schema: {ntab} tables in public, {nrls} with row-level security, {npol} policies")
            if a.expect_tables is not None:
                check("the restored schema has the expected number of tables", ntab == a.expect_tables, f"{ntab} vs {a.expect_tables}")
            # ---- row integrity: dump text vs restored table ----
            out = run([tool["pg_restore"], "--data-only", "--table=demo_trades", "-f", "-", dump]).stdout
            cols, drows = dump_rows(out)
            check("the demo_trades rows can be read straight out of the dump", bool(cols) and len(drows) > 0, f"{len(drows)} rows")
            live_lines = copy_lines(c, cols)
            n_db = c.execute("SELECT COUNT(*) FROM demo_trades").fetchone()[0]
            check("restored row count equals the dump's row count", n_db == len(drows), f"{n_db} vs {len(drows)}")
            base_hash = copy_hash(live_lines)
            check("every restored row is byte-identical to the dump (content hash)", copy_hash(drows) == base_hash, f"md5 {base_hash}")

        def sql_file(name, sha):
            text_ = (Path(a.sql_dir) / name).read_text(encoding="utf-8")
            assert lf_sha(Path(a.sql_dir) / name) == sha
            with psycopg.connect(dsn, autocommit=True) as c2:
                c2.execute(text_)

        def table_hash():
            with psycopg.connect(dsn) as c3:
                return copy_hash(copy_lines(c3, cols))

        def has_audit():
            with psycopg.connect(dsn) as c4:
                return c4.execute("SELECT to_regclass('public.intraday_signal_audit') IS NOT NULL").fetchone()[0]

        def postcheck():
            env = dict(os.environ, DATABASE_URL=f"postgresql://scratch@127.0.0.1:{a.port}/restore_check?sslmode=disable")
            p = subprocess.run([sys.executable, a.gate, "postcheck", "--root", str(work), "--out", str(work), "--expect-demo-rows", str(len(drows))],
                               capture_output=True, text=True, env=env)
            return p.returncode, p.stdout

        # ---- migration A, rollback, re-apply ----
        check("audit table absent before migration A", not has_audit())
        try:
            sql_file(*MIG_A)
            check("migration A applies on the restored production schema", True)
        except Exception as exc:  # noqa: BLE001
            check("migration A applies on the restored production schema", False, str(exc).splitlines()[0][:160])
            return 1
        rc, out = postcheck()
        for l in out.splitlines():
            if l.startswith(("PASS", "FAIL", "who holds", "MANDATORY")):
                print("   postcheck |", l[:170])
        check("the strengthened postcheck passes (RLS, privileges, complete ACL, no policy, demo_trades intact)", rc == 0, f"exit {rc}")
        check("demo_trades is byte-identical after migration A", table_hash() == base_hash)
        sql_file(*MIG_A_ROLLBACK)
        check("rollback removes the audit table", not has_audit())
        check("demo_trades is byte-identical after the rollback", table_hash() == base_hash)
        sql_file(*MIG_A)
        rc2, _ = postcheck()
        check("migration A can be re-applied after a rollback and passes the postcheck again", rc2 == 0 and has_audit())
        sql_file(*MIG_A)                                                             # idempotence
        check("migration A is idempotent (second apply)", has_audit() and table_hash() == base_hash)
    except Exception as exc:  # noqa: BLE001
        print(f"TEST ERROR ({type(exc).__name__}): {str(exc).splitlines()[0][:200]}")
        return 2
    finally:
        if started:
            ctl([tool["pg_ctl"], "stop", "-m", "fast", "-D", data])
        if not a.keep and work.exists() and "scratch" in work.name.lower():
            shutil.rmtree(work, ignore_errors=True)
            print("scratch cluster stopped and deleted")
    bad = [r for r in results if not r["ok"]]
    print(f"\n{len(results) - len(bad)} passed, {len(bad)} failed")
    out_file = Path(a.work).parent / f"scratch-restore-result-{time.strftime('%Y%m%d-%H%M')}.json"
    out_file.write_text(json.dumps({"dump": dump.name, "results": results}, indent=2), encoding="utf-8")
    print("result:", out_file)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
