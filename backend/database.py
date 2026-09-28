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

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=3,
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