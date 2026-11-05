"""MONDAY_BMO_FRIDAY variant: separate Friday window for Monday-BMO events, frozen scorer unchanged, own ledger, no signal after the Friday deadline, outcomes graded with the existing methodology. Stub provider only."""
import json
import sys
from argparse import Namespace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "docs" / "smart_move" / "shadow_impl"))
sys.path.insert(0, str(ROOT / "docs" / "smart_move" / "app"))
import smart_move_collection_runner as RUNNER  # noqa: E402
import smart_move_engine20 as E20  # noqa: E402
import smart_move_live as L  # noqa: E402
import smart_move_live_publisher as PUB  # noqa: E402
import smart_move_monday_bmo as MB  # noqa: E402
from smvshadow import cutoff as co  # noqa: E402
from smvshadow import governor as gv  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_p1_fix_f1_f2_f4 import Prov, add, svc_at  # noqa: E402

UTC = timezone.utc
FRI = datetime(2026, 11, 13, 19, 0, tzinfo=UTC)            # Friday 14:00 ET: the variant window for Monday 16 Nov BMO is open (Thu 16:00 ET .. Fri 15:30 ET)
MON = date(2026, 11, 16)


def bars_to(last, closes=None, n=60):
    """Daily bars ending on `last`; `closes` overrides (date -> (open, close))."""
    rows, d = [], date.fromisoformat(last)
    while len(rows) < n:
        if d.weekday() < 5:
            o, c = (closes or {}).get(d.isoformat(), ("100", "101"))
            rows.append({"date": d.isoformat(), "open": o, "high": "106", "low": "99", "close": c, "volume": 1_000_000, "total_volume": 1_000_000, "market_time": "r"})
        d -= timedelta(days=1)
    return {"data": sorted(rows, key=lambda r: r["date"])}


class FriProv(Prov):
    def __init__(self, last="2026-11-12", closes=None, **kw):
        super().__init__(**kw)
        self.last, self.closes = last, closes

    def fetch(self, kind, symbol, params):
        if kind == "ohlc":
            self.calls.append((kind, symbol))
            return json.dumps(bars_to(self.last, self.closes)).encode()
        return super().fetch(kind, symbol, params)


def args(tmp, **kw):
    base = dict(state_dir=str(tmp / "state"), publish_dir=str(tmp / "pub"), ceiling=50, daily_limit=100, authorize_requests=50, with_f7=False)
    base.update(kw)
    return Namespace(**base)


# =================================================================== the window
def test_variant_plan_monday_bmo_is_a_friday_close_order():
    p = MB.variant_plan(MON, "BMO")
    assert p["window_start_utc"] == datetime(2026, 11, 12, 21, 0, tzinfo=UTC)            # Thursday close
    assert p["cutoff_utc"] == datetime(2026, 11, 13, 20, 30, tzinfo=UTC)                  # Friday 15:30 ET
    assert p["base_session"] == date(2026, 11, 13) and p["reaction_session"] == MON and p["last_usable_session"] == date(2026, 11, 12)
    assert p["tradable"] is True and p["frozen_tradable"] is False and p["variant"] == "MONDAY_BMO_FRIDAY"
    assert p["frozen_cutoff_utc"] == datetime(2026, 11, 15, 11, 0, tzinfo=UTC)           # the frozen Sunday cutoff is kept for the record
    assert p["cutoff_utc"] < co.order_deadline_utc(p["base_session"])


def test_variant_plan_early_close_friday_moves_the_deadline():
    p = MB.variant_plan(date(2026, 11, 30), "BMO")                                         # Friday 27 Nov closes at 13:00 ET
    assert p["early_close_base"] is True and p["cutoff_utc"] == datetime(2026, 11, 27, 17, 30, tzinfo=UTC) and p["tradable"] is True


def test_applies_only_to_bmo_events_the_frozen_rule_cannot_trade():
    assert MB.applies(MON, "BMO") is True
    assert MB.applies(MON, "AMC") is False
    assert MB.applies(date(2026, 11, 17), "BMO") is False                                 # Tuesday BMO: the frozen window is tradable
    assert MB.applies(date(2026, 11, 27), "BMO") is True                                  # Friday after Thanksgiving: no session between the frozen window and the release
    assert MB.applies(MON, "UNKNOWN") is False
    assert MB.applies(date(2026, 11, 15), "BMO") is False                                 # Sunday: not a session, no release


def test_frozen_model_still_says_not_tradable(tmp_path):
    svc = svc_at(tmp_path, FriProv(), now=FRI)
    add(svc, "AAA", MON.isoformat(), "BMO", at=FRI)
    assert E20.tradability({"earnings_date": MON.isoformat(), "kind": "BMO", "plan": co.plan_event(MON, "BMO")}) is False
    assert svc.eligible_events(FRI) == []                                                  # the frozen engine does not see the event on Friday: nothing changed for it


# =================================================================== event discovery
def test_events_open_only_between_thursday_close_and_friday_deadline(tmp_path):
    svc = svc_at(tmp_path, FriProv(), now=FRI)
    add(svc, "AAA", MON.isoformat(), "BMO", at=FRI)
    add(svc, "BBB", MON.isoformat(), "BMO", at=FRI, cap=20e9)
    add(svc, "CCC", MON.isoformat(), "AMC", at=FRI)
    add(svc, "DDD", "2026-11-17", "BMO", at=FRI)
    assert [e["symbol"] for e in MB.events(svc, FRI)] == ["BBB", "AAA"]                  # both Monday BMO, larger cap first; AMC and Tuesday excluded
    assert MB.events(svc, datetime(2026, 11, 12, 20, 0, tzinfo=UTC)) == []                # Thursday before the close: not open
    assert MB.events(svc, datetime(2026, 11, 13, 20, 31, tzinfo=UTC)) == []               # after the Friday deadline: closed
    svc2 = svc_at(tmp_path / "early", FriProv(), now=FRI)
    tue = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    add(svc2, "AAA", MON.isoformat(), "BMO", at=tue)
    add(svc2, "BBB", MON.isoformat(), "BMO", at=tue, cap=20e9)
    assert [e["symbol"] for e in MB.events(svc2, tue, horizon_days=14)] == ["BBB", "AAA"]  # tracking view: upcoming, window not open yet


# =================================================================== dry run
def test_dry_run_sends_nothing_and_writes_only_the_variant_folder(tmp_path):
    svc = L.RefreshService(L.LiveConfig(mode="SIMULATION", store_dir=tmp_path / "store", refresh_enabled=False, authorized_daily=0, spacing_s=0.0, max_retries=0), RUNNER.NoNetworkFetcher(), gv.FakeClock(FRI))
    add(svc, "AAA", MON.isoformat(), "BMO", at=FRI)
    a = args(tmp_path)
    assert MB.profile(a, svc, FRI, live=False) == 0
    plan = json.loads((tmp_path / "state" / "monday_bmo_plan.json").read_text())
    assert plan["mode"] == "DRY_RUN" and plan["provider_requests"] == 0 and [e[0] for e in plan["events_window_open"]] == ["AAA"]
    assert plan["attempts_planned_for_evidence"] == 4                                     # SPY, AAA bars, AAA history, sector ETF (XLK)
    sub = tmp_path / "pub" / MB.SUBDIR
    st = json.loads((sub / PUB.STATUS).read_text())
    assert st["mode"] == "DRY_RUN" and st["variant"] == "MONDAY_BMO_FRIDAY" and not (sub / PUB.LEDGER).exists()
    assert not (tmp_path / "pub" / PUB.LEDGER).exists() and not (tmp_path / "pub" / PUB.STATUS).exists()   # the frozen ledger folder is not touched


# =================================================================== live: collect, score with the frozen scorer, publish to the variant ledger
def live_run(tmp_path, prov, now, a=None):
    svc = svc_at(tmp_path, prov, now=now, authorized=100)
    svc.store.kill_file.parent.mkdir(parents=True, exist_ok=True)
    svc.store.kill_file.write_text("engaged")
    return svc


def test_live_run_publishes_variant_records_with_the_friday_deadline(tmp_path):
    prov = FriProv()
    svc = live_run(tmp_path, prov, FRI)
    add(svc, "AAA", MON.isoformat(), "BMO", at=FRI)
    add(svc, "BBB", MON.isoformat(), "BMO", at=FRI)
    assert MB.profile(args(tmp_path), svc, FRI, live=True) == 0
    assert svc.store.kill_file.exists()                                                   # re-engaged
    kinds = sorted(prov.calls)
    assert ("ohlc", "SPY") in kinds and ("ohlc", "AAA") in kinds and ("earnings", "AAA") in kinds and ("ohlc", "BBB") in kinds and ("earnings", "BBB") in kinds
    assert not any(k == "uw_calendar" for k, _ in prov.calls)                             # the calendar was fresh: no refresh
    led = PUB.read_ledger(tmp_path / "pub" / MB.SUBDIR / PUB.LEDGER)
    assert led["ok"] and [r["symbol"] for r in led["records"]] == ["AAA", "BBB"]
    for r in led["records"]:
        assert r["record_type"] == "PREDICTION" and r["variant"] == "MONDAY_BMO_FRIDAY" and r["schema"] == MB.SCHEMA and r["model_version"] == "score40-v1"
        assert r["window_cutoff_utc"] == "2026-11-13T20:30:00+00:00" == r["friday_entry_deadline_utc"] and r["monday_earnings_date"] == MON.isoformat()
        assert r["base_session"] == "2026-11-13" and r["reaction_session"] == MON.isoformat() and r["frozen_cutoff_utc"] == "2026-11-15T11:00:00+00:00"
        assert r["result"] in ("BUY", "SELL", "NO_TRADE", "NO_VALID_PREDICTION") and "NOT_TRADABLE" not in r["reason_codes"]
        assert r["coverage_max"] == 40
    assert not (tmp_path / "pub" / PUB.LEDGER).exists()                                   # frozen ledger untouched
    rep = json.loads((tmp_path / "state" / "monday_bmo_report.json").read_text())
    assert {x["symbol"] for x in rep["rows"]} == {"AAA", "BBB"}
    # a second pass with identical evidence publishes nothing new
    assert MB.profile(args(tmp_path), svc, FRI + timedelta(minutes=5), live=True) == 0
    assert len(PUB.read_ledger(tmp_path / "pub" / MB.SUBDIR / PUB.LEDGER)["records"]) == 2


def test_no_signal_after_the_friday_deadline(tmp_path):
    prov = FriProv()
    late = datetime(2026, 11, 13, 20, 45, tzinfo=UTC)                                     # 15:45 ET: deadline passed
    svc = live_run(tmp_path, prov, late)
    add(svc, "AAA", MON.isoformat(), "BMO", at=late)
    assert MB.profile(args(tmp_path), svc, late, live=True) == 0
    assert prov.calls == []                                                               # no open event: nothing collected
    led = PUB.read_ledger(tmp_path / "pub" / MB.SUBDIR / PUB.LEDGER)
    assert led["ok"] and led["records"] == []                                             # events() excludes closed windows, so nothing is scored or noted
    # publish() guard: a row whose window closed is never a PREDICTION
    svc2 = live_run(tmp_path / "b", FriProv(), FRI)
    add(svc2, "AAA", MON.isoformat(), "BMO", at=FRI)
    rows = MB.score(svc2, MB.events(svc2, FRI), FRI)
    st = MB.publish(rows, late, tmp_path / "b" / "out", scan_id="x", mode="LIVE", scan_ok=True)
    assert st["events"]["published_new"] == 0 and st["events"]["window_missed_noted"] == 1
    rec = PUB.read_ledger(tmp_path / "b" / "out" / PUB.LEDGER)["records"][0]
    assert rec["record_type"] == "WINDOW_MISSED" and rec["predicted"] is False and rec["variant"] == "MONDAY_BMO_FRIDAY"


def test_missing_evidence_is_never_a_buy_or_sell(tmp_path):
    prov = FriProv(fail=("AAA",))
    svc = live_run(tmp_path, prov, FRI)
    add(svc, "AAA", MON.isoformat(), "BMO", at=FRI)
    assert MB.profile(args(tmp_path), svc, FRI, live=True) == 0
    rec = PUB.read_ledger(tmp_path / "pub" / MB.SUBDIR / PUB.LEDGER)["records"]
    assert len(rec) == 1 and rec[0]["symbol"] == "AAA"
    assert rec[0]["result"] in ("NO_VALID_PREDICTION", "PENDING_REFRESH") and rec[0]["result"] not in ("BUY", "SELL")   # frozen classify: missing fresh evidence is never a call


def test_stale_calendar_is_refreshed_for_the_monday_slot_only(tmp_path):
    prov = FriProv(cal={(MON.isoformat(), "premarket"): ["AAA"]})
    svc = live_run(tmp_path, prov, FRI)
    add(svc, "AAA", MON.isoformat(), "BMO", at=FRI - timedelta(hours=8))                  # older than the 6 h calendar limit
    assert MB.profile(args(tmp_path), svc, FRI, live=True) == 0
    plan = json.loads((tmp_path / "state" / "monday_bmo_plan.json").read_text())
    assert plan["calendar_refresh"]["dates"] == [MON.isoformat()] and plan["calendar_refresh"]["requests"] >= 1
    assert any(c[0] == "uw_calendar" for c in prov.calls)
    assert MB.stale_slots(svc, FRI) == []                                                 # after the refresh the slot is fresh (the stub observation is stamped now)
    fresh = svc_at(tmp_path / "f", FriProv(), now=FRI)
    add(fresh, "AAA", MON.isoformat(), "BMO", at=FRI)
    assert MB.stale_slots(fresh, FRI) == []
    assert MB.stale_slots(svc_at(tmp_path / "e", FriProv(), now=FRI), FRI) == [MON]        # no observation at all for the Monday slot: refresh


# =================================================================== outcomes: existing methodology, separate files
def test_outcomes_graded_after_monday_close_with_separate_accuracy(tmp_path):
    prov = FriProv()
    svc = live_run(tmp_path, prov, FRI)
    add(svc, "AAA", MON.isoformat(), "BMO", at=FRI)
    assert MB.profile(args(tmp_path), svc, FRI, live=True) == 0
    out = tmp_path / "pub" / MB.SUBDIR
    pred = MB.latest_predictions(out)[0]
    # Monday 16:10 ET: reaction session closed but the 30-minute safety delay not passed -> nothing graded
    early = datetime(2026, 11, 16, 21, 10, tzinfo=UTC)
    svc.store.kill_file.unlink()                                                          # the runner releases the kill file for the run; grade() is called inside that span
    svc.gov.enabled = True
    svc.clock.now = lambda: early
    g = MB.grade(svc, out, early, live=True, budget=10)
    assert g["fetched"] == [] and g["recorded"] == [] and g["not_due_yet"] == ["AAA"]
    # Monday 17:00 ET: due. Friday close 101 -> Monday open 103, close 105
    mon = datetime(2026, 11, 16, 22, 0, tzinfo=UTC)
    svc.clock.now = lambda: mon
    prov.last, prov.closes = MON.isoformat(), {"2026-11-13": ("100", "101"), MON.isoformat(): ("103", "105")}
    g = MB.grade(svc, out, mon, live=True, budget=10)
    assert g["fetched"] == ["AAA"] and len(g["recorded"]) == 1
    r = g["recorded"][0]
    assert r["status"] == "GRADED" and abs(r["move_pct"] - (105 / 101 - 1) * 100) < 1e-6 and abs(r["gap_pct"] - (103 / 101 - 1) * 100) < 1e-6
    acc = json.loads((out / MB.ACCURACY).read_text())
    s = acc["summary"]
    assert s["variant"] == "MONDAY_BMO_FRIDAY" and s["total_predictions"] == 1 and s["separate_from_frozen_model"] is True and s["claim_allowed"] is False
    assert s["weekend_gap_risk"]["n"] == 1 and s["weekend_gap_risk"]["mean_gap_pct"] == round((103 / 101 - 1) * 100, 3)
    row = acc["rows"][0]
    assert row["symbol"] == "AAA" and row["outcome_status"] == "GRADED" and row["outcome_class"] == "UP" and row["signal"] == pred["result"]
    if pred["result"] in ("BUY", "SELL"):
        assert row["hit"] is (pred["result"] == "BUY") and s["decided_calls"] == 1
    else:
        assert row["hit"] is None and s["decided_calls"] == 0
    # grading again records nothing new (append-only, duplicate ignored) and stays inside the same files
    g2 = MB.grade(svc, out, mon + timedelta(hours=1), live=True, budget=10)
    assert g2["fetched"] == [] and (out / MB.OUTCOMES_DIR / "outcome_details.jsonl").read_text().count("\n") == 1
    assert not (tmp_path / "pub" / "outcome_details.jsonl").exists()


def test_accuracy_counts_buy_and_sell_separately(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    now = datetime(2026, 11, 16, 22, 0, tzinfo=UTC)
    prev, seq = "0" * 64, 0
    for sym, res, bull, bear in (("AAA", "BUY", 16, 2), ("BBB", "SELL", 2, 16), ("CCC", "NO_TRADE", 8, 8), ("DDD", "BUY", 15, 1)):
        rec = PUB._append(out / PUB.LEDGER, {"schema": MB.SCHEMA, "record_type": "PREDICTION", "symbol": sym, "company": sym, "event_date": MON.isoformat(), "session": "BMO", "result": res, "bull": bull, "bear": bear,
                                             "coverage_points": 30, "coverage_max": 40, "adv20_usd": 50e6, "generated_at": "2026-11-13T19:00:00+00:00", "friday_entry_deadline_et": "2026-11-13 15:30 EST", "variant": MB.VARIANT}, prev, seq + 1)
        prev, seq = rec["record_sha256"], seq + 1
    import smart_move_outcomes as O
    for sym, mv in (("AAA", 3.0), ("BBB", 2.0), ("CCC", -1.0), ("DDD", -2.0)):
        O.record(out / MB.OUTCOMES_DIR, {"symbol": sym, "earnings_date": MON.isoformat(), "kind": "BMO", "mode": "LIVE", "source": "test", "computed_at": now.isoformat(), "status": "GRADED", "move_pct": mv, "gap_pct": mv / 2, "base_close": 100.0, "reaction_close": 100 + mv})
    s = MB.accuracy(out, now)["summary"]
    assert s["buy_accuracy"] == {"n": 2, "hits": 1, "rate": 0.5, "wilson95": s["buy_accuracy"]["wilson95"]}
    assert s["sell_accuracy"]["n"] == 1 and s["sell_accuracy"]["hits"] == 0 and s["sell_accuracy"]["rate"] == 0.0
    assert s["decided_calls"] == 3 and s["graded_events"] == 4 and s["overall_directional_accuracy"]["strict_hit_rate"] == round(1 / 3, 3)
    assert s["average_post_earnings_move_pct"] == 0.5 and s["minimum_sample_met"] is False and "NO ACCURACY CLAIM" in s["claim_text"]


# =================================================================== runner CLI
def test_runner_profile_dry_run_cli(tmp_path):
    svc = svc_at(tmp_path, FriProv(), now=FRI)
    add(svc, "AAA", MON.isoformat(), "BMO", at=FRI)
    rc = RUNNER.main(["--profile", "monday_bmo_friday", "--store-dir", str(tmp_path / "store"), "--state-dir", str(tmp_path / "state"), "--publish-dir", str(tmp_path / "pub"), "--as-of", FRI.isoformat(), "--ceiling", "40"])
    assert rc == 0
    plan = json.loads((tmp_path / "state" / "monday_bmo_plan.json").read_text())
    assert plan["profile"] == "monday_bmo_friday" and plan["ceiling"] == 40 and plan["provider_requests"] == 0
    assert (tmp_path / "pub" / MB.SUBDIR / PUB.STATUS).exists() and not (tmp_path / "pub" / PUB.STATUS).exists()


def test_runner_accepts_the_wrappers_exact_arguments_for_the_variant(tmp_path):
    """The production wrapper passes the shared settings file to every profile: --select-policy deadline_priority --select-top 80 --calendar-horizon-days 30. The variant must accept them (found on the VPS: exit 6)."""
    svc = svc_at(tmp_path, FriProv(), now=FRI)
    add(svc, "AAA", MON.isoformat(), "BMO", at=FRI)
    rc = RUNNER.main(["--profile", "monday_bmo_friday", "--store-dir", str(tmp_path / "store"), "--state-dir", str(tmp_path / "state"), "--publish-dir", str(tmp_path / "pub"), "--select-policy", "deadline_priority", "--select-top", "80",
                      "--calendar-horizon-days", "30", "--as-of", FRI.isoformat()])
    assert rc == 0
    plan = json.loads((tmp_path / "state" / "monday_bmo_plan.json").read_text())
    assert plan["profile"] == "monday_bmo_friday" and plan["provider_requests"] == 0 and [e[0] for e in plan["events_window_open"]] == ["AAA"]
    assert RUNNER.main(["--profile", "monday_bmo_friday", "--store-dir", str(tmp_path / "store"), "--state-dir", str(tmp_path / "state2"), "--calendar-horizon-days", "30", "--calendar-days-ahead", "3", "--as-of", FRI.isoformat()]) == 6   # the conflict rule still applies


def test_runner_live_gate_still_applies_to_the_variant(tmp_path, monkeypatch):
    monkeypatch.delenv("SMART_MOVE_COLLECTION_LIVE", raising=False)
    rc = RUNNER.main(["--profile", "monday_bmo_friday", "--store-dir", str(tmp_path / "store"), "--state-dir", str(tmp_path / "state"), "--mode", "live", "--arm", "--authorize-requests", "10"])
    assert rc == 6
