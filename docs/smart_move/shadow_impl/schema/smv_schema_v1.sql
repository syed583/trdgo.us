-- Smart Move shadow pilot: PostgreSQL schema v1 (metadata only; raw payloads live in hashed files). Derived from design_proof/smv_schema_design.sql.
-- NOT APPLIED to any database except throw-away local test clusters. Every table is append-only (UPDATE / DELETE / TRUNCATE are rejected).
CREATE SCHEMA IF NOT EXISTS smv;

CREATE OR REPLACE FUNCTION smv.append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'smv tables are append-only (% on %)', TG_OP, TG_TABLE_NAME;
END $$;

CREATE TABLE IF NOT EXISTS smv.event_versions (
  symbol               text        NOT NULL,
  earnings_date        date        NOT NULL,
  version              integer     NOT NULL CHECK (version >= 1),
  timing_label         text        NOT NULL CHECK (timing_label IN ('BMO','AMC','UNKNOWN')),
  timing_label_source  text        NOT NULL,
  assumed_release_utc  timestamptz NOT NULL,            -- conservative assumption, never called verified
  sec_acceptance_utc   timestamptz,                     -- a filing fact (upper bound), kept apart
  sec_accession        text,
  public_release_utc   timestamptz,                     -- only with independent evidence
  public_release_evidence text,
  window_start_utc     timestamptz NOT NULL,            -- assumed release - 48 h
  deadline_utc         timestamptz NOT NULL,            -- assumed release - 24 h
  scope                text        NOT NULL CHECK (scope IN ('PILOT','WARMED','CALENDAR')),
  recorded_at          timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (symbol, earnings_date, version),
  CHECK (deadline_utc > window_start_utc),
  CHECK (public_release_utc IS NULL OR public_release_evidence IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS smv.payloads (
  sha256        char(64)    PRIMARY KEY,                -- of the raw provider bytes, before compression
  provider      text        NOT NULL,
  endpoint      text        NOT NULL,
  params_hash   char(64)    NOT NULL,                   -- parameters only, never credentials
  http_status   integer     NOT NULL,
  bytes_raw     bigint      NOT NULL CHECK (bytes_raw >= 0),
  bytes_stored  bigint      NOT NULL CHECK (bytes_stored >= 0),
  compression   text        NOT NULL CHECK (compression IN ('gzip','none')),
  rel_path      text        NOT NULL UNIQUE,            -- payloads/ab/cd/<sha256>.json.gz
  captured_at   timestamptz NOT NULL,
  CHECK (rel_path = 'payloads/' || substr(sha256,1,2) || '/' || substr(sha256,3,2) || '/' || sha256 || CASE compression WHEN 'gzip' THEN '.json.gz' ELSE '.json' END)
);

CREATE TABLE IF NOT EXISTS smv.payload_verifications (    -- integrity checks are appended, never updated
  id            bigserial   PRIMARY KEY,
  sha256        char(64)    NOT NULL REFERENCES smv.payloads(sha256),
  verified_at   timestamptz NOT NULL DEFAULT now(),
  ok            boolean     NOT NULL,
  detail        text
);

CREATE TABLE IF NOT EXISTS smv.capture_runs (             -- one START row and one END row per run
  id            bigserial   PRIMARY KEY,
  run_id        uuid        NOT NULL,
  phase         text        NOT NULL CHECK (phase IN ('START','END','TICK')),
  at_utc        timestamptz NOT NULL DEFAULT now(),
  events_seen   integer, events_in_window integer, predictions_made integer, missed_by_reason jsonb,
  requests_by_source jsonb, daily_cap integer, stop_reason text, detail jsonb
);

CREATE TABLE IF NOT EXISTS smv.input_snapshots (
  snapshot_id     bigserial   PRIMARY KEY,
  symbol          text        NOT NULL,
  earnings_date   date        NOT NULL,
  event_version   integer     NOT NULL,
  factor          smallint    NOT NULL CHECK (factor BETWEEN 1 AND 8),
  source_name     text        NOT NULL,
  payload_sha256  char(64)    REFERENCES smv.payloads(sha256),   -- NULL only when status = MISSING
  provider_as_of  timestamptz,                                    -- the provider's own stamp, NULL if none (never inferred)
  capture_ts_utc  timestamptz NOT NULL,
  session_reflected date,
  as_of_quality   text        NOT NULL CHECK (as_of_quality IN ('PER_ROW_STAMPED','DATED_NOT_STAMPED','FETCH_TIME_ONLY','DERIVED')),
  freshness_class text        NOT NULL CHECK (freshness_class IN ('FRESH','LAST_SESSION_WEEKEND','LAST_SESSION_HOLIDAY','STALE_AGE','UNSTAMPED')),
  status          text        NOT NULL CHECK (status IN ('OK','MISSING')),
  missing_reason  text        CHECK (missing_reason IN ('NO_SOURCE','NOT_AVAILABLE_AS_OF','PROVIDER_EMPTY','HTTP_ERROR','TIMEOUT','BUDGET_STOP','INSUFFICIENT_HISTORY','UNINFORMATIVE_FIELD','UNKNOWN_TIMING','STALE_AGE','FLAG_OFF')),
  extracted       jsonb,                                          -- reduced numbers used by the scorer
  run_id          uuid        NOT NULL,
  UNIQUE (run_id, symbol, earnings_date, event_version, factor, source_name),
  FOREIGN KEY (symbol, earnings_date, event_version) REFERENCES smv.event_versions(symbol, earnings_date, version),
  CHECK ((status = 'OK' AND payload_sha256 IS NOT NULL AND missing_reason IS NULL) OR (status = 'MISSING' AND missing_reason IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS ix_smv_snap_event ON smv.input_snapshots (symbol, earnings_date, event_version, factor);
CREATE INDEX IF NOT EXISTS ix_smv_snap_capture ON smv.input_snapshots (capture_ts_utc);

CREATE TABLE IF NOT EXISTS smv.predictions (
  prediction_id   bigserial   PRIMARY KEY,
  symbol          text        NOT NULL,
  earnings_date   date        NOT NULL,
  event_version   integer     NOT NULL,
  scoring_version text        NOT NULL,                -- e.g. score40-v1 + variant
  variant         text        NOT NULL CHECK (variant IN ('continuation','contrarian')),
  prereg_sha256   char(64)    NOT NULL,
  prediction_version integer  NOT NULL CHECK (prediction_version >= 1),
  supersedes_id   bigint      REFERENCES smv.predictions(prediction_id),
  predicted_at_utc timestamptz NOT NULL,
  cutoff_utc      timestamptz NOT NULL,
  in_window       boolean     NOT NULL,
  signal          text        NOT NULL CHECK (signal IN ('BUY','SELL','NO_TRADE')),
  bull_points     numeric(6,2) NOT NULL,
  bear_points     numeric(6,2) NOT NULL,
  weight_available numeric(6,2) NOT NULL,
  weight_max      numeric(6,2) NOT NULL DEFAULT 40,
  reason_codes    text[]      NOT NULL,
  explanation     jsonb       NOT NULL,
  UNIQUE (symbol, earnings_date, event_version, scoring_version, variant, prediction_version),
  FOREIGN KEY (symbol, earnings_date, event_version) REFERENCES smv.event_versions(symbol, earnings_date, version),
  CHECK (predicted_at_utc <= cutoff_utc OR in_window = false)
);

CREATE TABLE IF NOT EXISTS smv.prediction_inputs (          -- which snapshots fed which prediction (provenance)
  prediction_id bigint NOT NULL REFERENCES smv.predictions(prediction_id),
  snapshot_id   bigint NOT NULL REFERENCES smv.input_snapshots(snapshot_id),
  PRIMARY KEY (prediction_id, snapshot_id)
);

CREATE TABLE IF NOT EXISTS smv.ledger (                      -- skipped events, capacity overflow, provider failures, missed deadlines, denials
  id          bigserial   PRIMARY KEY,
  run_id      uuid        NOT NULL,
  at_utc      timestamptz NOT NULL,
  entry_type  text        NOT NULL,
  symbol      text,
  earnings_date date,
  detail      jsonb       NOT NULL,
  UNIQUE (run_id, entry_type, symbol, earnings_date, at_utc)
);

CREATE TABLE IF NOT EXISTS smv.seals (                      -- daily hash chain over the payload files and snapshot rows
  seal_date          date PRIMARY KEY,
  manifest_sha256    char(64) NOT NULL,
  prev_seal_sha256   char(64),
  n_payloads         integer NOT NULL,
  n_snapshots        integer NOT NULL,
  created_at         timestamptz NOT NULL DEFAULT now()
);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['event_versions','payloads','payload_verifications','capture_runs','input_snapshots','predictions','prediction_inputs','ledger','seals'] LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS %I ON smv.%I', t || '_ro_row', t);
    EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON smv.%I FOR EACH ROW EXECUTE FUNCTION smv.append_only()', t || '_ro_row', t);
    EXECUTE format('DROP TRIGGER IF EXISTS %I ON smv.%I', t || '_ro_trunc', t);
    EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON smv.%I FOR EACH STATEMENT EXECUTE FUNCTION smv.append_only()', t || '_ro_trunc', t);
  END LOOP;
END $$;
