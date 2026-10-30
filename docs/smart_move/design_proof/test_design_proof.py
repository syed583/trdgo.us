"""Tests of the storage design proof. Payload-store tests need nothing; the schema tests need a throw-away PostgreSQL given in SMV_TEST_PG
(a URL on 127.0.0.1 only). Nothing here touches production."""
import gzip
import hashlib
import os
import stat
from pathlib import Path

import pytest

import payload_store_proof as ps

HERE = Path(__file__).resolve().parent
PG = os.environ.get("SMV_TEST_PG", "")


def test_atomic_content_addressed_write_and_readonly(tmp_path):
    raw = b'{"data":[1,2,3]}'
    sha, final, created = ps.write_payload(tmp_path, raw)
    assert created and sha == hashlib.sha256(raw).hexdigest() and final.exists()
    assert str(final.relative_to(tmp_path)).replace("\\", "/") == ps.rel_for(sha)
    assert gzip.decompress(final.read_bytes()) == raw
    assert not os.access(final, os.W_OK) or not (final.stat().st_mode & stat.S_IWRITE)      # read-only attribute set
    sha2, _, created2 = ps.write_payload(tmp_path, raw)
    assert sha2 == sha and created2 is False                                               # same bytes are never rewritten
    assert list((tmp_path / "payloads").rglob(".tmp-*")) == []                              # no temporary file left behind


def test_corrupt_existing_file_is_never_overwritten(tmp_path):
    sha, final, _ = ps.write_payload(tmp_path, b"abc")
    os.chmod(final, stat.S_IWRITE)
    final.write_bytes(gzip.compress(b"tampered"))
    with pytest.raises(RuntimeError):
        ps.write_payload(tmp_path, b"abc")
    n, bad = ps.verify_all(tmp_path)
    assert n == 1 and bad == [str(final)]


def test_integrity_scan_orphans_and_crash_leftovers(tmp_path):
    a, fa, _ = ps.write_payload(tmp_path, b"one")
    b, fb, _ = ps.write_payload(tmp_path, b"two")
    (fa.parent / ".tmp-crash").write_bytes(b"partial")                                      # simulated crash during a write
    orphan_files, missing_files, tmps = ps.orphans(tmp_path, {a, "f" * 64})
    assert orphan_files == [b] and missing_files == ["f" * 64] and len(tmps) == 1


def test_seal_chain_changes_when_any_payload_changes(tmp_path):
    ps.write_payload(tmp_path, b"one")
    s1, n1 = ps.seal(tmp_path, "2026-11-02", None)
    ps.write_payload(tmp_path, b"two")
    s2, n2 = ps.seal(tmp_path, "2026-11-02", None)
    s3, _ = ps.seal(tmp_path, "2026-11-03", s2)
    assert (n1, n2) == (1, 2) and len({s1, s2, s3}) == 3


pg = pytest.mark.skipif(not PG.startswith("postgresql://") or "127.0.0.1" not in PG, reason="SMV_TEST_PG (local scratch cluster) not set")


@pg
def test_schema_applies_and_is_append_only_with_constraints():
    import psycopg
    c = psycopg.connect(PG, autocommit=True)
    c.execute("DROP SCHEMA IF EXISTS smv CASCADE")
    c.execute((HERE / "smv_schema_design.sql").read_text(encoding="utf-8"))
    c.execute("INSERT INTO smv.event_versions(symbol,earnings_date,version,timing_label,timing_label_source,assumed_release_utc,window_start_utc,deadline_utc,scope) "
              "VALUES ('AAA','2026-11-03',1,'AMC','PROVIDER','2026-11-03 21:00+00','2026-11-01 21:00+00','2026-11-02 21:00+00','PILOT')")
    sha = "a" * 64
    path = "payloads/aa/aa/" + sha + ".json.gz"
    c.execute("INSERT INTO smv.payloads VALUES (%s,'UW','/x',%s,200,10,5,'gzip',%s,now())", (sha, "b" * 64, path))
    c.execute("INSERT INTO smv.input_snapshots(symbol,earnings_date,event_version,factor,source_name,payload_sha256,capture_ts_utc,as_of_quality,freshness_class,status) "
              "VALUES ('AAA','2026-11-03',1,5,'uw.ohlc',%s,now(),'DATED_NOT_STAMPED','FRESH','OK')", (sha,))
    c.execute("INSERT INTO smv.input_snapshots(symbol,earnings_date,event_version,factor,source_name,capture_ts_utc,as_of_quality,freshness_class,status,missing_reason) "
              "VALUES ('AAA','2026-11-03',1,7,'uw.volatility.context',now(),'FETCH_TIME_ONLY','UNSTAMPED','MISSING','PROVIDER_EMPTY')")
    for sql in ("UPDATE smv.payloads SET http_status=500", "DELETE FROM smv.input_snapshots", "UPDATE smv.event_versions SET scope='WARMED'"):
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute(sql)
    # TRUNCATE: referenced tables are refused by PostgreSQL itself (FeatureNotSupported); tables without references reach the trigger (RaiseException)
    for sql in ("TRUNCATE smv.predictions", "TRUNCATE smv.event_versions", "TRUNCATE smv.input_snapshots", "TRUNCATE smv.payloads", "TRUNCATE smv.seals", "TRUNCATE smv.capture_runs", "TRUNCATE smv.payload_verifications"):
        with pytest.raises((psycopg.errors.RaiseException, psycopg.errors.FeatureNotSupported)):
            c.execute(sql)
    assert c.execute("SELECT count(*) FROM smv.event_versions").fetchone()[0] == 1
    bad = [
        "INSERT INTO smv.input_snapshots(symbol,earnings_date,event_version,factor,source_name,capture_ts_utc,as_of_quality,freshness_class,status) VALUES ('AAA','2026-11-03',1,3,'x',now(),'FETCH_TIME_ONLY','FRESH','OK')",   # OK without payload
        "INSERT INTO smv.input_snapshots(symbol,earnings_date,event_version,factor,source_name,capture_ts_utc,as_of_quality,freshness_class,status,missing_reason) VALUES ('AAA','2026-11-03',1,9,'x',now(),'FETCH_TIME_ONLY','FRESH','MISSING','NO_SOURCE')",  # factor 9
        "INSERT INTO smv.input_snapshots(symbol,earnings_date,event_version,factor,source_name,capture_ts_utc,as_of_quality,freshness_class,status,missing_reason) VALUES ('ZZZ','2026-11-03',1,3,'x',now(),'FETCH_TIME_ONLY','FRESH','MISSING','NO_SOURCE')",  # unknown event
        "INSERT INTO smv.payloads VALUES ('%s','UW','/x','%s',200,1,1,'gzip','payloads/wrong/path.json.gz',now())" % ("c" * 64, "d" * 64),                                                           # path not derived from hash
        "INSERT INTO smv.event_versions(symbol,earnings_date,version,timing_label,timing_label_source,assumed_release_utc,public_release_utc,window_start_utc,deadline_utc,scope) VALUES ('BBB','2026-11-03',1,'AMC','P','2026-11-03 21:00+00','2026-11-03 21:00+00','2026-11-01 21:00+00','2026-11-02 21:00+00','PILOT')",  # public release without evidence
        "INSERT INTO smv.predictions(symbol,earnings_date,event_version,scoring_version,variant,prereg_sha256,prediction_version,predicted_at_utc,cutoff_utc,in_window,signal,bull_points,bear_points,weight_available,reason_codes,explanation) VALUES ('AAA','2026-11-03',1,'s','continuation','%s',1,'2026-11-03 00:00+00','2026-11-02 21:00+00',true,'BUY',1,1,1,'{RULE}','{}')" % ("e" * 64),   # predicted after the cutoff but flagged in-window
    ]
    for sql in bad:
        with pytest.raises((psycopg.errors.CheckViolation, psycopg.errors.ForeignKeyViolation)):
            c.execute(sql)
    c.execute("INSERT INTO smv.predictions(symbol,earnings_date,event_version,scoring_version,variant,prereg_sha256,prediction_version,predicted_at_utc,cutoff_utc,in_window,signal,bull_points,bear_points,weight_available,reason_codes,explanation) VALUES ('AAA','2026-11-03',1,'s','continuation',%s,1,'2026-11-02 12:00+00','2026-11-02 21:00+00',true,'NO_TRADE',0,0,10,'{INSUFFICIENT_COVERAGE}','{}')", ("e" * 64,))
    assert c.execute("SELECT count(*) FROM smv.input_snapshots").fetchone()[0] == 2
    c.execute("DROP SCHEMA smv CASCADE")
