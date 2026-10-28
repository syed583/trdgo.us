-- Rollback of A: removes ONLY the audit table. demo_trades and every historical row are untouched. Safe at any time (the application
-- logs and ignores audit write errors; set INTRADAY_VERIFICATION mode to "off" first so it stops trying).
BEGIN;
SET LOCAL lock_timeout = '3s';
DROP TABLE IF EXISTS intraday_signal_audit;
COMMIT;
