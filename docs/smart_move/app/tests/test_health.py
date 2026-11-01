"""Health checks and backups over a synthetic run folder; also the real full-universe folder when present (read only)."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_health as H  # noqa: E402
from smart_move_event_registry import EventRegistry  # noqa: E402


def make(tmp_path, kill=True, dup=False, stops=False):
    d = tmp_path / "run_x"
    (d / "store").mkdir(parents=True)
    if kill:
        (d / "store" / "KILL").write_text("x")
    res = [{"symbol": s, "earnings_date": "2026-11-02", "capture_ts": "2026-10-31T21:00:00+00:00", "evidence": [{"raw_sha256": "ab"}]} for s in ("A", "B")]
    if dup:
        res.append(dict(res[0]))
    (d / "audit_report.json").write_text(json.dumps({"status": "OK", "universe": len(res), "results": res, "not_evaluated": [], "stop_events": [{"x": 1}] if stops else []}))
    (d / "store" / "evidence.jsonl").write_text("{}\n")
    return d


def test_healthy_run_passes_and_reconciles_with_the_registry(tmp_path):
    d = make(tmp_path)
    reg = EventRegistry(None)
    reg.apply_snapshot([{"symbol": "A", "earnings_date": "2026-11-02", "kind": "AMC", "confirmed": True}, {"symbol": "B", "earnings_date": "2026-11-02", "kind": "AMC", "confirmed": True}], "2026-10-31T20:00:00+00:00")
    h = H.health(d, reg, calendar_age_h=1.0)
    assert h["ok"] and all(c["ok"] for c in h["checks"])


@pytest.mark.parametrize("kw,failed", [({"kill": False}, "kill_switch_engaged"), ({"dup": True}, "no_duplicate_symbols"), ({"stops": True}, "no_provider_stop")])
def test_each_integrity_problem_is_flagged(tmp_path, kw, failed):
    h = H.health(make(tmp_path, **kw))
    assert not h["ok"] and [c["check"] for c in h["checks"] if not c["ok"]] == [failed]


def test_orphan_prediction_and_stale_calendar_are_flagged(tmp_path):
    d = make(tmp_path)
    h = H.health(d, EventRegistry(None), calendar_age_h=9.0)
    assert {c["check"] for c in h["checks"] if not c["ok"]} == {"predictions_reconcile_with_registry", "calendar_fresh"}


def test_backup_is_verified_never_overwrites_and_detects_tampering(tmp_path):
    from datetime import datetime, timezone
    d = make(tmp_path)
    t = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
    b = H.backup(d, tmp_path / "bk", now=t)
    assert H.verify_backup(b["dest"]) == {"ok": True, "files": b["files"], "mismatched_or_missing": []}
    with pytest.raises(FileExistsError):
        H.backup(d, tmp_path / "bk", now=t)
    (Path(b["dest"]) / "store" / "evidence.jsonl").write_text("tampered")
    assert H.verify_backup(b["dest"])["mismatched_or_missing"] == ["store/evidence.jsonl"]


def test_health_route_is_read_only_and_reports_the_newest_run(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import smart_move_local_server as srv
    make(tmp_path)
    monkeypatch.setenv("SMART_MOVE_RUN_ROOT", str(tmp_path))
    j = TestClient(srv.app).get("/api/smart-move/health").json()
    assert j["read_only"] is True and j["sends_requests"] is False and j["run"] == "run_x" and any(c["check"] == "kill_switch_engaged" and c["ok"] for c in j["checks"])
