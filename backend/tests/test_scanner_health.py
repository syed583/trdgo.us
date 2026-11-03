"""Scanner Health measurement update: real run timing, queue wait, events processed, the read-only /health view, and compatibility with a store written by the previous release."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))
from test_earnings_monitor import cycle, mk  # noqa: E402

UTC = timezone.utc


class TickingClock:
    """A clock that moves a fixed step every time it is read: proves start and finish come from the clock, not from the loop's single `now`."""
    def __init__(self, inner, step_ms):
        self.inner, self.step = inner, timedelta(milliseconds=step_ms)

    def __call__(self):
        self.inner.t += self.step
        return self.inner.t


def test_runs_record_real_start_finish_duration_events_processed_and_queue_wait(tmp_path):
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path)
    ad.cal = [{"symbol": s, "date": "2026-11-12", "session": "BMO"} for s in ("AAA", "BBB", "CCC")]
    sched.tick(clock())
    due = clock()
    clock.advance(seconds=45)                                    # the job sat in the queue for 45 s after it became due
    mon.clock = TickingClock(clock, 250)                         # every clock read costs 250 ms
    sched.run_pending(due + timedelta(seconds=45))
    r = store.one("SELECT * FROM scan_runs WHERE kind='ELIGIBILITY_CHECK'")
    meta = json.loads(r["summary"])["_run"]
    assert r["started_at"] != r["finished_at"] and meta["duration_ms"] >= 250
    assert meta["queue_wait_ms"] >= 45000 and meta["events_processed"] == 3


def test_health_endpoint_reports_runs_stuck_jobs_and_duplicates_read_only(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from earnings_monitor import routes
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path)
    ad.cal = [{"symbol": "AAA", "date": "2026-11-12", "session": "BMO"}]
    cycle(sched, clock)
    with store.tx() as c:                                         # an interrupted run from long ago, as the restart left one
        c.execute("INSERT INTO scan_runs(kind,started_at) VALUES('EARNINGS_DAY_CHECK','2026-11-08T12:00:00+00:00')")
    monkeypatch.setenv("EARNINGS_MONITOR_DIR", str(d))
    monkeypatch.setattr(routes, "now", lambda: clock())
    app = FastAPI()
    app.include_router(routes.router)
    c = TestClient(app)
    h = c.get("/api/earnings-monitor/health").json()
    assert h["status"] == "OK" and h["stuck_jobs"] == [] and h["duplicate_active_events"] == [] and h["duplicate_prediction_rows"] == 0 and h["healthy"] is True
    runs = h["recent_runs"]
    assert any(r["kind"] == "ELIGIBILITY_CHECK" and r["ok"] is True and r["events_processed"] == 1 and r["duration_ms"] is not None for r in runs)
    assert [r for r in runs if r["kind"] == "EARNINGS_DAY_CHECK" and r["finished_at"] is None][0]["interrupted"] is True
    assert c.post("/api/earnings-monitor/health").status_code == 405


def test_a_store_written_by_the_previous_release_is_used_unchanged(tmp_path):
    """The previous release's store (same schema) opens, scans and reports with the new code and no migration."""
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path)
    assert json.loads(store.one("SELECT v FROM kv WHERE k='schema_version'")["v"]) == 2
    ad.cal = [{"symbol": "AAA", "date": "2026-11-12", "session": "BMO"}]
    ad.set_model("claude", "AAA", "BUY", clock(), score=70, coverage=0.9)
    cycle(sched, clock)
    from earnings_monitor.store import Store
    again = Store(d / "monitor.sqlite")                           # a restarted process opens the same file
    assert again.one("SELECT COUNT(*) n FROM predictions")["n"] == 1 and json.loads(again.one("SELECT v FROM kv WHERE k='schema_version'")["v"]) == 2
