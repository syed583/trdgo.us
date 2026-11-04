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
