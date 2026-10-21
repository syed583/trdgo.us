"""Institutional Intelligence schema migration 001 (version-controlled).

PREPARED, NOT EXECUTED in Phase 7 local work. Running it requires an approved
institutional target in INSTITUTIONAL_DATABASE_URL and explicit approval.

It is idempotent and affects ONLY the institutional database:
* refuses the Earnings AI production ref and any ref not on the institutional
  allow-list (reuses institutional_dashboard_service's guard),
* creates the model-backed tables (institutional_funds / holdings / activity /
  securities / ingest) via SQLAlchemy create_all,
* creates institutional_cusip_map (not an ORM model) explicitly,
* creates the additive performance indexes.

Usage (ONLY on approval, against an approved institutional DB):
    INSTITUTIONAL_DATABASE_URL=... python migrations/institutional/001_institutional_schema.py --apply
Without --apply it prints the plan (dry run) and connects to nothing.
"""
from __future__ import annotations

import os
import sys

CUSIP_MAP_DDL = """
CREATE TABLE IF NOT EXISTS institutional_cusip_map (
    cusip             varchar PRIMARY KEY,
    sec_issuer_name   text,
    ticker            varchar,
    security_type     varchar,
    exchange          varchar,
    share_class_figi  varchar,
    figi              varchar,
    mapping_source    varchar,
    confidence        varchar,
    status            varchar,
    reason            text,
    is_current        boolean DEFAULT true,
    verified_at       timestamptz
);
CREATE INDEX IF NOT EXISTS ix_cusip_map_ticker ON institutional_cusip_map (ticker);
CREATE INDEX IF NOT EXISTS ix_cusip_map_confidence ON institutional_cusip_map (confidence);
"""

PERF_INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_ih_cusip_q  ON institutional_holdings (cusip, report_quarter)",
    "CREATE INDEX IF NOT EXISTS ix_ih_fund_q   ON institutional_holdings (fund_id, report_quarter)",
    "CREATE INDEX IF NOT EXISTS ix_ih_q        ON institutional_holdings (report_quarter)",
    "CREATE INDEX IF NOT EXISTS ix_ih_filing   ON institutional_holdings (filing_date)",
]


def plan() -> list[str]:
    return (["create_all: institutional_funds, institutional_holdings, "
             "institutional_activity, institutional_securities, institutional_ingest"]
            + [l.strip() for l in CUSIP_MAP_DDL.strip().splitlines() if l.strip().startswith(("CREATE",))]
            + PERF_INDEXES)


def apply() -> None:
    # Reuse the institutional guard: allow-list + deny the Earnings AI prod ref.
    import institutional_dashboard_service as ins
    ins._engine.cache_clear()
    engine = ins._engine()  # raises if the target is not an approved institutional DB
    import models_institutional  # noqa: F401 - registers the ORM tables
    from database import Base
    from sqlalchemy import text

    inst_tables = [t for n, t in Base.metadata.tables.items() if n.startswith("institutional_")]
    Base.metadata.create_all(engine, tables=inst_tables)
    with engine.begin() as c:
        c.execute(text("SET statement_timeout='60000'"))
        for stmt in CUSIP_MAP_DDL.strip().split(";"):
            if stmt.strip():
                c.execute(text(stmt))
        for stmt in PERF_INDEXES:
            c.execute(text(stmt))
    print("migration 001 applied to the institutional database.")


if __name__ == "__main__":
    if "--apply" in sys.argv:
        apply()
    else:
        print("DRY RUN — migration 001 plan (no connection made):")
        for step in plan():
            print("  -", step)
        print("\nRun with --apply against an APPROVED INSTITUTIONAL_DATABASE_URL only.")
