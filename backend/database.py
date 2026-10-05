import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

# SQLAlchemy defaults (pool_size 5 + max_overflow 10) let a single process open
# 15 connections, which is exactly Supabase's session-mode pooler limit -- so
# concurrent requests could exhaust it and fail with
# "(EMAXCONNSESSION) max clients reached in session mode".
# Cap well below that and wait briefly for a free connection instead of opening
# an extra one.
# Supabase's pooler drops idle connections fairly quickly; a 30-minute recycle
# left SQLAlchemy holding sockets the server had already closed, which surfaced
# as intermittent "SSL connection has been closed unexpectedly". Recycle well
# inside that window and keep the TCP link alive so a dead connection is noticed
# and replaced rather than used mid-query. pool_pre_ping still re-checks at
# checkout as the final guard.
_connect_args = {}
if (DATABASE_URL or "").startswith("postgresql"):
    _connect_args = {
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5,
    }
    # Supabase's TRANSACTION pooler (port 6543) multiplexes many clients onto a
    # few server connections -- far more headroom than the SESSION pooler's hard
    # 15-client cap (EMAXCONNSESSION). But in transaction mode a connection is
    # handed back after every transaction, so psycopg3's server-side PREPARED
    # statements (default prepare_threshold=5) break: the next statement lands on
    # a different backend that never saw the PREPARE. Disable them when talking
    # to the transaction pooler. Detected by port so switching DATABASE_URL from
    # :5432 to :6543 is all that's needed -- no code change at deploy time.
    if ":6543" in (DATABASE_URL or ""):
        _connect_args["prepare_threshold"] = None

# Pool sizes are overridable via env so a second process sharing the same
# Supabase (e.g. a local dev backend run while the VPS is live) can be given a
# tiny pool and stay under the 15-connection pooler limit instead of competing
# with production for it. Defaults match the production single-process sizing.
_pool_size = int(os.getenv("DB_POOL_SIZE") or 5)
_max_overflow = int(os.getenv("DB_MAX_OVERFLOW") or 3)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=_pool_size,
    max_overflow=_max_overflow,
    pool_timeout=30,
    pool_recycle=280,
    connect_args=_connect_args,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()