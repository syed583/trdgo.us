"""
Daily ETF holdings.

One row per ETF, per stock, per day. Small: eleven funds times a few hundred
names is a few thousand rows a day, and it is the only holdings data in the
app that updates daily rather than quarterly.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (Column, Date, DateTime, Float, Index, Integer, String,
                        UniqueConstraint)

from database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class EtfHolding(Base):
    __tablename__ = "etf_holdings"
    __table_args__ = (
        UniqueConstraint("etf", "ticker", "as_of", name="uq_etf_ticker_date"),
        Index("ix_etf_ticker_date", "ticker", "as_of"),
    )

    id = Column(Integer, primary_key=True, index=True)
    etf = Column(String(12), nullable=False, index=True)
    ticker = Column(String(20), nullable=False, index=True)
    name = Column(String(255), nullable=True)
    as_of = Column(Date, nullable=False, index=True)
    shares = Column(Float, nullable=True)
    weight_pct = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


def create_all(engine) -> None:
    Base.metadata.create_all(engine, tables=[EtfHolding.__table__])
