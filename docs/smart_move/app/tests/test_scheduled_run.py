"""The one-time unattended wrapper: audit report, kill switch in every outcome, time guards, exactly-once guard. Simulated provider and fake clock only."""
import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "docs" / "smart_move" / "app"))
import scheduled_run as SR  # noqa: E402
import smart_move_sim as S  # noqa: E402
from smvshadow import governor as gv  # noqa: E402

from test_live import T0  # noqa: E402
from test_window_run import seeded  # noqa: E402


def test_full_run_writes_the_audit_and_re_engages_the_kill_switch(tmp_path):
    svc, prov, clock = seeded(tmp_path, T0 + timedelta(minutes=1))
    out = tmp_path / "out"
    out.mkdir()
    rep = SR.execute(out, svc)
    assert rep["status"] == "OK" and rep["attempts_total"] <= 60 and len(rep["selected"]) == 10 and len(rep["results"]) == 10
    assert rep["kill_switch_final"]["engaged"] is True and svc.kill_engaged and (svc.store.root / "KILL").exists()
    j = json.loads((out / "audit_report.json").read_text())
    md = (out / "audit_report.md").read_text()
    assert j["kill_switch_final"]["engaged"] and "final kill switch" in md and "Selected (largest market caps)" in md and "| Symbol |" in md
    assert all(r["capture_ts"] and r["hours_to_cutoff"] > 0 for r in j["results"]) and {r["continuation"]["signal"] for r in j["results"]} <= {"BUY", "SELL", "NO_TRADE"}
    assert svc.run()["status"] == "KILL_SWITCH"                           # nothing more can be sent afterwards


def test_time_guards_send_nothing(tmp_path):
    svc, prov, clock = seeded(tmp_path, T0 + timedelta(minutes=1))
    out = tmp_path / "early"
    out.mkdir()
    rep = SR.execute(out, svc, not_before=clock.now() + timedelta(hours=1))
    assert rep["status"] == "TOO_EARLY_NOTHING_SENT" and prov.calls == [] and rep["kill_switch_final"]["engaged"]
    svc2, prov2, clock2 = seeded(tmp_path / "b", T0 + timedelta(minutes=1))
    out2 = tmp_path / "late"
    out2.mkdir()
    rep2 = SR.execute(out2, svc2, not_after=clock2.now() - timedelta(minutes=1))
    assert rep2["status"] == "TOO_LATE_NOTHING_SENT" and prov2.calls == [] and rep2["kill_switch_final"]["engaged"]


def test_windows_not_open_sends_nothing_and_still_reports(tmp_path):
    svc, prov, _ = seeded(tmp_path, T0 + timedelta(hours=26))
    out = tmp_path / "closed"
    out.mkdir()
    rep = SR.execute(out, svc)
    assert rep["status"] == "WINDOWS_NOT_OPEN" and rep["requests_sent"] == 0 and prov.calls == [] and rep["kill_switch_final"]["engaged"] and (out / "audit_report.md").exists()


def test_an_unexpected_error_still_ends_in_a_report_and_an_engaged_kill_switch(tmp_path):
    class Boom(S.SimulatedProvider):
        def fetch(self, kind, symbol, params):
            if kind == "ohlc":
                raise RuntimeError("unexpected failure in the fetcher")
            return super().fetch(kind, symbol, params)
    svc, prov, _ = seeded(tmp_path, T0 + timedelta(minutes=1), fetcher=Boom(gv.FakeClock(T0)))
    out = tmp_path / "boom"
    out.mkdir()
    rep = SR.execute(out, svc)
    assert rep["status"] == "ERROR" and rep["errors"] and rep["errors"][0]["error"] == "RuntimeError" and rep["kill_switch_final"]["engaged"] is True


def test_rate_limit_and_authorization_failures_are_reported_as_stop_events(tmp_path):
    class Limited(S.SimulatedProvider):
        def fetch(self, kind, symbol, params):
            if kind == "ohlc" and symbol != "SPY":
                self.calls.append((kind, symbol))
                raise gv.ProviderError("rate_limited", 429)
            return super().fetch(kind, symbol, params)
    svc, prov, _ = seeded(tmp_path, T0 + timedelta(minutes=1), fetcher=Limited(gv.FakeClock(T0)))
    out = tmp_path / "limit"
    out.mkdir()
    rep = SR.execute(out, svc)
    assert "STOP_429" in rep["stop_events"] and rep["attempts_total"] < 60 and rep["kill_switch_final"]["engaged"]
    class Auth(S.SimulatedProvider):
        def fetch(self, kind, symbol, params):
            if kind == "uw_calendar":
                self.calls.append((kind, symbol))
                raise gv.ProviderError("auth", 401)
            return super().fetch(kind, symbol, params)
    svc2, prov2, _ = seeded(tmp_path / "b", T0 + timedelta(minutes=1), fetcher=Auth(gv.FakeClock(T0)))
    out2 = tmp_path / "auth"
    out2.mkdir()
    rep2 = SR.execute(out2, svc2)
    assert "STOP_AUTH" in rep2["stop_events"] and len(prov2.calls) <= 2 and rep2["kill_switch_final"]["engaged"]


def test_the_program_refuses_to_run_twice(tmp_path):
    existing = tmp_path / "already"
    existing.mkdir()
    r = subprocess.run([sys.executable, str(ROOT / "docs" / "smart_move" / "app" / "scheduled_run.py"), "--out-dir", str(existing), "--precheck-store", str(tmp_path)], capture_output=True, text=True)
    assert r.returncode == 3 and "exactly one execution" in r.stdout
