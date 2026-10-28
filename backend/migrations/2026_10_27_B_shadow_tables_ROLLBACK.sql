-- Rollback of B (shadow tables only; demo_trades untouched).
BEGIN;
SET LOCAL lock_timeout = '3s';
DROP TABLE IF EXISTS intraday_shadow_log, intraday_shadow_trades, intraday_data_exceptions, intraday_signal_events, intraday_snapshots;
COMMIT;
