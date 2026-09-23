"""
Every buy or sell call the app makes, kept.

A score shown on a screen and then forgotten cannot be checked. This morning's
board named eighteen names and nobody could say afterwards what each one had
been scored on, what price it was called at, or whether the reasons held up --
the numbers were recomputed on the next pass and the old ones were gone.

So each call is written down at the moment it is made: the decision, the
score, the confidence, every parameter with its points and the evidence behind
them, the plain reasons, and the price of the stock and of SPY at that moment.
The outcome columns are filled in later by the scorecard, which is what turns
"Conf 85%" into a measured hit rate.

Rows are never updated in place except to add their outcome. A call that is
rescored is a new row; overwriting the old one would erase exactly the history
the scorecard needs.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Column, Date, DateTime, Float, Index, Integer, String, Text,
)

from database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# The horizon a call is about. Scored separately because the right inputs and
# the right way to judge the call differ: a Today call is right or wrong by the
# close, a Tomorrow call by the next session, a Swing call over weeks.
HORIZONS = ("TODAY", "TOMORROW", "SWING")


class TradeCall(Base):
    __tablename__ = "trade_calls"

    id = Column(Integer, primary_key=True)

    symbol = Column(String(16), nullable=False)
    horizon = Column(String(12), nullable=False, default="SWING")
    # Where the call was made: a deliberate analysis run, or the board's scan.
    origin = Column(String(16), nullable=False, default="analysis")

    made_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    # The trading session the call was made in (US/Eastern date), and the
    # session it is about -- the same day for Today, the next for Tomorrow.
    session_date = Column(Date, nullable=True)
    target_date = Column(Date, nullable=True)
    market_session = Column(String(16), nullable=True)

    decision = Column(String(24), nullable=False)
    direction_score = Column(Float, nullable=True)
    confidence = Column(Float, nullable=True)
    agreement_pct = Column(Float, nullable=True)
    coverage_pct = Column(Float, nullable=True)
    actionable = Column(Integer, nullable=True)

    price_at_call = Column(Float, nullable=True)
    spy_at_call = Column(Float, nullable=True)

    # The working, as JSON: every parameter with its points, detail and
    # evidence; the bullish and bearish reasons; why a call was withheld.
    parameters = Column(Text, nullable=True)
    reasons = Column(Text, nullable=True)
    blocked_reasons = Column(Text, nullable=True)
    # A plain-English summary, when one has been written.
    explanation = Column(Text, nullable=True)
    model_version = Column(String(32), nullable=True)

    # --- outcome, filled in by the scorecard ------------------------------
    price_close = Column(Float, nullable=True)
    spy_close = Column(Float, nullable=True)
    next_open = Column(Float, nullable=True)
    next_close = Column(Float, nullable=True)
    spy_next_close = Column(Float, nullable=True)
    return_pct = Column(Float, nullable=True)
    spy_return_pct = Column(Float, nullable=True)
    excess_pct = Column(Float, nullable=True)
    # 1 right, 0 wrong, null not yet judged or not a directional call.
    correct = Column(Integer, nullable=True)
    evaluated_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_trade_calls_symbol_made", "symbol", "made_at"),
        Index("ix_trade_calls_horizon_target", "horizon", "target_date"),
        Index("ix_trade_calls_unevaluated", "evaluated_at", "target_date"),
    )


def create_all(engine) -> list[str]:
    Base.metadata.create_all(bind=engine, tables=[TradeCall.__table__])
    return [TradeCall.__tablename__]
