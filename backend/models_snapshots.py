"""
Daily score snapshots, for validating the model's weights against outcomes.

Why this table exists
---------------------
The weights in the directional model are informed guesses. Nothing has tested
whether options flow deserves ten points and price action five, and most of
the model cannot be backtested historically: the options provider keeps
fifteen days of prints, the SEC code fetches "latest" rather than "as of
date", and estimates are current-only. Roughly sixty of the hundred points
have no reconstructable past.

So the past gets built going forward instead. Every score computed is stored
with the reading each parameter gave at the time, and the forward return is
filled in once enough sessions have passed. After a few months that is real
out-of-sample evidence: which parameters actually preceded a move, and by how
much.

Storing the parameters individually is the point. A record of the final score
alone can only answer "was the score right"; storing every signal answers
"which parts of it were right", which is the question a weight table needs.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, Index, Integer, String, Text,
    UniqueConstraint,
)

from database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ScoreSnapshot(Base):
    """
    One symbol's directional reading on one date, plus what happened next.

    Forward returns start null and are filled in by a later pass, because the
    answer does not exist yet when the row is written. That is the whole
    mechanism: the row is a prediction until time supplies the outcome.
    """

    __tablename__ = "score_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    snapshot_date = Column(Date, nullable=False, index=True)

    # --- the reading -----------------------------------------------------
    direction_score = Column(Float, nullable=True)
    confidence = Column(Float, nullable=True)
    decision = Column(String(20), nullable=True)
    lean = Column(String(20), nullable=True)
    actionable = Column(Boolean, nullable=True, index=True)

    coverage_pct = Column(Float, nullable=True)
    agreement_pct = Column(Float, nullable=True)
    conviction_pct = Column(Float, nullable=True)

    # Every parameter's bias and points as JSON, so a later pass can measure
    # each one separately rather than only the blend.
    signals = Column(Text, nullable=True)

    # --- what it was scored against --------------------------------------
    price = Column(Float, nullable=True)

    # --- what happened next ----------------------------------------------
    forward_5d_pct = Column(Float, nullable=True)
    forward_10d_pct = Column(Float, nullable=True)
    forward_20d_pct = Column(Float, nullable=True)
    forward_filled_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), default=_utcnow,
                        nullable=False)

    __table_args__ = (
        # One reading per symbol per day. Re-running the capture overwrites
        # rather than accumulating, so a retry does not double-weight a day.
        UniqueConstraint("symbol", "snapshot_date",
                         name="uq_snapshot_symbol_date"),
        Index("ix_snapshot_pending", "forward_filled_at", "snapshot_date"),
    )


def create_all(engine) -> list[str]:
    tables = [ScoreSnapshot.__table__]
    Base.metadata.create_all(bind=engine, tables=tables)
    return [t.name for t in tables]
