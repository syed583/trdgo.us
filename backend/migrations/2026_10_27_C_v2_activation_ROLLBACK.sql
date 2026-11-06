-- Rollback of C: restores the one-position-per-day rule; the evidence columns are left in place (nullable, harmless) unless you also run
-- the DROP COLUMN lines. No row is changed. The old constraint can only be re-added if no strategy/symbol/day pair has two rows.
BEGIN;
SET LOCAL lock_timeout = '3s';
DROP INDEX IF EXISTS uq_demo_trades_one_open;
DROP INDEX IF EXISTS uq_demo_trades_strategy_symbol_day_session;
-- ALTER TABLE demo_trades ADD CONSTRAINT uq_demo_strategy_symbol_day UNIQUE (strategy, symbol, session_date);
-- optional: ALTER TABLE demo_trades DROP COLUMN IF EXISTS entry_session, DROP COLUMN IF EXISTS signal_event_id, DROP COLUMN IF EXISTS snapshot_id, DROP COLUMN IF EXISTS entry_quote, DROP COLUMN IF EXISTS risk_checks, DROP COLUMN IF EXISTS validation, DROP COLUMN IF EXISTS exit_price_source, DROP COLUMN IF EXISTS fill_assumption;
COMMIT;
