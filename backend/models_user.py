"""
Personal-workspace tables: watchlist, alerts, trade journal, strategy config.

Deliberately a separate module from models.py. The market-data models there
are fed by providers and are not ours to churn; these are user-owned records
for a single personal operator. Nothing here is imported by the scoring
services, so adding to this file cannot affect a score.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Integer, Numeric, String, Text,
    UniqueConstraint,
)

from database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (UniqueConstraint("symbol", name="uq_watchlist_symbol"),)

    id = Column(Integer, primary_key=True)
    symbol = Column(String(16), nullable=False, index=True)
    note = Column(Text, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class AlertRule(Base):
    """
    An in-app alert definition. Evaluation is on demand (the UI asks the
    backend to check); nothing here sends an email or an SMS.
    """

    __tablename__ = "alert_rules"

    id = Column(Integer, primary_key=True)
    symbol = Column(String(16), nullable=False, index=True)
    kind = Column(String(32), nullable=False)     # SCORE / CONFIDENCE / PRICE / EXPECTED_MOVE
    comparator = Column(String(8), nullable=False, default=">=")   # >= or <=
    threshold = Column(Numeric(18, 4), nullable=False)
    note = Column(Text, nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    last_triggered_at = Column(DateTime(timezone=True), nullable=True)
    last_value = Column(Numeric(18, 4), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class JournalEntry(Base):
    __tablename__ = "journal_entries"

    id = Column(Integer, primary_key=True)
    trade_date = Column(Date, nullable=False, index=True)
    symbol = Column(String(16), nullable=False, index=True)
    direction = Column(String(8), nullable=False)          # LONG / SHORT
    entry_price = Column(Numeric(18, 4), nullable=True)
    exit_price = Column(Numeric(18, 4), nullable=True)
    stop_price = Column(Numeric(18, 4), nullable=True)
    target_price = Column(Numeric(18, 4), nullable=True)
    quantity = Column(Numeric(18, 4), nullable=True)
    result = Column(String(16), nullable=True)             # WIN / LOSS / FLAT / OPEN
    pnl = Column(Numeric(18, 4), nullable=True)
    notes = Column(Text, nullable=True)
    # Captured at entry so a review reflects what was known then, not now.
    score_at_entry = Column(Numeric(8, 2), nullable=True)
    confidence_at_entry = Column(Numeric(8, 2), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class StrategySetting(Base):
    """Key/value store for the personal TRDGO strategy configuration."""

    __tablename__ = "strategy_settings"
    __table_args__ = (UniqueConstraint("key", name="uq_strategy_key"),)

    id = Column(Integer, primary_key=True)
    key = Column(String(64), nullable=False, index=True)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


def create_all(engine) -> list[str]:
    """Create only these tables; existing market-data tables are untouched."""
    tables = [
        WatchlistItem.__table__, AlertRule.__table__,
        JournalEntry.__table__, StrategySetting.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=tables)
    return [t.name for t in tables]


class AppUser(Base):
    """
    A login account. The admin (the owner) is not stored here -- it logs in
    with ACCESS_PASSWORD -- so every row here is an invited, non-admin user
    the admin created. Passwords are never stored in the clear: only a scrypt
    hash and its per-user salt.
    """

    __tablename__ = "app_users"

    id = Column(Integer, primary_key=True)
    username = Column(String(40), nullable=False, unique=True, index=True)
    password_hash = Column(String(256), nullable=False)
    salt = Column(String(64), nullable=False)
    role = Column(String(16), nullable=False, default="user")
    active = Column(Boolean, nullable=False, default=True)
    # Full access lets a user run analysis and change data. Off by default: a
    # new account is view-only until the admin grants it from the Users panel.
    full_access = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    last_login_ip = Column(String(64), nullable=True)
    login_count = Column(Integer, nullable=False, default=0)


class LoginEvent(Base):
    """One login attempt: who, from where, and whether it succeeded."""

    __tablename__ = "login_events"

    id = Column(Integer, primary_key=True)
    username = Column(String(40), nullable=False, index=True)
    ok = Column(Boolean, nullable=False, default=False)
    ip = Column(String(64), nullable=True)
    user_agent = Column(String(256), nullable=True)
    at = Column(DateTime(timezone=True), default=_utcnow, nullable=False, index=True)
