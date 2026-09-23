from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    Date,
    Numeric,
    ForeignKey
)
from sqlalchemy.sql import func

from database import Base


# =========================================================
# COMPANY
# =========================================================

class Company(Base):
    __tablename__ = "companies"

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    symbol = Column(
        String(20),
        unique=True,
        nullable=False,
        index=True
    )

    company_name = Column(
        String(255),
        nullable=False
    )

    exchange = Column(
        String(50),
        nullable=True
    )

    sector = Column(
        String(100),
        nullable=True
    )

    industry = Column(
        String(150),
        nullable=True
    )

    currency = Column(
        String(10),
        default="USD"
    )

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now()
    )


# =========================================================
# EARNINGS EVENT
# =========================================================

class EarningsEvent(Base):
    __tablename__ = "earnings_events"

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    company_id = Column(
        Integer,
        ForeignKey("companies.id"),
        nullable=False,
        index=True
    )

    earnings_date = Column(
        Date,
        nullable=False,
        index=True
    )

    reporting_time = Column(
        String(20),
        nullable=True
    )

    eps_estimate = Column(
        Numeric(18, 6),
        nullable=True
    )

    eps_actual = Column(
        Numeric(18, 6),
        nullable=True
    )

    revenue_estimate = Column(
        Numeric(24, 2),
        nullable=True
    )

    revenue_actual = Column(
        Numeric(24, 2),
        nullable=True
    )

    status = Column(
        String(30),
        default="scheduled"
    )

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now()
    )


# =========================================================
# PRICE BAR
# =========================================================

class PriceBar(Base):
    __tablename__ = "price_bars"

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    company_id = Column(
        Integer,
        ForeignKey("companies.id"),
        nullable=False,
        index=True
    )

    timeframe = Column(
        String(20),
        nullable=False,
        index=True
    )

    timestamp = Column(
        DateTime(timezone=True),
        nullable=False,
        index=True
    )

    open = Column(
        Numeric(18, 6),
        nullable=False
    )

    high = Column(
        Numeric(18, 6),
        nullable=False
    )

    low = Column(
        Numeric(18, 6),
        nullable=False
    )

    close = Column(
        Numeric(18, 6),
        nullable=False
    )

    volume = Column(
        Numeric(24, 2),
        nullable=True
    )

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now()
    )


# =========================================================
# ESTIMATE SNAPSHOT
# =========================================================

class EstimateSnapshot(Base):
    __tablename__ = "estimate_snapshots"

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    company_id = Column(
        Integer,
        ForeignKey("companies.id"),
        nullable=False,
        index=True
    )

    earnings_event_id = Column(
        Integer,
        ForeignKey("earnings_events.id"),
        nullable=True,
        index=True
    )

    eps_estimate = Column(
        Numeric(18, 6),
        nullable=True
    )

    revenue_estimate = Column(
        Numeric(24, 2),
        nullable=True
    )

    analyst_count = Column(
        Integer,
        nullable=True
    )

    snapshot_time = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True
    )

    source = Column(
        String(50),
        nullable=True
    )

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    # =========================================================
# FUNDAMENTAL SNAPSHOT
# =========================================================

class FundamentalSnapshot(Base):
    __tablename__ = "fundamental_snapshots"

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    company_id = Column(
        Integer,
        ForeignKey("companies.id"),
        nullable=False,
        index=True
    )

    fiscal_period = Column(
        String(20),
        nullable=True
    )

    fiscal_year = Column(
        Integer,
        nullable=True
    )

    revenue = Column(
        Numeric(24, 2),
        nullable=True
    )

    net_income = Column(
        Numeric(24, 2),
        nullable=True
    )

    eps_diluted = Column(
        Numeric(18, 6),
        nullable=True
    )

    total_assets = Column(
        Numeric(24, 2),
        nullable=True
    )

    total_liabilities = Column(
        Numeric(24, 2),
        nullable=True
    )

    cash = Column(
        Numeric(24, 2),
        nullable=True
    )

    debt = Column(
        Numeric(24, 2),
        nullable=True
    )

    operating_cash_flow = Column(
        Numeric(24, 2),
        nullable=True
    )

    capex = Column(
        Numeric(24, 2),
        nullable=True
    )

    source = Column(
        String(50),
        nullable=True
    )

    snapshot_time = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True
    )

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now()
    )