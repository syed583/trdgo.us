"""
Cache tables for externally-sourced earnings data.

Separate from models.py (untouched provider snapshots) and models_user.py
(operator-owned records). Everything here is a *cache* of a third-party feed:
each row records which provider it came from and when it was fetched, so the
UI can always show provenance and staleness rather than an anonymous number.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column, Date, DateTime, Index, Integer, Numeric, String, Text,
    UniqueConstraint,
)

from database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EarningsCalendarEntry(Base):
    """One scheduled or reported earnings event from an external calendar."""

    __tablename__ = "earnings_calendar"
    __table_args__ = (
        UniqueConstraint("symbol", "earnings_date", name="uq_earnings_symbol_date"),
        Index("ix_earnings_calendar_date", "earnings_date"),
    )

    id = Column(Integer, primary_key=True)
    symbol = Column(String(16), nullable=False, index=True)
    company_name = Column(String(160), nullable=True)
    earnings_date = Column(Date, nullable=False)
    reporting_time = Column(String(8), nullable=True)     # BMO / AMC / UNKNOWN
    fiscal_period = Column(String(16), nullable=True)     # Q1 / Q2 / ...
    fiscal_year = Column(Integer, nullable=True)

    # Clock time the company reports, as the provider publishes it
    # ("06:00:00"). Kept alongside reporting_time because BMO/AMC is derived
    # from it and loses the detail: two BMO names an hour apart are not the
    # same trade.
    report_time = Column(String(8), nullable=True)
    exchange = Column(String(16), nullable=True)

    # Provider's 0-5 ranking of how much the market cares about this report.
    importance = Column(Integer, nullable=True)

    eps_estimate = Column(Numeric(18, 4), nullable=True)
    eps_actual = Column(Numeric(18, 4), nullable=True)
    eps_surprise_percent = Column(Numeric(10, 4), nullable=True)
    # The same quarter a year earlier, as the provider reports it. Preferred
    # over looking up the previous row: the provider knows which quarter is
    # the comparable one, and the previous row on file may be a different
    # fiscal period or missing entirely.
    eps_prior = Column(Numeric(18, 4), nullable=True)

    revenue_estimate = Column(Numeric(24, 2), nullable=True)
    revenue_actual = Column(Numeric(24, 2), nullable=True)
    revenue_surprise_percent = Column(Numeric(10, 4), nullable=True)
    revenue_prior = Column(Numeric(24, 2), nullable=True)

    # Close-to-close move on the first session the market could react to the
    # report, and the drift over the sessions after that. Two separate things:
    # the first is the gap the report caused, the second is whether the market
    # kept going in the same direction once it had read the filing.
    post_earnings_move_percent = Column(Numeric(10, 4), nullable=True)
    post_earnings_drift_percent = Column(Numeric(10, 4), nullable=True)
    # Which session the move was measured against, so a figure can be checked.
    reaction_date = Column(String(12), nullable=True)

    # Lifecycle: SCHEDULED / PRE_READY / RESULT_DETECTED / PARTIAL_RESULT /
    # OFFICIAL_VERIFIED / POST_SCORE_READY / ENTRY_MONITORING /
    # TRADE_READY / NO_TRADE
    lifecycle = Column(String(24), nullable=False, default="SCHEDULED")

    source = Column(String(32), nullable=False, default="UNKNOWN")
    data_status = Column(String(32), nullable=False, default="OK")
    fetched_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    raw = Column(Text, nullable=True)


class EstimateRevision(Base):
    """
    A point-in-time view of consensus estimates for one symbol.

    One row per (symbol, horizon) where horizon is 0/7/30/60/90 days ago, so a
    revision trend can be read without inventing the windows a provider does
    not supply - a missing window is simply an absent row.
    """

    __tablename__ = "estimate_revisions"
    __table_args__ = (
        UniqueConstraint("symbol", "horizon_days", "fiscal_period",
                         name="uq_revision_symbol_horizon"),
    )

    id = Column(Integer, primary_key=True)
    symbol = Column(String(16), nullable=False, index=True)
    horizon_days = Column(Integer, nullable=False)     # 0, 7, 30, 60, 90
    fiscal_period = Column(String(16), nullable=True)

    eps_estimate = Column(Numeric(18, 4), nullable=True)
    revenue_estimate = Column(Numeric(24, 2), nullable=True)
    analyst_count = Column(Integer, nullable=True)
    eps_high = Column(Numeric(18, 4), nullable=True)
    eps_low = Column(Numeric(18, 4), nullable=True)
    eps_mean = Column(Numeric(18, 4), nullable=True)

    source = Column(String(32), nullable=False, default="UNKNOWN")
    data_status = Column(String(32), nullable=False, default="OK")
    fetched_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class ProviderFetchLog(Base):
    """
    When each provider/endpoint was last called.

    This is what stops a page view turning into a provider call: services check
    the log first and only refetch once the TTL has elapsed.
    """

    __tablename__ = "provider_fetch_log"
    __table_args__ = (
        UniqueConstraint("provider", "endpoint", "scope", name="uq_fetch_key"),
    )

    id = Column(Integer, primary_key=True)
    provider = Column(String(32), nullable=False)
    endpoint = Column(String(64), nullable=False)
    scope = Column(String(64), nullable=False, default="")
    status = Column(String(32), nullable=False, default="OK")
    detail = Column(Text, nullable=True)
    rows = Column(Integer, nullable=True)
    fetched_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


def create_all(engine) -> list[str]:
    tables = [
        EarningsCalendarEntry.__table__,
        EstimateRevision.__table__,
        ProviderFetchLog.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=tables)
    ensure_columns(engine)
    return [t.name for t in tables]


# Columns added after the table was first created. ``create_all`` only creates
# missing *tables*; it never alters an existing one, so a new attribute on the
# model shows up as "column does not exist" at query time instead.
_ADDED_COLUMNS = (
    ("earnings_calendar", "report_time", "VARCHAR(8)"),
    ("earnings_calendar", "exchange", "VARCHAR(16)"),
    ("earnings_calendar", "importance", "INTEGER"),
    ("earnings_calendar", "eps_prior", "NUMERIC(18, 4)"),
    ("earnings_calendar", "revenue_prior", "NUMERIC(24, 2)"),
    ("earnings_calendar", "post_earnings_drift_percent", "NUMERIC(10, 4)"),
    ("earnings_calendar", "reaction_date", "VARCHAR(12)"),
)


def ensure_columns(engine) -> None:
    """Add any column the model gained since the table was created."""
    from sqlalchemy import text

    with engine.begin() as conn:
        for table, column, ddl in _ADDED_COLUMNS:
            conn.execute(text(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}"))
