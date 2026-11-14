"""Stage A.5 integration: the application-side pieces (provider ledger endpoint, Options Flow data_class) behind the real access gate, and the ledger across REAL process restarts.
No provider is contacted: swr.serve is replaced by a canned answer, and the restart test runs child Python processes that only touch the ledger file."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PASSWORD = "a5-test-password"
BACKEND = Path(__file__).resolve().parent.parent


@pytest.fixture
def gated(monkeypatch, tmp_path):
    monkeypatch.setenv("ACCESS_PASSWORD", PASSWORD)
    monkeypatch.setenv("SESSION_SECRET", "fixed-secret-for-tests")
    monkeypatch.setenv("PROVIDER_LEDGER_DIR", str(tmp_path / "ledger"))
    import main
    return TestClient(main.app)


def _login(c):
    assert c.post("/auth/login", json={"password": PASSWORD}).status_code == 200


def test_provider_ledger_endpoint_needs_the_session_and_answers_for_the_admin(gated):
    assert gated.get("/api/admin/provider-ledger").status_code == 401
    _login(gated)
    r = gated.get("/api/admin/provider-ledger")
    assert r.status_code == 200
    body = r.json()
    assert "providers" in body and "day" in body
    assert "UNUSUAL_WHALES" in body["providers"]
    assert gated.get("/api/admin/provider-ledger?days=9999").status_code == 200       # the window is clamped, not an error


def test_options_flow_keeps_its_answer_and_gains_a_data_class(gated, monkeypatch):
    import api_routes
    canned = {"status": "OK", "trades": [{"s": "AAPL"}], "unusual": [], "delay_minutes": 0, "freshness": {"kind": "LIVE"}}
    seen = {}
    monkeypatch.setattr(api_routes.swr, "serve", lambda key, build, ttl, **k: seen.setdefault("key", key) and canned)
    monkeypatch.setattr(api_routes.swr, "held_at", lambda key: None)
    _login(gated)
    r = gated.get("/api/options/flow/AAPL?limit=5")
    assert r.status_code == 200
    out = r.json()
    assert out["data_class"] in ("LIVE", "CACHED", "STALE", "HISTORICAL", "UNAVAILABLE")
    assert {k: v for k, v in out.items() if k != "data_class"} == canned            # nothing else changed
    assert seen["key"] == "options:flow:AAPL:5"                                      # the cache key is unchanged, so warm caches stay valid
    assert "data_class" not in canned                                                # the cached object itself was not mutated
    m = gated.get("/api/options/flow?limit=7")
    assert m.status_code == 200 and "data_class" in m.json()


def test_options_flow_survives_a_failing_classifier(gated, monkeypatch):
    import api_routes
    import signal_class
    canned = {"status": "OK", "trades": [{"s": "X"}]}
    monkeypatch.setattr(api_routes.swr, "serve", lambda key, build, ttl, **k: canned)
    monkeypatch.setattr(signal_class, "options_flow_class", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    _login(gated)
    r = gated.get("/api/options/flow/AAPL?limit=5")
    assert r.status_code == 200 and r.json() == canned                               # a labelling fault never breaks the data


CHILD = r"""
import json, os, sys
sys.path.insert(0, os.environ["A5_BACKEND"])
import unusualwhales_service as uw
import provider_ledger as pl
count = int(sys.argv[1])
uw._spent.update(day=uw._today(), count=count)
snap = pl.snapshot(1)
pl.record("FINNHUB", "OK", 12.0)
assert pl.flush()
print(json.dumps({"uw": snap["providers"]["UNUSUAL_WHALES"]["today_requests"]}))
"""


def _run_child(tmp, count):
    env = dict(os.environ, A5_BACKEND=str(BACKEND), PROVIDER_LEDGER_DIR=str(tmp), SESSION_SECRET="x")
    p = subprocess.run([sys.executable, "-c", CHILD, str(count)], capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode == 0, p.stderr[-800:]
    return json.loads(p.stdout.strip().splitlines()[-1])["uw"]


def test_ledger_survives_real_restarts_without_double_counting_unusual_whales(tmp_path):
    d = tmp_path / "ledger"
    assert _run_child(d, 10) == 10                    # process 1 counted 10
    assert _run_child(d, 3) == 13                     # process 2 restarted its own counter at 3: banked 10 + 3, not 3 and not 23
    assert _run_child(d, 5) == 18                     # process 3: banked 10 + 3, plus its own 5
    assert _run_child(d, 5) == 23                     # process 4 starts at 5 again: banked 10 + 3 + 5, plus its own 5


def test_warm_universe_read_never_builds_the_universe_or_calls_a_provider(monkeypatch):
    """The monitor's coverage-reason read must only LOOK at the held universe. universe_service.symbols()/get() would rebuild it through the provider screener when it is missing or stale; peek() cannot."""
    import swr
    import universe_service as US
    from earnings_monitor.adapters import ProdAdapters

    def boom(*a, **k):
        raise AssertionError("a provider build was attempted")
    monkeypatch.setattr(swr, "serve", boom)
    monkeypatch.setattr(US, "build", boom)
    monkeypatch.setattr(US, "get", boom)
    monkeypatch.setattr(US, "symbols", boom)
    monkeypatch.setattr(US, "peek", lambda: None)
    assert ProdAdapters.warm_universe(object.__new__(ProdAdapters)) is None          # nothing held: no answer, no build
    monkeypatch.setattr(US, "peek", lambda: {"rows": [{"ticker": "AAPL"}, {"ticker": "MSFT"}]})
    out = ProdAdapters.warm_universe(object.__new__(ProdAdapters))
    # horizon_days is the CALENDAR span that covers 10 trading days, so it moves with the weekday the test runs on
    # (13 from a Saturday, 14 from a Monday). Assert the invariant, not one weekday's answer.
    assert out["symbols"] == ["AAPL", "MSFT"] and 10 <= out["horizon_days"] <= 20


def _arm_provider_tripwires(monkeypatch):
    """Every way the monitor could reach a provider or rebuild a cache now raises: the real provider doors, urllib, any non-local socket, and the swr build/refresh paths."""
    import socket
    import urllib.request
    import finnhub_service
    import finviz_service
    import swr
    import universe_service
    import unusualwhales_service

    touched = []

    def boom(*a, **k):
        touched.append(1)                                  # recorded as well as raised: a broad `except` in the code under test cannot hide a touch
        raise AssertionError("A PROVIDER OR BUILD PATH WAS TOUCHED")
    real_connect = socket.socket.connect

    def guarded(self, address, *a, **k):
        host = address[0] if isinstance(address, tuple) else str(address)
        if host not in ("127.0.0.1", "::1", "localhost"):
            touched.append(address)
            raise AssertionError("network access attempted: %r" % (address,))
        return real_connect(self, address, *a, **k)
    monkeypatch.setattr(socket.socket, "connect", guarded)
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(unusualwhales_service, "get", boom)
    monkeypatch.setattr(finnhub_service, "_get", boom)
    monkeypatch.setattr(finviz_service, "_get", boom)
    monkeypatch.setattr(universe_service, "build", boom)
    monkeypatch.setattr(swr, "serve", boom)
    monkeypatch.setattr(swr, "_refresh", boom, raising=False)
    monkeypatch.setattr(swr, "_run", boom, raising=False)
    return swr, touched


@pytest.mark.parametrize("universe_state", ["missing", "stale_over_24h", "fresh"])
def test_a_full_monitor_cycle_on_the_production_adapter_cannot_rebuild_the_universe_or_reach_a_provider(tmp_path, monkeypatch, universe_state):
    import time
    from datetime import datetime, timezone
    from earnings_monitor.adapters import ProdAdapters
    from earnings_monitor.sim import Clock, build_sim, write_approval
    from earnings_monitor.smart_move import SmartMoveSource
    swr, touched = _arm_provider_tripwires(monkeypatch)
    now = time.time()
    swr._values.pop("universe:top1000", None)
    if universe_state != "missing":
        age = 30 * 3600 if universe_state == "stale_over_24h" else 60
        swr._values["universe:top1000"] = (now - age, {"rows": [{"ticker": "AAA"}, {"ticker": "BBB"}], "count": 2})
    swr._values["v3:scan:v2:15"] = (now, {"rows": [{"symbol": "AAA", "signal": "BUY", "score": 72, "coverage": 80, "date": "2026-11-12", "company": "Alpha", "report_timing": "Before Open"}]})
    swr._values["earn:equity:AAA"] = (now, {"params": [{"name": "eps_estimates", "bias": 0.4, "weight": 16}]})
    clock = Clock(datetime(2026, 11, 9, 13, 0, tzinfo=timezone.utc))
    d = tmp_path / "em"
    d.mkdir()
    write_approval(d, clock(), mode="cache_only", days=14)
    ad = ProdAdapters(smart_move=SmartMoveSource(), session_factory=lambda: (_ for _ in ()).throw(RuntimeError("no db")), clock=clock)
    store, cfg, _, mon, sched, access = build_sim(d, clock, adapters=ad)
    for _ in range(3):                                                    # several full cycles: scan, evidence, recompute (which reads the warm universe), health and provider snapshots, alerts
        sched.tick(clock())
        sched.run_pending(clock())
        mon.recompute(clock())
        mon.evidence_scan(clock())
        mon.alert_dispatch(clock())
        clock.advance(minutes=20)
    assert touched == [], "the monitor touched a provider or build path %d time(s)" % len(touched)
    held = swr._values.get("universe:top1000")
    if universe_state == "missing":
        assert held is None                                              # nothing was built to fill the gap
    elif universe_state == "stale_over_24h":
        assert held[0] == now - 30 * 3600                                # the stale copy was left exactly as it was (no refresh was started)
    assert store.q("SELECT COUNT(*) n FROM provider_calls")[0]["n"] == 0 if _table_exists(store, "provider_calls") else True


def _table_exists(store, name):
    return bool(store.q("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)))
