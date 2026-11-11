"""2026-11-11 handoff, P0-02 scanner verification defects D1 / D2 / D4 (publisher + Monday-BMO variant).

D1  a calendar row whose vendor observation is merely too old was counted as "announcement session unknown";
D2  an event that only ever received PENDING_REFRESH got no WINDOW_MISSED marker when its window closed (the
    PENDING record counted as a prediction), so the dashboard showed "PENDING FINAL REFRESH" as a historical outcome;
D4  the status file hard-coded schedule.every_minutes = 60 although LIVE runs every 6 h (feeds the STALE label).
"""
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "docs" / "smart_move" / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import smart_move_live_publisher as PUB  # noqa: E402
from test_engine20 import NOW  # noqa: E402
from test_live_publisher import EV, synth  # noqa: E402


def _pending(sym):
    r = synth(sym, "NO_TRADE", EV)
    r["engine"]["continuation"]["reason_codes"] = ["INSUFFICIENT_COVERAGE"]
    r["missing_factors"] = [{"factor": "F5", "name": "Price / technical", "reason": "STALE_AGE"}]
    return r


CLOSED = {"window_start_utc": "2026-10-31T11:00:00+00:00", "cutoff_utc": "2026-11-01T11:00:00+00:00"}


def test_d1_a_stale_calendar_row_is_counted_as_stale_not_as_session_unknown(tmp_path):
    stale = synth("STL", "BUY", EV)
    stale["kind"] = "UNKNOWN"
    stale["calendar"] = {"status_now": "STALE", "reasons": ["ONLY_STALE_VENDOR_ROWS"]}
    unk = synth("UNK", "BUY", EV)
    unk["kind"] = "UNKNOWN"
    st = PUB.publish_rows([stale, unk], NOW, tmp_path, scan_id="s", mode="LIVE", scan_ok=True)
    assert st["events"]["skipped"] == {"calendar observation stale": 1, "announcement session unknown": 1}


def test_d2_an_event_that_only_ever_got_pending_refresh_is_marked_missed_when_its_window_closes(tmp_path):
    r = _pending("PEN")
    st1 = PUB.publish_rows([r], NOW, tmp_path, scan_id="s1", mode="LIVE", scan_ok=True)
    recs = PUB.read_ledger(tmp_path / PUB.LEDGER)["records"]
    assert recs[-1]["record_type"] == "PREDICTION" and recs[-1]["result"] == "PENDING_REFRESH" and st1["events"]["open_scanned"] == 1
    r["plan"] = CLOSED
    st2 = PUB.publish_rows([r], NOW + timedelta(hours=2), tmp_path, scan_id="s2", mode="LIVE", scan_ok=True)
    recs = PUB.read_ledger(tmp_path / PUB.LEDGER)["records"]
    assert recs[-1]["record_type"] == "WINDOW_MISSED" and recs[-1]["reason"] == "ONLY_PENDING_REFRESH_BEFORE_CLOSE"
    assert st2["events"]["window_missed_noted"] == 1
    PUB.publish_rows([r], NOW + timedelta(hours=3), tmp_path, scan_id="s3", mode="LIVE", scan_ok=True)
    assert len(PUB.read_ledger(tmp_path / PUB.LEDGER)["records"]) == 2                      # marked once, never again


def test_d2_a_real_prediction_before_the_close_never_gets_a_missed_marker(tmp_path):
    ok = synth("OKK", "BUY", EV)
    PUB.publish_rows([ok], NOW, tmp_path, scan_id="s4", mode="LIVE", scan_ok=True)
    ok["plan"] = CLOSED
    PUB.publish_rows([ok], NOW + timedelta(hours=2), tmp_path, scan_id="s5", mode="LIVE", scan_ok=True)
    assert [x["record_type"] for x in PUB.read_ledger(tmp_path / PUB.LEDGER)["records"]] == ["PREDICTION"]


def test_d4_the_status_schedule_describes_the_live_cadence(tmp_path):
    st = PUB.publish_rows([synth("AAA", "BUY", EV)], NOW, tmp_path, scan_id="s", mode="LIVE", scan_ok=True)
    assert st["schedule"] == {"every_minutes": 360, "live_every_minutes": 360, "dry_run_every_minutes": 60}
    st = PUB.publish_rows([synth("AAA", "BUY", EV)], NOW + timedelta(hours=1), tmp_path, scan_id="d", mode="DRY_RUN", scan_ok=True)
    assert st["schedule"]["every_minutes"] == 60 and st["schedule"]["live_every_minutes"] == 360
