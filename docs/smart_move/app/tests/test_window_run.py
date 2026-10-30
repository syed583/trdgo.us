"""The supervised window collection, rehearsed with the SIMULATED provider and a fake clock: cap, selection, window rule, kill switch, stored predictions."""
import json
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "docs" / "smart_move" / "app"))
import smart_move_live as L  # noqa: E402
import smart_move_sim as S  # noqa: E402
import supervised_window_run as W  # noqa: E402
from smvshadow import governor as gv  # noqa: E402

from test_live import T0, make  # noqa: E402


def seeded(tmp_path, start, fetcher=None):
    """A service whose store already holds yesterday's calendar observations (as the real run's --precheck-store provides) and a provider on the same clock."""
    clock0 = gv.FakeClock(T0)
    s0, prov0, _ = make(tmp_path / "seed", start=T0)
    s0.refresh_calendar([8])
    clock = gv.FakeClock(start)
    cfg = L.LiveConfig(mode="SIMULATION", store_dir=tmp_path / "run", refresh_enabled=False, authorized_daily=0, spacing_s=0.0)
    prov = fetcher or S.SimulatedProvider(gv.FakeClock(T0))
    prov.clock = clock
    svc = L.RefreshService(cfg, prov, clock)
    for r in s0.store.read("cal_obs"):
        svc.store.append("cal_obs", r)
    return svc, prov, clock


def test_nothing_is_sent_while_no_window_is_open(tmp_path):
    svc, prov, _ = seeded(tmp_path, T0 + timedelta(hours=26))         # after the windows of the simulated events have closed
    rep = W.collect(svc)
    assert rep["status"] == "WINDOWS_NOT_OPEN" and rep["requests_sent"] == 0 and prov.calls == []
    assert svc.kill_engaged and svc.store.read("requests") == []


def test_window_run_respects_the_cap_the_ten_names_and_stores_timestamped_predictions(tmp_path):
    svc, prov, clock = seeded(tmp_path, T0 + timedelta(minutes=1))
    rep = W.collect(svc)
    assert rep["status"] == "OK" and rep["attempts_total"] <= 60 and len(prov.calls) <= 60
    assert len(rep["selected"]) == 10 and [s["marketcap"] for s in rep["selected"]] == sorted((s["marketcap"] for s in rep["selected"]), reverse=True)
    names = {s["symbol"] for s in rep["selected"]}
    assert {c[1] for c in prov.calls if c[0] in ("earnings", "vol_context", "iv_rank")} <= names             # evidence only for the selected names
    assert len(rep["results"]) == 10 and all(r["capture_ts"] and r["hours_to_cutoff"] > 0 for r in rep["results"])      # all captured before their cutoff
    sigs = {r[v]["signal"] for r in rep["results"] for v in ("continuation", "contrarian")}
    assert sigs <= {"BUY", "SELL", "NO_TRADE"} and any(r["available_points"] >= 24 for r in rep["results"])
    stored = svc.store.read("predictions")
    assert stored and all(p["captured_at"] and p["evidence"] and p["mode"] == "SIMULATION" for p in stored if p["symbol"] in names)
    cutoffs = {s["symbol"]: s["window_cutoff_utc"] for s in rep["selected"]}
    assert all(p["captured_at"] < cutoffs[p["symbol"]] for p in stored if p["symbol"] in names)             # saved before the announcement window closes
    svc.engage_kill_switch("done")
    assert svc.run()["status"] == "KILL_SWITCH"


def test_failures_and_retries_still_stay_under_the_cap(tmp_path):
    class Failing(S.SimulatedProvider):
        def fetch(self, kind, symbol, params):
            if kind in ("ohlc", "earnings"):
                self.calls.append((kind, symbol))
                raise gv.ProviderError("transient", 503)
            return super().fetch(kind, symbol, params)
    svc, prov, _ = seeded(tmp_path, T0 + timedelta(minutes=1), fetcher=Failing(gv.FakeClock(T0)))
    rep = W.collect(svc)
    assert rep["attempts_total"] <= 60 and len(prov.calls) <= 60
    assert all(r["continuation"]["signal"] == "NO_TRADE" and r["available_points"] < 24 for r in rep["results"])        # no evidence, no signal


def test_rate_limit_stops_the_run(tmp_path):
    class Limited(S.SimulatedProvider):
        def fetch(self, kind, symbol, params):
            if kind == "ohlc" and symbol != "SPY":
                self.calls.append((kind, symbol))
                raise gv.ProviderError("rate_limited", 429)
            return super().fetch(kind, symbol, params)
    svc, prov, _ = seeded(tmp_path, T0 + timedelta(minutes=1), fetcher=Limited(gv.FakeClock(T0)))
    rep = W.collect(svc)
    n = len(prov.calls)
    assert any(d == "STOP_429" for d in rep["denials"]) and n < 60
