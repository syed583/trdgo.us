"""Batch-completion validation and the explicit completion marker. Synthetic run folders only; nothing here touches a provider."""
import gzip
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_batch_marker as B  # noqa: E402
from smvshadow.store import rel_for  # noqa: E402

UTC = timezone.utc
START = datetime(2026, 11, 2, 21, 30, tzinfo=UTC)                         # after the 21:00Z close
NOW = START + timedelta(minutes=20)


def make_run(tmp_path, *, n=5, at=START, error=None, kill=True, status="COMPLETED", ceiling_extra=0, payload_ok=True):
    run, store = tmp_path / "run", tmp_path / "store"
    run.mkdir(parents=True)
    store.mkdir(parents=True)
    if kill:
        (store / "KILL").write_text("engaged")
    lines, evid = [], []
    for i in range(n + ceiling_extra):
        t = (at + timedelta(seconds=2 * i)).isoformat()
        row = {"at": t, "bytes": 100, "kind": "uw_news", "params": {"ticker": "S%d" % i}, "result": "OK", "symbol": "S%d" % i}
        if error and i == n - 1:
            row.update(result="ERROR", error_kind=error)
        lines.append(row)
        body = json.dumps({"data": [i]}).encode()
        sha = hashlib.sha256(body).hexdigest()
        p = store / rel_for(sha)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(gzip.compress(body) if payload_ok else b"corrupt")
        evid.append({"id": "e%d" % i, "kind": "uw_news", "symbol": "S%d" % i, "retrieved_at": t, "raw_sha256": sha, "status": "OK"})
    (run / "attempts.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    (store / "v2_evidence.jsonl").write_text("\n".join(json.dumps(x) for x in evid) + "\n", encoding="utf-8")
    state = {"jobs": {"collect-1": {"id": "collect-1", "mode": "LIVE", "status": status, "started_utc": at.isoformat(), "requests": n + ceiling_extra, "failures": [], "halted": None}}, "done_tasks": [], "requests_by_day": {}}
    (run / "collection_state.json").write_text(json.dumps(state), encoding="utf-8")
    return run, store


def results(v):
    return {c["check"]: c["result"] for c in v["checks"]}


def test_a_clean_after_close_run_within_the_ceiling_validates(tmp_path):
    run, store = make_run(tmp_path)
    v = B.validate_batch(run, store, NOW)
    assert v["ok"] is True and not [c for c in v["checks"] if c["result"] == "FAIL"] and v["job_id"] == "collect-1" and v["summary"]["attempts"] == 5


def test_each_violation_fails_validation(tmp_path):
    for name, kw, failing in (("inside", dict(at=datetime(2026, 11, 2, 16, 0, tzinfo=UTC)), "every attempt was outside regular US market hours"),
                              ("over", dict(n=48, ceiling_extra=5), "attempts within the ceiling (failures included)"),
                              ("rate", dict(error="rate_limited"), "no 429 / authentication error"),
                              ("auth", dict(error="auth"), "no 429 / authentication error"),
                              ("nokill", dict(kill=False), "kill switch re-engaged"),
                              ("running", dict(status="RUNNING"), "job finished (COMPLETED or PARTIAL)"),
                              ("corrupt", dict(payload_ok=False), "raw payload files verify against their hashes")):
        run, store = make_run(tmp_path / name, **kw)
        v = B.validate_batch(run, store, NOW)
        assert v["ok"] is False and results(v)[failing] == "FAIL", name


def test_missing_files_and_stale_evidence_fail(tmp_path):
    (tmp_path / "r").mkdir()
    (tmp_path / "s").mkdir()
    v = B.validate_batch(tmp_path / "r", tmp_path / "s", NOW)
    assert v["ok"] is False and results(v)["attempt log exists"] == "FAIL" and results(v)["a LIVE job is recorded"] == "FAIL"
    run, store = make_run(tmp_path / "old")
    assert results(B.validate_batch(run, store, NOW + timedelta(hours=40)))["evidence is fresh"] == "FAIL"


def test_the_marker_is_written_only_on_explicit_seal_by_a_named_person_and_never_overwritten(tmp_path):
    run, store = make_run(tmp_path)
    assert B.verify_marker(run) == {"valid": False, "reason": "NO_COMPLETION_MARKER", "marker": None}                    # files appearing is not completion
    with pytest.raises(ValueError):
        B.seal(run, store, NOW, " ")
    bad_run, bad_store = make_run(tmp_path / "bad", error="auth")
    with pytest.raises(RuntimeError):
        B.seal(bad_run, bad_store, NOW, "Syed")
    assert not (bad_run / B.MARKER).exists()
    m = B.seal(run, store, NOW, "Syed")
    assert m["sealed_by"] == "Syed" and m["batch_id"] == "collect-1" and B.verify_marker(run)["valid"] is True
    with pytest.raises(FileExistsError):
        B.seal(run, store, NOW, "Syed")


def test_a_marker_is_rejected_when_the_files_it_hashed_change_or_it_is_malformed(tmp_path):
    run, store = make_run(tmp_path)
    B.seal(run, store, NOW, "Syed")
    with (run / "attempts.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": NOW.isoformat(), "result": "OK"}) + "\n")
    assert B.verify_marker(run) == {"valid": False, "reason": "MARKER_HASH_MISMATCH:attempts.jsonl", "marker": json.loads((run / B.MARKER).read_text(encoding="utf-8"))}
    run2, store2 = make_run(tmp_path / "two")
    (run2 / B.MARKER).write_text(json.dumps({"schema": "other"}), encoding="utf-8")
    assert B.verify_marker(run2)["reason"] == "MARKER_MALFORMED"
    (run2 / B.MARKER).write_text("{nope", encoding="utf-8")
    assert B.verify_marker(run2)["reason"] == "MARKER_UNREADABLE"


def test_the_real_pilot_run_fails_validation_because_it_ran_in_session():
    run, store = Path(r"C:\Users\initi\SmartMoveRun\pilot_run_20261102"), Path(r"C:\Users\initi\SmartMoveRun\pilot_store_20261102")
    if not (run / "attempts.jsonl").exists():
        pytest.skip("pilot run not on this machine")
    v = B.validate_batch(run, store, datetime(2026, 11, 2, 18, 0, tzinfo=UTC))
    assert v["ok"] is False and results(v)["every attempt was outside regular US market hours"] == "FAIL" and results(v)["attempts within the ceiling (failures included)"] == "PASS"
