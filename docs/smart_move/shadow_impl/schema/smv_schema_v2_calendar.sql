-- Smart Move calendar verification tables (local scratch only; apply AFTER smv_schema_v1.sql; NOT applied to any production database).
-- Append-only: UPDATE, DELETE and TRUNCATE are refused by the same trigger function as v1.

CREATE TABLE IF NOT EXISTS smv.calendar_observations (
  id               bigserial   PRIMARY KEY,
  source           text        NOT NULL CHECK (source IN ('UW_CALENDAR','UW_ROW','FINVIZ','FINNHUB')),
  vendor           text        NOT NULL,
  symbol           text        NOT NULL,
  event_date       date        NOT NULL,
  label_raw        text,                                   -- provider label as received
  clock_raw        text,                                   -- provider clock time as received (Finviz)
  vendor_flag_raw  text,                                   -- Unusual Whales 'source' verbatim ('company' | 'estimation'); NULL when the provider sent none
  raw_sha256       char(64)    NOT NULL,                   -- hash of the stored raw payload (smv.payloads) the row came from
  calendar_observed_at timestamptz NOT NULL,               -- retrieval time: the CALENDAR clock
  published_at     timestamptz,
  chain_sha256     char(64)    NOT NULL,                   -- hash chain over the append order
  UNIQUE (source, symbol, event_date, raw_sha256, calendar_observed_at)
);
CREATE INDEX IF NOT EXISTS ix_smv_calobs_sym ON smv.calendar_observations (symbol, calendar_observed_at);

CREATE TABLE IF NOT EXISTS smv.calendar_evaluations (
  id               bigserial   PRIMARY KEY,
  run_id           uuid        NOT NULL,
  symbol           text        NOT NULL,
  event_date       date,
  status           text        NOT NULL CHECK (status IN ('UNKNOWN','STALE','MOVED','ESTIMATED','VENDOR_CONFIRMED','COMPANY_VERIFIED')),
  kind             text        NOT NULL,
  eligible_for_shadow boolean  NOT NULL,
  reasons          text[]      NOT NULL,
  no_trade_reasons text[]      NOT NULL,
  calendar_observed_at timestamptz,                        -- newest observation used
  prediction_snapshot_at timestamptz,                      -- the PREDICTION clock, kept apart
  detail           jsonb       NOT NULL,
  evaluated_at     timestamptz NOT NULL,
  UNIQUE (run_id, symbol, event_date, evaluated_at),
  CHECK (eligible_for_shadow OR cardinality(no_trade_reasons) > 0)      -- an excluded event must carry at least one reason
);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['calendar_observations','calendar_evaluations'] LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS %I ON smv.%I', t || '_ro_row', t);
    EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON smv.%I FOR EACH ROW EXECUTE FUNCTION smv.append_only()', t || '_ro_row', t);
    EXECUTE format('DROP TRIGGER IF EXISTS %I ON smv.%I', t || '_ro_trunc', t);
    EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON smv.%I FOR EACH STATEMENT EXECUTE FUNCTION smv.append_only()', t || '_ro_trunc', t);
  END LOOP;
END $$;
