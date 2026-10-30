"""Payload store (no database) and append-only schema + idempotent Db wrapper (needs a throw-away local PostgreSQL in SMV_TEST_PG, 127.0.0.1 only)."""
import gzip
import json
import os
import uuid
from datetime import date, datetime, timezone

import pytest

from smvshadow import governor as gv, runner as rn, scorer as sc
from smvshadow.store import Db, PayloadStore, rel_for
from test_e2e_rehearsal import EVENT, build

UTC = timezone.utc
PG = os.environ.get("SMV_TEST_PG", "")
pg = pytest.mark.skipif(not PG.startswith("postgresql://") or "127.0.0.1" not in PG, reason="SMV_TEST_PG (local scratch cluster) not set")


def test_payload_hash_integrity_and_corruption_detected(tmp_path):
    s = PayloadStore(tmp_path)
    sha, rel, created = s.put(b'{"a":1}')
    assert created and rel == rel_for(sha) and s.get(sha) == b'{"a":1}'
    assert s.put(b'{"a":1}') == (sha, rel, False)                                           # same bytes: no new file
    f = tmp_path / rel
    os.chmod(f, 0o600)
    f.write_bytes(gzip.compress(b'{"a":2}'))                                                 # tamper
    with pytest.raises(RuntimeError):
        s.get(sha)
    with pytest.raises(RuntimeError):
        s.put(b'{"a":1}')                                                                    # corrupt existing file is never overwritten
    total, bad = s.verify_all()
    assert total == 1 and len(bad) == 1


def test_crash_leftover_tmp_removed_final_files_kept_and_orphans_found(tmp_path):
    s = PayloadStore(tmp_path)
    sha, rel, _ = s.put(b"one")
    (tmp_path / "payloads" / sha[:2] / sha[2:4] / ".tmp-deadbeef").write_bytes(b"half written")
    assert s.recover() == [".tmp-deadbeef"] and s.get(sha) == b"one"
    orphans, missing = s.orphans(known_hashes=["f" * 64])
    assert orphans == [sha] and missing == ["f" * 64]                                       # file without row / row without file are both reported


def test_seal_chain_changes_when_payloads_or_previous_seal_change(tmp_path):
    s = PayloadStore(tmp_path)
    s.put(b"one")
    a, n1 = s.seal("2026-11-03", "0" * 64)
    s.put(b"two")
    b, n2 = s.seal("2026-11-03", "0" * 64)
    c, _ = s.seal("2026-11-03", a)
    assert n1 == 1 and n2 == 2 and len({a, b, c}) == 3


@pytest.fixture
def db():
    import psycopg
    conn = psycopg.connect(PG, autocommit=True)
    conn.execute("DROP SCHEMA IF EXISTS smv CASCADE")
    d = Db(conn)
    d.apply_schema()
    yield d
    conn.close()


@pg
def test_schema_is_append_only(db):
    import psycopg
    db.add_payload("a" * 64, rel_for("a" * 64), "UW", "ohlc", "h", 200, 1, 1, datetime(2026, 11, 3, tzinfo=UTC))
    for sql in ("UPDATE smv.payloads SET provider='X'", "DELETE FROM smv.payloads"):
        with pytest.raises(psycopg.Error):
            db.c.execute(sql)
    with pytest.raises(psycopg.Error):
        db.c.execute("TRUNCATE smv.payloads")
    assert db._one("SELECT count(*) FROM smv.payloads") == 1


@pg
def test_rehearsal_persists_idempotently_with_provenance(db, tmp_path):
    clock, fetch, g, plan, infos, pre, sha, store = build(tmp_path, ["AAA", "BBB"])
    run_id = str(uuid.uuid4())
    from smvshadow import calendar_validation as cv
    for (sym, _), info in infos.items():
        v = cv.EventVersion(sym, EVENT, 1, cv.CONFIRMED, cv.T_CONFIRMED, "BMO", [], clock.now())
        db.add_event_version(v, info["plan"])
        db.add_event_version(v, info["plan"])                                                  # idempotent
    assert db._one("SELECT count(*) FROM smv.event_versions") == 2
    r = rn.ShadowRunner(g, store, fetch, pre, sha, db=db, run_id=run_id)
    r.run(plan["assignments"], infos)
    r.finish()
    counts = {t: db._one("SELECT count(*) FROM smv.%s" % t) for t in ("payloads", "input_snapshots", "predictions", "prediction_inputs")}
    assert counts["predictions"] == 4 and counts["payloads"] > 0 and counts["prediction_inputs"] > 0
    for p in r.predictions:                                                                     # replay: same rows, no duplicates, no error
        db.add_prediction(p)
    again = db._one("SELECT count(*) FROM smv.predictions")
    assert again == counts["predictions"]
    # every payload row points at a file whose bytes match its hash
    shas = [row[0] for row in db.c.execute("SELECT sha256 FROM smv.payloads").fetchall()]
    assert store.orphans(shas) == ([], [])
