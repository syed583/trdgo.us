"""Smart Money Move LIVE dashboard reader. The fixture under tests/fixtures/smart_live was produced by the real (private) publisher from INVENTED rows, so these tests also pin the writer/reader contract. No provider, no network."""
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
FIX = Path(__file__).resolve().parent / "fixtures" / "smart_live"
T2 = datetime(2026, 11, 2, 21, 30, tzinfo=UTC)                           # the instant of the fixture's last pass (16:30 ET)


@pytest.fixture
def live(tmp_path):
    d = tmp_path / "state" / "live"
    shutil.copytree(FIX, d)
    return d


def rows(out):
    return {r["symbol"]: r for r in out["rows"]}


def test_not_configured_says_so_and_shows_nothing_as_live():
    out = SL.load(None, T2)
    assert out["configured"] is False and out["rows"] == [] and "not installed" in out["note"]


def test_the_writers_real_output_is_accepted_and_every_required_field_is_present(live):
    out = SL.load(str(live), T2 + timedelta(minutes=10))
    assert out["ledger"]["ok"] and out["ledger"]["records"] == 6 and out["state"] == "OK" and out["kill_switch"] is False
    r = rows(out)
    assert {k: r[k]["label"] for k in ("AAA", "BBB", "CCC", "LOW")} == {"AAA": "LIVE", "BBB": "LIVE", "CCC": "LIVE", "LOW": "LIVE"} and r["OLD"]["label"] == "WINDOW MISSED"
    a = r["AAA"]
    assert (a["signal"], a["earnings_date"], a["session"]) == ("BUY", "2026-11-04", "BMO") and a["generated_at_et"] == "2026-11-02 16:30 EST" and a["evidence_latest_et"] == "2026-11-02 16:20 EST"
    assert a["evidence_age_h"] == 0.3 and a["evidence_items"] == 3 and a["coverage"] == "30/40" and a["window_cutoff_et"] == "2026-11-03 06:00 EST" and a["hours_to_cutoff"] == 13.3
    assert r["BBB"]["signal"] == "SELL" and r["CCC"]["signal"] == "NO TRADE" and r["LOW"]["signal"] == "NO VALID PREDICTION" and any("F7" in m for m in r["LOW"]["missing"])
    assert r["OLD"]["signal"] is None and r["OLD"]["missing"] == ["no prediction was produced before the window closed (nothing is back-filled)"]


def test_the_status_block_answers_last_next_scanned_skipped_and_requests(live):
    out = SL.load(str(live), T2 + timedelta(minutes=10))
    s, e, q = out["scan"], out["events"], out["requests"]
    assert s["mode"] == "LIVE" and s["last_successful_scan_et"] == "2026-11-02 16:30 EST" and s["minutes_since_success"] == 10 and s["last_scan_ok"] is True
    assert s["next_scan_expected_et"] == "2026-11-02 17:31 EST" and s["last_trigger"]["event"] == "FINISHED"
    assert e["open_scanned"] == 4 and e["published_new"] == 1 and e["unchanged"] == 3 and e["skipped"] == {}
    assert (q["used_today"], q["remaining_today"], q["daily_limit"], q["run_limit"], q["concurrency"]) == (17, 683, 700, 150, 1)
    assert out["calendar"] == {"age_h": 2.2, "through": "2026-11-04"} and out["counts"] == {"LIVE": 4, "WINDOW MISSED": 1}


def test_a_scan_that_is_too_old_turns_every_open_call_stale_not_live(live):
    out = SL.load(str(live), T2 + timedelta(minutes=151))
    assert out["state"] == "STALE" and "151" in out["state_text"] and {r["label"] for r in out["rows"] if r["symbol"] in ("AAA", "BBB", "CCC", "LOW")} == {"STALE"}
    assert SL.load(str(live), T2 + timedelta(minutes=150))["state"] == "OK"                      # the limit is inclusive
    assert SL.load(str(live), T2 + timedelta(minutes=151), stale_min=240)["state"] == "OK"        # configurable


def test_once_the_prediction_window_has_closed_a_call_is_historical_never_live(live):
    after = datetime(2026, 11, 3, 11, 5, tzinfo=UTC)                                           # just after the 11:00Z cutoff
    out = SL.load(str(live), after)
    assert {r["symbol"]: r["label"] for r in out["rows"] if r["symbol"] in ("AAA", "BBB")} == {"AAA": "HISTORICAL", "BBB": "HISTORICAL"} and rows(out)["AAA"]["hours_to_cutoff"] is None


def test_the_kill_switch_stops_the_collector_and_nothing_stays_live(live):
    (live.parent / "KILL").write_text("engaged", encoding="utf-8")
    out = SL.load(str(live), T2 + timedelta(minutes=5))
    assert out["state"] == "STOPPED" and out["kill_switch"] is True and out["scan"]["next_scan_expected_at"] is None and "LIVE" not in out["counts"]


def test_a_tampered_ledger_makes_every_row_unavailable_and_says_why(live):
    f = live / "live_predictions.jsonl"
    lines = f.read_text(encoding="utf-8").splitlines()
    lines[1] = lines[1].replace('"SELL"', '"BUY"')
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = SL.load(str(live), T2 + timedelta(minutes=5))
    assert out["ledger"]["ok"] is False and "hash chain" in out["ledger"]["problem"] and out["rows"] == [] and out["counts"] == {}
    f.write_text("\n".join(lines[:1] + lines[2:]) + "\n", encoding="utf-8")                  # a removed line is also detected
    assert SL.load(str(live), T2)["ledger"]["ok"] is False


def test_a_half_written_last_line_is_not_damage(live):
    with open(live / "live_predictions.jsonl", "a", encoding="utf-8") as fh:
        fh.write('{"record_type": "PRED')
    assert SL.load(str(live), T2)["ledger"]["ok"] is True


def test_dry_run_only_history_is_not_a_live_state(tmp_path):
    d = tmp_path / "live"
    d.mkdir()
    (d / "live_status.json").write_text(json.dumps({"mode": "DRY_RUN", "written_at": T2.isoformat(), "scan_ok": True, "last_successful_scan_at": None, "events": {"open_scanned": 12, "published_new": 0},
                                                    "requests": {"used_today": 0, "ny_date": "2026-11-02"}, "limits": {"daily_limit": 700, "run_limit": 150, "concurrency": 1}, "schedule": {"every_minutes": 60}}), encoding="utf-8")
    out = SL.load(str(d), T2)
    assert out["state"] == "DRY_RUN" and out["rows"] == [] and out["events"]["open_scanned"] == 12 and out["requests"]["remaining_today"] == 700


def test_a_blocked_trigger_is_reported_with_its_reason(live):
    with open(live / "collection_runs.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"event": "BLOCKED", "at": (T2 + timedelta(hours=1)).isoformat(), "reason": "live run without an attestation file"}) + "\n")
    out = SL.load(str(live), T2 + timedelta(hours=1, minutes=1))
    assert out["state"] == "BLOCKED" and "attestation" in out["state_text"]


def test_requests_used_belongs_to_the_new_york_day_it_was_counted_for(live):
    nextday = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)
    q = SL.load(str(live), nextday)["requests"]
    assert q["counted_for"] == "2026-11-02" and q["used_today"] == 0 and q["remaining_today"] == 700                 # a new New York day starts from zero


def test_the_historical_snapshot_is_passed_through_marked_not_live(live):
    meta = {"generated_at": "2026-11-03T08:29:08+00:00", "rows": 20, "label": "HISTORICAL", "is_live_signal": False}
    assert SL.load(str(live), T2, snapshot_meta=meta)["historical_snapshot"] == meta


def test_the_route_serves_the_view_reads_only_files_and_keeps_the_snapshot_historical(live, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from earnings_monitor import routes
    monkeypatch.setattr(routes, "now", lambda: T2 + timedelta(minutes=10))
    monkeypatch.setenv("SMART_MOVE_LIVE_DIR", str(live))
    monkeypatch.delenv("SMART_MOVE_ENGINE20_DIR", raising=False)
    app = FastAPI()
    app.include_router(routes.router)
    c = TestClient(app)
    j = c.get("/api/earnings-monitor/smart-move/live").json()
    assert j["status"] == "OK" and j["configured"] and j["state"] == "OK" and j["counts"] == {"LIVE": 4, "WINDOW MISSED": 1} and j["historical_snapshot"] is None
    monkeypatch.delenv("SMART_MOVE_LIVE_DIR")
    assert c.get("/api/earnings-monitor/smart-move/live").json()["configured"] is False
