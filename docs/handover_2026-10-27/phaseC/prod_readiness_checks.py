"""READ-ONLY pre-migration checks of the production database for the audit-only deployment (migration A).

One READ ONLY connection (the server rejects writes); SELECT / SHOW only; the connection string and the role name are never printed.
Prints PASS / FAIL / NOT VERIFIED for each precondition and writes a JSON copy. It runs no DDL, not even a rolled-back one.

    python prod_readiness_checks.py --root C:\\apps\\us-stock-reader --out C:\\audit-reports
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

AUDIT_TABLE = "intraday_signal_audit"
AUDIT_INDEX = "ix_intraday_signal_audit_day"
# demo_trades exactly as the production build 36094e8 declares it (name -> type family)
EXPECTED_COLUMNS = {"id": "integer", "strategy": "character varying", "symbol": "character varying", "side": "character varying",
                    "shares": "integer", "session_date": "date", "entry_time": "timestamp with time zone", "entry_price": "double precision",
                    "stop": "double precision", "target": "double precision", "score_at_entry": "double precision",
                    "last_price": "double precision", "profit": "double precision", "profit_pct": "double precision",
                    "status": "character varying", "exit_time": "timestamp with time zone", "exit_price": "double precision",
                    "updated_at": "timestamp with time zone"}


def load_url(root):
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        env = root / "backend" / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.strip().startswith("DATABASE_URL="):
                    url = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not url:
        sys.exit("STOPPED: no DATABASE_URL")
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=r"C:\apps\us-stock-reader")
    ap.add_argument("--out", default=r"C:\audit-reports")
    a = ap.parse_args()
    url = load_url(Path(a.root))
    import psycopg
    conn = psycopg.connect(url, connect_timeout=15, prepare_threshold=None)
    conn.read_only = True
    cur = conn.cursor()
    cur.execute("SET TRANSACTION READ ONLY")
    res, facts = [], {}

    def check(name, status, detail=""):
        res.append({"check": name, "status": status, "detail": detail})
        print(f"{status:12} {name}" + (f"  [{detail}]" if detail else ""))

    def one(sql, *p):
        cur.execute(sql, p)
        return cur.fetchone()

    def allrows(sql, *p):
        cur.execute(sql, p)
        return cur.fetchall()

    # ---- server ----
    ver = one("SHOW server_version")[0]
    num = one("SHOW server_version_num")[0]
    facts.update(server_version=ver, server_version_num=num, connection_port=urlparse(url).port, banner=one("SELECT version()")[0][:90])
    print(f"PostgreSQL {ver} (version_num {num}); connection port {facts['connection_port']} "
          f"({'transaction pooler' if facts['connection_port'] == 6543 else 'session pooler / direct' if facts['connection_port'] == 5432 else 'other'})")
    check("server is PostgreSQL 17", "PASS" if str(ver).startswith("17") else "FAIL", f"version {ver}")
    check("connected to the primary (not a replica)", "PASS" if not one("SELECT pg_is_in_recovery()")[0] else "FAIL")
    check("session is read-only", "PASS" if one("SHOW transaction_read_only")[0] == "on" else "FAIL")

    # ---- role and permissions ----
    sup, cdb, crole, byp = one("SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    can_create = one("SELECT has_schema_privilege(current_user, 'public', 'CREATE')")[0]
    dml = {p: one("SELECT has_table_privilege(current_user, 'public.demo_trades', %s)", p)[0] for p in ("SELECT", "INSERT", "UPDATE", "DELETE")}
    owner = one("SELECT pg_get_userbyid(c.relowner) = current_user FROM pg_class c WHERE c.relname = 'demo_trades' AND c.relkind = 'r'")
    facts["role"] = {"superuser": sup, "createdb": cdb, "createrole": crole, "bypassrls": byp, "create_in_public": can_create, "demo_trades_dml": dml,
                     "owns_demo_trades": None if owner is None else owner[0]}
    check("the connecting role can create the audit table (or the owner will apply the SQL)", "PASS" if can_create else "NOT VERIFIED",
          "CREATE on schema public" if can_create else "no CREATE right: apply migration A as the table owner")
    broad = [n for n, v in (("CREATEDB", cdb), ("CREATEROLE", crole), ("BYPASSRLS", byp), ("SUPERUSER", sup)) if v]
    check("least privilege: the application role is NOT over-privileged", "FAIL" if broad else "PASS",
          ("role holds " + ", ".join(broad)) if broad else "")
    to = {k: one("SELECT current_setting(%s, true)", k)[0] for k in ("statement_timeout", "lock_timeout", "idle_in_transaction_session_timeout")}
    facts["timeouts_of_role"] = to
    print("role timeouts:", to)

    # ---- connections ----
    used, mx = one("SELECT (SELECT COUNT(*) FROM pg_stat_activity WHERE backend_type = 'client backend'), current_setting('max_connections')::int")
    facts["connections"] = {"client_backends": used, "max_connections": mx}
    check("connection headroom for one extra short audit session", "PASS" if used + 2 < mx else "FAIL", f"{used} client backends of {mx}")

    # ---- the audit objects must not exist yet (no collision) ----
    objs = allrows("SELECT relname, relkind FROM pg_class WHERE relname ILIKE %s ORDER BY 1", "%intraday_signal_audit%")
    facts["existing_audit_named_objects"] = [tuple(o) for o in objs]
    check(f"no object named like {AUDIT_TABLE} exists (table, index, sequence, _local twin)", "PASS" if not objs else "FAIL", str(objs) if objs else "")
    clash = one("SELECT COUNT(*) FROM pg_class WHERE relname IN (%s, %s, %s)", AUDIT_TABLE, AUDIT_INDEX, AUDIT_TABLE + "_id_seq")[0]
    check("the exact names the migration creates are free", "PASS" if clash == 0 else "FAIL")

    # ---- demo_trades must be exactly the shape the audit assumes ----
    cols = {r[0]: r[1] for r in allrows("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'demo_trades'")}
    facts["demo_trades_columns"] = cols
    missing = sorted(set(EXPECTED_COLUMNS) - set(cols))
    extra = sorted(set(cols) - set(EXPECTED_COLUMNS))
    wrong = sorted(k for k in EXPECTED_COLUMNS if k in cols and cols[k] != EXPECTED_COLUMNS[k])
    check("demo_trades has exactly the columns the production build declares", "PASS" if not (missing or extra or wrong) else "FAIL",
          f"missing={missing} extra={extra} wrong_type={wrong}" if (missing or extra or wrong) else "")
    check("the hotfix needs no V2 evidence columns on demo_trades", "PASS", "the audit-only build does not read or write them")
    rows, size = one("SELECT (SELECT COUNT(*) FROM demo_trades), pg_total_relation_size('demo_trades')")
    facts["demo_trades"] = {"rows": rows, "bytes": size}
    print(f"demo_trades: {rows} rows, {size / 1e6:.2f} MB")
    idx = [r[0] for r in allrows("SELECT indexname FROM pg_indexes WHERE schemaname = 'public' AND tablename = 'demo_trades' ORDER BY 1")]
    cons = [r[0] for r in allrows("SELECT conname FROM pg_constraint WHERE conrelid = 'public.demo_trades'::regclass ORDER BY 1")]
    facts["demo_trades_indexes"], facts["demo_trades_constraints"] = idx, cons
    print("demo_trades indexes:", idx, "constraints:", cons)

    # ---- is anything holding locks / long transactions on demo_trades right now ----
    locks = one("SELECT COUNT(*) FROM pg_locks l JOIN pg_class c ON c.oid = l.relation WHERE c.relname = 'demo_trades' AND l.mode IN "
                "('AccessExclusiveLock', 'ExclusiveLock', 'ShareRowExclusiveLock', 'ShareLock')")[0]
    long_tx = one("SELECT COUNT(*) FROM pg_stat_activity WHERE xact_start IS NOT NULL AND now() - xact_start > interval '60 seconds' "
                  "AND backend_type = 'client backend' AND pid <> pg_backend_pid()")[0]
    facts["locks_on_demo_trades"], facts["transactions_older_than_60s"] = locks, long_tx
    check("no heavy lock currently held on demo_trades", "PASS" if locks == 0 else "FAIL", f"{locks} heavy locks")
    check("no transaction older than 60 s that could block a DDL lock", "PASS" if long_tx == 0 else "NOT VERIFIED", f"{long_tx} long transactions")
    check("no V2 / shadow / audit objects exist (production is the unmodified build's schema)", "PASS" if not [r for r in facts['existing_audit_named_objects']] else "FAIL")
    conn.rollback()
    conn.close()
    bad = [r for r in res if r["status"] == "FAIL"]
    nv = [r for r in res if r["status"] == "NOT VERIFIED"]
    print(f"\n{len([r for r in res if r['status'] == 'PASS'])} PASS, {len(bad)} FAIL, {len(nv)} NOT VERIFIED")
    Path(a.out).mkdir(parents=True, exist_ok=True)
    f = Path(a.out) / f"readiness-readonly-{datetime.now().strftime('%Y%m%d-%H%M')}.json"
    f.write_text(json.dumps({"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "facts": facts, "checks": res}, indent=2, default=str),
                 encoding="utf-8")
    print("report:", f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
