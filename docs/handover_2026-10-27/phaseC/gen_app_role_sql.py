"""Generate (never execute) the SQL that would move the application from the platform's default, over-privileged role to a dedicated
least-privilege role. READ-ONLY: one read-only catalog connection; it writes a .sql file and a rollback file, changes nothing.

What the application really needs (from the code): LOGIN; USAGE + CREATE on schema public (about 25 modules run `CREATE TABLE IF NOT
EXISTS` / `CREATE INDEX IF NOT EXISTS` / `ALTER TABLE ADD COLUMN` at runtime); OWNERSHIP of its own tables (so those ALTERs keep working);
DML on them and USAGE on their sequences. It does NOT need CREATEDB, CREATEROLE, REPLICATION or BYPASSRLS (no code creates a role or a
database, and no table uses row-level security).

    python gen_app_role_sql.py --root C:\\apps\\us-stock-reader --out C:\\audit-reports --role trdgo_app
"""
import argparse
import os
import sys
from datetime import datetime
from pathlib import Path


def q(name):
    return '"' + name.replace('"', '""') + '"'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=r"C:\apps\us-stock-reader")
    ap.add_argument("--out", default=r"C:\audit-reports")
    ap.add_argument("--role", default="trdgo_app")
    a = ap.parse_args()
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        env = Path(a.root) / "backend" / ".env"
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines() if env.exists() else []:
            if line.strip().startswith("DATABASE_URL="):
                url = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not url:
        sys.exit("STOPPED: no DATABASE_URL")
    import psycopg
    conn = psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://", 1), connect_timeout=15, prepare_threshold=None)
    conn.read_only = True
    cur = conn.cursor()
    cur.execute("SET TRANSACTION READ ONLY")
    cur.execute("SELECT current_user")
    me = cur.fetchone()[0]
    cur.execute("SELECT c.relname, c.relkind, pg_get_userbyid(c.relowner) = current_user FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'S', 'v', 'm', 'p') ORDER BY 2, 1")
    objs = cur.fetchall()
    cur.execute("SELECT COUNT(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public'")
    nfunc = cur.fetchone()[0]
    cur.execute("SELECT s.relname FROM pg_class s JOIN pg_namespace n ON n.oid = s.relnamespace WHERE n.nspname = 'public' AND s.relkind = 'S' "
                "AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = s.oid AND d.deptype IN ('a', 'i'))")
    standalone_seq = [r[0] for r in cur.fetchall()]
    conn.rollback()
    conn.close()
    tables = [o[0] for o in objs if o[1] in ("r", "p") and o[2]]
    foreign = [o[0] for o in objs if o[1] in ("r", "p") and not o[2]]
    views = [o[0] for o in objs if o[1] in ("v", "m")]
    R = a.role
    up = ["-- GENERATED, NOT EXECUTED. Dedicated least-privilege application role (proposal). Review, rehearse on a restored copy of a fresh dump,",
          "-- run the whole backend test suite against it there, and only then (with your approval) change the application's DATABASE_URL.",
          f"-- Generated {datetime.now().isoformat(timespec='seconds')} from the live catalog by {me!s}-role read-only session ({len(tables)} tables to own,",
          f"-- {len(standalone_seq)} standalone sequences, {len(views)} views, {nfunc} functions in public). Password: choose in a secret manager; never commit.",
          "BEGIN;", "SET LOCAL lock_timeout = '3s';",
          f"CREATE ROLE {R} LOGIN PASSWORD '<<set-from-secret-manager>>' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOREPLICATION CONNECTION LIMIT 30;",
          f"ALTER ROLE {R} SET statement_timeout = '2min';", f"GRANT USAGE, CREATE ON SCHEMA public TO {R};",
          "-- ownership of the application's tables (their owned sequences follow the table); needed for the runtime ALTER / CREATE INDEX"]
    up += [f"ALTER TABLE public.{q(t)} OWNER TO {R};" for t in tables]
    up += [f"ALTER SEQUENCE public.{q(s)} OWNER TO {R};" for s in standalone_seq]
    if views:
        up += ["-- views: review by hand before moving"] + [f"-- ALTER VIEW public.{q(v)} OWNER TO {R};" for v in views]
    if foreign:
        up += [f"-- NOT owned by this role (left alone, review): {', '.join(foreign)}"]
    up += [f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {R};", "COMMIT;"]
    down = [f"-- Rollback of the role move (transfers ownership back to {me}); the old DATABASE_URL keeps working throughout.", "BEGIN;",
            "SET LOCAL lock_timeout = '3s';"]
    down += [f"ALTER TABLE public.{q(t)} OWNER TO {q(me)};" for t in tables]
    down += [f"ALTER SEQUENCE public.{q(s)} OWNER TO {q(me)};" for s in standalone_seq]
    down += [f"REVOKE ALL ON SCHEMA public FROM {R};", f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM {R};", f"DROP OWNED BY {R};",
             f"DROP ROLE {R};", "COMMIT;"]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    (out / f"app-role-proposal-{stamp}.sql").write_text("\n".join(up) + "\n", encoding="utf-8")
    (out / f"app-role-proposal-{stamp}-ROLLBACK.sql").write_text("\n".join(down) + "\n", encoding="utf-8")
    print(f"tables to own: {len(tables)}; not owned: {len(foreign)}; standalone sequences: {len(standalone_seq)}; views: {len(views)}; functions: {nfunc}")
    print("written:", out / f"app-role-proposal-{stamp}.sql", "and the matching -ROLLBACK.sql (nothing executed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
