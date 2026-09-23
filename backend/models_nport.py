"""
Monthly fund holdings from Form N-PORT.

Kept apart from the 13F tables on purpose. They answer the same question --
who owns this stock -- but on different clocks and from different filers:
13F is quarterly and covers institutions over $100m; N-PORT is monthly and
covers registered funds, which is every mutual fund and ETF. Merging them
into one table would average two different measurements into a number that
is neither.

Only securities the app already tracks are stored. The quarterly dataset is
910MB of holdings across every fund in the country, nearly all of it bonds,
swaps and names nobody here searches.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (BigInteger, Column, Date, DateTime, Float, Index,
                        Integer, String, UniqueConstraint)

from database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class NportHolding(Base):
    """One fund's position in one security, as of one month end."""

    __tablename__ = "nport_holdings"
    __table_args__ = (
        # A fund restating a month must not double-count.
        UniqueConstraint("accession", "cusip", name="uq_nport_accession_cusip"),
        Index("ix_nport_cusip_period", "cusip", "report_date"),
    )

    id = Column(BigInteger, primary_key=True, index=True)
    accession = Column(String(25), nullable=False, index=True)
    fund_cik = Column(String(20), nullable=True, index=True)
    fund_name = Column(String(255), nullable=True)
    cusip = Column(String(12), nullable=False, index=True)
    ticker = Column(String(20), nullable=True, index=True)
    issuer_name = Column(String(255), nullable=True)
    report_date = Column(Date, nullable=False, index=True)
    shares = Column(Float, nullable=True)
    value = Column(Float, nullable=True)
    percent_of_fund = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class NportIngest(Base):
    """Which quarterly datasets have been loaded, so none is read twice."""

    __tablename__ = "nport_ingests"

    id = Column(Integer, primary_key=True, index=True)
    dataset = Column(String(120), unique=True, nullable=False, index=True)
    status = Column(String(30), nullable=False)
    holdings = Column(Integer, nullable=True)
    months = Column(String(120), nullable=True)
    detail = Column(String(500), nullable=True)
    ingested_at = Column(DateTime(timezone=True), default=_utcnow)


def create_all(engine) -> None:
    Base.metadata.create_all(
        engine, tables=[NportHolding.__table__, NportIngest.__table__])
