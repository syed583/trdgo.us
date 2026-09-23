"""
Cached company profiles.

Sector and share count change a few times a year at most, while the calendar
is read many times a day. Fetching them from EDGAR on every render would mean
two or three requests per company per page load for figures that are stale by
a quarter anyway, so they are stored and refreshed on a slow cadence.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Column, DateTime, Integer, Numeric, String, Text,
)

from database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CompanyProfile(Base):
    """Sector, industry and share count for one symbol, as filed with the SEC."""

    __tablename__ = "company_profiles"

    symbol = Column(String(16), primary_key=True)
    cik = Column(String(16), nullable=True)
    company_name = Column(String(200), nullable=True)

    # SIC as filed, its description, and the SIC division it falls in. Kept as
    # three fields rather than one "sector" string so the screen can show the
    # broad grouping and the specific industry without re-deriving either.
    sic = Column(String(8), nullable=True)
    industry = Column(String(160), nullable=True)
    sector = Column(String(64), nullable=True)

    shares_outstanding = Column(Numeric(28, 2), nullable=True)
    # The period the share count covers, and which XBRL concept it came from.
    # Both are shown in the UI: a market cap built on a six-month-old share
    # count should say so.
    shares_as_of = Column(String(12), nullable=True)
    shares_concept = Column(String(80), nullable=True)
    shares_status = Column(String(24), nullable=True)

    status = Column(String(32), nullable=False, default="OK")
    detail = Column(Text, nullable=True)
    fetched_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


def create_all(engine) -> list[str]:
    tables = [CompanyProfile.__table__]
    Base.metadata.create_all(bind=engine, tables=tables)
    return [t.name for t in tables]
