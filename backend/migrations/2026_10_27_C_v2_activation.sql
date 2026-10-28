-- TRDGO.US V2 ACTIVATION schema (only when mode = 'v2'; a separate approval, between sessions)
-- Generated from the ORM models; reviewed; NOT executed by the repository. Run only after explicit approval, as the application's DB
-- owner, after a verified backup. Every statement is IF NOT EXISTS (idempotent). It fails FAST instead of queueing behind live
-- trading: lock_timeout 3s. A failure rolls the whole transaction back and changes nothing.
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '30s';

-- Pre-check (must return zero rows, otherwise the second index cannot be created; nothing is deleted to make it fit):
--   SELECT strategy, symbol, COUNT(*) FROM demo_trades WHERE status = 'OPEN' GROUP BY 1, 2 HAVING COUNT(*) > 1;
ALTER TABLE demo_trades ADD COLUMN IF NOT EXISTS entry_session VARCHAR(12);
ALTER TABLE demo_trades ADD COLUMN IF NOT EXISTS signal_event_id INTEGER;
ALTER TABLE demo_trades ADD COLUMN IF NOT EXISTS snapshot_id VARCHAR(40);
ALTER TABLE demo_trades ADD COLUMN IF NOT EXISTS entry_quote TEXT;
ALTER TABLE demo_trades ADD COLUMN IF NOT EXISTS risk_checks TEXT;
ALTER TABLE demo_trades ADD COLUMN IF NOT EXISTS validation TEXT;
ALTER TABLE demo_trades ADD COLUMN IF NOT EXISTS exit_price_source VARCHAR(24);
ALTER TABLE demo_trades ADD COLUMN IF NOT EXISTS fill_assumption VARCHAR(160);
ALTER TABLE demo_trades DROP CONSTRAINT IF EXISTS uq_demo_strategy_symbol_day;
CREATE UNIQUE INDEX IF NOT EXISTS uq_demo_trades_strategy_symbol_day_session ON demo_trades (strategy, symbol, session_date, COALESCE(entry_session, ''));
CREATE UNIQUE INDEX IF NOT EXISTS uq_demo_trades_one_open ON demo_trades (strategy, symbol) WHERE status = 'OPEN';

COMMIT;
