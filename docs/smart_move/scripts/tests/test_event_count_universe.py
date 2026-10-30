"""Offline tests of event_count_universe.py (v2) against a throw-away local PostgreSQL (SMV_TEST_PG, 127.0.0.1 only). Synthetic rows; no production data.
Covers: counting events not rows, every merge/uncertain pair with its reason, coverage grid, read-only statement audit, no credential printing,
bounded/safe paths, schema-compatibility stop, and that the database is unchanged."""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "event_count_universe.py"
sys.path.insert(0, str(HERE.parent))
PG = os.environ.get("SMV_TEST_PG", "")
pytestmark = pytest.mark.skipif(not PG.startswith("postgresql://") or "127.0.0.1" not in PG, reason="SMV_TEST_PG not set")


def build(c):
    for t in ("model_predictions", "earnings_event_outcomes", "earnings_calendar", "local_warm_state"):
        c.execute("DROP TABLE IF EXISTS %s" % t)
    c.execute("CREATE TABLE model_predictions(id serial primary key, model text, symbol text, earnings_date date, report_timing text, snapshot_date date, predicted_at timestamptz, signal text)")
    c.execute("CREATE TABLE earnings_event_outcomes(symbol text, earnings_date date, status text)")
    c.execute("CREATE TABLE earnings_calendar(symbol text, earnings_date date, eps_actual float)")
    c.execute("CREATE TABLE local_warm_state(id int primary key, state text, updated_at timestamptz)")

    def add(sym, ed, lab, snaps, models=("m1", "m2", "m3")):
        for sd in snaps:
            for m in models:
                c.execute("INSERT INTO model_predictions(model,symbol,earnings_date,report_timing,snapshot_date,predicted_at,signal) VALUES (%s,%s,%s,%s,%s,%s::date + time '12:00','BUY')", (m, sym, ed, lab, sd, sd))
    add("A", "2026-10-28", "After Close", ["2026-10-25", "2026-10-26"])                 # 6 prediction rows, ONE event
    add("B", "2026-10-20", "Before Open", ["2026-10-10"])                               # moved: first date last predicted 10-10, second date appears 10-18 -> CONFIDENT merge
    add("B", "2026-10-22", "After Close", ["2026-10-18", "2026-10-19"])
    add("C", None, None, ["2026-10-25"], models=("m1", "m2"))                           # undated rows only
    add("D", "2026-11-05", None, ["2026-09-01", "2026-10-15"])                          # future, unknown timing, first seen > 45 days ahead, stale
    add("E", "2026-10-10", "Before Open", ["2026-09-25"])                               # past, no outcome row, last snapshot 15 days before
    add("F", "2026-11-02", "After Close", ["2026-10-29"])
    add("G", "2026-10-28", "Before Open", ["2026-10-26"])
    add("H", "2026-10-28", None, ["2026-10-26"])
    add("I", "2026-10-28", "After Close", ["2026-10-27"])
    add("J", "2026-10-21", "After Close", ["2026-10-20", "2026-10-21"])                 # two dates predicted at the same time, 14 days apart -> UNCERTAIN, kept separate
    add("J", "2026-11-04", "After Close", ["2026-10-20", "2026-10-21"])
    add("K", "2026-07-29", "After Close", ["2026-07-20"])                               # two real events 91 days apart: no relation at all
    add("K", "2026-10-28", "After Close", ["2026-10-26"])
    c.execute("INSERT INTO earnings_event_outcomes VALUES ('A','2026-10-28','GRADED'),('B','2026-10-22','PENDING_REACTION')")
    c.execute("INSERT INTO earnings_calendar VALUES ('E','2026-10-10',NULL),('A','2026-10-28',1.0)")
    c.execute("INSERT INTO local_warm_state VALUES (2, %s, now())", (json.dumps({"total": 4, "complete": 3, "day_id": "2026-10-30", "symbols": {"A": {"s": "ok"}, "G": {"s": "ok"}, "F": {"s": "ok"}, "Z": {"s": "retry"}}}),))


def run(out, pg=PG, extra=()):
    env = dict(os.environ, DATABASE_URL=pg)
    return subprocess.run([sys.executable, "-I", str(SCRIPT), "--out-dir", str(out), "--today", "2026-10-30", *extra], capture_output=True, text=True, env=env)


def test_counts_events_not_rows_every_merge_listed_nothing_changed(tmp_path):
    import psycopg
    c = psycopg.connect(PG, autocommit=True)
    build(c)
    snapshot = lambda: (c.execute("SELECT count(*), sum(id) FROM model_predictions").fetchone(), c.execute("SELECT count(*) FROM earnings_event_outcomes").fetchone(),
                        c.execute("SELECT count(*) FROM earnings_calendar").fetchone(), c.execute("SELECT state FROM local_warm_state").fetchone())
    before = snapshot()
    r = run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    rep = json.loads(next(tmp_path.glob("event_counts_*.json")).read_text())
    ev = rep["events"]
    assert ev["raw_distinct_symbol_date_pairs"] == 13 and ev["deduplicated_events"] == 12 and ev["merged_confident_date_shifts"] == 1
    assert ev["close_pairs_kept_separate_uncertain"] == 1 and ev["symbols_with_a_close_pair"] == 2
    m = rep["merges_all"][0]
    assert (m["symbol"], m["date_1"], m["date_2"], m["code"], m["decision"]) == ("B", "2026-10-20", "2026-10-22", "CONFIDENT_DATE_SHIFT", "MERGED_KEEP_DATE_2") and m["reason"]
    u = rep["uncertain_pairs_all"][0]
    assert (u["symbol"], u["code"], u["decision"]) == ("J", "UNCERTAIN_CONCURRENT_PREDICTIONS", "KEPT_SEPARATE") and u["reason"]
    # K's two events 91 days apart are never examined as a pair
    assert not any(x["symbol"] == "K" for x in rep["merges_all"] + rep["uncertain_pairs_all"])
    assert rep["rows"]["null_earnings_date_rows"] == 2 and rep["rows"]["symbols_only_null_dates"] == 1
    s = rep["summary_deduplicated_all_symbols"]
    assert s["events"] == 12 and s["peak_day"] == {"date": "2026-10-28", "events": 5}            # A, G, H, I, K
    assert s["timing"] == {"AMC": 8, "BMO": 2, "UNKNOWN": 2}
    assert rep["summary_raw_all_symbols"]["events"] == 13 and len(s["top_10_days"]) <= 10 and s["weekly_distribution"]
    grid = s["plans_all_days"]["core_ceiling_grid"]
    assert set(grid) == {"30", "70", "115", "170", "195", "220"} and grid["115"]["core_coverage"] == 1.0
    assert grid["115"]["peak_requests_day_with_full_30"] == 5 * 5 + 5 * 13 + 24 and grid["30"]["core_coverage"] == 1.0
    assert s["plans_all_days"]["full_capture_30_per_day"]["full_coverage"] == 1.0
    un = rep["universes"]
    assert un["ALL"] == 11 and un["WARMED_queue"] == 4 and un["WARMED_with_events_in_data"] == 3 and rep["summary_WARMED_symbols"]["events"] == 3
    q = rep["quality"]
    assert q["future_events_timing_unknown"] == 1 and q["future_events_first_seen_more_than_45_days_ahead"] == 1
    assert q["past_events_without_outcome_row_older_than_5_days"] == 3                         # E, K(July), J(10-21)
    assert q["past_events_last_snapshot_more_than_7_days_before_event"] == 2                   # E, K(July)
    assert q["earnings_calendar_past_rows_without_actual_older_than_3_days"] == 1 and q["future_events_with_a_date_shift_or_uncertain_pair"] == 1
    assert snapshot() == before                                                                 # nothing in the database changed
    assert "MERGE B 2026-10-20 -> 2026-10-22" in r.stdout and "UNCERTAIN J" in r.stdout
    time.sleep(1.2)
    assert run(tmp_path).returncode == 0 and len(list(tmp_path.glob("event_counts_*.json"))) == 2   # a new file each run, never overwritten


def test_only_read_statements_are_ever_sent(tmp_path, monkeypatch):
    import psycopg
    c = psycopg.connect(PG, autocommit=True)
    build(c)
    import event_count_universe as ecu
    sent = []
    real_connect = psycopg.connect

    class Rec:
        def __init__(self, cur): self.cur = cur
        def execute(self, sql, *a, **k):
            sent.append(" ".join(str(sql).split()))
            return self.cur.execute(sql, *a, **k)
        def __getattr__(self, n): return getattr(self.cur, n)

    class Conn:
        def __init__(self, conn): self.conn = conn
        def cursor(self): return Rec(self.conn.cursor())
        def __getattr__(self, n): return getattr(self.conn, n)

    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: Conn(real_connect(*a, **k)))
    monkeypatch.setenv("DATABASE_URL", PG)
    monkeypatch.setattr(sys, "argv", ["x", "--out-dir", str(tmp_path), "--today", "2026-10-30"])
    ecu.main()
    assert len(sent) >= 8
    first = [s.split()[0].upper() for s in sent]
    assert set(first) <= {"SELECT", "WITH", "SHOW", "SET"}
    assert [s for s in sent if s.upper().startswith("SET ")] == ["SET default_transaction_read_only = on", "SET statement_timeout = '30s'", "SET lock_timeout = '5s'"]
    assert sent.index("SHOW transaction_read_only") < min(i for i, s in enumerate(sent) if s.upper().startswith("SELECT"))   # verified read-only before any query
    forbidden = re.compile(r"\b(insert|update|delete|truncate|drop|alter|create|grant|revoke|copy|vacuum|call|do)\b", re.I)
    assert not [s for s in sent if forbidden.search(s)]


def test_credentials_never_printed_and_failures_are_quiet(tmp_path):
    secret = "S3cretPassw0rd"
    host = PG.split("@")[1]
    ok = run(tmp_path, "postgresql://postgres:%s@%s" % (secret, host))
    assert ok.returncode == 0 and secret not in ok.stdout + ok.stderr and secret not in next(tmp_path.glob("event_counts_*.json")).read_text()
    bad = run(tmp_path / "x", "postgresql://postgres:%s@127.0.0.1:1/postgres" % secret)
    assert bad.returncode != 0 and secret not in bad.stdout + bad.stderr and "could not connect" in bad.stderr


def test_output_folder_must_be_outside_the_application_folder(tmp_path):
    r = run(tmp_path / "app" / "reports", extra=("--root", str(tmp_path / "app")))
    assert r.returncode != 0 and "must not be inside the application folder" in r.stderr
    assert not (tmp_path / "app" / "reports").exists()


def test_schema_mismatch_stops_before_counting(tmp_path):
    import psycopg
    admin = psycopg.connect(PG, autocommit=True)
    admin.execute("DROP DATABASE IF EXISTS smv_bad")
    admin.execute("CREATE DATABASE smv_bad")
    bad_url = PG.rsplit("/", 1)[0] + "/smv_bad"
    c = psycopg.connect(bad_url, autocommit=True)
    c.execute("CREATE TABLE model_predictions(symbol text, earnings_date date, snapshot_date date, predicted_at timestamptz, model text)")   # report_timing missing
    c.execute("CREATE TABLE local_warm_state(id int, state text)")
    r = run(tmp_path, bad_url)
    assert r.returncode != 0 and "schema differs" in r.stderr and "report_timing" in r.stderr and not list(tmp_path.glob("event_counts_*.json"))
    c.close()
    admin.execute("DROP DATABASE smv_bad")
