"""Full offline pipeline: calendar verification -> eligibility -> selection -> governor -> capture -> frozen scorer -> NO TRADE records -> optional scratch PostgreSQL."""
import os
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from fixtures import FakeProvider
from smvshadow import calendar_confirm as cc
from smvshadow import governor as gv, scorer as sc
from smvshadow.pipeline import ShadowPipeline
from smvshadow.store import Db, PayloadStore
from test_e2e_rehearsal import EVENT, START, world

UTC = timezone.utc
TODAY = date(2026, 11, 3)
PG = os.environ.get("SMV_TEST_PG", "")
pg = pytest.mark.skipif(not PG.startswith("postgresql://") or "127.0.0.1" not in PG, reason="SMV_TEST_PG not set")


def uw(sym, flag="company", label="premarket", age_h=1, sha=None):
    row = {"symbol": sym, "report_date": EVENT.isoformat(), "report_time": label, "date_confirmed": True}
    if flag:
        row["source"] = flag
    return cc.from_uw_row(row, START - timedelta(hours=age_h), sha or "u-" + sym)


def fz(sym, raw="11/5/2026 7:00:00 AM", age_h=1):
    return cc.from_finviz(sym, raw, START - timedelta(hours=age_h), "f-" + sym)


def setup(tmp_path, authorized=500):
    clock = gv.FakeClock(START)
    worlds = {"__default__": world("DEF", [4] * 6), "SPY": world("SPY", [0.5] * 6), "XLK": world("XLK", [0.5] * 6)}
    for s in ("AAA", "FFF"):
        worlds[s] = world(s, [4] * 6)
    fetch = FakeProvider(worlds, clock.now)
    log = cc.ObservationLog()
    for o in (uw("AAA"), fz("AAA"),                                   # VENDOR_CONFIRMED
              uw("BBB", flag="estimation"), fz("BBB"),                # source=estimation
              uw("CCC"),                                              # one vendor
              uw("DDD"), fz("DDD", "11/5/2026 4:30:00 PM"),           # timing contradiction
              uw("EEE", age_h=30), fz("EEE", age_h=30),               # stale calendar
              uw("FFF"), fz("FFF")):                                  # confirmed but illiquid
        log.append(o)
    meta = {s: {"sector_etf": "XLK", "liquid": (False if s == "FFF" else None), "prediction_snapshot_at": START - timedelta(days=8)} for s in ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG")}
    pre, sha = sc.load_prereg()
    g = gv.Governor(clock, authorized_daily=authorized)
    return clock, fetch, log, meta, pre, sha, g, PayloadStore(tmp_path / "store")


def test_only_vendor_confirmed_events_are_captured_and_everything_else_is_no_trade_with_reasons(tmp_path):
    clock, fetch, log, meta, pre, sha, g, store = setup(tmp_path)
    out = ShadowPipeline(g, store, fetch, pre, sha).run(meta, log, START, TODAY)
    assert {p["symbol"] for p in out["predictions"]} == {"AAA"} and len(out["predictions"]) == 2
    assert len(fetch.calls) == 4 + 2 + 13                                      # AAA: 4 core + SPY + sector + 13 capture-only (it is the day's full-capture slot); nothing for excluded names
    assert all(s in ("AAA", "SPY", "XLK") for _, s in fetch.calls)
    nt = {}
    for r in out["no_trade"]:
        nt.setdefault(r["symbol"], set()).update(r["reason_codes"])
    assert "SOURCE_ESTIMATION" in nt["BBB"] and "SINGLE_VENDOR" in nt["CCC"] and "TIMING_CONTRADICTED" in nt["DDD"] and "STALE_CALENDAR" in nt["EEE"]
    assert "SKIPPED_ILLIQUID" in nt["FFF"] and "NO_OBSERVATION" in nt["GGG"]
    assert all(r["reason_codes"] and r["signal"] == "NO_TRADE" for r in out["no_trade"])
    for p in out["predictions"]:
        c = p["explanation"]["calendar"]
        assert c["status"] == "VENDOR_CONFIRMED" and "not direct company" in c["note"] and c["calendar_observed_at"] != c["prediction_snapshot_at"]
        assert p["explanation"]["never_scaled_to_100"] and p["weight_available"] <= 40
    assert {p["variant"] for p in out["predictions"]} == {"continuation", "contrarian"} and out["requests_used"]


def test_calendar_clock_is_independent_of_prediction_snapshot_age(tmp_path):
    clock, fetch, log, meta, pre, sha, g, store = setup(tmp_path)
    out = ShadowPipeline(g, store, fetch, pre, sha).run(meta, log, START, TODAY)
    ev = out["evaluations"]["AAA"]
    assert ev["prediction_snapshot_at"] == START - timedelta(days=8) and ev["eligible_for_shadow"]


def test_pipeline_is_deterministic_and_idempotent(tmp_path):
    clock, fetch, log, meta, pre, sha, g, store = setup(tmp_path / "x")
    o1 = ShadowPipeline(g, store, fetch, pre, sha, run_id="11111111-1111-1111-1111-111111111111").run(meta, log, START, TODAY)
    clock2, fetch2, log2, meta2, pre2, sha2, g2, _ = setup(tmp_path / "y")
    o2 = ShadowPipeline(g2, store, fetch2, pre2, sha2, run_id="11111111-1111-1111-1111-111111111111").run(meta2, log2, START, TODAY)
    assert o1["plan"]["plan_hash"] == o2["plan"]["plan_hash"]
    assert [(p["symbol"], p["variant"], p["signal"], p["bull_points"], p["bear_points"]) for p in o1["predictions"]] == [(p["symbol"], p["variant"], p["signal"], p["bull_points"], p["bear_points"]) for p in o2["predictions"]]
    assert store.verify_all()[1] == []


def test_zero_authorization_yields_no_requests_and_no_trade_for_everything(tmp_path):
    clock, fetch, log, meta, pre, sha, g, store = setup(tmp_path, authorized=0)
    out = ShadowPipeline(g, store, fetch, pre, sha).run(meta, log, START, TODAY)
    assert fetch.calls == [] and out["predictions"] == []
    aaa = [r for r in out["no_trade"] if r["symbol"] == "AAA"]
    assert aaa and "MISSED_DEADLINE" in aaa[0]["reason_codes"]


@pg
def test_pipeline_persists_calendar_and_results_append_only_on_scratch_postgres(tmp_path):
    import psycopg
    conn = psycopg.connect(PG, autocommit=True)
    conn.execute("DROP SCHEMA IF EXISTS smv CASCADE")
    db = Db(conn)
    db.apply_schema()
    db.apply_calendar_schema()
    clock, fetch, log, meta, pre, sha, g, store = setup(tmp_path)
    out = ShadowPipeline(g, store, fetch, pre, sha, db=db).run(meta, log, START, TODAY)
    n = lambda t: db._one("SELECT count(*) FROM smv.%s" % t)
    assert n("calendar_observations") == len(log) == 11 and n("calendar_evaluations") == 7 and n("predictions") == 2
    flags = dict(db.c.execute("SELECT symbol, vendor_flag_raw FROM smv.calendar_observations WHERE vendor='UNUSUAL_WHALES'").fetchall())
    assert flags["AAA"] == "company" and flags["BBB"] == "estimation"          # flag preserved from the raw row
    db.add_calendar_observations(log)                                           # idempotent replay
    assert n("calendar_observations") == 11
    assert db._one("SELECT count(*) FROM smv.ledger WHERE entry_type LIKE 'NO_TRADE%%'") >= 6
    for sql in ("UPDATE smv.calendar_observations SET vendor_flag_raw='company'", "DELETE FROM smv.calendar_evaluations", "TRUNCATE smv.calendar_observations"):
        with pytest.raises(psycopg.Error):
            db.c.execute(sql)
    with pytest.raises(psycopg.Error):                                         # an excluded event without any reason is refused by the database
        db.c.execute("INSERT INTO smv.calendar_evaluations(run_id,symbol,status,kind,eligible_for_shadow,reasons,no_trade_reasons,detail,evaluated_at) "
                     "VALUES (%s,'ZZZ','UNKNOWN','UNKNOWN',false,'{}','{}','{}',now())", (str(uuid.uuid4()),))
    conn.close()
