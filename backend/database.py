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
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=3,
    pool_timeout=30,
    pool_recycle=1800,
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