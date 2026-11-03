"""Earnings Intelligence additions: in-place schema upgrade, independent scheduler lanes, the evidence ledger (discovered / collected / verified / relevant / used), the 30-day calendar with the 15-day model window,
prediction versions that carry their reason, and the new alert rules. Scripted adapters only; nothing here can reach a provider."""
import json
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))
from earnings_monitor import evidence as EV  # noqa: E402
from earnings_monitor import logic as L  # noqa: E402
from earnings_monitor.store import Store  # noqa: E402
from test_earnings_monitor import mk  # noqa: E402


def test_a_schema_2_store_is_upgraded_in_place_without_losing_rows(tmp_path):
    f = tmp_path / "old.sqlite"
    s = Store(f)
    with s.tx() as c:
        c.execute("INSERT INTO events(symbol,company,earnings_date,session,first_seen,last_seen,last_changed) VALUES('AAA','A','2026-11-11','BMO','x','x','x')")
        c.execute("INSERT INTO predictions(event_id,model,signal,as_of,recorded_at,input_hash) VALUES(1,'claude','BUY','a','b','h')")
    con = sqlite3.connect(f)                                   # rebuild what the server's schema-2 file looks like
    con.executescript("""DROP TABLE evidence_items;
        ALTER TABLE jobs DROP COLUMN lane; ALTER TABLE events DROP COLUMN last_verified; ALTER TABLE predictions DROP COLUMN reason;
        UPDATE kv SET v='2' WHERE k='schema_version';""")
    con.commit()
    con.close()
    s2 = Store(f)
    assert json.loads(s2.one("SELECT v FROM kv WHERE k='schema_version'")["v"]) == 3
    assert s2.one("SELECT symbol FROM events")["symbol"] == "AAA" and s2.one("SELECT signal FROM predictions")["signal"] == "BUY"
    assert "lane" in {r["name"] for r in s2.q("PRAGMA table_info(jobs)")} and "last_verified" in {r["name"] for r in s2.q("PRAGMA table_info(events)")}
    assert "reason" in {r["name"] for r in s2.q("PRAGMA table_info(predictions)")}
    assert s2.q("SELECT COUNT(*) n FROM evidence_items")[0]["n"] == 0
    Store(f)                                                   # opening it again is a no-op


def test_a_slow_scan_cannot_block_alert_delivery_or_outcome_jobs(tmp_path):
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path)
    ad.cal = [{"symbol": "AAA", "date": "2026-11-11", "session": "BMO"}]
    sched.tick(clock())
    assert sched.locks["heavy"].acquire()                      # another process is in the middle of a long scan
    try:
        assert sched.run_once(clock(), "heavy")["reason"] == "SCAN_LOCK_HELD"      # a second scan is refused (never two at once)
        r = sched.run_pending(clock(), lane="light")           # ... but the light lane still delivers alerts and plans
        assert r and all(x["ran"] for x in r) and "ALERT_DISPATCH" in {x["kind"] for x in r}
    finally:
        sched.locks["heavy"].release()
    kinds = {r["kind"]: r["lane"] for r in store.q("SELECT kind, lane FROM jobs")}
    assert kinds["ELIGIBILITY_CHECK"] == "heavy" and kinds["ALERT_DISPATCH"] == "light" and kinds["EVIDENCE_SCAN"] == "heavy"


def item(kind, ext, bias=None, title=None, **data):
    d = dict(data)
    if bias is not None:
        d["bias"] = bias
    return {"kind": kind, "provider": "earn:equity" if bias is not None else "UW", "ext_id": ext, "occurred_at": None, "title": title or ext, "summary": None, "data": d, "provider_structured": bias is not None}


def test_the_evidence_ledger_records_new_then_changed_then_nothing_and_keeps_stages_apart(tmp_path):
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path)
    ad.cal = [{"symbol": "AAA", "company": "A", "date": "2026-11-11", "session": "BMO"}]
    mon.observe_calendar(ad.cal, clock(), complete=True)
    ad.sources = {"claude": "p = ['eps_estimates', 'guidance']", "grok": "p = ['price_action']"}
    ad.ev_items = {"AAA": [item("ESTIMATES", "eps_estimates", bias=0.40), item("NEWS", "n1", title="AAA beats the street", sentiment="positive")]}
    r1 = mon.evidence_scan(clock())
    assert r1["new"] == 2 and r1["changed"] == 0
    rows = {r["kind"]: r for r in store.q("SELECT * FROM evidence_items")}
    assert rows["ESTIMATES"]["stage"] == "VERIFIED" and rows["ESTIMATES"]["verified"] == 1 and json.loads(rows["ESTIMATES"]["relevant_models"]) == ["claude"]
    assert rows["NEWS"]["stage"] == "COLLECTED" and rows["NEWS"]["verified"] == 0 and json.loads(rows["NEWS"]["relevant_models"]) == []      # a headline is stored, never relevant to a score
    assert mon.evidence_scan(clock())["new"] == 0
    assert mon.evidence_scan(clock())["changed"] == 0                                                              # identical copy: nothing recorded
    ad.ev_items["AAA"][0] = item("ESTIMATES", "eps_estimates", bias=0.42)                                           # noise below the step
    assert mon.evidence_scan(clock())["changed"] == 0
    ad.ev_items["AAA"][0] = item("ESTIMATES", "eps_estimates", bias=0.70)                                           # a real move
    r2 = mon.evidence_scan(clock())
    assert r2["changed"] == 1 and r2["recomputed"] == 1
    ch = store.one("SELECT * FROM changes WHERE kind='EVIDENCE_CHANGED'")
    assert json.loads(ch["detail"])["changes_a_score"] is False and json.loads(ch["detail"])["relevant_models"] == ["claude"]
    ad.ev_items["AAA"][1] = item("NEWS", "n1", title="AAA beats the street", sentiment="negative")                  # the headline changed: stored, no recompute
    r3 = mon.evidence_scan(clock())
    assert r3["changed"] == 1 and r3["recomputed"] == 0


def test_a_recompute_after_evidence_keeps_both_versions_and_says_why(tmp_path):
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path)
    ad.cal = [{"symbol": "AAA", "company": "A", "date": "2026-11-11", "session": "BMO"}]
    mon.observe_calendar(ad.cal, clock(), complete=True)
    ad.sources = {"claude": "['eps_estimates']"}
    ad.set_model("claude", "AAA", "NEUTRAL", clock(), score=50, coverage=0.9)
    mon.recompute(clock())
    ad.ev_items = {"AAA": [item("ESTIMATES", "eps_estimates", bias=0.1)]}
    mon.evidence_scan(clock())
    clock.advance(minutes=20)
    ad.ev_items["AAA"] = [item("ESTIMATES", "eps_estimates", bias=0.8)]
    ad.set_model("claude", "AAA", "BUY", clock(), score=72, coverage=0.9)
    mon.evidence_scan(clock())
    ps = store.q("SELECT * FROM predictions WHERE model='claude' ORDER BY id")
    assert [p["signal"] for p in ps] == ["NEUTRAL", "BUY"] and "ESTIMATES evidence changed" in ps[1]["reason"] and "first stored call" in ps[0]["reason"]
    ch = store.q("SELECT * FROM changes WHERE model='claude' ORDER BY id")
    det = json.loads(ch[-1]["detail"])
    assert det["previous_prediction_id"] == ps[0]["id"] and det["new_prediction_id"] == ps[1]["id"]


def test_thirty_day_calendar_models_only_inside_the_fifteen_day_window(tmp_path):
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path)
    ad.cal = [{"symbol": "NEAR", "date": "2026-11-20", "session": "BMO"}, {"symbol": "FAR", "date": "2026-12-04", "session": "AMC"}, {"symbol": "TOOFAR", "date": "2026-12-14", "session": "AMC"}]
    mon.observe_calendar(ad.cal, clock(), complete=True)
    for s in ("NEAR", "FAR", "TOOFAR"):
        ad.set_model("claude", s, "BUY", clock(), score=70, coverage=0.9)
    mon.recompute(clock())
    got = {r["symbol"] for r in store.q("SELECT DISTINCT e.symbol FROM predictions p JOIN events e ON e.id=p.event_id")}
    assert got == {"NEAR"}                                     # FAR (25 days) is on the calendar but outside the model window: no prediction, no invented neutral
    far = store.one("SELECT * FROM events WHERE symbol='FAR'")
    assert mon.eligible(far, clock())["reasons"] == ["BEYOND_15_DAY_MODEL_WINDOW"]
    assert L.display_status({"status": "OUT_OF_WINDOW"}) == "OUT_OF_WINDOW"


def test_new_earnings_and_disagreement_alerts_are_raised_once(tmp_path):
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path)
    ad.cal = [{"symbol": "AAA", "company": "A", "date": "2026-11-11", "session": "BMO"}]
    mon.observe_calendar(ad.cal, clock(), complete=True)
    clock.advance(hours=1)
    ad.cal = ad.cal + [{"symbol": "BBB", "company": "B", "date": "2026-11-12", "session": "AMC"}]
    mon.observe_calendar(ad.cal, clock(), complete=False)
    assert [r["rule"] for r in store.q("SELECT rule FROM alerts")].count("NEW_EARNINGS") == 1
    for m, sig in (("claude", "BUY"), ("grok", "SELL"), ("meta", "BUY"), ("ped", "SELL")):
        ad.set_model(m, "BBB", sig, clock(), score=60, coverage=0.9)
    mon.recompute(clock())
    mon.recompute(clock())
    assert [r["rule"] for r in store.q("SELECT rule FROM alerts")].count("MODEL_DISAGREEMENT") == 1


def test_options_flow_alert_needs_premium_lean_and_prints(tmp_path):
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path)
    ad.cal = [{"symbol": "AAA", "company": "A", "date": "2026-11-11", "session": "BMO"}]
    mon.observe_calendar(ad.cal, clock(), complete=True)
    ad.ev_items = {"AAA": [item("OPTIONS_FLOW", "flow-recent", bias=0.3, total_premium=400_000, bullish_share=80, prints=30)]}
    mon.evidence_scan(clock())
    assert [r["rule"] for r in store.q("SELECT rule FROM alerts")].count("UNUSUAL_OPTIONS_FLOW") == 0
    ad.ev_items = {"AAA": [item("OPTIONS_FLOW", "flow-recent", bias=0.6, total_premium=3_200_000, bullish_share=82, prints=40)]}
    mon.evidence_scan(clock())
    assert [r["rule"] for r in store.q("SELECT rule FROM alerts")].count("UNUSUAL_OPTIONS_FLOW") == 1


def test_kind_mapping_and_fingerprint_helpers():
    assert EV.kind_of_param("options_combined") == "OPTIONS_FLOW" and EV.kind_of_param("calendar_timing") is None
    assert EV.fingerprint({"data": {"bias": 0.40}}) == EV.fingerprint({"data": {"bias": 0.42}}) != EV.fingerprint({"data": {"bias": 0.60}})


def test_new_endpoints_report_evidence_smart_move_health_and_accuracy_without_writing(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from earnings_monitor import routes
    from test_earnings_monitor import cycle
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path)
    ad.cal = [{"symbol": "AAA", "company": "Alpha", "date": "2026-11-12", "session": "BMO"}, {"symbol": "FAR", "company": "Far", "date": "2026-12-03", "session": "AMC"}]
    for m, sig in (("claude", "BUY"), ("grok", "BUY"), ("meta", "SELL"), ("smart_move", "NO_TRADE")):
        ad.set_model(m, "AAA", sig, clock(), score=60, coverage=0.9)
    ad.smd = {("AAA", "2026-11-12"): {"result": "NO TRADE (genuine)", "coverage_points": 30, "coverage_max": 40}}
    ad.ev_items = {"AAA": [item("OPTIONS_FLOW", "flow-recent", bias=0.4, total_premium=2_000_000, bullish_share=75, prints=12), item("NEWS", "n1", title="AAA outlook raised", guidance_keyword=True)]}
    cycle(sched, clock)
    sched.run_pending(clock())
    monkeypatch.setenv("EARNINGS_MONITOR_DIR", str(d))
    monkeypatch.setattr(routes, "now", lambda: clock())
    app = FastAPI()
    app.include_router(routes.router)
    c = TestClient(app)
    ev = c.get("/api/earnings-monitor/evidence?symbol=AAA").json()
    assert {r["kind"] for r in ev["rows"]} == {"OPTIONS_FLOW", "NEWS"} and any(r["stage"] == "VERIFIED" for r in ev["rows"]) and ev["unsupported"]
    assert {x["kind"]: x["feeds_a_score"] for x in ev["summary"]}["NEWS"] is False
    sm = c.get("/api/earnings-monitor/smart-move").json()
    row = [r for r in sm["rows"] if r["symbol"] == "AAA"][0]
    assert row["label"] == "NO_TRADE" and row["details"]["coverage_points"] == 30 and sm["engine"]["model_version"] == "score40-v1"
    far = [r for r in sm["rows"] if r["symbol"] == "FAR"][0]
    assert far["label"] == "NO_PREDICTION_AVAILABLE" and far["reason"]["code"] == "NOT_YET_IN_WINDOW" and far["origin"] == "NOT_GENERATED"
    h = c.get("/api/earnings-monitor/health").json()
    assert h["status"] == "OK" and h["last_runs"]["EVIDENCE_SCAN"]["ok"] is True and h["stuck_jobs"] == [] and h["duplicate_active_events"] == [] and h["evidence_scan"]["new"] == 2
    up = c.get("/api/earnings-monitor/upcoming").json()
    assert {r["symbol"]: r["out_of_window"] for r in up["rows"]} == {"AAA": False, "FAR": True} and up["horizon_days"] == 30 and up["model_window_days"] == 15
    models = c.get("/api/earnings-monitor/models").json()
    assert {r["symbol"] for r in models["rows"]} == {"AAA", "FAR"} and [r for r in models["rows"] if r["symbol"] == "FAR"][0]["cells"] == {}
    acc = c.get("/api/earnings-monitor/accuracy").json()
    assert "agreement_vs_outcome" in acc and "events_outcome_unverifiable" in acc
    for path in ("evidence", "smart-move", "health"):
        assert c.post("/api/earnings-monitor/" + path).status_code == 405


def test_when_the_budget_runs_out_every_unrefreshed_stock_is_listed_with_the_reason(tmp_path):
    from datetime import datetime, timezone
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path, budget=700, events=8)
    ad.cal = [{"symbol": s, "date": "2026-11-09", "session": "BMO"} for s in ("AAA", "BBB", "CCC")]
    mon.observe_calendar(ad.cal, clock(), complete=True)
    clock.t = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)                       # 07:00 ET, inside the Before-Open window
    sched.tick(clock())
    access.state = sched.gate_fn(clock())
    store.add_usage = None
    with store.tx() as c:                                                              # most of today's budget is already spent
        c.execute("INSERT INTO usage(day,provider,requests,failures) VALUES('2026-11-09','event',680,0) ON CONFLICT(day,provider) DO UPDATE SET requests=680")
    res = mon.earnings_day_check(clock())
    assert res["refreshed"] == 0 and sorted(res["skipped_symbols"]) == ["AAA", "BBB", "CCC"]
    rows = store.kv_get("refresh:skipped")["rows"]
    assert {r["symbol"] for r in rows} == {"AAA", "BBB", "CCC"} and all(r["reason"].startswith("DAILY_BUDGET_EXHAUSTED") for r in rows)


def test_the_cache_only_calendar_reads_thirty_days_of_cached_dates_and_nothing_else(monkeypatch):
    import time as _t
    import types
    from datetime import date, datetime, timezone
    from earnings_monitor.adapters import ProdAdapters
    from earnings_monitor.smart_move import SmartMoveSource
    today = date(2026, 11, 9)
    cache = types.SimpleNamespace(_data={})
    lms = types.ModuleType("live_market_service")
    lms.cache = cache
    cal = types.ModuleType("uw_earnings_calendar")
    cal.TTL_FUTURE = 6 * 3600.0
    cal.MIN_PRICE = 15.0
    cal._us_listed = lambda r: True
    cal._row = lambda r, w: {"symbol": r["symbol"], "company": r["symbol"], "date": w, "reporting_time": r.get("t"), "prior_close": r.get("px")}
    uw = types.ModuleType("unusualwhales_service")
    uw._rows = lambda payload: payload["data"]
    for n, m in (("live_market_service", lms), ("uw_earnings_calendar", cal), ("unusualwhales_service", uw)):
        monkeypatch.setitem(__import__("sys").modules, n, m)
    now = _t.time()
    cache._data["uwcal:/api/earnings/premarket:2026-11-10"] = (now, {"status": "OK", "data": [{"symbol": "NEAR", "t": "BMO", "px": 50}]})
    cache._data["uwcal:/api/earnings/afterhours:2026-12-02"] = (now, {"status": "OK", "data": [{"symbol": "FAR", "t": "AMC", "px": 80}, {"symbol": "CHEAP", "t": "AMC", "px": 3}]})
    cache._data["uwcal:/api/earnings/premarket:2026-12-04"] = (now, {"status": "OK", "data": [{"symbol": "BEYOND", "t": "BMO", "px": 50}]})      # day 25: inside 30
    cache._data["uwcal:/api/earnings/premarket:2026-12-14"] = (now, {"status": "OK", "data": [{"symbol": "TOOFAR", "t": "BMO", "px": 50}]})      # day 35: outside 30
    cache._data["uwcal:/api/earnings/premarket:2026-11-11"] = (now - 3 * 24 * 3600, {"status": "OK", "data": [{"symbol": "OLD", "t": "BMO", "px": 50}]})   # cached three days ago: too old to trust
    ad = ProdAdapters(smart_move=SmartMoveSource(), session_factory=lambda: (_ for _ in ()).throw(RuntimeError("no db")), clock=lambda: datetime(2026, 11, 9, 13, 0, tzinfo=timezone.utc))
    rows = {r["symbol"]: r for r in ad.calendar()}
    assert set(rows) == {"NEAR", "FAR", "BEYOND"}                                      # CHEAP is below the app's price floor, TOOFAR is past 30 days, OLD is stale
    assert rows["NEAR"]["session"] == "BMO" and rows["NEAR"]["sources"][0]["vendor"] == "UW" and rows["FAR"]["date"] == "2026-12-02"


def test_thirty_day_offline_simulation_with_a_crash_has_no_duplicates_no_stuck_jobs_and_no_provider_requests(tmp_path):
    import random
    from datetime import datetime, timedelta, timezone
    from earnings_monitor.sim import Clock, build_sim, write_approval
    from earnings_monitor.scheduler import Runner  # noqa: F401  (imported to prove the module loads)
    rnd = random.Random(7)
    start = datetime(2026, 11, 9, 13, 0, tzinfo=timezone.utc)
    clock = Clock(start)
    d = tmp_path / "em"
    d.mkdir()
    write_approval(d, start, mode="cache_only", days=30)             # an approval may not exceed 30 days
    store, cfg, ad, mon, sched, access = build_sim(d, clock)
    syms = ["S%03d" % i for i in range(60)]
    from earnings_monitor import calendar_us as CAL
    days = [start.date() + timedelta(days=i) for i in range(1, 29) if CAL.is_trading_day(start.date() + timedelta(days=i))]
    ad.cal = [{"symbol": s, "company": s, "date": rnd.choice(days).isoformat(), "session": rnd.choice(["BMO", "AMC"])} for s in syms]
    ad.sources = {"claude": "['eps_estimates']"}
    crashed = False
    for hour in range(0, 29 * 24, 3):
        clock.t = start + timedelta(hours=hour)
        if hour == 24 * 10:                                                              # a date moves and a symbol is dropped
            ad.cal[0] = dict(ad.cal[0], date=days[-1].isoformat())
            ad.cal.pop(1)
        for s in syms:
            if rnd.random() < 0.03:
                for m, sig in (("claude", "BUY"), ("grok", "SELL"), ("meta", "BUY")):
                    ad.set_model(m, s, rnd.choice(["BUY", "SELL", "NEUTRAL"]), clock(), score=rnd.randint(30, 70), coverage=0.8)
                ad.ev_items[s] = [item("ESTIMATES", "eps_estimates", bias=round(rnd.uniform(-1, 1), 1))]
        sched.tick(clock())
        if hour == 24 * 5 and not crashed:                                               # a worker claims a job, then the process dies
            assert store.claim(clock(), "dead", 60)
            crashed = True
        sched.run_pending(clock())
        sched.run_pending(clock(), lane="light")
    assert store.q("SELECT COUNT(*) n FROM jobs WHERE status='RUNNING'")[0]["n"] == 0                       # the crashed job was recovered, nothing is stuck
    assert not store.q("SELECT symbol FROM events WHERE status='ACTIVE' GROUP BY symbol HAVING COUNT(*) > 1")
    assert store.q("SELECT COUNT(*) n FROM (SELECT input_hash h, LAG(input_hash) OVER (PARTITION BY event_id, model ORDER BY id) p FROM predictions) WHERE h = p")[0]["n"] == 0      # a row is only written when the call changed (BUY, SELL, BUY is three real versions)
    assert store.q("SELECT COUNT(*) n FROM (SELECT dedupe_key, COUNT(*) c FROM alerts GROUP BY 1 HAVING c>1)")[0]["n"] == 0
    assert store.usage("2026-11-09") == 0 and ad.calls == {"refresh_calendar": 0, "refresh_event": 0, "verify_outcome": 0}   # cache-only: not one provider call in 30 days
    assert store.q("SELECT COUNT(*) n FROM jobs WHERE status='DEAD'")[0]["n"] == 0
    assert store.q("SELECT COUNT(*) n FROM events")[0]["n"] >= 59 and store.q("SELECT COUNT(*) n FROM predictions")[0]["n"] > 50
    assert store.one("SELECT COUNT(*) n FROM changes WHERE kind='EVENT_RESCHEDULED'")["n"] == 1 and store.one("SELECT COUNT(*) n FROM changes WHERE kind='EVENT_CANCELED'")["n"] == 0   # cache-only never cancels


def test_every_run_records_real_start_finish_duration_events_and_queue_wait(tmp_path):
    from datetime import timedelta
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path)
    ad.cal = [{"symbol": s, "date": "2026-11-12", "session": "BMO"} for s in ("AAA", "BBB", "CCC")]
    sched.tick(clock())
    clock.advance(seconds=45)                                   # the job waited 45 s after it became due
    sched.run_pending(clock())
    r = store.one("SELECT * FROM scan_runs WHERE kind='ELIGIBILITY_CHECK'")
    meta = json.loads(r["summary"])["_run"]
    assert meta["queue_wait_ms"] == 45000 and meta["events_processed"] == 3 and meta["duration_ms"] >= 0 and meta["lane"] == "heavy"


def test_refresh_plan_ranks_by_priority_funds_only_what_the_budget_allows_and_lists_the_rest(tmp_path):
    from earnings_monitor import config as C
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path, budget=700, events=8)
    ad.cal = [{"symbol": "A%02d" % i, "date": "2026-11-10" if i < 5 else "2026-11-13", "session": "AMC" if i % 2 else "BMO"} for i in range(30)]
    mon.observe_calendar(ad.cal, clock(), complete=True)
    sched.tick(clock())
    r = mon.refresh_plan(clock())
    plan = store.kv_get("refresh:plan")
    reserve = plan["external_reserve"] + plan["calendar_reserve"] + plan["outcome_reserve"]
    assert plan["event_slots"] == (700 - reserve) // C.COST_EVENT_REFRESH == len(plan["funded"]) and plan["unfunded_count"] == 30 - plan["event_slots"]
    assert all(f["days_to"] <= 1 for f in plan["funded"][:5]) and plan["funded"][0]["session"] == "BMO"          # soonest first, Before-Open before After-Close
    assert {u["reason"] for u in plan["unfunded"]} == {"NOT_FUNDED_BY_DAILY_BUDGET_PLAN"} and plan["profile"] == "lean"
    assert ad.calls["refresh_event"] == 0                                                                        # planning makes no request


def test_event_refresh_runs_only_funded_events_in_refresh_mode_and_lists_the_skipped_when_the_budget_ends(tmp_path):
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path, budget=700, events=8)
    ad.cal = [{"symbol": "A%02d" % i, "date": "2026-11-10", "session": "BMO"} for i in range(6)]
    mon.observe_calendar(ad.cal, clock(), complete=True)
    sched.tick(clock())
    access.state = sched.gate_fn(clock())
    out = mon.event_refresh(clock())
    assert out["refreshed"] == cfg.refresh_batch == ad.calls["refresh_event"]
    with store.tx() as c:                                                                                          # the day's budget is nearly spent
        c.execute("INSERT INTO usage(day,provider,requests,failures) VALUES('2026-11-09','event',690,0) ON CONFLICT(day,provider) DO UPDATE SET requests=690")
    clock.advance(hours=3)
    out2 = mon.event_refresh(clock())
    assert out2["refreshed"] == 0 and out2["skipped"] >= 1
    assert all(r["reason"].startswith("DAILY_BUDGET_EXHAUSTED") for r in store.kv_get("refresh:skipped")["rows"])
    # cache-only mode never refreshes
    d2, clock2, store2, cfg2, ad2, mon2, sched2, access2 = mk(tmp_path / "c", mode="cache_only")
    ad2.cal = [{"symbol": "AAA", "date": "2026-11-10", "session": "BMO"}]
    mon2.observe_calendar(ad2.cal, clock2(), complete=True)
    sched2.tick(clock2())
    access2.state = sched2.gate_fn(clock2())
    assert mon2.event_refresh(clock2())["refreshed"] == 0 and ad2.calls["refresh_event"] == 0


def test_an_approval_that_is_about_to_end_raises_one_alert_a_day_and_health_shows_the_days_left(tmp_path):
    from earnings_monitor.sim import write_approval
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path, days=2)
    sched.tick(clock())
    sched.tick(clock())
    assert [r["rule"] for r in store.q("SELECT rule FROM alerts")].count("APPROVAL_EXPIRING") == 1
    clock.advance(hours=5)
    sched.tick(clock())
    assert [r["rule"] for r in store.q("SELECT rule FROM alerts")].count("APPROVAL_EXPIRING") == 1                 # same day: still one
    clock.advance(days=1)
    sched.tick(clock())
    assert [r["rule"] for r in store.q("SELECT rule FROM alerts")].count("APPROVAL_EXPIRING") == 2


def test_grading_uses_only_calls_made_before_the_reporting_window_opened(tmp_path, monkeypatch):
    import json as _j
    from datetime import datetime, timezone
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from earnings_monitor import routes
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path)
    ad.cal = [{"symbol": "AAA", "date": "2026-11-10", "session": "AMC"}]
    mon.observe_calendar(ad.cal, clock(), complete=True)
    ev = store.one("SELECT * FROM events WHERE symbol='AAA'")
    with store.tx() as c:                                                                  # BUY recorded the day before; a SELL recorded AFTER 15:00 ET on the report day (look-ahead)
        c.execute("INSERT INTO predictions(event_id,model,signal,score,as_of,recorded_at,input_hash) VALUES(?,?,?,?,?,?,?)", (ev["id"], "claude", "BUY", 70, "2026-11-09T15:00:00+00:00", "2026-11-09T15:00:00+00:00", "h1"))
        c.execute("INSERT INTO predictions(event_id,model,signal,score,as_of,recorded_at,input_hash) VALUES(?,?,?,?,?,?,?)", (ev["id"], "claude", "SELL", 30, "2026-11-10T21:30:00+00:00", "2026-11-10T21:30:00+00:00", "h2"))
        c.execute("UPDATE events SET outcome_json=?, outcome_at=?, status='REPORTED' WHERE id=?", (_j.dumps({"move_pct": 4.0, "verified": True, "source": "daily_bars"}), "2026-11-11T22:00:00+00:00", ev["id"]))
    monkeypatch.setenv("EARNINGS_MONITOR_DIR", str(d))
    monkeypatch.setattr(routes, "now", lambda: clock())
    app = FastAPI()
    app.include_router(routes.router)
    c = TestClient(app)
    out = c.get("/api/earnings-monitor/outcomes").json()["rows"][0]
    assert out["calls"]["claude"]["signal"] == "BUY" and out["results"]["claude"] == "CORRECT" and out["prediction_cutoff"].startswith("2026-11-10T20:00")   # 15:00 ET = 20:00 UTC
    acc = {r["model"]: r for r in c.get("/api/earnings-monitor/accuracy").json()["rows"]}
    assert acc["claude"]["correct"] == 1 and acc["claude"]["incorrect"] == 0
    sched.tick(clock())
    h = c.get("/api/earnings-monitor/health").json()
    assert "usage_by_day" in h and h["approval_days_left"] is not None


def test_the_plan_counts_what_the_rest_of_the_application_already_used_against_the_shared_cap(tmp_path):
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path, budget=700, events=8)
    ad.cal = [{"symbol": "A%02d" % i, "date": "2026-11-10", "session": "BMO"} for i in range(10)]
    mon.observe_calendar(ad.cal, clock(), complete=True)
    sched.tick(clock())
    ad.app_requests_today = lambda: 100                                           # the warmers used 100 so far: below the planning reserve, which still applies
    mon.refresh_plan(clock())
    base = store.kv_get("refresh:plan")
    assert base["external_reserve"] == cfg.external_reserve and base["other_requests_today"] == 100
    ad.app_requests_today = lambda: 900                                           # the application has already used 900 of the 1,000: nothing is left for refreshes
    mon.refresh_plan(clock())
    full = store.kv_get("refresh:plan")
    assert full["external_reserve"] == 900 and full["event_slots"] == 0 and full["unfunded_count"] == 10 and full["funded"] == []


def test_production_shaped_load_302_events_nine_models_with_evidence_scans_fast_and_stays_idempotent(tmp_path):
    import random
    import time as _t
    from datetime import timedelta
    from earnings_monitor import calendar_us as CAL
    from earnings_monitor.adapters import LABELS
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path)
    ad.model_labels = dict(LABELS)                                                    # the nine production sources
    rnd = random.Random(3)
    days = [clock().date() + timedelta(days=i) for i in range(1, 22) if CAL.is_trading_day(clock().date() + timedelta(days=i))][:11]
    ad.cal = [{"symbol": "S%03d" % i, "company": "Company %d" % i, "date": rnd.choice(days).isoformat(), "session": rnd.choice(["BMO", "AMC"])} for i in range(302)]
    ad.sources = {m: "['eps_estimates','guidance','options_combined','price_action_rs']" for m in LABELS}
    for s in (r["symbol"] for r in ad.cal):
        for m in LABELS:
            ad.set_model(m, s, rnd.choice(["BUY", "SELL", "NEUTRAL"]), clock(), score=rnd.randint(30, 70), coverage=round(rnd.uniform(0.5, 1), 2))
        ad.ev_items[s] = [item("ESTIMATES", "eps_estimates", bias=round(rnd.uniform(-1, 1), 1)), item("OPTIONS_FLOW", "flow-recent", bias=0.2, total_premium=500000, bullish_share=60, prints=9),
                          item("NEWS", "n1", title="headline %s" % s), item("PRICE_VOLUME", "price_action_rs", bias=0.1)]
    t0 = _t.perf_counter()
    first = mon.eligibility_check(clock())
    t1 = _t.perf_counter()
    ev1 = mon.evidence_scan(clock())
    t2 = _t.perf_counter()
    plan = mon.refresh_plan(clock())
    t3 = _t.perf_counter()
    again = mon.eligibility_check(clock())
    ev2 = mon.evidence_scan(clock())
    n_pred = store.one("SELECT COUNT(*) n FROM predictions")["n"]
    assert first["recompute"]["events"] > 150 and n_pred == first["recompute"]["new_predictions"] == first["recompute"]["events"] * 9    # one stored version per model per eligible event
    assert again["recompute"]["new_predictions"] == 0 and ev2["new"] == 0 and ev2["changed"] == 0                  # a repeat records nothing
    assert ev1["items"] == 302 * 4 and ev1["new"] == 302 * 4 and plan["eligible_events"] == first["recompute"]["events"]
    print("\nLOCAL TIMING (not a production figure): eligibility %.2fs | evidence %.2fs | plan %.3fs" % (t1 - t0, t2 - t1, t3 - t2))
    assert (t1 - t0) < 30 and (t2 - t1) < 30


def test_the_internal_cap_is_a_configurable_guard_with_the_same_default_and_an_approval_alone_cannot_raise_it(tmp_path):
    import json as _j
    from datetime import datetime, timezone
    from earnings_monitor import config as C
    from earnings_monitor.gate import evaluate
    from earnings_monitor.sim import write_approval
    t = datetime(2026, 11, 9, 13, 0, tzinfo=timezone.utc)
    env = {"EARNINGS_MONITOR_ENABLED": "1"}
    assert C.Config().hard_daily_cap == C.HARD_DAILY_CAP == 1000
    write_approval(tmp_path, t, mode="refresh", budget=3000, events=8)
    assert "DAILY_BUDGET_INVALID" in evaluate(C.Config(), tmp_path, t, env).reasons                      # the approval file alone cannot lift the cap
    (tmp_path / "config.json").write_text(_j.dumps({"hard_daily_cap": 5000}))
    cfg = C.Config.load(str(tmp_path / "config.json"))
    assert cfg.hard_daily_cap == 5000 and evaluate(cfg, tmp_path, t, env).mode == "REFRESH"                 # the owner's config file can


def test_plan_driven_refresh_is_off_by_default_even_with_a_refresh_approval(tmp_path):
    from earnings_monitor import config as C
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path, budget=700, events=8)                   # REFRESH approval, default config
    ad.cal = [{"symbol": "AAA", "date": "2026-11-12", "session": "BMO"}]
    sched.tick(clock())
    assert not store.q("SELECT 1 FROM jobs WHERE kind='EVENT_REFRESH'") and ad.calls["refresh_event"] == 0
    d2, clock2, store2, cfg2, ad2, mon2, sched2, access2 = mk(tmp_path / "on", budget=700, events=8, cfg=C.Config(plan_driven_refresh=True))
    sched2.tick(clock2())
    assert store2.q("SELECT 1 FROM jobs WHERE kind='EVENT_REFRESH'")                                       # only the owner's explicit setting adds it


def _nov3_world(tmp_path):
    """The real situation: events dated Nov 3 and Nov 4; a historical snapshot made on the morning of Nov 3 that scored only Nov 4 events."""
    from datetime import datetime, timezone
    from earnings_monitor.sim import Clock, build_sim, write_approval
    t = datetime(2026, 11, 3, 13, 0, tzinfo=timezone.utc)
    clock = Clock(t)
    d = tmp_path / "em"
    d.mkdir()
    write_approval(d, t, mode="cache_only", days=14)
    store, cfg, ad, mon, sched, access = build_sim(d, clock)
    ad.cal = [{"symbol": "AAA", "date": "2026-11-03", "session": "BMO"}, {"symbol": "BBB", "date": "2026-11-03", "session": "AMC"},
              {"symbol": "CCC", "date": "2026-11-04", "session": "BMO"}, {"symbol": "DDD", "date": "2026-11-04", "session": "AMC"}, {"symbol": "EEE", "date": "2026-11-04", "session": "BMO"}]
    snap_at = datetime(2026, 11, 3, 8, 29, 8, tzinfo=timezone.utc)
    ad.set_model("smart_move", "CCC", "BUY", snap_at, coverage="30/40", source="smart_move_snapshot", event_date="2026-11-04")
    ad.set_model("smart_move", "DDD", "NO_VALID_PREDICTION", snap_at, coverage="15/40", source="smart_move_snapshot", event_date="2026-11-04")
    ad.sm_cov = {"source": "HISTORICAL_SNAPSHOT", "generated_at": snap_at.isoformat(), "rows": 2, "event_dates": ["2026-11-04"], "model_version": "score40-v1"}
    mon.eligibility_check(clock())
    return d, clock, store, ad, mon


def test_smart_money_move_shows_each_market_date_with_the_truthful_label_origin_and_reason(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from earnings_monitor import routes
    d, clock, store, ad, mon = _nov3_world(tmp_path)
    monkeypatch.setenv("EARNINGS_MONITOR_DIR", str(d))
    monkeypatch.setattr(routes, "now", lambda: clock())
    app = FastAPI()
    app.include_router(routes.router)
    c = TestClient(app)
    rows = {r["symbol"]: r for r in c.get("/api/earnings-monitor/smart-move").json()["rows"]}
    for s in ("AAA", "BBB"):                                                                  # Nov 3: inside the last 24 hours when the snapshot was made: nothing was allowed or generated
        assert rows[s]["earnings_date"] == "2026-11-03" and rows[s]["label"] == "NO_PREDICTION_AVAILABLE" and rows[s]["origin"] == "NOT_GENERATED" and rows[s]["reason"]["code"] == "WINDOW_CLOSED_BEFORE_RUN"
        assert "less than 24 hours" in rows[s]["reason"]["text"] and rows[s]["signal"] is None
    assert rows["CCC"]["earnings_date"] == "2026-11-04" and rows["CCC"]["label"] == "BUY" and rows["CCC"]["origin"] == "HISTORICAL_SNAPSHOT"
    assert rows["DDD"]["label"] == "NO_VALID_PREDICTION" and rows["DDD"]["origin"] == "HISTORICAL_SNAPSHOT" and rows["DDD"]["reason"]["code"] == "ENGINE_RESULT"   # the engine's own "no valid prediction" is kept, with its own reason
    assert rows["EEE"]["label"] == "NO_PREDICTION_AVAILABLE" and rows["EEE"]["reason"]["code"] == "NOT_SELECTED_OR_NOT_COLLECTED" and rows["EEE"]["origin"] == "NOT_GENERATED"
    cell = {r["symbol"]: r for r in c.get("/api/earnings-monitor/models").json()["rows"]}["AAA"]["cells"]["smart_move"]
    assert cell["label"] == "NO_PREDICTION_AVAILABLE" and cell["origin"] == "NOT_GENERATED" and "less than 24 hours" in cell["reason"]
    assert c.get("/api/earnings-monitor/smart-move").json()["coverage"]["event_dates"] == ["2026-11-04"]


def test_a_result_for_one_report_date_is_never_attached_to_an_event_of_another_date(tmp_path):
    from datetime import datetime, timezone
    d, clock, store, ad, mon = _nov3_world(tmp_path)
    # the Nov 4 result for CCC exists; if the same symbol ALSO had a Nov 3 event it must not inherit it
    ad.cal.append({"symbol": "ZZZ", "date": "2026-11-03", "session": "BMO"})
    ad.set_model("smart_move", "ZZZ", "SELL", datetime(2026, 11, 3, 8, 29, tzinfo=timezone.utc), coverage="30/40", source="smart_move_snapshot", event_date="2026-11-04")   # a Nov 4 call about a symbol whose event is Nov 3
    mon.observe_calendar(ad.cal, clock(), complete=False)
    out = mon.recompute(clock())
    zzz = store.one("SELECT id FROM events WHERE symbol='ZZZ'")
    assert out["date_mismatch"] >= 1 and not store.q("SELECT 1 FROM predictions WHERE event_id=? AND model='smart_move'", (zzz["id"],))
    assert store.one("SELECT status FROM model_state WHERE event_id=? AND model='smart_move'", (zzz["id"],))["status"] != "VALID"
    # and the correct pairing still works
    ccc = store.one("SELECT id FROM events WHERE symbol='CCC'")
    assert store.one("SELECT signal FROM predictions WHERE event_id=? AND model='smart_move'", (ccc["id"],))["signal"] == "BUY"


def test_smart_money_move_window_watch_alerts_once_before_a_window_closes_and_never_guesses_without_the_engine(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from earnings_monitor import routes
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path)
    ad.cal = [{"symbol": "AAA", "date": "2026-11-12", "session": "BMO"}]
    mon.observe_calendar(ad.cal, clock(), complete=True)
    mon.recompute(clock())
    assert store.kv_get("smd:windows") is None and not [r for r in store.q("SELECT rule FROM alerts") if r["rule"] == "SMART_MOVE_WINDOW"]       # no live engine: nothing is guessed, nothing alerted
    far = (clock() + timedelta(hours=20)).isoformat()
    ad.sm_win = {"as_of": clock().isoformat(), "open": 40, "open_without_result": 25, "first_cutoff_utc": far, "missing_by_date": {"2026-11-12": 25}, "closed": 100}
    mon.recompute(clock())
    assert not [r for r in store.q("SELECT rule FROM alerts") if r["rule"] == "SMART_MOVE_WINDOW"]                                              # the window is 20 h from closing: too early to alert
    soon = (clock() + timedelta(hours=3)).isoformat()
    ad.sm_win = dict(ad.sm_win, first_cutoff_utc=soon)
    mon.recompute(clock())
    mon.recompute(clock())
    alerts = [r for r in store.q("SELECT payload FROM alerts") if "SMART_MOVE_WINDOW" in r["payload"]]
    assert len(alerts) == 1 and "25 event(s) are inside the Smart Money Move prediction window" in alerts[0]["payload"] and "After that no prediction is allowed" in alerts[0]["payload"]
    monkeypatch.setenv("EARNINGS_MONITOR_DIR", str(d))
    monkeypatch.setattr(routes, "now", lambda: clock())
    app = FastAPI()
    app.include_router(routes.router)
    assert TestClient(app).get("/api/earnings-monitor/smart-move").json()["windows"]["open_without_result"] == 25


def test_the_engine_window_report_comes_from_the_engines_own_rows():
    from earnings_monitor.smart_move import SmartMoveSource
    src = SmartMoveSource()
    assert src.window_report() is None
    src._meta = {"source": "HISTORICAL_SNAPSHOT"}
    assert src.window_report() is None                                                             # a snapshot says nothing about windows
    src._meta = {"source": "LIVE_ENGINE", "generated_at": "2026-11-03T08:00:00+00:00"}
    src._rows = {"A": {"window": "OPEN", "result": "BUY", "event_date": "2026-11-04", "window_cutoff_utc": "2026-11-03T12:00:00+00:00"},
                 "B": {"window": "OPEN", "result": "PENDING FINAL REFRESH", "event_date": "2026-11-04", "window_cutoff_utc": "2026-11-03T12:00:00+00:00"},
                 "C": {"window": "OPEN", "result": "NO VALID PREDICTION", "event_date": "2026-11-05", "window_cutoff_utc": "2026-11-04T12:00:00+00:00"},
                 "D": {"window": "CLOSED", "result": "NO VALID PREDICTION", "event_date": "2026-11-03"}}
    r = src.window_report()
    assert r["open"] == 3 and r["open_without_result"] == 2 and r["first_cutoff_utc"] == "2026-11-03T12:00:00+00:00" and r["missing_by_date"] == {"2026-11-04": 1, "2026-11-05": 1} and r["closed"] == 1


def test_a_stock_outside_a_models_universe_is_not_scored_rather_than_pending(tmp_path):
    from earnings_monitor import logic as L
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path)
    ad.cal = [{"symbol": "AAA", "date": "2026-11-12", "session": "BMO"}, {"symbol": "BBB", "date": "2026-11-12", "session": "BMO"}]
    mon.observe_calendar(ad.cal, clock(), complete=True)
    ad.set_model("claude", "AAA", "BUY", clock(), score=70, coverage=0.9, event_date="2026-11-12")              # Claude scored AAA but has no row for BBB; Grok has no scan at all
    mon.recompute(clock())
    st = {(r["symbol"], r["model"]): r["status"] for r in store.q("SELECT e.symbol, m.model, m.status FROM model_state m JOIN events e ON e.id=m.event_id")}
    assert st[("AAA", "claude")] == "VALID" and st[("BBB", "claude")] == "NOT_COVERED" and st[("BBB", "grok")] == "ABSENT"
    assert L.display_status({"status": "NOT_COVERED"}) == "NOT_COVERED" and L.display_status({"status": "ABSENT"}) == "PENDING"
    assert not store.q("SELECT 1 FROM predictions WHERE model='claude' AND event_id=(SELECT id FROM events WHERE symbol='BBB')")       # nothing manufactured


def test_the_calendar_is_paged_by_market_date_and_scope_with_one_shared_overview(tmp_path, monkeypatch):
    from datetime import timedelta
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from earnings_monitor import calendar_us as CAL, routes
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path)
    days = [clock().date() + timedelta(days=i) for i in range(1, 40) if CAL.is_trading_day(clock().date() + timedelta(days=i))]
    ad.cal = [{"symbol": "S%03d" % i, "date": days[i % 18].isoformat(), "session": "BMO"} for i in range(120)]
    for r in ad.cal[:30]:
        ad.set_model("claude", r["symbol"], "BUY", clock(), score=60, coverage=0.9, event_date=r["date"])
    mon.observe_calendar(ad.cal, clock(), complete=True)
    mon.recompute(clock())
    monkeypatch.setenv("EARNINGS_MONITOR_DIR", str(d))
    monkeypatch.setattr(routes, "now", lambda: clock())
    app = FastAPI()
    app.include_router(routes.router)
    c = TestClient(app)
    up = c.get("/api/earnings-monitor/upcoming?limit=50").json()
    ov = up["summary"]
    assert up["count"] == 50 and up["total"] == ov["events"] == 120 and sum(x["events"] for x in up["by_date"]) == 120 and ov["dates"] == len(up["by_date"])
    assert ov["in_window"] + ov["outside_window"] == 120 and ov["in_window"] > 0 and ov["outside_window"] > 0 and ov["fresh_calls_per_model"]["claude"] == ov["with_fresh_call"] <= 30
    # the SAME definitions in every view
    pages = [c.get("/api/earnings-monitor/%s?limit=1000" % v).json() for v in ("upcoming", "models", "smart-move")]
    assert {p["summary"]["events"] for p in pages} == {120} and {p["total"] for p in pages} == {120}
    day = up["by_date"][3]["date"]
    one = c.get("/api/earnings-monitor/upcoming?date=%s&limit=1000" % day).json()
    assert one["total"] == up["by_date"][3]["events"] and {r["earnings_date"] for r in one["rows"]} == {day}
    assert c.get("/api/earnings-monitor/upcoming?scope=outside&limit=1000").json()["total"] == ov["outside_window"]
    assert all(r["out_of_window"] for r in c.get("/api/earnings-monitor/upcoming?scope=outside&limit=1000").json()["rows"])
    p2 = c.get("/api/earnings-monitor/upcoming?limit=50&offset=50").json()
    assert p2["count"] == 50 and {r["id"] for r in p2["rows"]}.isdisjoint({r["id"] for r in up["rows"]})
    assert c.get("/api/earnings-monitor/upcoming?scope=bogus").status_code == 422 and c.get("/api/earnings-monitor/upcoming?date=nope").status_code == 422
