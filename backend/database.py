import os
import re

from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()

# --- production database is for production only --------------------------------
PROD_REF = "ddetojbocaxhcfxtcopc"          # the production Supabase project


def is_production() -> bool:
    return (os.getenv("APP_ENV") or "").strip().lower() == "production"


def _development_url():
    """The development database: LOCAL_DATABASE_URL, else DATABASE_URL in .env.dev."""
    from dotenv import dotenv_values
    url = (os.getenv("LOCAL_DATABASE_URL") or
           dotenv_values(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env.dev")).get("DATABASE_URL") or "").strip()
    if not url:
        return None
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql+psycopg") and "sslmode=" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return url


def resolve_database_url(raw, production, development_url=_development_url):
    """The URL this process may use. Production uses what it is given. Anything else
    (a developer machine, a script, a probe) is NEVER allowed to use the production
    project: it is redirected to the development database, or refused if there is none.
    Fail-closed: forgetting APP_ENV can never point a local run at production data."""
    if production or PROD_REF not in (raw or ""):
        return raw
    dev = development_url()
    if dev and PROD_REF not in dev:
        return dev
    raise RuntimeError(
        "REFUSING TO START: DATABASE_URL points at the PRODUCTION database but APP_ENV is not "
        "'production'. Local runs, tests and probes must use the development database: set "
        "LOCAL_DATABASE_URL (or DATABASE_URL in backend/.env.dev) to the dev project. "
        "(On the production server set APP_ENV=production.)")


DATABASE_URL = resolve_database_url(os.getenv("DATABASE_URL"), is_production())

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
_txn_pooler = ":6543" in (DATABASE_URL or "")
if _txn_pooler:
    _connect_args["prepare_threshold"] = None

# Pool sizes are overridable via env so a second process sharing the same
# Supabase (e.g. a local dev backend run while the VPS is live) can be given a
# tiny pool and stay under the 15-connection pooler limit instead of competing
# with production for it.
#
# Defaults depend on which pooler is in use. The SESSION pooler (5432) caps the
# whole project at 15 client connections, so stay small (5 + 3). The TRANSACTION
# pooler (6543) multiplexes many clients, so use a larger pool and keep
# connections longer -- that way requests rarely pay the slow (~seconds) cost of
# opening a brand-new pooled connection, which is the main source of slowness.
_default_size, _default_overflow = (15, 10) if _txn_pooler else (5, 3)
_pool_size = int(os.getenv("DB_POOL_SIZE") or _default_size)
_max_overflow = int(os.getenv("DB_MAX_OVERFLOW") or _default_overflow)
# The session pooler drops idle connections quickly, so recycle inside that
# window; the transaction pooler keeps them, so hold them far longer to avoid
# re-opening (slow) connections on every recycle.
_pool_recycle = 1800 if _txn_pooler else 280

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=_pool_size,
    max_overflow=_max_overflow,
    pool_timeout=30,
    pool_recycle=_pool_recycle,
    connect_args=_connect_args,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Base = declarative_base()


# --- environment isolation of saved predictions / signal history ---------------
# These tables hold history that must only ever be written by production. Outside
# production (APP_ENV != "production": a dev machine, CI, an unset variable) every
# statement that names one of them is rewritten to a "_local" twin, so local testing
# reads and writes its own copy and can never touch production history. Fail-closed:
# forgetting APP_ENV isolates, it never shares.
ISOLATED_TABLES = (
    "model_predictions", "model_backtests", "earnings_signal_snapshots", "score_snapshots",
    "ped_scores", "pe_events", "pe_predictions", "pe_grades", "pe_scheduler_state",
    "pe_pilot_state", "pe_pilot_daily", "intraday_sig_v2", "straddle_snap_v2",
    # paper trades and trade-call/plan records (ORM-managed; twins made by ensure_local_orm_tables)
    "demo_trades", "trade_calls", "trade_plans", "trade_plan_events", "trade_plan_seen",
    "intraday_signal_audit",
)
ORM_ISOLATED_TABLES = ("score_snapshots", "demo_trades", "trade_calls", "trade_plans",
                       "trade_plan_events", "trade_plan_seen")
_ISO_RE = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(ISOLATED_TABLES) + r")(?![A-Za-z0-9])")


def isolate_sql(statement: str) -> str:
    """The statement with every isolated table renamed to its "_local" twin."""
    return _ISO_RE.sub(lambda m: m.group(1) + "_local", statement)


if not is_production():
    @event.listens_for(engine, "before_cursor_execute", retval=True)
    def _isolate_history(conn, cursor, statement, parameters, context, executemany):
        return isolate_sql(statement), parameters


def ensure_local_orm_tables() -> None:
    """Outside production, make sure every ORM-managed isolated table has its "_local" twin
    (raw SQL creates the others itself). Uses a raw DBAPI cursor so the rewrite above does
    not touch the statement. A twin whose source table does not exist is simply skipped."""
    if is_production():
        return
    for table in ORM_ISOLATED_TABLES:
        raw = engine.raw_connection()
        try:
            cur = raw.cursor()
            cur.execute(f"CREATE TABLE IF NOT EXISTS {table}_local (LIKE {table} INCLUDING ALL)")
            raw.commit()
        except Exception:  # noqa: BLE001 - the source table may not exist yet
            raw.rollback()
        finally:
            raw.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
