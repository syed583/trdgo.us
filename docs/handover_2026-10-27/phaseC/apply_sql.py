"""Apply ONE reviewed migration file (backend/migrations/*.sql) to the database named by DATABASE_URL / backend\\.env.

Only for the explicitly approved production step. It prints the file's SHA-256 and the statement summary, refuses to run any file that
is not in backend\\migrations, runs the whole file as ONE transaction (the files carry BEGIN / SET LOCAL lock_timeout / COMMIT), never
prints the connection string, and rolls back on any error. Without --apply it only shows what it would run.

    python apply_sql.py --root C:\\apps\\us-stock-reader --file 2026_10_27_A_audit_only.sql            (dry run)
    python apply_sql.py --root C:\\apps\\us-stock-reader --file 2026_10_27_A_audit_only.sql --apply    (after approval + backup)
"""
import argparse
import hashlib
import os
import sys
from pathlib import Path
from urllib.parse import urlparse


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=r"C:\apps\us-stock-reader")
    ap.add_argument("--file", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--port", type=int, default=None, help="override the connection port, e.g. 5432 (session pooler) instead of 6543 (transaction pooler)")
    a = ap.parse_args()
    mig = (Path(a.root) / "backend" / "migrations").resolve()
    f = (mig / a.file).resolve()
    if f.parent != mig or not f.exists() or f.suffix != ".sql":
        sys.exit("STOPPED: the file must be a .sql in backend\\migrations")
    sql = f.read_text(encoding="utf-8")
    print("file:", f.name, "sha256:", hashlib.sha256(sql.encode("utf-8")).hexdigest())
    stmts = [s.strip() for s in sql.split(";") if s.strip() and not all(l.strip().startswith("--") or not l.strip() for l in s.splitlines())]
    for s in stmts:
        print("  ", " ".join(x for x in s.splitlines() if not x.strip().startswith("--"))[:110])
    if not a.apply:
        print("DRY RUN: nothing was executed (add --apply after the backup and your approval)")
        return 0
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        for line in (Path(a.root) / "backend" / ".env").read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip().startswith("DATABASE_URL="):
                url = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not url:
        sys.exit("STOPPED: no DATABASE_URL")
    url = url.replace("postgresql+psycopg://", "postgresql://", 1)
    if a.port:                                     # DDL in one explicit transaction belongs on a session-mode connection
        p = urlparse(url)
        url = url.replace(f":{p.port}/", f":{a.port}/", 1) if p.port else url
    import psycopg
    conn = psycopg.connect(url, connect_timeout=15, prepare_threshold=None, autocommit=True)
    print("connected (port %s), server %s" % (urlparse(url).port, conn.execute("SHOW server_version").fetchone()[0]))
    try:
        conn.execute(sql)                          # the file's own BEGIN ... COMMIT; one transaction, simple protocol
    except Exception as exc:  # noqa: BLE001
        try:
            conn.execute("ROLLBACK")
        except Exception:  # noqa: BLE001
            pass
        print("FAILED and rolled back:", type(exc).__name__, str(exc).splitlines()[0][:160])
        return 1
    print("APPLIED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
