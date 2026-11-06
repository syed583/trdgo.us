"""READ-ONLY structural fingerprint of the production database, to prove that a test (e.g. the scratch restore) changed nothing.
One READ ONLY connection, SELECTs only, nothing is printed about credentials. Compare two runs: every key except the *_rows / max_id ones
must be identical; demo_trades row count may only grow (the paper tracker keeps trading).

    python prod_fingerprint.py [C:\\apps\\us-stock-reader]  ->  one JSON line on stdout
"""
import hashlib
import json
import os
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else r"C:\apps\us-stock-reader")
url = (os.environ.get("DATABASE_URL") or "").strip()
if not url:
    env = root / "backend" / ".env"
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


def one(sql):
    cur.execute(sql)
    return cur.fetchone()[0]


def md5(rows):
    return hashlib.md5("|".join(str(r) for r in rows).encode()).hexdigest()


tables = [r[0] for r in cur.execute("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY 1").fetchall()]
cols = [tuple(r) for r in cur.execute("SELECT table_name, column_name, data_type FROM information_schema.columns WHERE table_schema = 'public' ORDER BY 1, ordinal_position").fetchall()]
idx = [r[0] for r in cur.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'public' ORDER BY 1").fetchall()]
out = {
    "public_tables": len(tables), "table_names_md5": md5(tables), "all_columns_md5": md5(cols), "indexes_md5": md5(idx), "indexes": len(idx),
    "audit_named_objects": one("SELECT COUNT(*) FROM pg_class WHERE relname ILIKE '%intraday_signal_audit%'"),
    "scratch_named_databases": one("SELECT COUNT(*) FROM pg_database WHERE datname ILIKE '%restore_check%'"),
    "roles": one("SELECT COUNT(*) FROM pg_roles"), "role_attributes_md5": md5(cur.execute("SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls, rolcanlogin FROM pg_roles ORDER BY 1").fetchall()),
    "policies": one("SELECT COUNT(*) FROM pg_policies"), "rls_tables": one("SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity"),
    "event_triggers": one("SELECT COUNT(*) FROM pg_event_trigger"),
    "demo_trades_acl_md5": md5(cur.execute("SELECT relacl::text FROM pg_class WHERE relname = 'demo_trades' AND relkind = 'r'").fetchall()),
    "demo_trades_rows": one("SELECT COUNT(*) FROM demo_trades"), "demo_trades_max_id": one("SELECT COALESCE(MAX(id), 0) FROM demo_trades"),
}
conn.rollback()
conn.close()
print(json.dumps(out, sort_keys=True))
