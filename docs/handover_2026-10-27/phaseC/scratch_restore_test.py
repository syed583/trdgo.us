"""Restore a production dump into a THROW-AWAY local PostgreSQL cluster and rehearse migration A and its rollback there.

NEVER touches production: it does not read DATABASE_URL or backend\\.env, connects only to 127.0.0.1 on a scratch port, and creates / deletes
only its own work folder. Needs a PostgreSQL 17 binary folder (initdb, pg_ctl, pg_restore, psql) -- e.g. C:\\pgtools17\\pgsql\\bin.

    python scratch_restore_test.py --dump C:\\audit-reports\\db-backups\\<file>_predeploy.dump --pg-bin C:\\pgtools17\\pgsql\\bin ^
        --sql-dir C:\\audit-reports\\apply\\migrations --gate C:\\audit-reports\\phaseC\\prod_gate_checks.py
Options: --port 54320 (scratch port), --work C:\\audit-reports\\scratch_restore, --keep (do not delete the scratch cluster), --expect-tables N.
Exit code 0 = every check passed, 1 = a check failed, 2 = the test itself could not run.
"""
import argparse
import collections
import hashlib
import json
import os
import re
import shutil
import platform
import socket
import subprocess
import sys
import tempfile
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


def ctl(cmd, out_file=None):
    """pg_ctl start / stop: the server it spawns inherits the handles, so PIPES must not be used (capturing would wait for the server to exit);
    the output goes to a FILE instead, so the reason of a failure is never lost."""
    sink = open(out_file, "ab") if out_file else subprocess.DEVNULL
    try:
        p = subprocess.run([str(c) for c in cmd], stdin=subprocess.DEVNULL, stdout=sink, stderr=subprocess.STDOUT)
    finally:
        if out_file:
            sink.close()
    return p.returncode


def can_bind(port):
    """Can this process listen on 127.0.0.1:port? (Windows can forbid a port inside an excluded range or one held by another program.)"""
    with socket.socket() as s_:
        try:
            if os.name == "nt":                 # PostgreSQL binds with SO_EXCLUSIVEADDRUSE: test the same way (EACCES when another process, e.g. Docker, holds the port)
                s_.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            s_.bind(("127.0.0.1", port))
            return True, ""
        except OSError as exc:
            return False, f"errno {exc.errno}: {exc.strerror}"


def pick_port(preferred, want=4):
    """Up to `want` bindable ports (the first is used, the others are fall-backs if the server still cannot start on it)."""
    cands = ([preferred] if preferred else []) + [55443] + list(range(54320, 54340)) + [15432, 25432, 35432, 45432, 55432, 5439, 15439]
    tried, good = [], []
    for p in dict.fromkeys(cands):
        ok, why = can_bind(p)
        tried.append((p, ok, why))
        if ok:
            good.append(p)
            if len(good) >= want:
                break
    return good, tried


def sh(cmd, timeout=40):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "") + (r.stderr or "")
    except Exception as exc:  # noqa: BLE001
        return f"<{type(exc).__name__}: {exc}>"


def grant_acl(path):
    """On Windows the PostgreSQL tools run the server with a RESTRICTED token (administrator rights removed). A folder whose access list names
    only 'Administrators' then becomes unreadable to the server. Give the current user (by SID) explicit full control of THIS scratch folder only."""
    if os.name != "nt":
        return "not Windows"
    me = sh(["whoami", "/user", "/fo", "csv", "/nh"]).strip().split(",")
    sid = me[-1].strip().strip('"') if me else ""
    if not sid.startswith("S-1-"):
        return f"could not determine the user SID ({me})"
    return f"icacls grant {sid}: " + sh(["icacls", str(path), "/grant", f"*{sid}:(OI)(CI)F", "/T", "/C", "/Q"]).strip()[:200]


def collect_diag(work, data, port, tried, diag_dir, reason, tool_bin):
    """Everything needed to find out WHY the scratch server did not start, saved OUTSIDE the work folder (which is deleted afterwards)."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = Path(diag_dir) / stamp
    out.mkdir(parents=True, exist_ok=True)
    lines = [f"reason: {reason}", f"time: {stamp}", f"python {sys.version.split()[0]} on {platform.platform()}", f"user: {sh(['whoami']).strip()}"]
    try:
        import ctypes
        lines.append(f"running elevated (administrator): {bool(ctypes.windll.shell32.IsUserAnAdmin())}")
    except Exception:  # noqa: BLE001
        lines.append("elevation: unknown")
    lines.append(f"port chosen: {port}; bind tests: " + "; ".join(f"{p}:{'ok' if ok else why}" for p, ok, why in tried[:12]))
    lines.append("--- excluded TCP port ranges (netsh) ---\n" + sh(["netsh", "int", "ipv4", "show", "excludedportrange", "protocol=tcp"]))
    lines.append("--- listeners on the port ---\n" + "\n".join(l for l in sh(["netstat", "-ano"]).splitlines() if f":{port} " in l))
    lines.append("--- ACL of the work folder and the data folder ---\n" + sh(["icacls", str(work)]) + sh(["icacls", str(data)]))
    lines.append("--- ACL of the parent folder ---\n" + sh(["icacls", str(Path(work).parent)]))
    lines.append(f"--- work folder listing ---\n" + sh(["cmd", "/c", "dir", "/a", str(work)]))
    lines.append("--- data folder listing ---\n" + sh(["cmd", "/c", "dir", "/a", str(data)]))
    lines.append("--- pg_ctl status ---\n" + sh([str(Path(tool_bin) / "pg_ctl.exe"), "status", "-D", str(data)]))
    lines.append("--- postgres --version ---\n" + sh([str(Path(tool_bin) / "postgres.exe"), "--version"]))
    lines.append("--- Microsoft Defender detections mentioning postgres (read-only query; nothing is changed) ---\n" + sh(
        ["powershell", "-NoProfile", "-Command", "try { Get-MpThreatDetection | Where-Object { $_.Resources -match 'postgres|initdb|pg_ctl|scratch' } | Select-Object -First 5 "
         "| Format-List ThreatID, ProcessName, Resources, InitialDetectionTime } catch { 'not available: ' + $_.Exception.Message }"], timeout=60))
    (out / "diagnosis.txt").write_text("\n".join(lines), encoding="utf-8", errors="replace")
    for name in ("server.log", "pg_ctl.out", "initdb.out"):
        f = Path(work) / name
        if f.exists():
            shutil.copy(f, out / name)
    conf = Path(data) / "postgresql.conf"
    if conf.exists():
        (out / "postgresql.conf.effective.txt").write_text("\n".join(l for l in conf.read_text(errors="ignore").splitlines() if l.strip() and not l.strip().startswith("#")), encoding="utf-8")
    return out


def lf_sha(path):
    return hashlib.sha256(Path(path).read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8")).hexdigest()


def copy_hash(lines):
    """Order-independent content hash that is exact for duplicates too (sorted byte strings, length-prefixed so no row can merge into another)."""
    h = hashlib.md5()
    for b_ in sorted(lines):
        h.update(len(b_).to_bytes(8, "big") + b_)
    return h.hexdigest()


def row_diff(live, dump, limit=3):
    """Multiset comparison (counts duplicates): rows only in the restored table / only in the dump. Returns (n_only_live, n_only_dump, samples)."""
    a, b_ = collections.Counter(live), collections.Counter(dump)
    only_live, only_dump = a - b_, b_ - a
    sample = [("restored", r[:120]) for r in list(only_live)[:limit]] + [("dump", r[:120]) for r in list(only_dump)[:limit]]
    return sum(only_live.values()), sum(only_dump.values()), sample


def copy_lines(conn, cols):
    """The table's rows in COPY text format (the same format pg_restore prints), one string per row."""
    with conn.cursor().copy(f"COPY public.demo_trades ({', '.join(cols)}) TO STDOUT") as cp:
        blob = b"".join(bytes(x) for x in cp)
    return [l for l in blob.split(b"\n") if l != b""]          # BYTES: no decoding, no newline translation


def dump_rows(blob):
    """Rows of demo_trades from `pg_restore --data-only` output, as BYTES, split ONLY on b"\\n". (str.splitlines() would also split on U+2028, U+0085,
    VT, FF ... which COPY text does not escape, and a text-mode pipe on Windows decodes with the ANSI code page and rewrites newlines.)"""
    cols, rows, inside = None, [], False
    for line in blob.split(b"\n"):
        if line.endswith(b"\r"):                  # pg_restore writes CRLF on Windows; a real CR inside data is escaped by COPY (backslash-r), never raw
            line = line[:-1]
        if not inside:
            m = re.match(rb"COPY (?:public\.)?demo_trades \((.*?)\) FROM stdin;", line)
            if m:
                cols, inside = [c.strip().decode() for c in m.group(1).split(b",")], True
        elif line == b"\\.":
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
    ap.add_argument("--port", type=int, default=0, help="0 = pick the first port Windows lets this process listen on (default)")
    ap.add_argument("--work", default=str(Path(tempfile.gettempdir()) / "trdgo_scratch_restore"),
                    help="scratch folder (deleted afterwards); default: the current user's temp folder, which the server's restricted token can use")
    ap.add_argument("--diag-dir", default=r"C:\audit-reports\scratch_diag", help="where the diagnostics of a failure are kept (never deleted)")
    ap.add_argument("--debug-bad-option", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--debug-corrupt", choices=["change", "swap"], help=argparse.SUPPRESS)   # regression test: damage the SCRATCH copy, the check must fail
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
    ports, tried = pick_port(a.port)
    port = ports[0] if ports else None
    if port is None:
        print("STOPPED: no port in the candidate list can be bound on 127.0.0.1:", [(p, why) for p, ok, why in tried[:8]])
        return 2
    a.port = port
    print(f"scratch port: {port} (bind-tested on 127.0.0.1" + (f"; skipped: {[(p, why) for p, ok, why in tried if not ok]}" if any(not ok for _, ok, _ in tried) else "") + ")")
    print("pg_restore:", run([tool["pg_restore"], "--version"]).stdout.strip(), "| dump:", dump.name, f"({dump.stat().st_size / 1e6:.1f} MB)")
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    print("work folder:", work, "|", grant_acl(work))
    data = work / "pgdata"
    started = False
    failed_start = None
    try:
        r = run([tool["initdb"], "-D", data, "-U", "scratch", "-A", "trust", "--encoding=UTF8", "--no-sync"])
        (work / "initdb.out").write_text((r.stdout or "") + (r.stderr or ""), encoding="utf-8", errors="replace")
        if not check("scratch cluster created (initdb)", r.returncode == 0, (r.stderr or r.stdout)[-160:] if r.returncode else ""):
            failed_start = "initdb failed"
            return 1
        opts = f"-p {a.port} -c listen_addresses=127.0.0.1 -c fsync=off -c synchronous_commit=off" + (" -c bogus_setting=1" if a.debug_bad_option else "")
        rc = 1
        for i, port_try in enumerate(ports):      # a bind refusal by the OS (e.g. a port taken by Docker) -> try the next free port
            a.port = port_try
            opts = f"-p {a.port} -c listen_addresses=127.0.0.1 -c fsync=off -c synchronous_commit=off" + (" -c bogus_setting=1" if a.debug_bad_option else "")
            rc = ctl([tool["pg_ctl"], "start", "-w", "-t", "90", "-D", data, "-l", work / "server.log", "-o", opts], work / "pg_ctl.out")
            if rc == 0:
                break
            log = (work / "server.log").read_text(errors="replace") if (work / "server.log").exists() else ""
            print(f"   start on port {port_try} failed (pg_ctl exit {rc}): {log.strip().splitlines()[-1][:160] if log.strip() else 'no log'}")
            if "bind" not in log.lower():
                break
        started = rc == 0
        if not check("scratch cluster started on 127.0.0.1 only", started, f"port {a.port}" if started else f"pg_ctl exit {rc}"):
            failed_start = f"pg_ctl start exit {rc}"
            d = collect_diag(work, data, a.port, tried, a.diag_dir, failed_start, b)
            print(f"DIAGNOSTICS SAVED (they survive the cleanup): {d}")
            for name in ("pg_ctl.out", "server.log"):
                f = work / name
                if f.exists() and f.stat().st_size:
                    print(f"--- {name} (last lines) ---")
                    print("\n".join(f.read_text(errors="replace").splitlines()[-25:]))
            return 1
        base = f"host=127.0.0.1 port={a.port} user=scratch connect_timeout=10"
        with psycopg.connect(base + " dbname=postgres", autocommit=True) as c:
            c.execute("CREATE DATABASE restore_check")
        dsn = base + " dbname=restore_check"
        with psycopg.connect(dsn, autocommit=True) as c:
            for rname in ROLES:
                c.execute(f"CREATE ROLE {rname} NOLOGIN" + (" BYPASSRLS" if rname in ("service_role", "supabase_admin") else ""))
            # the dump contains CREATE SCHEMA public: start from a database WITHOUT it (scratch only), so the restore is clean and reproducible
            c.execute("DROP SCHEMA public CASCADE")
        # ---- restore ----
        t0 = time.time()
        r = run([tool["pg_restore"], "--no-owner", "--no-privileges", "-h", "127.0.0.1", "-p", a.port, "-U", "scratch", "-d", "restore_check", dump])
        errs = [l for l in (r.stderr or "").splitlines() if l.strip()]
        print(f"pg_restore finished in {time.time() - t0:.0f}s, exit {r.returncode}, {len(errs)} message lines")
        for l in errs[:12]:
            print("   |", l[:170])
        check("pg_restore completed with exit code 0 and no error (any error fails the test)", r.returncode == 0 and not any("error" in l.lower() for l in errs),
              f"exit {r.returncode}, {sum('error' in l.lower() for l in errs)} error lines")
        with psycopg.connect(dsn, autocommit=True) as c:      # emulate Supabase: every NEW table / sequence in public is granted to the Data API roles by default
            c.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon, authenticated, service_role")
            c.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO anon, authenticated, service_role")
        if a.debug_corrupt:                                    # only ever on the scratch copy
            with psycopg.connect(dsn, autocommit=True) as c:
                if a.debug_corrupt == "change":
                    c.execute("UPDATE demo_trades SET entry_price = entry_price + 0.0001 WHERE id = 500")
                else:                                           # same row count and same column sums: two rows swap their entry price
                    c.execute("UPDATE demo_trades t SET entry_price = x.p FROM (SELECT id, CASE id WHEN 10 THEN (SELECT entry_price FROM demo_trades WHERE id = 11) ELSE (SELECT entry_price FROM demo_trades WHERE id = 10) END AS p FROM demo_trades WHERE id IN (10, 11)) x WHERE t.id = x.id")
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
            out = subprocess.run([str(x) for x in (tool["pg_restore"], "--data-only", "--table=demo_trades", "-f", "-", dump)], capture_output=True).stdout   # bytes
            cols, drows = dump_rows(out)
            check("the demo_trades rows can be read straight out of the dump", bool(cols) and len(drows) > 0, f"{len(drows)} rows")
            live_lines = copy_lines(c, cols)
            n_db = c.execute("SELECT COUNT(*) FROM demo_trades").fetchone()[0]
            check("restored row count equals the dump's row count", n_db == len(drows), f"{n_db} vs {len(drows)}")
            base_hash = copy_hash(live_lines)
            n_live, n_dump, sample = row_diff(live_lines, drows)
            check("every restored row is byte-identical to the dump (exact multiset comparison, duplicates counted)",
                  n_live == 0 and n_dump == 0 and copy_hash(drows) == base_hash,
                  f"{len(drows)} rows, md5 {base_hash}" if n_live == 0 and n_dump == 0 else f"{n_live} restored-only, {n_dump} dump-only; e.g. {sample}")

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
            ctl([tool["pg_ctl"], "stop", "-m", "fast", "-D", data], work / "pg_ctl_stop.out")
        try:                                    # keep the server log, even after a pass (small); never lose the reason of a failure
            keep = Path(a.diag_dir) / "last_run"
            keep.mkdir(parents=True, exist_ok=True)
            for name in ("server.log", "pg_ctl.out", "initdb.out"):
                if (work / name).exists():
                    shutil.copy(work / name, keep / name)
        except Exception:  # noqa: BLE001
            pass
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
