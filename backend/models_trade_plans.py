"""
Every trade plan the app issues, kept so its outcome can be read back.

A plan names levels -- entry, do-not-chase, stop, targets -- and a window they
are valid for. A level shown once and recomputed on the next refresh can never
be checked: did price reach the entry, did it hit the target or the stop, or did
it just expire untouched? So each issued plan is written down once per
symbol/horizon/session, and a later pass fills in what happened.

Rows are updated only to advance their status and record the outcome. A new
session's plan is a new row.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Column, Date, DateTime, Float, Index, Integer, String, Text,
)

from database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# How a plan can end up. PENDING: issued, price has not entered the zone.
# ACTIVE: price traded into/through the entry zone. Then one of the terminals.
STATUSES = ("PENDING", "ACTIVE", "TP1_HIT", "TP2_HIT", "SL_HIT",
            "EXPIRED", "INVALIDATED")


class TradePlan(Base):
    __tablename__ = "trade_plans"

    id = Column(Integer, primary_key=True)

    symbol = Column(String(16), nullable=False)
    horizon = Column(String(12), nullable=False, default="SWING")
    session_date = Column(Date, nullable=True)

    issued_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)

    bias = Column(String(8), nullable=False)          # LONG / SHORT
    decision = Column(String(24), nullable=True)
    direction_score = Column(Float, nullable=True)
    confidence = Column(Float, nullable=True)

    spot_at_issue = Column(Float, nullable=True)
    entry_low = Column(Float, nullable=True)
    entry_high = Column(Float, nullable=True)
    do_not_chase = Column(Float, nullable=True)
    stop = Column(Float, nullable=True)
    tp1 = Column(Float, nullable=True)
    tp2 = Column(Float, nullable=True)
    reward_risk = Column(Float, nullable=True)

    read = Column(Text, nullable=True)

    # --- lifecycle, advanced by evaluate_plans ----------------------------
    status = Column(String(16), nullable=False, default="PENDING")
    # How far the plan got, so a closed plan still shows what it did.
    entered = Column(Integer, nullable=True)          # 1 once price hit the zone
    last_price = Column(Float, nullable=True)
    best_price = Column(Float, nullable=True)         # most favourable reached
    outcome_note = Column(Text, nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, nullable=True)

    __table_args__ = (
        Index("ix_trade_plans_symbol_issued", "symbol", "issued_at"),
        Index("ix_trade_plans_open", "status", "expires_at"),
    )


# The kinds of change worth a notification.
EVENT_TYPES = ("BIAS_FLIP", "NEW_SETUP", "TP1_HIT", "TP2_HIT", "SL_HIT",
               "EXPIRED", "INVALIDATED")


class TradePlanEvent(Base):
    """One change in a stock's plan -- a view flip, a new setup, a hit stop/target."""
    __tablename__ = "trade_plan_events"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    symbol = Column(String(16), nullable=False)
    horizon = Column(String(12), nullable=False, default="SWING")
    type = Column(String(16), nullable=False)
    from_bias = Column(String(10), nullable=True)
    to_bias = Column(String(10), nullable=True)
    price = Column(Float, nullable=True)
    note = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_tp_events_created", "created_at"),
        Index("ix_tp_events_symbol", "symbol", "created_at"),
    )


class TradePlanSeen(Base):
    """Per-user 'last looked at the signals feed', for the unread badge."""
    __tablename__ = "trade_plan_seen"

    owner = Column(String(64), primary_key=True)
    seen_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


def create_all(engine) -> list[str]:
    tables = [TradePlan.__table__, TradePlanEvent.__table__, TradePlanSeen.__table__]
    Base.metadata.create_all(bind=engine, tables=tables)
    return [t.name for t in tables]
