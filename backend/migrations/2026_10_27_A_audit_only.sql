-- TRDGO.US signal verification AUDIT table (the only schema change audit mode needs)
-- Generated from the ORM models; reviewed; NOT executed by the repository. Run only after explicit approval, as the application's DB
-- owner, after a verified backup. Every statement is IF NOT EXISTS (idempotent). It fails FAST instead of queueing behind live
-- trading: lock_timeout 3s. A failure rolls the whole transaction back and changes nothing.
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '30s';

CREATE TABLE IF NOT EXISTS intraday_signal_audit (
  id SERIAL NOT NULL,
  ts TIMESTAMP WITH TIME ZONE NOT NULL,
  strategy VARCHAR(24) NOT NULL,
  symbol VARCHAR(16) NOT NULL,
  trading_date DATE NOT NULL,
  session_type VARCHAR(12) NOT NULL,
  direction VARCHAR(8) NOT NULL,
  score FLOAT,
  recomputed_score FLOAT,
  score_diff FLOAT,
  entry_price FLOAT,
  verdict VARCHAR(8) NOT NULL,
  data_status VARCHAR(16) NOT NULL,
  failed_checks TEXT,
  checks TEXT,
  breakdown TEXT,
  data TEXT,
  legacy_opened BOOLEAN,
  trade_id INTEGER,
  legacy_held BOOLEAN,
  attribution_ok BOOLEAN,
  attribution_detail TEXT,
  PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_intraday_signal_audit_day ON intraday_signal_audit (trading_date, strategy);

COMMIT;
