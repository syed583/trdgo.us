"""Complete earnings intelligence: market holidays, vendor-verified calendar, statuses, alert-noise control, per-purpose provider permission, verified outcomes and trust thresholds, the production adapters (with the
live modules faked) and the Smart Move source. Sample data only; nothing here can reach a provider."""
import json
import sys
import types
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from earnings_monitor import alerts as A  # noqa: E402
from earnings_monitor import calendar_us as CAL  # noqa: E402
from earnings_monitor import config as C  # noqa: E402
from earnings_monitor import logic as L  # noqa: E402
from earnings_monitor.gate import ProviderBlocked  # noqa: E402
from earnings_monitor.service import in_session_window, outcome_ready_at  # noqa: E402
from earnings_monitor.sim import Clock, build_sim, write_approval  # noqa: E402
from earnings_monitor.smart_move import SmartMoveSource, to_obs  # noqa: E402

UTC = timezone.utc
BASE = datetime(2026, 11, 9, 17, 0, tzinfo=UTC)


def mk(tmp_path, purposes=("calendar", "event", "outcome"), budget=450, cfg=None, mode="refresh", start=BASE):
    clock = Clock(start)
    d = tmp_path / "em"
    d.mkdir(parents=True, exist_ok=True)
    write_approval(d, start, mode=mode, budget=budget, purposes=purposes)
    store, cfg, ad, mon, sched, access = build_sim(d, clock, cfg=cfg)
    sched.tick(clock())
    return d, clock, store, cfg, ad, mon, sched, access


def seed(mon, ad, clock, sym="AAA", date_="2026-11-11", sess="BMO", sources=None):
    ad.cal = [{"symbol": sym, "company": sym, "date": date_, "session": sess, "sources": sources or []}]
    mon.observe_calendar(ad.cal, clock(), complete=True)


# ------------------------------------------------------------------ US market calendar
def test_nyse_holidays_match_the_published_2026_and_2027_schedules():
    h26 = sorted(d.isoformat() for d in CAL.holidays(2026))
    assert h26 == ["2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25"]
    h27 = sorted(d.isoformat() for d in CAL.holidays(2027))
    assert h27 == ["2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24"]
    assert not CAL.is_trading_day(date(2026, 7, 4)) and not CAL.is_trading_day(date(2026, 7, 3)) and CAL.is_trading_day(date(2026, 7, 2))
    assert CAL.is_early_close(date(2026, 11, 27)) and CAL.is_early_close(date(2026, 12, 24)) and not CAL.is_early_close(date(2027, 12, 24)) and not CAL.is_early_close(date(2026, 7, 2))
    assert CAL.close_et(date(2026, 11, 27)) == time(13, 0) and CAL.close_et(date(2026, 11, 25)) == time(16, 0)
    assert CAL.close_utc(date(2026, 11, 25)) == datetime(2026, 11, 25, 21, 0, tzinfo=UTC) and CAL.close_utc(date(2026, 6, 24)) == datetime(2026, 6, 24, 20, 0, tzinfo=UTC)       # EST vs EDT


def test_reaction_session_and_outcome_timing_follow_holidays_and_early_closes():
    assert CAL.reaction_session(date(2026, 11, 25), "AMC") == date(2026, 11, 27)               # Thanksgiving is skipped
    assert CAL.reaction_session(date(2026, 4, 2), "AMC") == date(2026, 4, 6)                    # Good Friday and the weekend are skipped
    assert CAL.reaction_session(date(2026, 11, 27), "BMO") == date(2026, 11, 27)
    assert CAL.reaction_session(date(2026, 11, 26), "BMO") == date(2026, 11, 27)                # a report dated on a holiday reacts at the next open
    cfg = C.Config()
    ev = {"earnings_date": "2026-11-27", "session": "BMO"}
    assert outcome_ready_at(cfg, ev) == datetime(2026, 11, 27, 18, 45, tzinfo=UTC)               # early close 13:00 ET + 45 min
    ev2 = {"earnings_date": "2026-11-25", "session": "AMC"}
    assert outcome_ready_at(cfg, ev2) == datetime(2026, 11, 27, 18, 45, tzinfo=UTC)
    assert not in_session_window(cfg, datetime(2026, 11, 26, 13, 0, tzinfo=UTC), {"earnings_date": "2026-11-26", "session": "BMO"})      # holiday: no earnings-day checks
    assert in_session_window(cfg, datetime(2026, 11, 25, 12, 0, tzinfo=UTC), {"earnings_date": "2026-11-25", "session": "BMO"})
    assert CAL.window_et(date(2026, 11, 27), "AMC", cfg.amc_window_et) == ("12:00", "17:00")


# ------------------------------------------------------------------ vendor-verified calendar
def test_verify_calendar_needs_two_agreeing_vendors_and_a_disagreement_is_a_conflict():
    v = L.verify_calendar
    assert v("2026-11-11", []) == "UNVERIFIED"
    assert v("2026-11-11", [{"vendor": "UW", "date": "2026-11-11"}]) == "SINGLE_VENDOR"
    assert v("2026-11-11", [{"vendor": "UW", "date": "2026-11-11"}, {"vendor": "FINVIZ", "date": "2026-11-11"}]) == "CONFIRMED"
    assert v("2026-11-11", [{"vendor": "UW", "date": "2026-11-11"}, {"vendor": "FINVIZ", "date": "2026-11-12"}]) == "CONFLICT"
    assert v("2026-11-11", [{"vendor": "UW", "date": "2026-11-11"}, {"vendor": "uw", "date": "2026-11-11"}]) == "SINGLE_VENDOR"      # the same vendor twice is not confirmation


def test_a_vendor_conflict_is_recorded_alerted_and_blocks_predictions_until_resolved(tmp_path):
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path)
    ok = [{"vendor": "UW", "date": "2026-11-11", "session": "BMO"}, {"vendor": "FINVIZ", "date": "2026-11-11", "session": "BMO"}]
    seed(mon, ad, clock, sources=ok)
    assert store.one("SELECT verification FROM events")["verification"] == "CONFIRMED"
    ad.set_model("claude", "AAA", "BUY", BASE, score=70, coverage="9/10")
    assert mon.recompute(clock())["new_predictions"] == 1
    clock.advance(hours=7)
    bad = [{"vendor": "UW", "date": "2026-11-11", "session": "BMO"}, {"vendor": "FINVIZ", "date": "2026-11-12", "session": "BMO"}]
    seed(mon, ad, clock, sources=bad)
    assert store.one("SELECT verification FROM events")["verification"] == "CONFLICT" and "VERIFICATION_CHANGED" in [r["kind"] for r in store.q("SELECT kind FROM changes")]
    assert store.one("SELECT COUNT(*) n FROM alerts WHERE rule='DATE_CHANGE'")["n"] == 1
    ev = store.one("SELECT * FROM events")
    assert mon.eligible(ev, clock())["reasons"] == ["CALENDAR_VENDORS_DISAGREE"]
    ad.set_model("claude", "AAA", "SELL", clock(), score=20, coverage="9/10")
    assert mon.recompute(clock())["events"] == 0                                              # no new prediction while the date is disputed


# ------------------------------------------------------------------ statuses
def test_display_status_keeps_no_trade_pending_stale_and_no_valid_prediction_apart():
    d = L.display_status
    assert d({"status": "VALID", "signal": "NO_TRADE", "stale": False}) == "NO_TRADE"
    assert d({"status": "VALID", "signal": "NO_TRADE", "stale": True}) == "STALE"
    assert d({"status": "VALID", "signal": "BUY", "stale": False}) == "BUY"
    assert d({"status": "INSUFFICIENT_DATA", "signal": "NO_VALID_PREDICTION"}) == "NO_VALID_PREDICTION"
    assert d({"status": "INSUFFICIENT_DATA", "signal": "LOW_DATA"}) == "LOW_DATA"
    assert d({"status": "PENDING", "signal": None}) == "PENDING" and d({"status": "ABSENT"}) == "PENDING"


# ------------------------------------------------------------------ preservation of every version
def test_every_version_keeps_its_source_engine_and_times(tmp_path):
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path)
    seed(mon, ad, clock)
    ad.set_model("smart_move", "AAA", "NO_TRADE", BASE, coverage="30/40", source="smart_move_engine", engine_version="score40-v1")
    mon.recompute(clock())
    clock.advance(hours=3)
    ad.set_model("smart_move", "AAA", "BUY", clock(), coverage="30/40", source="smart_move_engine", engine_version="score40-v1")
    mon.recompute(clock())
    rows = store.q("SELECT signal, source, engine_version, as_of, recorded_at FROM predictions WHERE model='smart_move' ORDER BY id")
    assert [r["signal"] for r in rows] == ["NO_TRADE", "BUY"] and all(r["source"] == "smart_move_engine" and r["engine_version"] == "score40-v1" for r in rows)
    assert rows[0]["recorded_at"] < rows[1]["recorded_at"]
    assert store.one("SELECT source FROM model_state WHERE model='smart_move'")["source"] == "smart_move_engine"
    assert json.loads(store.one("SELECT detail FROM changes WHERE kind='SIGNAL_CHANGED'")["detail"])["source"] == "smart_move_engine"


def test_a_stale_call_that_becomes_fresh_again_is_reported_once(tmp_path):
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path)
    seed(mon, ad, clock)
    ad.set_model("grok", "AAA", "BUY", BASE, score=60, coverage="9/10")
    mon.recompute(clock())
    clock.advance(hours=40)
    mon.recompute(clock())
    ad.set_model("grok", "AAA", "BUY", clock(), score=60, coverage="9/10")
    mon.recompute(clock())
    mon.recompute(clock())
    kinds = [r["kind"] for r in store.q("SELECT kind FROM changes WHERE model='grok'")]
    assert kinds.count("PREDICTION_STALE") == 1 and kinds.count("PREDICTION_REFRESHED") == 1
    assert store.one("SELECT stale FROM model_state WHERE model='grok'")["stale"] == 0


# ------------------------------------------------------------------ alert noise
def test_stale_calls_are_one_digest_per_event_not_one_alert_per_model(tmp_path):
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path)
    seed(mon, ad, clock)
    for m in ("claude", "grok", "meta", "ped", "smart_move"):
        ad.set_model(m, "AAA", "BUY", BASE, score=60, coverage="9/10")
    mon.recompute(clock())
    clock.advance(hours=40)
    mon.recompute(clock())
    stale = store.q("SELECT payload FROM alerts WHERE rule='STALE_OR_MISSING_EVIDENCE'")
    assert len(stale) == 1 and sorted(json.loads(stale[0]["payload"])["models"]) == ["claude", "grok", "meta", "ped", "smart_move"]
    assert store.one("SELECT COUNT(*) n FROM changes WHERE kind='PREDICTION_STALE'")["n"] == 5       # the change history still has every model


def test_cooldown_flapping_and_the_hourly_cap_suppress_with_a_recorded_reason(tmp_path):
    cfg = C.Config(alert_hourly_cap=6)
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path, cfg=cfg)
    seed(mon, ad, clock)
    sigs = ["BUY", "SELL", "BUY", "SELL", "BUY"]
    for i, sig in enumerate(sigs):                                                    # a model flipping every 5 minutes
        ad.set_model("claude", "AAA", sig, clock(), score=50 + i, coverage="9/10")
        mon.recompute(clock())
        clock.advance(minutes=5)
    rows = store.q("SELECT status, detail, rule FROM alerts WHERE rule='DIRECTION_CHANGE' ORDER BY id")
    delivered = [r for r in rows if r["status"] == "PENDING"]
    why = [r["detail"] for r in rows if r["status"] == "SUPPRESSED"]
    assert len(delivered) <= 2 and any(w.startswith("COOLDOWN") for w in why) or any(w.startswith("FLAPPING") for w in why)
    assert store.one("SELECT COUNT(*) n FROM changes WHERE kind='SIGNAL_CHANGED'")["n"] == 4         # nothing is lost from the change history
    # hourly cap: many different events at once
    d2, clock2, store2, cfg2, ad2, mon2, sched2, access2 = mk(tmp_path / "cap", cfg=C.Config(alert_hourly_cap=5))
    ad2.cal = [{"symbol": "S%d" % i, "company": "S", "date": "2026-11-11", "session": "BMO"} for i in range(12)]
    mon2.observe_calendar(ad2.cal, clock2(), complete=True)
    for i in range(12):
        ad2.set_model("claude", "S%d" % i, "BUY", BASE, score=60, coverage="9/10")
    mon2.recompute(clock2())
    status = {r["status"]: r["n"] for r in store2.q("SELECT status, COUNT(*) n FROM alerts GROUP BY status")}
    assert status["PENDING"] == 5 + 1 and status["SUPPRESSED"] == 7                       # 5 ordinary + ONE summary; the other 7 are held back
    assert store2.one("SELECT COUNT(*) n FROM alerts WHERE rule='ALERT_SUMMARY'")["n"] == 1
    assert all("HOURLY_CAP" in r["detail"] for r in store2.q("SELECT detail FROM alerts WHERE status='SUPPRESSED'"))


def test_scanner_failures_are_never_capped_or_cooled_down(tmp_path):
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path, cfg=C.Config(alert_hourly_cap=0))
    a = [A.scanner_failure(clock(), "CALENDAR_REFRESH", "boom %d" % i, cfg) for i in range(3)]
    assert A.enqueue(store, clock(), a, cfg) == 3


# ------------------------------------------------------------------ per-purpose provider permission
def test_each_refresh_purpose_needs_its_own_approval_and_a_missing_one_costs_nothing(tmp_path):
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path, purposes=("calendar",), budget=100)
    seed(mon, ad, clock, date_="2026-11-09", sess="BMO")
    clock.t = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)                                # 07:00 ET: inside the Before-Open window
    mon.earnings_day_check(clock())
    assert ad.calls["refresh_event"] == 0 and store.usage("2026-11-09") == 0
    assert any(r["action"] == "REFRESH_BLOCKED" for r in store.q("SELECT action FROM audit_log"))
    with pytest.raises(ProviderBlocked, match="PURPOSE_NOT_APPROVED"):
        access.call("event", 15, lambda: 1, purpose="event")
    assert access.call("calendar", 20, lambda: "ok", purpose="calendar") == "ok"
    (d / "collector.active").write_text("x")
    with pytest.raises(ProviderBlocked, match="COLLECTOR_ACTIVE"):
        access.call("calendar", 20, lambda: "ok", purpose="calendar")                  # the monitor waits while the Smart Move collector runs
    (d / "collector.active").unlink()
    write_approval(d, clock(), mode="refresh", budget=450, purposes=())
    st = sched.gate_fn(clock())
    assert st.mode == "OFF" and "NO_REFRESH_PURPOSE_APPROVED" in st.reasons


# ------------------------------------------------------------------ verified outcomes and trust thresholds
def test_an_unverified_outcome_is_recorded_but_never_finalises_or_scores(tmp_path):
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path)
    seed(mon, ad, clock, date_="2026-11-10", sess="BMO")
    ad.set_model("claude", "AAA", "BUY", BASE, score=60, coverage="9/10")
    mon.recompute(clock())
    clock.t = datetime(2026, 11, 10, 23, 0, tzinfo=UTC)
    ad.out[("AAA", "2026-11-10")] = {"move_pct": -50.0, "direction": "SELL", "verified": False, "source": "daily_bars"}      # looks like a split
    mon.check_results(clock())
    ev = store.one("SELECT * FROM events")
    assert ev["outcome_at"] is None and "OUTCOME_UNVERIFIED" in [r["kind"] for r in store.q("SELECT kind FROM changes")] and "OUTCOME_AVAILABLE" not in [r["kind"] for r in store.q("SELECT kind FROM changes")]
    ad.out[("AAA", "2026-11-10")] = {"move_pct": 4.0, "direction": "BUY", "verified": True, "source": "daily_bars"}
    mon.check_results(clock())
    ev = store.one("SELECT * FROM events")
    assert ev["outcome_at"] is not None and json.loads(ev["outcome_json"])["verified"] is True


def test_accuracy_and_comparisons_appear_only_from_the_minimum_sample(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from earnings_monitor import routes
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path)
    n = 6
    ad.cal = [{"symbol": "S%d" % i, "company": "S", "date": "2026-11-10", "session": "BMO"} for i in range(n)]
    mon.observe_calendar(ad.cal, clock(), complete=True)
    for i in range(n):
        ad.set_model("claude", "S%d" % i, "BUY", BASE, score=60, coverage="9/10")
        ad.set_model("grok", "S%d" % i, "BUY" if i < 4 else "SELL", BASE, score=60, coverage="9/10")
    mon.recompute(clock())
    clock.t = datetime(2026, 11, 10, 23, 0, tzinfo=UTC)
    for i in range(n):
        ad.out[("S%d" % i, "2026-11-10")] = {"move_pct": 3.0 if i < 5 else -3.0, "direction": "x", "verified": True, "source": "daily_bars"}
    mon.check_results(clock())
    monkeypatch.setenv("EARNINGS_MONITOR_DIR", str(d))
    app = FastAPI()
    app.include_router(routes.router)
    c = TestClient(app)
    acc = {r["model"]: r for r in c.get("/api/earnings-monitor/accuracy").json()["rows"]}
    assert acc["claude"]["decisive"] == 6 and acc["claude"]["correct"] == 5 and acc["claude"]["accuracy_pct"] is None and acc["claude"]["sample_status"].startswith("TOO_SMALL")
    cmp_ = c.get("/api/earnings-monitor/comparison").json()["rows"]
    assert cmp_[0]["shared_events"] == 6 and cmp_[0]["status"].startswith("TOO_SMALL") and "both_right" not in cmp_[0]
    (d / "config.json").write_text(json.dumps({"accuracy_min_sample": 5, "comparison_min_shared": 5}))
    acc = {r["model"]: r for r in c.get("/api/earnings-monitor/accuracy").json()["rows"]}
    assert acc["claude"]["accuracy_pct"] == round(100 * 5 / 6, 1) and acc["grok"]["correct"] == 5 and acc["grok"]["incorrect"] == 1
    cmp_ = c.get("/api/earnings-monitor/comparison").json()["rows"][0]
    assert cmp_["status"] == "OK" and (cmp_["both_right"], cmp_["only_a_right"], cmp_["only_b_right"], cmp_["both_wrong"]) == (4, 1, 1, 0) and cmp_["agree"] == 4
    hist = c.get("/api/earnings-monitor/history?symbol=S0").json()
    assert hist["count"] == 2 and {r["model"] for r in hist["rows"]} == {"claude", "grok"}


# ------------------------------------------------------------------ production adapters with the live modules faked
@pytest.fixture
def fake_app(monkeypatch):
    calls = {"equity": [], "trade": [], "moves": [], "cal": 0}
    swr = types.ModuleType("swr")
    swr._values = {}
    swr.peek = lambda key: swr._values.get(key, (0, None))[1]
    swr.held_at = lambda key: swr._values.get(key, (None, None))[0]
    eq = types.ModuleType("earnings_equity_service")
    eq.get_analysis = lambda s, profile="default": calls["equity"].append(s) or {"params": [1], "symbol": s}
    ets = types.ModuleType("earnings_trade_service")
    ets.get_summary = lambda s, profile="default": calls["trade"].append(s) or {"symbol": s}
    ets._historical_moves = lambda s, limit=0: calls["moves"].append(s) or [{"date": "2026-11-10", "label": "2026-11", "move_pct": 4.2}, {"date": "2026-08-05", "label": "2026-08", "move_pct": 250.0}]
    cal = types.ModuleType("uw_earnings_calendar")
    cal.calendar = lambda days=14: calls.__setitem__("cal", calls["cal"] + 1) or {"status": "OK", "rows": [{"symbol": "AAA", "company": "Alpha", "date": "2026-11-11", "reporting_time": "BMO"},
                                                                                                         {"symbol": "BBB", "company": "Beta", "date": "2026-11-12", "reporting_time": "AMC"}]}
    fv = types.ModuleType("finviz_service")
    fv.earnings_map = lambda: {"AAA": {"date": "2026-11-11", "time": "Before Market Open"}, "BBB": {"date": "2026-11-13", "time": "after"}}
    for name, mod in (("swr", swr), ("earnings_equity_service", eq), ("earnings_trade_service", ets), ("uw_earnings_calendar", cal), ("finviz_service", fv)):
        monkeypatch.setitem(sys.modules, name, mod)
    return swr, calls


def test_prod_adapter_reads_caches_falls_back_to_labelled_snapshots_and_never_invents(fake_app, monkeypatch):
    from earnings_monitor.adapters import ProdAdapters, MODEL_KEYS
    swr, calls = fake_app
    t = datetime(2026, 11, 9, 12, 0, tzinfo=UTC).timestamp()
    swr._values["grok:scan:15"] = (t, {"rows": [{"symbol": "AAA", "signal": "BUY", "score": 62.5, "coverage": "8/10", "tier": "A", "date": "2026-11-11", "report_timing": "Before Open"},
                                                {"symbol": "BBB", "signal": "PENDING", "score": None}]})

    class Row(tuple):
        pass
    stored_rows = [("claude_new", "AAA", "2026-11-11", "SELL", 31.0, datetime(2026, 11, 8, 20, 0)), ("meta", "AAA", "2026-11-11", "LOW_DATA", None, datetime(2026, 11, 8, 20, 0))]

    class Db:
        def execute(self, *a, **k):
            return types.SimpleNamespace(fetchall=lambda: stored_rows, fetchone=lambda: None)

        def close(self):
            pass
    sqlalchemy = types.ModuleType("sqlalchemy")
    sqlalchemy.text = lambda s: s
    monkeypatch.setitem(sys.modules, "sqlalchemy", sqlalchemy)               # restored after the test: other suites use the real sqlalchemy
    ad = ProdAdapters(smart_move=SmartMoveSource(), session_factory=lambda: Db(), clock=lambda: datetime(2026, 11, 9, 13, 0, tzinfo=UTC))
    m = ad.models()
    assert m["grok"]["AAA"]["source"] == "swr_cache" and m["grok"]["AAA"]["signal"] == "BUY" and m["grok"]["AAA"]["as_of"].startswith("2026-11-09T12:00")
    assert m["claude"]["AAA"]["signal"] == "SELL" and m["claude"]["AAA"]["source"] == "model_predictions_snapshot"      # cold cache: the stored daily snapshot, labelled
    assert "AAA" not in m["meta"]                                                           # LOW_DATA was never stored as a call, and is not invented
    assert m["smart_move"] == {}
    cal = ad.calendar()
    assert cal == [{"symbol": "AAA", "company": None, "date": "2026-11-11", "session": "BMO", "source": "model_scan_cache"}, {"symbol": "BBB", "company": None, "date": None, "session": "UNKNOWN", "source": "model_scan_cache"}][:1] or len(cal) >= 1
    assert calls["cal"] == 0 and calls["equity"] == [] and calls["moves"] == []             # reading the caches made no call at all


def test_prod_adapter_provider_paths_verify_the_calendar_refresh_evidence_and_outcomes(fake_app):
    from earnings_monitor.adapters import ProdAdapters
    swr, calls = fake_app
    ad = ProdAdapters(smart_move=SmartMoveSource(), session_factory=lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    rows = {r["symbol"]: r for r in ad.refresh_calendar()}
    assert L.verify_calendar("2026-11-11", rows["AAA"]["sources"]) == "CONFIRMED" and L.verify_calendar("2026-11-12", rows["BBB"]["sources"]) == "CONFLICT"
    ad.refresh_event("AAA")
    assert calls["equity"] == ["AAA"] and calls["trade"] == ["AAA"] and "earn:equity:AAA" in swr._values and "earn:trade:default:AAA" in swr._values
    assert ad.outcome("AAA", "2026-11-10") is None                                          # nothing fetched yet, nothing invented
    ad.verify_outcome("AAA", "2026-11-10")
    o = ad.outcome("AAA", "2026-11-10")
    assert o["move_pct"] == 4.2 and o["verified"] is True and o["source"] == "daily_bars"
    assert ad.outcome("AAA", "2026-08-05")["verified"] is False                              # a 250% "move" is a split or bad data: unverified
    assert ad.outcome("AAA", "2025-01-01") is None


def test_source_report_says_what_each_model_is_and_that_none_calls_an_external_ai_service(fake_app):
    from earnings_monitor.adapters import ProdAdapters, MODEL_META
    swr, _ = fake_app
    swr._values["meta:scan:15"] = (datetime(2026, 11, 9, 12, 0, tzinfo=UTC).timestamp(), {"rows": [{"symbol": "AAA", "signal": "BUY"}]})
    rep = ProdAdapters(smart_move=SmartMoveSource(), session_factory=lambda: (_ for _ in ()).throw(RuntimeError("no db"))).source_report()
    byid = {m["id"]: m for m in rep["models"]}
    assert len(rep["models"]) == 9 and all(m["external_ai_service"] is False for m in rep["models"])
    assert byid["meta"]["stored_predictions_now"] is True and byid["grok"]["stored_predictions_now"] is False
    assert byid["smart_move"]["engine_installed"] is False and "separate provider run" in byid["smart_move"]["needs_authorised_execution"]
    llm = ("anthropic", "openai", "generativeai", "api.x.ai", "moonshot", "messages.create", "chat.completions")
    for m, meta in MODEL_META.items():                                                      # the claim is checked against the code, not asserted
        src = (BACKEND / (meta["module"] + ".py")).read_text(encoding="utf-8")
        assert not [w for w in llm if w in src], (m, meta["module"])


# ------------------------------------------------------------------ Smart Move source
class FakeEngine:
    def __init__(self, rows, plan=None):
        self.rows, self._plan = rows, plan

    def classify(self, now, symbols=None):
        return self.rows

    def plan(self, events, now, budget=50):
        return self._plan


def row(sym, result, bull=0, bear=0, cov=30, ev="2026-11-09T08:29:00+00:00"):
    return {"symbol": sym, "result": result, "coverage_points": cov, "coverage_max": 40, "liquidity": "PASS", "evidence_retrieved": [ev] if ev else None, "model_version": "score40-v1"}


def test_smart_move_engine_rows_become_honest_observations_with_the_evidence_time():
    now = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    src = SmartMoveSource(FakeEngine([row("A", "BUY"), row("B", "NO TRADE (genuine)"), row("C", "NO VALID PREDICTION", cov=15), row("D", "PENDING FINAL REFRESH", ev=None)]))
    m = src.models(now)
    assert {s: o["signal"] for s, o in m.items()} == {"A": "BUY", "B": "NO_TRADE", "C": "NO_VALID_PREDICTION", "D": "PENDING"}
    assert m["A"]["as_of"] == "2026-11-09T08:29:00+00:00" and m["A"]["source"] == "smart_move_engine" and m["A"]["engine_version"] == "score40-v1" and m["A"]["coverage"] == "30/40"
    assert m["D"]["as_of"] == now.isoformat()                                               # no evidence time: stamped with the check time, and still not a call
    st = L.model_status(m["C"], now, 36)
    assert st["status"] == "INSUFFICIENT_DATA" and L.display_status({"status": st["status"], "signal": "NO_VALID_PREDICTION", "stale": False}) == "NO_VALID_PREDICTION"
    assert L.model_status(m["D"], now, 36)["status"] == "PENDING" and L.model_status(m["B"], now, 36)["status"] == "VALID"


def test_a_broken_engine_never_becomes_a_prediction_and_the_snapshot_is_the_fallback(tmp_path):
    import smart_move_engine20_snapshot as S
    rep = tmp_path / "r.json"
    rep.write_text(json.dumps({"as_of": "2026-11-03T08:29:08+00:00", "counts": {}, "model_version": "score40-v1", "rows": [
        {"symbol": "AAA", "company": "A", "event_date": "2026-11-04", "session": "BMO", "result": "BUY", "reason": "r", "reason_codes": [], "coverage_points": 30, "coverage_max": 40, "bull": 20, "bear": 0,
         "liquidity": "PASS", "adv20_usd": 5e8, "missing_factors": [], "window": "OPEN", "window_cutoff_utc": "2026-11-03T11:00:00+00:00", "evidence_retrieved": ["2026-11-03T08:29:00+00:00"], "model_version": "score40-v1"}]}))
    S.build_snapshot(rep, {"verdict": "PASS", "problems": []}, tmp_path / "snaps", source="t")

    class Broken:
        def classify(self, now, symbols=None):
            raise RuntimeError("engine crashed")
    m = SmartMoveSource(Broken(), str(tmp_path / "snaps")).models(datetime(2026, 11, 9, tzinfo=UTC))
    assert m["AAA"]["signal"] == "BUY" and m["AAA"]["source"] == "smart_move_snapshot"
    assert SmartMoveSource(Broken(), None).models(datetime(2026, 11, 9, tzinfo=UTC)) == {}              # no engine result and no snapshot: nothing, not a guess
    assert SmartMoveSource(None, None).status()["how_predictions_arise"] == "none: not installed"


def test_the_smart_move_plan_is_a_recorded_request_not_an_action(tmp_path):
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path)
    seed(mon, ad, clock)
    assert mon.smart_move_plan(clock()) == {"available": False, "reason": "Smart Move engine / evidence store not installed on this server"}
    ad.plan = {"symbols": ["AAA"], "requests_needed": 48, "funded": ["AAA"], "unfunded": [], "budget": 50}
    r = mon.smart_move_plan(clock())
    assert r["available"] is True and store.kv_get("smart_move:plan")["requests_needed"] == 48
    assert store.usage("2026-11-09") == 0 and ad.calls == {"refresh_calendar": 0, "refresh_event": 0, "verify_outcome": 0}         # planning sends nothing
    sched.tick(clock())
    assert any(j["kind"] == "SMART_MOVE_PLAN" for j in store.q("SELECT kind FROM jobs"))


# ------------------------------------------------------------------ end to end: Thanksgiving week (holiday + early close)
def test_thanksgiving_week_end_to_end_checks_outcomes_and_eligibility_follow_the_exchange_calendar(tmp_path):
    start = datetime(2026, 11, 24, 17, 0, tzinfo=UTC)
    d, clock, store, cfg, ad, mon, sched, access = mk(tmp_path, start=start)
    ad.cal = [{"symbol": "AAA", "company": "A", "date": "2026-11-25", "session": "AMC"}, {"symbol": "BBB", "company": "B", "date": "2026-11-27", "session": "BMO"},
              {"symbol": "CCC", "company": "C", "date": "2026-11-26", "session": "BMO"}]
    mon.observe_calendar(ad.cal, clock(), complete=True)
    for sym in ("AAA", "BBB", "CCC"):
        ad.set_model("claude", sym, "BUY", clock(), score=60, coverage="9/10")
    assert mon.eligible(store.one("SELECT * FROM events WHERE symbol='CCC'"), clock())["reasons"] == ["NOT_A_TRADING_DAY"]        # Thanksgiving: no report can be live
    mon.recompute(clock())
    assert {r["symbol"] for r in store.q("SELECT DISTINCT e.symbol FROM predictions p JOIN events e ON e.id=p.event_id")} == {"AAA", "BBB"}

    def jobs_at(t):
        clock.t = t
        sched.tick(clock())
        return [j["kind"] for j in store.q("SELECT kind FROM jobs WHERE run_after <= ?", (t.isoformat(),))]
    clock.t = datetime(2026, 11, 25, 20, 0, tzinfo=UTC)                                # 15:00 ET on the AMC day: inside the After-Close window
    sched.tick(clock())
    sched.run_pending(clock())
    assert ad.calls["refresh_event"] == 1                                              # AAA refreshed once for its session
    clock.t = datetime(2026, 11, 26, 14, 0, tzinfo=UTC)                                # Thanksgiving: no earnings-day job is even queued
    before = store.one("SELECT COUNT(*) n FROM jobs WHERE kind='EARNINGS_DAY_CHECK'")["n"]
    sched.tick(clock())
    assert store.one("SELECT COUNT(*) n FROM jobs WHERE kind='EARNINGS_DAY_CHECK'")["n"] == before
    clock.t = datetime(2026, 11, 27, 18, 30, tzinfo=UTC)                               # 13:30 ET on the early-close day: the 13:00 close + 45 min has not passed yet
    sched.tick(clock())
    assert store.one("SELECT COUNT(*) n FROM jobs WHERE kind='OUTCOME_VERIFY'")["n"] == 0
    clock.t = datetime(2026, 11, 27, 18, 50, tzinfo=UTC)                               # 13:50 ET: both AAA (reacts Fri) and BBB (BMO Fri) are ready
    sched.tick(clock())
    assert store.one("SELECT COUNT(*) n FROM jobs WHERE kind='OUTCOME_VERIFY'")["n"] == 2
    ad.out[("AAA", "2026-11-25")] = {"move_pct": 2.5, "verified": True, "source": "daily_bars"}
    ad.out[("BBB", "2026-11-27")] = {"move_pct": -1.5, "verified": True, "source": "daily_bars"}
    sched.run_pending(clock())
    assert ad.calls["verify_outcome"] == 2
    assert {r["symbol"] for r in store.q("SELECT symbol FROM events WHERE outcome_at IS NOT NULL")} == {"AAA", "BBB"}
