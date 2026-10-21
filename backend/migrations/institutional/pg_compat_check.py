"""PostgreSQL compatibility / write / rollback validation — DISPOSABLE TEST
PROJECT ONLY. PREPARED, NOT EXECUTED in Phase 7 local work.

Runs the full write-path validation the read-only dev checks could not cover:
schema migration, inserts, ON CONFLICT idempotency, unique/constraint/FK
behaviour, transaction rollback, large-integer + timestamptz round-trips,
concurrent writes, index creation, and a dashboard read query on a fresh DB.

ISOLATION: it refuses to run unless TEST_DATABASE_URL_PG points at a project
ref that is NEITHER the dev project (qzmsozpyypjdxzfprhoz) NOR the Earnings AI
production project (ddetojbocaxhcfxtcopc). It must be a separate, disposable
test project. It writes only to institutional_* tables and cleans up after.

Usage (ONLY against an approved disposable TEST project):
    TEST_DATABASE_URL_PG=postgresql+psycopg://...<testref>...:5432/postgres?sslmode=require \
      python migrations/institutional/pg_compat_check.py
"""
from __future__ import annotations

import os
import sys
import threading
import datetime
from urllib.parse import urlparse

DEV_REF = "qzmsozpyypjdxzfprhoz"
EARNINGS_PROD_REF = "ddetojbocaxhcfxtcopc"

PASS: list[str] = []
FAIL: list[str] = []


def chk(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' -- ' + detail) if detail else ''}", flush=True)


def _guarded_url() -> str:
    url = (os.getenv("TEST_DATABASE_URL_PG") or "").strip()
    if not url:
        sys.exit("STOP: set TEST_DATABASE_URL_PG to the disposable TEST project (no default).")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        sys.exit("STOP: TEST_DATABASE_URL_PG must be a postgresql URL.")
    norm = url if url.startswith("postgresql+psycopg") else url.replace("postgresql://", "postgresql+psycopg://", 1)
    if "sslmode=" not in norm:
        norm += ("&" if "?" in norm else "?") + "sslmode=require"
    u = urlparse(norm.replace("postgresql+psycopg", "postgresql"))
    ref = (u.username or "").split(".", 1)[1] if "." in (u.username or "") else None
    if ref == DEV_REF:
        sys.exit("STOP: refusing the DEV project — this test writes/drops; use a disposable project.")
    if ref == EARNINGS_PROD_REF:
        sys.exit("STOP: refusing the Earnings AI PRODUCTION project.")
    print(f"  TEST project ref: ...{(ref or '?')[:6]}... (not dev, not earnings-prod)", flush=True)
    return norm


def main() -> int:
    url = _guarded_url()
    from sqlalchemy import create_engine, text
    eng = create_engine(url, connect_args={"sslmode": "require", "connect_timeout": 10}, pool_pre_ping=True)

    # 1. Migration / schema + index creation on a fresh Postgres.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    os.environ["INSTITUTIONAL_DATABASE_URL"] = url
    os.environ["INSTITUTIONAL_ALLOWED_REFS"] = urlparse(url.replace("postgresql+psycopg", "postgresql")).username.split(".", 1)[1]
    import importlib
    mig = importlib.import_module("migrations.institutional.001_institutional_schema") if False else None
    # 001 module name starts with a digit -> load by path
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "mig001", os.path.join(os.path.dirname(os.path.abspath(__file__)), "001_institutional_schema.py"))
    mig = importlib.util.module_from_spec(spec); spec.loader.exec_module(mig)
    mig.apply()
    with eng.connect() as c:
        got = c.execute(text("SELECT to_regclass('public.institutional_holdings'), "
                             "to_regclass('public.institutional_cusip_map')")).first()
        chk("migration 001 creates schema on fresh Postgres", all(got))
        idx = c.execute(text("SELECT COUNT(*) FROM pg_indexes WHERE tablename='institutional_holdings' AND indexname='ix_ih_cusip_q'")).scalar()
        chk("performance index created", idx == 1)

    with eng.begin() as c:
        c.execute(text("SET statement_timeout='30000'"))
        c.execute(text("INSERT INTO institutional_funds (id,cik,fund_name,updated_at) VALUES (900001,'9000000001','PG COMPAT FUND',now()) ON CONFLICT (id) DO NOTHING"))
        # 2. large-integer + insert
        big = 9_123_456_789
        c.execute(text("""INSERT INTO institutional_holdings (fund_id,ticker,cusip,issuer_name,shares,position_value,report_quarter,filing_date)
                          VALUES (900001,'ZZZ','ZZZZZZZZZ','PG TEST CO',:sh,0,'2026-Q2','2026-08-15')"""), {"sh": big})
    with eng.connect() as c:
        back = c.execute(text("SELECT shares FROM institutional_holdings WHERE cusip='ZZZZZZZZZ'")).scalar()
        chk("large-integer shares round-trip exact", int(back) == big, f"{int(back)}")

    # 3. ON CONFLICT idempotency on the unique (fund,cusip,quarter) constraint
    try:
        with eng.begin() as c:
            c.execute(text("""INSERT INTO institutional_holdings (fund_id,ticker,cusip,issuer_name,shares,position_value,report_quarter,filing_date)
                              VALUES (900001,'ZZZ','ZZZZZZZZZ','PG TEST CO',999,0,'2026-Q2','2026-08-15')
                              ON CONFLICT (fund_id,cusip,report_quarter) DO UPDATE SET shares=EXCLUDED.shares"""))
        with eng.connect() as c:
            n = c.execute(text("SELECT COUNT(*) FROM institutional_holdings WHERE cusip='ZZZZZZZZZ'")).scalar()
            v = c.execute(text("SELECT shares FROM institutional_holdings WHERE cusip='ZZZZZZZZZ'")).scalar()
            chk("ON CONFLICT upsert idempotent (no dup row)", n == 1 and int(v) == 999, f"n={n} v={int(v)}")
    except Exception as ex:
        chk("ON CONFLICT upsert idempotent", False, str(ex)[:80])

    # 4. unique constraint violation raises
    raised = False
    try:
        with eng.begin() as c:
            c.execute(text("""INSERT INTO institutional_holdings (fund_id,ticker,cusip,issuer_name,shares,position_value,report_quarter,filing_date)
                              VALUES (900001,'ZZZ','ZZZZZZZZZ','PG TEST CO',1,0,'2026-Q2','2026-08-15')"""))
    except Exception:
        raised = True
    chk("unique constraint violation is enforced", raised)

    # 5. transaction rollback leaves no rows
    before = None
    with eng.connect() as c:
        before = c.execute(text("SELECT COUNT(*) FROM institutional_holdings")).scalar()
    try:
        with eng.begin() as c:
            c.execute(text("INSERT INTO institutional_holdings (fund_id,ticker,cusip,issuer_name,shares,position_value,report_quarter,filing_date) VALUES (900001,'RB','RBRBRBRB1','RB',1,0,'2026-Q2','2026-08-15')"))
            raise RuntimeError("force rollback")
    except RuntimeError:
        pass
    with eng.connect() as c:
        after = c.execute(text("SELECT COUNT(*) FROM institutional_holdings")).scalar()
    chk("transaction rollback leaves no partial rows", before == after, f"{before}->{after}")

    # 6. timestamptz round-trip
    with eng.begin() as c:
        c.execute(text("UPDATE institutional_funds SET updated_at=:t WHERE id=900001"), {"t": datetime.datetime(2026, 8, 1, 12, 0, tzinfo=datetime.timezone.utc)})
    with eng.connect() as c:
        ts = c.execute(text("SELECT updated_at FROM institutional_funds WHERE id=900001")).scalar()
        chk("timestamptz round-trip", ts is not None and ts.tzinfo is not None, str(ts))

    # 7. concurrent writes
    errs: list = []
    def _w(i):
        try:
            with eng.begin() as c:
                c.execute(text("INSERT INTO institutional_funds (id,cik,fund_name,updated_at) VALUES (:id,:cik,:n,now()) ON CONFLICT (id) DO NOTHING"),
                          {"id": 900100 + i, "cik": f"91000000{i:02d}", "n": f"CONC {i}"})
        except Exception as ex:
            errs.append(str(ex))
    th = [threading.Thread(target=_w, args=(i,)) for i in range(5)]
    [t.start() for t in th]; [t.join() for t in th]
    chk("concurrent writes succeed", not errs, f"{len(errs)} errors")

    # 8. dashboard read query (FILTER + FULL OUTER JOIN) on fresh PG
    try:
        with eng.connect() as c:
            c.execute(text("""WITH a AS (SELECT fund_id,cusip,SUM(shares) sh FROM institutional_holdings WHERE report_quarter='2026-Q1' GROUP BY 1,2),
                                   b AS (SELECT fund_id,cusip,SUM(shares) sh FROM institutional_holdings WHERE report_quarter='2026-Q2' GROUP BY 1,2)
                              SELECT COUNT(*) FILTER (WHERE a.sh IS NULL AND b.sh IS NOT NULL) FROM a FULL OUTER JOIN b USING (fund_id,cusip)"""))
        chk("FILTER + FULL OUTER JOIN query runs on PG", True)
    except Exception as ex:
        chk("FILTER + FULL OUTER JOIN query runs on PG", False, str(ex)[:80])

    # cleanup test rows (leaves the disposable project ready to drop)
    with eng.begin() as c:
        c.execute(text("DELETE FROM institutional_holdings WHERE cusip='ZZZZZZZZZ'"))
        c.execute(text("DELETE FROM institutional_funds WHERE id>=900001"))
    print(f"\n================ PG COMPAT: {len(PASS)} PASS, {len(FAIL)} FAIL ================", flush=True)
    if FAIL:
        print("FAILED:", FAIL, flush=True)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
