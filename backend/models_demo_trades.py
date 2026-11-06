"""Demo (paper) trades opened from the intraday model signals.

Each BUY/SELL signal on an intraday board opens ONE 100-share demo position per
(strategy, symbol, trading day). It is tracked live (last price -> P&L) until it
hits its stop, its target, a score drop, or the market close (timed exit). This
is paper only -- no real orders, and it never touches the Earnings AI tables.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import declared_attr, deferred
from sqlalchemy import (Boolean, Column, Date, DateTime, Float, Integer, String, Text, Index, func, text)

from database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _TradeColumns:
    """Columns shared by the live paper book (demo_trades) and the shadow book (intraday_shadow_trades)."""
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

    # OPEN / STOP / TARGET / SCORE_EXIT / EOD_CLOSE / CLEANUP / NO_PRICE / TIMED_EXIT (older rows) / MARKET_CLOSE /
    # PENDING_RECON (workflow v2)
    status = Column(String(16), nullable=False, default="OPEN")
    exit_time = Column(DateTime(timezone=True), nullable=True)
    exit_price = Column(Float, nullable=True)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    # Entry evidence of the pure-intraday tracker (all nullable: rows written before it keep NULL, nothing is invented).
    entry_bid = Column(Float, nullable=True)
    entry_ask = Column(Float, nullable=True)
    entry_spread_pct = Column(Float, nullable=True)
    entry_quote_age_s = Column(Float, nullable=True)
    be_moved = Column(Boolean, nullable=True)              # the breakeven trail moved the stop to the entry
    entry_evidence = Column(Text, nullable=True)           # JSON: every parameter reading, coverage %, score, quote, sizing


TRACKER_COLUMNS: dict[str, str] = {
    "entry_bid": "FLOAT", "entry_ask": "FLOAT", "entry_spread_pct": "FLOAT", "entry_quote_age_s": "FLOAT",
    "be_moved": "BOOLEAN", "entry_evidence": "TEXT",
}


def ensure_tracker_columns(engine, table: str = "demo_trades") -> list[str]:
    """Add the tracker's evidence columns to an EXISTING table (``create(checkfirst=True)`` never alters one). Nullable
    columns only: an older build ignores them, so this is safe to run before a deploy and harmless on rollback. Returns the
    columns added."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return []
    have = {c["name"] for c in insp.get_columns(table)}
    added = []
    with engine.begin() as conn:
        for col, typ in TRACKER_COLUMNS.items():
            if col not in have:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {typ}"))
                added.append(col)
    return added


class _EvidenceColumns:
    """Workflow v2 evidence (all nullable: historical rows keep NULL, never invented values). Present on the shadow book and, for V2
    activation only, on demo_trades -- where the LEGACY mapper (DemoTrade) deliberately does not map them, so Legacy, shadow and audit
    mode neither read nor write them and work on a demo_trades table that does not have them."""
    entry_session = Column(String(12), nullable=True)      # PREMARKET / REGULAR
    signal_event_id = Column(Integer, nullable=True)
    snapshot_id = Column(String(40), nullable=True)
    entry_quote = Column(Text, nullable=True)              # JSON: bid, ask, last, spread %, ages, source
    risk_checks = Column(Text, nullable=True)              # JSON: every limit and its reading at entry
    validation = Column(Text, nullable=True)               # JSON: data, coverage, confirmation, liquidity results
    exit_price_source = Column(String(24), nullable=True)  # board / quote / official_close / none
    fill_assumption = Column(String(160), nullable=True)   # the documented fill policy that produced the exit price


V2_EVIDENCE_COLUMNS = ("entry_session", "signal_event_id", "snapshot_id", "entry_quote", "risk_checks", "validation",
                       "exit_price_source", "fill_assumption")


class DemoTrade(_TradeColumns, _EvidenceColumns, Base):
    __tablename__ = "demo_trades"
    # The LEGACY mapper leaves the V2 evidence columns out: no SELECT, INSERT or UPDATE of Legacy / shadow / audit mode names them.
    __mapper_args__ = {"exclude_properties": list(V2_EVIDENCE_COLUMNS)}
    __table_args__ = (
        # One position per strategy, symbol, trading day AND entry session. Rows written before the session workflow have no
        # entry_session (NULL, read as an empty string), so they keep exactly the old one-per-day rule; a PREMARKET and a
        # REGULAR entry of the same day can now coexist as separate trades.
        Index("uq_demo_trades_strategy_symbol_day_session", "strategy", "symbol", "session_date",
              func.coalesce(text("entry_session"), text("''")), unique=True),
        # at most ONE open position per strategy and symbol, enforced by the database itself
        Index("uq_demo_trades_one_open", "strategy", "symbol", unique=True,
              postgresql_where=text("status = 'OPEN'"), sqlite_where=text("status = 'OPEN'")),
        Index("ix_demo_session", "session_date"),
        Index("ix_demo_strategy_session", "strategy", "session_date"),
    )


class DemoTradeV2(Base):
    """The SAME demo_trades table mapped WITH the V2 evidence columns. Used only when ``mode == "v2"`` (after the activation
    migration has added the columns). Never imported by Legacy code."""
    __table__ = DemoTrade.__table__


class ShadowTrade(_TradeColumns, _EvidenceColumns, Base):
    """The V2 workflow's VIRTUAL paper book in shadow mode: same shape as demo_trades, never read by the blotter or the legacy
    tracker. Nothing here is a trade the system placed; it records what V2 would have done on the same market data."""
    __tablename__ = "intraday_shadow_trades"
    __table_args__ = (
        Index("uq_intraday_shadow_trades_day_session", "strategy", "symbol", "session_date",
              func.coalesce(text("entry_session"), text("''")), unique=True),
        Index("uq_intraday_shadow_trades_one_open", "strategy", "symbol", unique=True,
              postgresql_where=text("status = 'OPEN'"), sqlite_where=text("status = 'OPEN'")),
        Index("ix_intraday_shadow_trades_session", "session_date"),
    )


class IntradayShadowLog(Base):
    """Shadow mode: every Legacy and V2 decision about a BUY / SELL signal, written when the decision CHANGES."""
    __tablename__ = "intraday_shadow_log"
    __table_args__ = (Index("ix_intraday_shadow_log_day", "trading_date", "engine", "strategy"),)

    id = Column(Integer, primary_key=True)
    ts = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    engine = Column(String(8), nullable=False)                   # LEGACY / V2
    strategy = Column(String(24), nullable=False)
    symbol = Column(String(16), nullable=False)
    trading_date = Column(Date, nullable=False)
    session_type = Column(String(12), nullable=False)            # PREMARKET / REGULAR / (POSTMARKET / CLOSED for legacy)
    direction = Column(String(8), nullable=False)
    score = Column(Float, nullable=True)
    decision = Column(String(12), nullable=False)                # OPENED / WOULD_OPEN / REJECTED
    code = Column(String(40), nullable=True)                     # the WAIT code or the legacy gate that refused
    reasons = Column(Text, nullable=True)                        # JSON list
    data_ts = Column(Text, nullable=True)                        # JSON: price age, engine age, per-source ages, quote age, board build time
    snapshot_id = Column(String(40), nullable=True)
    trade_id = Column(Integer, nullable=True)
    window_state = Column(String(24), nullable=True)             # e.g. OPENING_WINDOW / NORMAL (V2 opening rule state)


class IntradaySnapshot(Base):
    """What the strategy saw at one observation: every parameter reading and contribution, the data ages, the coverage."""
    __tablename__ = "intraday_snapshots"
    __table_args__ = (Index("ix_intraday_snapshots_strategy_symbol_day", "strategy", "symbol", "trading_date"),)

    id = Column(String(40), primary_key=True)              # uuid hex
    strategy = Column(String(24), nullable=False)
    symbol = Column(String(16), nullable=False)
    trading_date = Column(Date, nullable=False)
    session_type = Column(String(12), nullable=False)
    observed_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    signal = Column(String(8), nullable=True)              # the strategy's own BUY / SELL / NEUTRAL (raw)
    score = Column(Float, nullable=True)                   # the strategy's own score (raw)
    coverage_total_pct = Column(Float, nullable=True)
    coverage_eligible_pct = Column(Float, nullable=True)
    agreement_pct = Column(Float, nullable=True)
    low_confidence = Column(Boolean, nullable=True)
    fingerprint = Column(String(40), nullable=True)        # identical fingerprint = the same evidence, not a new observation
    params = Column(Text, nullable=True)                   # JSON: name, weight, bias, points, category per parameter
    sources = Column(Text, nullable=True)                  # JSON: per-signal age, status, source
    quote = Column(Text, nullable=True)
    validation = Column(Text, nullable=True)


class IntradaySignalEvent(Base):
    """One BUY / SELL signal of one strategy in one session of one trading day (PREMARKET and REGULAR are separate events)."""
    __tablename__ = "intraday_signal_events"
    __table_args__ = (
        Index("uq_intraday_signal_events_key", "strategy", "symbol", "trading_date", "session_type", "direction", unique=True),
        Index("ix_intraday_signal_events_day", "trading_date", "strategy"),
    )

    id = Column(Integer, primary_key=True)
    strategy = Column(String(24), nullable=False)
    symbol = Column(String(16), nullable=False)
    trading_date = Column(Date, nullable=False)
    session_type = Column(String(12), nullable=False)
    direction = Column(String(8), nullable=False)          # BUY / SELL
    score = Column(Float, nullable=True)                   # score of the FIRST observation
    signal_ts = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    last_seen_ts = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    snapshot_id = Column(String(40), nullable=True)        # first snapshot
    last_snapshot_id = Column(String(40), nullable=True)
    observations = Column(Integer, nullable=False, default=1)
    evidence_ids = Column(Text, nullable=True)             # JSON list of distinct evidence fingerprints (bounded)
    validation_status = Column(String(40), nullable=True)  # PASS or the latest WAIT code
    eligibility_status = Column(String(12), nullable=False, default="PENDING")   # PENDING / ELIGIBLE / REJECTED / TRADED
    reject_reasons = Column(Text, nullable=True)           # JSON list of the latest reasons
    trade_id = Column(Integer, nullable=True)


class IntradayDataException(Base):
    """A data-quality exception: a close with no valid price, awaiting reconciliation. Never a fabricated price."""
    __tablename__ = "intraday_data_exceptions"
    __table_args__ = (Index("ix_intraday_data_exceptions_open", "resolved_at"),)

    id = Column(Integer, primary_key=True)
    kind = Column(String(32), nullable=False)
    trade_id = Column(Integer, nullable=True)
    strategy = Column(String(24), nullable=True)
    symbol = Column(String(16), nullable=True)
    trading_date = Column(Date, nullable=True)
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolution = Column(Text, nullable=True)


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


def tables() -> list[str]:
    return [DemoTrade.__tablename__, IntradaySnapshot.__tablename__, IntradaySignalEvent.__tablename__,
            IntradayDataException.__tablename__, ShadowTrade.__tablename__, IntradayShadowLog.__tablename__,
            IntradaySignalAudit.__tablename__]
