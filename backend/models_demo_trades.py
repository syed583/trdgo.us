"""Demo (paper) trades opened from the intraday model signals.

Each BUY/SELL signal on an intraday board opens ONE 100-share demo position per
(strategy, symbol, trading day). It is tracked live (last price -> P&L) until it
hits its stop, its target, a score drop, or the market close (timed exit). This
is paper only -- no real orders, and it never touches the Earnings AI tables.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (Boolean, Column, Date, DateTime, Float, Integer, String, Text,
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


class IntradaySignalAudit(Base):
    """Signal verification (audit only): the independent re-computation of one BUY / SELL signal and its data verdict."""
    __tablename__ = "intraday_signal_audit"
    __table_args__ = (Index("ix_intraday_signal_audit_day", "trading_date", "strategy"),)

    id = Column(Integer, primary_key=True)
    ts = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    strategy = Column(String(24), nullable=False)
    symbol = Column(String(16), nullable=False)
    trading_date = Column(Date, nullable=False)
    session_type = Column(String(12), nullable=False)
    direction = Column(String(8), nullable=False)
    score = Column(Float, nullable=True)                 # as reported by the strategy
    recomputed_score = Column(Float, nullable=True)      # independently re-derived
    score_diff = Column(Float, nullable=True)
    entry_price = Column(Float, nullable=True)
    verdict = Column(String(8), nullable=False)          # PASS / FAIL (integrity of the score)
    data_status = Column(String(16), nullable=False)     # DATA_OK / DATA_THIN / DATA_UNVERIFIED
    failed_checks = Column(Text, nullable=True)          # JSON list
    checks = Column(Text, nullable=True)                 # JSON: every check and its result
    breakdown = Column(Text, nullable=True)              # JSON: weight, bias, points, expected points per parameter (internal only)
    data = Column(Text, nullable=True)                   # JSON: coverage, categories, ages
    legacy_opened = Column(Boolean, nullable=True)       # did the active workflow open a paper trade from it
    trade_id = Column(Integer, nullable=True)
    legacy_held = Column(Boolean, nullable=True)
    attribution_ok = Column(Boolean, nullable=True)      # the opened trade is this strategy's / symbol's / side's signal at this entry and score
    attribution_detail = Column(Text, nullable=True)
