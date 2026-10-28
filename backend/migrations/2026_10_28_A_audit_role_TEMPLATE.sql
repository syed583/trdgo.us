-- TEMPLATE, NOT EXECUTED. A dedicated least-privilege role for the signal-verification audit (optional; recommended).
-- With AUDIT_DATABASE_URL set to this role, the audit connects as it instead of as the application's broad role, so an audit fault or a
-- compromise of the audit path cannot touch anything but its own table. Run as the database owner AFTER migration A. Choose the password
-- in a secret manager; never commit it. Adapt names to the Supabase project.
BEGIN;
SET LOCAL lock_timeout = '3s';
CREATE ROLE trdgo_audit LOGIN PASSWORD '<<set-from-secret-manager>>'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOREPLICATION CONNECTION LIMIT 2;
GRANT USAGE ON SCHEMA public TO trdgo_audit;
GRANT SELECT ON demo_trades TO trdgo_audit;                 -- to attribute an opened trade to its signal (read only)
GRANT SELECT, INSERT ON intraday_signal_audit TO trdgo_audit;
GRANT USAGE ON SEQUENCE intraday_signal_audit_id_seq TO trdgo_audit;
ALTER ROLE trdgo_audit SET statement_timeout = '5s';
ALTER ROLE trdgo_audit SET lock_timeout = '2s';
COMMIT;
-- Rollback: REVOKE ALL ON demo_trades, intraday_signal_audit FROM trdgo_audit; REVOKE USAGE ON SEQUENCE intraday_signal_audit_id_seq FROM trdgo_audit;
--           REVOKE USAGE ON SCHEMA public FROM trdgo_audit; DROP ROLE trdgo_audit;
