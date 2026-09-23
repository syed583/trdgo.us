"""
Institutional holdings from the SEC's Form 13F bulk datasets.

Deliberately separate from Form 4 insider activity and 13D/13G ownership.
Those answer "is someone close to the company trading" and "does anyone own
more than five percent"; this answers "are professional managers, in
aggregate, adding or trimming". Different questions, different cadences, so
different tables.

The cadence matters most. A 13F is filed up to 45 days after the quarter it
describes, so the freshest possible reading is six weeks stale and can be a
full quarter old. It is confirmation of a trend, never a trigger.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger, Column, Date, DateTime, Float, ForeignKey, Index, Integer,
    String, Text, UniqueConstraint,
)

from database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class InstitutionalFund(Base):
    """One filing manager. CIK is the stable identity; names get restyled."""

    __tablename__ = "institutional_funds"

    id = Column(Integer, primary_key=True, index=True)
    cik = Column(String(20), unique=True, nullable=False, index=True)
    fund_name = Column(String(255), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow,
                        onupdate=_utcnow, nullable=False)


class InstitutionalHolding(Base):
    """
    One fund's position in one security for one reported quarter.

    Rows are share positions only: option lines (PUTCALL set) and principal
    amounts (SSHPRNAMTTYPE 'PRN') are dropped during ingest, because summing
    them into a share count produces a number that means nothing.
    """

    __tablename__ = "institutional_holdings"

    id = Column(BigInteger, primary_key=True, index=True)
    fund_id = Column(Integer, ForeignKey("institutional_funds.id"),
                     nullable=False, index=True)

    # Ticker is resolved from CUSIP and may be unknown; CUSIP is always present
    # and is what the raw filing actually identifies.
    ticker = Column(String(20), nullable=True, index=True)
    cusip = Column(String(12), nullable=False, index=True)
    issuer_name = Column(String(255), nullable=True)

    shares = Column(Float, nullable=True)
    position_value = Column(Float, nullable=True)

    report_quarter = Column(String(10), nullable=False, index=True)  # 2026-Q1
    filing_date = Column(Date, nullable=True)

    __table_args__ = (
        # One position per fund, security and quarter. Amendments overwrite
        # rather than accumulate, which is what stops a restated filing from
        # doubling a fund's stake.
        UniqueConstraint("fund_id", "cusip", "report_quarter",
                         name="uq_holding_fund_cusip_quarter"),
        Index("ix_holdings_ticker_quarter", "ticker", "report_quarter"),
        Index("ix_holdings_cusip_quarter", "cusip", "report_quarter"),
    )


class InstitutionalActivity(Base):
    """
    Pre-computed quarter-over-quarter change for one ticker.

    Derived from the holdings table rather than queried live: comparing two
    quarters across millions of rows is a heavy aggregate, and the answer only
    changes when a new quarter is ingested.
    """

    __tablename__ = "institutional_activity"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String(20), nullable=False, index=True)
    report_quarter = Column(String(10), nullable=False)
    previous_quarter = Column(String(10), nullable=True)

    funds_increasing = Column(Integer, nullable=True)
    funds_decreasing = Column(Integer, nullable=True)
    funds_unchanged = Column(Integer, nullable=True)
    new_positions = Column(Integer, nullable=True)
    closed_positions = Column(Integer, nullable=True)
    total_funds = Column(Integer, nullable=True)

    total_shares_current = Column(Float, nullable=True)
    total_shares_previous = Column(Float, nullable=True)
    net_share_change = Column(Float, nullable=True)
    net_share_change_pct = Column(Float, nullable=True)

    institutional_score = Column(Float, nullable=True)
    signal = Column(String(16), nullable=True)

    # Top movers as JSON. Recomputing them means joining thousands of holdings
    # across two quarters, which is seconds of work for an answer that only
    # changes once a quarter.
    top_buyers = Column(Text, nullable=True)
    top_sellers = Column(Text, nullable=True)

    computed_at = Column(DateTime(timezone=True), default=_utcnow,
                         nullable=False)

    __table_args__ = (
        UniqueConstraint("ticker", "report_quarter",
                         name="uq_activity_ticker_quarter"),
    )


class InstitutionalSecurity(Base):
    """
    One row per security we hold detail for: CUSIP, issuer name, holder count.

    Exists purely so resolving a ticker to a CUSIP is a lookup against a few
    hundred rows instead of a grouped scan of a million holdings. That scan
    was taking twenty seconds per search.
    """

    __tablename__ = "institutional_securities"

    id = Column(Integer, primary_key=True, index=True)
    cusip = Column(String(12), unique=True, nullable=False, index=True)
    issuer_name = Column(String(255), nullable=True)
    # Normalised form of the issuer name, matched against the SEC registry.
    match_name = Column(String(255), nullable=True, index=True)
    ticker = Column(String(20), nullable=True, index=True)
    holders = Column(Integer, nullable=True)
    updated_at = Column(DateTime(timezone=True), default=_utcnow,
                        onupdate=_utcnow, nullable=False)


class InstitutionalIngest(Base):
    """
    What has already been loaded.

    Each dataset is ~90MB and takes minutes to parse, so re-ingesting one by
    accident is expensive. This is the record that stops it.
    """

    __tablename__ = "institutional_ingest"

    id = Column(Integer, primary_key=True, index=True)
    dataset = Column(String(120), unique=True, nullable=False, index=True)
    report_quarter = Column(String(10), nullable=True, index=True)
    submissions = Column(Integer, nullable=True)
    holdings = Column(Integer, nullable=True)
    skipped_options = Column(Integer, nullable=True)
    skipped_non_share = Column(Integer, nullable=True)
    status = Column(String(24), nullable=True)
    detail = Column(String(500), nullable=True)
    ingested_at = Column(DateTime(timezone=True), default=_utcnow,
                         nullable=False)


def create_all(engine) -> list[str]:
    tables = [
        InstitutionalFund.__table__,
        InstitutionalHolding.__table__,
        InstitutionalActivity.__table__,
        InstitutionalSecurity.__table__,
        InstitutionalIngest.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=tables)
    return [t.name for t in tables]
