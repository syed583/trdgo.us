"""Smart Money Move event-universe reader. The fixture under tests/fixtures/smart_universe was produced by the real (private) universe builder from INVENTED rows, so these tests pin the writer/reader contract. Files only: no provider, no network."""
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from earnings_monitor import smart_live as SL  # noqa: E402

UTC = timezone.utc
FIX = Path(__file__).resolve().parent / "fixtures" / "smart_universe"
T = datetime(2026, 11, 2, 20, 40, tzinfo=UTC)                        # ten minutes after the fixture was written


@pytest.fixture
def live(tmp_path):
    d = tmp_path / "live"
    shutil.copytree(FIX, d)
    return d


def test_not_configured_and_missing_file_are_reported_never_invented(tmp_path):
    a = SL.load_universe(None, T)
    assert a["configured"] is False and a["available"] is False and a["rows"] == [] and "not installed" in a["note"]
    b = SL.load_universe(str(tmp_path), T)
    assert b["configured"] is True and b["available"] is False and b["rows"] == [] and "not written an event universe" in b["note"]


def test_the_builders_real_output_is_read_with_every_field_the_dashboard_shows(live):
    out = SL.load_universe(str(live), T)
    assert out["available"] is True and out["stale"] is False and out["age_h"] == 0.17 and out["horizon_days"] == 30 and out["summary"]["events"] == 4 and out["summary"]["excluded_by_proxy"] == 0
    by = {r["symbol"]: r for r in out["rows"]}
    assert by["AAA"]["collection_status"] == "EVIDENCE_COMPLETE" and by["AAA"]["evidence"]["F5_price"] == "FRESH" and by["AAA"]["window_start"]["new_york"] and by["AAA"]["priority_proxy_tier"] == "A"
    assert by["CCC"]["collection_status"] == "DEFERRED" and by["CCC"]["deferred"] is True and "still eligible" in by["CCC"]["deferred_reason"]
    assert all(r["excluded_by_proxy"] is False for r in out["rows"]) and out["calendar_completeness"]["t0_complete"] is False


def test_an_old_file_is_flagged_stale_and_a_damaged_or_foreign_file_shows_nothing(live):
    assert SL.load_universe(str(live), T + timedelta(hours=9))["stale"] is True                       # nothing has refreshed it within 8 h
    (live / "universe.json").write_text("{not json", encoding="utf-8")
    assert SL.load_universe(str(live), T)["available"] is False
    (live / "universe.json").write_text(json.dumps({"schema": "something-else", "rows": []}), encoding="utf-8")
    bad = SL.load_universe(str(live), T)
    assert bad["available"] is False and bad["rows"] == [] and "not in the expected format" in bad["note"]


def test_the_route_reads_only_the_file_and_changes_nothing(live, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from earnings_monitor import routes
    before = {p.name: p.read_bytes() for p in live.iterdir()}
    monkeypatch.setattr(routes, "now", lambda: T)
    monkeypatch.setenv("SMART_MOVE_LIVE_DIR", str(live))
    app = FastAPI()
    app.include_router(routes.router)
    c = TestClient(app)
    j = c.get("/api/earnings-monitor/smart-move/universe").json()
    assert j["status"] == "OK" and j["available"] and len(j["rows"]) == 4
    assert {p.name: p.read_bytes() for p in live.iterdir()} == before                                  # read only
    monkeypatch.delenv("SMART_MOVE_LIVE_DIR")
    assert c.get("/api/earnings-monitor/smart-move/universe").json()["configured"] is False
