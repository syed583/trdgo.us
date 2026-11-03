"""Engine20 snapshot overlay: read-only, no provider, no network, hash-checked on every read, honest labels. Self-contained: needs only the two overlay modules and fastapi."""
import ast
import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
import smart_move_engine20_routes as R  # noqa: E402
import smart_move_engine20_snapshot as S  # noqa: E402

ALLOWED_IMPORTS = {"__future__", "os", "json", "gzip", "hashlib", "shutil", "sys", "tempfile", "datetime", "pathlib", "typing", "fastapi", "smart_move_engine20_snapshot"}


def row(sym, result, bull, bear, cov=30):
    return {"symbol": sym, "company": sym + " INC", "event_date": "2026-11-04", "session": "BMO", "result": result, "reason": "r", "reason_codes": [], "coverage_points": cov, "coverage_max": 40, "bull": bull, "bear": bear,
            "liquidity": "PASS", "adv20_usd": 5e8, "missing_factors": [["F7", "NOT_CAPTURED_LIVE_IN_WINDOW"]], "window": "OPEN", "window_cutoff_utc": "2026-11-03T11:00:00+00:00",
            "evidence_retrieved": ["2026-11-03T08:29:00+00:00"], "model_version": "score40-v1"}


@pytest.fixture
def snap_dir(tmp_path):
    rep = tmp_path / "r.json"
    rows = [row("AAA", "BUY", 20, 0), row("BBB", "SELL", 0, 20), row("CCC", "NO TRADE (genuine)", 10, 0), row("DDD", "NO VALID PREDICTION", 3, 7, cov=15)]
    rep.write_text(json.dumps({"as_of": "2026-11-03T08:29:08+00:00", "counts": {}, "rows": rows, "model_version": "score40-v1"}), encoding="utf-8")
    S.build_snapshot(rep, {"verdict": "PASS", "problems": []}, tmp_path / "snaps", source="run / store (48 attempts)")
    return tmp_path / "snaps"


def client():
    app = FastAPI()
    app.include_router(R.router)
    return TestClient(app)


def test_routes_serve_a_historical_snapshot_and_never_a_live_signal(snap_dir, monkeypatch):
    monkeypatch.setenv("SMART_MOVE_ENGINE20_DIR", str(snap_dir))
    j = client().get("/api/smart-move/engine20/latest").json()
    assert j["status"] == "OK" and j["is_live_signal"] is False and j["kind"] == "HISTORICAL_SNAPSHOT" and len(j["rows"]) == 4
    assert {r["category"] for r in j["rows"]} == {"BUY", "SELL", "NO_TRADE", "NO_VALID_PREDICTION"}
    assert all("Historical snapshot generated" in r["snapshot_label"] for r in j["rows"])


def test_without_configuration_or_snapshot_nothing_is_invented(tmp_path, monkeypatch):
    monkeypatch.delenv("SMART_MOVE_ENGINE20_DIR", raising=False)
    assert client().get("/api/smart-move/engine20/latest").json()["status"] == "NO_SNAPSHOT"
    monkeypatch.setenv("SMART_MOVE_ENGINE20_DIR", str(tmp_path))
    assert client().get("/api/smart-move/engine20/latest").json()["status"] == "NO_SNAPSHOT"


def test_hash_is_checked_on_every_read(snap_dir, monkeypatch):
    monkeypatch.setenv("SMART_MOVE_ENGINE20_DIR", str(snap_dir))
    c = client()
    assert c.get("/api/smart-move/engine20/latest").json()["status"] == "OK"
    f = next(snap_dir.glob("engine20_snapshot_*.json"))
    f.write_text(f.read_text(encoding="utf-8").replace("BUY", "SELL", 1), encoding="utf-8")
    j = c.get("/api/smart-move/engine20/latest").json()
    assert j["status"] == "SNAPSHOT_UNVERIFIED" and "rows" not in j


def test_outcomes_never_grade_anything_without_the_outcome_module(snap_dir, monkeypatch):
    monkeypatch.setenv("SMART_MOVE_ENGINE20_DIR", str(snap_dir))
    monkeypatch.setitem(sys.modules, "smart_move_outcomes", None)
    j = client().get("/api/smart-move/engine20/outcomes").json()
    assert j["available"] is False and j["rows"] == []


def test_routes_are_get_only_and_work_with_every_network_connection_blocked(snap_dir, monkeypatch):
    monkeypatch.setenv("SMART_MOVE_ENGINE20_DIR", str(snap_dir))

    def refuse(*a, **k):
        raise AssertionError("network access attempted")
    monkeypatch.setattr(socket, "create_connection", refuse)                # outbound connections and name lookups are refused (the local event loop's own socketpair is not touched)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    c = client()
    assert c.get("/api/smart-move/engine20/latest").status_code == 200 and c.get("/api/smart-move/engine20/outcomes").status_code == 200
    for m in ("post", "put", "patch", "delete"):
        assert getattr(c, m)("/api/smart-move/engine20/latest").status_code == 405


def test_the_two_modules_import_nothing_that_could_reach_a_provider():
    forbidden = ("requests", "httpx", "urllib", "aiohttp", "socket", "http", "unusualwhales", "finnhub", "finviz", "smart_move_live", "smart_move_collection", "infra")
    for f in ("smart_move_engine20_routes.py", "smart_move_engine20_snapshot.py"):
        tree = ast.parse((BACKEND / f).read_text(encoding="utf-8"))
        top = {n.names[0].name.split(".")[0] for n in tree.body if isinstance(n, ast.Import)} | {n.module.split(".")[0] for n in tree.body if isinstance(n, ast.ImportFrom) and n.module}
        assert top <= ALLOWED_IMPORTS, (f, top - ALLOWED_IMPORTS)
        every = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert not [m for m in every if any(m.lower().startswith(k) for k in forbidden)] or f.endswith("snapshot.py"), (f, every)
    # the only deferred imports are in offline helpers (verify, outcomes); the routes call neither verify nor build_snapshot, and the modules they would import are not part of the release
    rtree = ast.parse((BACKEND / "smart_move_engine20_routes.py").read_text(encoding="utf-8"))
    used = {n.attr for n in ast.walk(rtree) if isinstance(n, ast.Attribute)} | {n.id for n in ast.walk(rtree) if isinstance(n, ast.Name)}
    assert not (used & {"verify", "build_snapshot"}), used                  # the routes reach only load_latest / read_status / outcomes
    snap_tree = ast.parse((BACKEND / "smart_move_engine20_snapshot.py").read_text(encoding="utf-8"))
    deferred = {a.name.split(".")[0] for fn in ast.walk(snap_tree) if isinstance(fn, ast.FunctionDef) for n in ast.walk(fn) if isinstance(n, ast.Import) for a in n.names} | {n.module.split(".")[0] for fn in ast.walk(snap_tree) if isinstance(fn, ast.FunctionDef) for n in ast.walk(fn) if isinstance(n, ast.ImportFrom) and n.module}
    assert deferred <= {"smart_move_engine20", "smart_move_live", "smvshadow", "smart_move_outcomes"}, deferred
    for name in ("smart_move_engine20.py", "smart_move_live.py", "smart_move_outcomes.py"):
        assert not (BACKEND / name).exists()                                 # not shipped: an accidental call would fail with ImportError, never reach a provider


def test_serving_the_routes_loads_no_provider_module(snap_dir):
    code = ("import os,sys; sys.path.insert(0, %r); os.environ['SMART_MOVE_ENGINE20_DIR']=%r\n"
            "from fastapi import FastAPI; from fastapi.testclient import TestClient\n"
            "import smart_move_engine20_routes as R\n"
            "app=FastAPI(); app.include_router(R.router); c=TestClient(app)\n"
            "c.get('/api/smart-move/engine20/latest'); c.get('/api/smart-move/engine20/outcomes')\n"
            "bad=[m for m in sys.modules if any(k in m.lower() for k in ('unusualwhales','uw_','infra','finnhub','finviz','market_data','fetcher'))]\n"
            "print('LOADED', sorted(bad))\n") % (str(BACKEND), str(snap_dir))
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-400:]
    loaded = out.stdout.strip().splitlines()[-1]
    assert loaded == "LOADED []", loaded                                    # no Unusual Whales, Finnhub, Finviz or fetcher module is even imported
