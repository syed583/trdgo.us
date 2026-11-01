"""The one-time V2 live runner, rehearsed with the SIMULATED provider on a fake clock: probe then full phase, cap, kill switch, ordering, windows, 429 stop, resume without repeats."""
import json
import shutil
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "docs" / "smart_move" / "app"))
import smart_move_live as L  # noqa: E402
import smart_move_sim as S  # noqa: E402
import v2_live_run as R  # noqa: E402
from smvshadow import governor as gv  # noqa: E402

from test_full_universe_run import seed  # noqa: E402
from test_live import T0  # noqa: E402
from test_v2_collect import BODIES  # noqa: E402


class V2Sim(S.SimulatedProvider):
    def __init__(self, clock, stop_after=None):
        super().__init__(clock)
        self.stop_after, self.v2_calls = stop_after, []

    def fetch(self, kind, symbol, params):
        if kind.startswith("uw_") and kind != "uw_calendar":
            self.v2_calls.append((kind, symbol))
            if self.stop_after is not None and len(self.v2_calls) > self.stop_after:
                raise gv.ProviderError("rate_limited", 429)
            return json.dumps(BODIES[kind]).encode()
        return super().fetch(kind, symbol, params)


def v1_dir(tmp_path):
    store, report, first, _ = seed(tmp_path)
    d = tmp_path / "v1run"
    shutil.copytree(store, d / "store")
    shutil.copy2(report, d / "audit_report.json")
    return d


def run(monkeypatch, phase, out, v1, prov, clock, *extra):
    import dotenv
    import finnhub_service as fh
    import finviz_service as fz
    import unusualwhales_service as uw
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(uw, "configured", lambda: True)
    monkeypatch.setattr(fh, "_key", lambda: "x")
    monkeypatch.setattr(fz, "configured", lambda: True)
    monkeypatch.setattr(L, "InfraFetcher", lambda: prov)
    monkeypatch.setattr(L, "SystemClock", lambda: clock)
    saved = sys.stdout, sys.stderr
    try:
        return R.main(["--phase", phase, "--out-dir", str(out), "--v1-run", str(v1), *extra])
    finally:
        sys.stdout, sys.stderr = saved


def test_probe_then_full_respects_the_cap_the_kill_switch_and_never_repeats(tmp_path, monkeypatch):
    v1 = v1_dir(tmp_path)
    clock = gv.FakeClock(T0 + timedelta(minutes=2))
    prov = V2Sim(clock)
    prov.clock = clock
    out = tmp_path / "v2run"
    assert run(monkeypatch, "probe", out, v1, prov, clock, "--cap", "120") == 0
    pr = json.loads((out / "probe.json").read_text())
    assert set(pr["by_kind"]) == {"uw_analysts", "uw_estimates", "uw_flow_alerts", "uw_short_float", "uw_inst_own", "uw_news"} and all(v["status"] == "OK" for v in pr["by_kind"].values())
    assert (out / "store" / "KILL").exists()
    assert run(monkeypatch, "full", out, v1, prov, clock, "--cap", "120") in (0, 5)
    rep = json.loads((out / "report.json").read_text())
    assert rep["attempts_total"] <= 120 and rep["kill_switch_final"]["engaged"] and not rep["stop_events"] and (out / "store" / "KILL").exists()
    assert len(set(prov.v2_calls)) == len(prov.v2_calls)                                  # no V2 request repeated
    assert all(k != "uw_news" for k, _ in prov.v2_calls[len(pr["by_kind"]):]) or sum(1 for k, _ in prov.v2_calls if k == "uw_news") <= 4
    assert rep["collected_symbols"] and rep["universe"]["open_window_events"] >= 1


def test_a_429_stops_the_run_immediately_and_engages_the_kill_switch(tmp_path, monkeypatch):
    v1 = v1_dir(tmp_path)
    clock = gv.FakeClock(T0 + timedelta(minutes=2))
    prov = V2Sim(clock)
    prov.clock = clock
    out = tmp_path / "v2run"
    assert run(monkeypatch, "probe", out, v1, prov, clock, "--cap", "120") == 0
    prov.stop_after = len(prov.v2_calls) + 3
    assert run(monkeypatch, "full", out, v1, prov, clock, "--cap", "120") == 5
    rep = json.loads((out / "report.json").read_text())
    assert rep["status_full"] == "STOPPED_ON_PROVIDER_ERROR" and rep["stop_events"] and rep["kill_switch_final"]["engaged"]
    n = len(prov.v2_calls)
    assert n <= prov.stop_after + 2                                                       # at most the denied attempt(s) reached the provider layer; nothing continued after the stop


def test_full_phase_refuses_without_a_probe_and_a_probe_never_reuses_a_folder(tmp_path, monkeypatch):
    v1 = v1_dir(tmp_path)
    clock = gv.FakeClock(T0)
    prov = V2Sim(clock)
    prov.clock = clock
    assert run(monkeypatch, "full", tmp_path / "none", v1, prov, clock) == 3 and prov.v2_calls == []
    (tmp_path / "exists").mkdir()
    assert run(monkeypatch, "probe", tmp_path / "exists", v1, prov, clock) == 3 and prov.v2_calls == []
