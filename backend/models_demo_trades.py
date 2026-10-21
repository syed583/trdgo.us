"""Demo (paper) trades opened from the intraday model signals.

Each BUY/SELL signal on an intraday board opens ONE 100-share demo position per
(strategy, symbol, trading day). It is tracked live (last price -> P&L) until it
hits its stop, its target, a score drop, or the market close (timed exit). This
is paper only -- no real orders, and it never touches the Earnings AI tables.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (Column, Date, DateTime, Float, Integer, String,
                        UniqueConstraint, Index)

from database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DemoTrade(Base):
    __tablename__ = "demo_trades"
    __table_args__ = (
        UniqueConstraint("strategy", "symbol", "session_date", name="uq_demo_strategy_symbol_day"),
        Index("ix_demo_session", "session_date"),
        Index("ix_demo_strategy_session", "strategy", "session_date"),
    )

    id = Column(Integer, primary_key=True)
    strategy = Column(String(24), nullable=False)      # grok / meta / kimi / ...
    symbol = Column(String(16), nullable=False)
    side = Column(String(5), nullable=False)           # LONG / SHORT
    shares = Column(Integer, nullable=False, default=100)
    session_date = Column(Date, nullable=False)

    entry_time = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    entry_price = Column(Float, nullable=False)
    stop = Column(Float, nullable=True)
    target = Column(Float, nullable=True)
    score_at_entry = Column(Float, nullable=True)

    last_price = Column(Float, nullable=True)
    profit = Column(Float, nullable=True)              # $ for the 100 shares
    profit_pct = Column(Float, nullable=True)

    status = Column(String(16), nullable=False, default="OPEN")  # OPEN/STOP/TARGET/TIMED_EXIT/SCORE_EXIT
    exit_time = Column(DateTime(timezone=True), nullable=True)
    exit_price = Column(Float, nullable=True)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


def tables() -> list[str]:
    return [DemoTrade.__tablename__]
