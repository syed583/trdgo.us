-- TRDGO.US V2 SHADOW-mode tables (not needed for audit mode)
-- Generated from the ORM models; reviewed; NOT executed by the repository. Run only after explicit approval, as the application's DB
-- owner, after a verified backup. Every statement is IF NOT EXISTS (idempotent). It fails FAST instead of queueing behind live
-- trading: lock_timeout 3s. A failure rolls the whole transaction back and changes nothing.
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '30s';

CREATE TABLE IF NOT EXISTS intraday_snapshots (
  id VARCHAR(40) NOT NULL,
  strategy VARCHAR(24) NOT NULL,
  symbol VARCHAR(16) NOT NULL,
  trading_date DATE NOT NULL,
  session_type VARCHAR(12) NOT NULL,
  observed_at TIMESTAMP WITH TIME ZONE NOT NULL,
  signal VARCHAR(8),
  score FLOAT,
  coverage_total_pct FLOAT,
  coverage_eligible_pct FLOAT,
  agreement_pct FLOAT,
  low_confidence BOOLEAN,
  fingerprint VARCHAR(40),
  params TEXT,
  sources TEXT,
  quote TEXT,
  validation TEXT,
  PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_intraday_snapshots_strategy_symbol_day ON intraday_snapshots (strategy, symbol, trading_date);

CREATE TABLE IF NOT EXISTS intraday_signal_events (
  id SERIAL NOT NULL,
  strategy VARCHAR(24) NOT NULL,
  symbol VARCHAR(16) NOT NULL,
  trading_date DATE NOT NULL,
  session_type VARCHAR(12) NOT NULL,
  direction VARCHAR(8) NOT NULL,
  score FLOAT,
  signal_ts TIMESTAMP WITH TIME ZONE NOT NULL,
  last_seen_ts TIMESTAMP WITH TIME ZONE NOT NULL,
  snapshot_id VARCHAR(40),
  last_snapshot_id VARCHAR(40),
  observations INTEGER NOT NULL,
  evidence_ids TEXT,
  validation_status VARCHAR(40),
  eligibility_status VARCHAR(12) NOT NULL,
  reject_reasons TEXT,
  trade_id INTEGER,
  PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_intraday_signal_events_day ON intraday_signal_events (trading_date, strategy);
CREATE UNIQUE INDEX IF NOT EXISTS uq_intraday_signal_events_key ON intraday_signal_events (strategy, symbol, trading_date, session_type, direction);

CREATE TABLE IF NOT EXISTS intraday_data_exceptions (
  id SERIAL NOT NULL,
  kind VARCHAR(32) NOT NULL,
  trade_id INTEGER,
  strategy VARCHAR(24),
  symbol VARCHAR(16),
  trading_date DATE,
  detail TEXT,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL,
  resolved_at TIMESTAMP WITH TIME ZONE,
  resolution TEXT,
  PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_intraday_data_exceptions_open ON intraday_data_exceptions (resolved_at);

CREATE TABLE IF NOT EXISTS intraday_shadow_trades (
  id SERIAL NOT NULL,
  strategy VARCHAR(24) NOT NULL,
  symbol VARCHAR(16) NOT NULL,
  side VARCHAR(5) NOT NULL,
  shares INTEGER NOT NULL,
  session_date DATE NOT NULL,
  entry_time TIMESTAMP WITH TIME ZONE NOT NULL,
  entry_price FLOAT NOT NULL,
  stop FLOAT,
  target FLOAT,
  score_at_entry FLOAT,
  last_price FLOAT,
  profit FLOAT,
  profit_pct FLOAT,
  status VARCHAR(16) NOT NULL,
  exit_time TIMESTAMP WITH TIME ZONE,
  exit_price FLOAT,
  updated_at TIMESTAMP WITH TIME ZONE,
  entry_session VARCHAR(12),
  signal_event_id INTEGER,
  snapshot_id VARCHAR(40),
  entry_quote TEXT,
  risk_checks TEXT,
  validation TEXT,
  exit_price_source VARCHAR(24),
  fill_assumption VARCHAR(160),
  PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_intraday_shadow_trades_session ON intraday_shadow_trades (session_date);
CREATE UNIQUE INDEX IF NOT EXISTS uq_intraday_shadow_trades_day_session ON intraday_shadow_trades (strategy, symbol, session_date, coalesce(entry_session, ''));
CREATE UNIQUE INDEX IF NOT EXISTS uq_intraday_shadow_trades_one_open ON intraday_shadow_trades (strategy, symbol) WHERE status = 'OPEN';

CREATE TABLE IF NOT EXISTS intraday_shadow_log (
  id SERIAL NOT NULL,
  ts TIMESTAMP WITH TIME ZONE NOT NULL,
  engine VARCHAR(8) NOT NULL,
  strategy VARCHAR(24) NOT NULL,
  symbol VARCHAR(16) NOT NULL,
  trading_date DATE NOT NULL,
  session_type VARCHAR(12) NOT NULL,
  direction VARCHAR(8) NOT NULL,
  score FLOAT,
  decision VARCHAR(12) NOT NULL,
  code VARCHAR(40),
  reasons TEXT,
  data_ts TEXT,
  snapshot_id VARCHAR(40),
  trade_id INTEGER,
  window_state VARCHAR(24),
  PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_intraday_shadow_log_day ON intraday_shadow_log (trading_date, engine, strategy);

COMMIT;
