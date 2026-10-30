"""Offline tests of event_count_universe_v3.py against a throw-away local PostgreSQL (SMV_TEST_PG, 127.0.0.1 only). Synthetic rows; no production data."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "event_count_universe_v3.py"
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from test_event_count_universe import build                                    # same synthetic database as the v2 tests
PG = os.environ.get("SMV_TEST_PG", "")
pytestmark = pytest.mark.skipif(not PG.startswith("postgresql://") or "127.0.0.1" not in PG, reason="SMV_TEST_PG not set")


def run(out, extra=()):
    return subprocess.run([sys.executable, "-I", str(SCRIPT), "--out-dir", str(out), "--today", "2026-10-30", *extra], capture_output=True, text=True, env=dict(os.environ, DATABASE_URL=PG))


def test_v3_sections_on_synthetic_data_and_database_unchanged(tmp_path):
    import psycopg
    c = psycopg.connect(PG, autocommit=True)
    build(c)
    snap = lambda: (c.execute("SELECT count(*), sum(id) FROM model_predictions").fetchone(), c.execute("SELECT count(*) FROM earnings_event_outcomes").fetchone(), c.execute("SELECT state FROM local_warm_state").fetchone())
    before = snap()
    r = run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    rep = json.loads(next(tmp_path.glob("event_counts_v3_*.json")).read_text())
    assert rep["version"] == "event-count-v3" and snap() == before
    v3 = rep["v3"]
    st = v3["staleness_by_days_to_earnings"]
    assert st["0-3 days"]["events"] == 1 and st["4-10 days"]["events"] == 2 and st["11-21 days"]["events"] == 0 and st["over 21 days"]["events"] == 0   # F | J, D
    assert st["4-10 days"]["not_refreshed_over_7_days"] == 2 and st["4-10 days"]["timing_unknown"] == 1 and st["4-10 days"]["first_seen_over_45_days_ahead"] == 1
    assert st["4-10 days"]["in_a_date_shift_or_uncertain_pair"] == 1
    dt = v3["daily_timing_future"]
    assert dt["2026-11-02"] == {"BMO": 0, "AMC": 1, "UNKNOWN": 0, "total": 1} and dt["2026-11-05"]["UNKNOWN"] == 1
    conf = v3["calendar_date_confidence_proxy"]["future_events"]
    assert sum(conf.values()) == 3 and conf.get("LOW", 0) == 2 and "NOT the CONFIRMED" in v3["calendar_date_confidence_proxy"]["definition"]
    pk = v3["peak_request_demand_220_core_30_full"]
    assert pk["peak_capture_day"]["events_with_window_touching_day"] == 2 and pk["peak_capture_day"]["requests"] == 2 * 5 + 2 * 13 + 24
    assert pk["days_above_1700_with_retry_margin"] == [] and "NOT an authorization" in pk["model"]
    mu = v3["projected_moved_uncertain_future"]
    assert mu["counts"]["uncertain"] == 1 and mu["counts"]["moved"] == 0 and mu["counts"]["projected_first_seen_over_45_days_ahead"] == 1
    assert "staleness by days to earnings" in r.stdout and "date confidence proxy" in r.stdout


def test_v3_pure_functions_peak_arithmetic():
    import event_count_universe_v3 as m
    from datetime import date, timedelta
    today = date(2026, 10, 30)
    kept = [{"symbol": "S%d" % i, "date": date(2026, 11, 3), "kind": "BMO", "first_snap": today, "last_snap": today, "n_snaps": 5, "n_models": 3} for i in range(230)]
    v = m.v3_sections(kept, [], [], today)
    top = v["peak_request_demand_220_core_30_full"]["peak_event_date"]
    assert top["events"] == 230 and top["requests"] == 220 * 5 + 30 * 13 + 24 == 1514
    assert top["requests_with_10pct_retry_margin"] == 1666 and v["peak_request_demand_220_core_30_full"]["days_above_1700_with_retry_margin"] == []
    assert v["calendar_date_confidence_proxy"]["future_events"] == {"HIGH": 230}


def test_v3_only_read_statements_and_no_overwrite(tmp_path, monkeypatch):
    import psycopg
    c = psycopg.connect(PG, autocommit=True)
    build(c)
    import event_count_universe_v3 as ecu
    sent, real = [], psycopg.connect

    class Rec:
        def __init__(self, cur): self.cur = cur
        def execute(self, sql, *a, **k):
            sent.append(" ".join(str(sql).split())); return self.cur.execute(sql, *a, **k)
        def __getattr__(self, n): return getattr(self.cur, n)

    class Conn:
        def __init__(self, conn): self.conn = conn
        def cursor(self): return Rec(self.conn.cursor())
        def __getattr__(self, n): return getattr(self.conn, n)
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: Conn(real(*a, **k)))
    monkeypatch.setenv("DATABASE_URL", PG)
    monkeypatch.setattr(sys, "argv", ["x", "--out-dir", str(tmp_path), "--today", "2026-10-30"])
    ecu.main()
    assert set(s.split()[0].upper() for s in sent) <= {"SELECT", "WITH", "SHOW", "SET"}
    assert not [s for s in sent if re.search(r"\b(insert|update|delete|truncate|drop|alter|create|grant|copy|vacuum|call|do)\b", s, re.I)]
    src = SCRIPT.read_text(encoding="utf-8")
    assert "requests" not in src.split("def main")[0].split("import")[0] and "urllib" not in src and "httpx" not in src and "import requests" not in src
