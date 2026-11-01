"""V2 evidence pipeline with a mock service: dedupe, accounting, validation, checkpoint resume, provider stop, as-of reads (no future information), real governor path with a fake fetcher."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "docs" / "smart_move" / "app"))
import smart_move_live as L  # noqa: E402
import smart_move_v2 as V  # noqa: E402
import smart_move_v2_collect as C  # noqa: E402
from smvshadow import governor as gv  # noqa: E402

UTC = timezone.utc
T0 = datetime(2026, 11, 1, 12, tzinfo=UTC)

BODIES = {
    "uw_analysts": {"data": [{"action": "upgraded", "timestamp": "2026-10-30T10:00:00+00:00", "ticker": "A"}]},
    "uw_estimates": {"data": {"estimates": [{"date": "2026-12-31", "horizon": "quarter", "eps_estimate_average": 1.5, "revenue_estimate_average": 100.0}]}},
    "uw_flow_alerts": {"data": [{"created_at": "2026-10-31T15:00:00+00:00", "type": "call", "total_ask_side_prem": 300000}]},
    "uw_short_float": {"data": [{"market_date": "2026-10-15", "si_float": 0.2, "short_interest": 1000000, "days_to_cover": 4}, {"market_date": "2026-09-30", "si_float": 0.18, "short_interest": 900000, "days_to_cover": 4}]},
    "uw_inst_own": {"data": [{"filing_date": "2026-09-30", "units_change": 100, "shares_outstanding": 1000000}]},
    "uw_news": {"data": [{"created_at": "2026-10-31T15:00:00+00:00"}]},
}


class FakeSvc:
    """Same contract as RefreshService._call + store, no network."""
    def __init__(self, tmp, clock, fail_after=None, bad=None):
        self.store = L.LiveStore(tmp)
        self.clock, self.calls, self.fail_after, self.bad = clock, [], fail_after, bad or {}

    def _call(self, kind, symbol, params, rid, priority="core"):
        self.calls.append((kind, symbol, dict(params)))
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            return None, None, self.clock.now()
        raw = self.bad.get(kind) or json.dumps(BODIES[kind]).encode()
        return raw, self.store.put(raw), self.clock.now()


def test_plan_covers_the_five_core_kinds_and_news_only_on_request(tmp_path):
    svc = FakeSvc(tmp_path, gv.FakeClock(T0))
    t = C.plan(["A", "B"], svc.store, T0)
    assert len(t) == 10 and {x["kind"] for x in t} == set(C.CORE)
    assert len(C.plan(["A"], svc.store, T0, include_news=True)) == 6


def test_run_accounts_every_request_validates_and_never_duplicates(tmp_path):
    svc = FakeSvc(tmp_path, gv.FakeClock(T0))
    acc = C.run(svc, C.plan(["A", "B"], svc.store, T0), max_requests=50)
    assert acc["attempted"] == len(svc.calls) == 10 and acc["ok"] == 10 and acc["failed"] == 0 and sum(acc["by_kind"].values()) == 10
    assert ("uw_analysts", "A", {"limit": 50, "ticker": "A"}) in svc.calls and any(k == "uw_estimates" and p == {} for k, _, p in svc.calls)
    assert C.plan(["A", "B"], svc.store, T0 + timedelta(hours=2)) == []                                   # the same day: nothing is due, nothing is re-requested
    again = C.plan(["A", "B"], svc.store, T0 + timedelta(hours=25))
    assert [x["kind"] for x in again if x["symbol"] == "A"] == ["uw_analysts", "uw_flow_alerts"]          # estimates (7 d), short interest (10 d), ownership (30 d) are NOT re-requested
    assert len(C.plan(["A"], svc.store, T0 + timedelta(days=40))) == 5                                      # all five are due again later (the history builds up)


def test_budget_cap_invalid_bodies_and_provider_stop(tmp_path):
    svc = FakeSvc(tmp_path, gv.FakeClock(T0))
    acc = C.run(svc, C.plan(["A", "B"], svc.store, T0), max_requests=3)
    assert acc["attempted"] == 3 and acc["budget_exhausted"] is True
    bad = FakeSvc(tmp_path / "bad", gv.FakeClock(T0), bad={"uw_flow_alerts": b"<html>oops</html>", "uw_inst_own": json.dumps({"data": [{"nope": 1}]}).encode()})
    r = C.run(bad, C.plan(["A"], bad.store, T0), max_requests=10)
    assert r["invalid"] == 2 and r["ok"] == 3 and {x["status"] for x in bad.store.read(C.EVIDENCE)} == {"OK", "INVALID"}
    stop = FakeSvc(tmp_path / "stop", gv.FakeClock(T0), fail_after=2)
    s = C.run(stop, C.plan(["A", "B"], stop.store, T0), max_requests=50, stopped=lambda: len(stop.calls) >= 3)
    assert s["stopped"] is True and s["attempted"] == 3 and len(stop.calls) == 3                          # no further request after the stop signal


def test_checkpoint_resume_repeats_nothing(tmp_path):
    ck = tmp_path / "ck.json"
    svc = FakeSvc(tmp_path / "s", gv.FakeClock(T0))
    tasks = C.plan(["A", "B"], svc.store, T0)
    C.run(svc, tasks, max_requests=4, checkpoint=ck)
    first = list(svc.calls)
    r2 = C.run(svc, tasks, max_requests=50, checkpoint=ck)
    assert r2["skipped_resume"] == 4 and len(svc.calls) == 10 and svc.calls[:4] == first and len({(k, sy) for k, sy, _ in svc.calls}) == 10


def test_inputs_are_read_as_of_the_capture_time_and_feed_the_scorers(tmp_path):
    svc = FakeSvc(tmp_path, gv.FakeClock(T0))
    C.run(svc, C.plan(["A"], svc.store, T0), max_requests=10)
    # a later snapshot of the consensus (saved a week after) must be invisible to a capture made at T0
    svc.clock.t = T0 + timedelta(days=8)
    svc.store.append(C.EVIDENCE, {"id": "x", "kind": "uw_estimates", "symbol": "A", "retrieved_at": svc.clock.now().isoformat(), "raw_sha256": svc.store.put(json.dumps({"data": {"estimates": [{"date": "2026-12-31", "eps_estimate_average": 2.0, "revenue_estimate_average": 120.0}]}}).encode()), "status": "OK", "rows": 1, "params": {}})
    at_t0 = C.load_inputs(svc.store, "A", T0 + timedelta(hours=1))
    assert len(at_t0["est_snapshots"]) == 1 and at_t0["est_snapshots"][0]["eps"] == 1.5 and at_t0["flow"] and at_t0["analyst_actions"]
    later = C.load_inputs(svc.store, "A", T0 + timedelta(days=9))
    assert len(later["est_snapshots"]) == 2 and later["est_snapshots"][1]["eps"] == 2.0
    cap = T0 + timedelta(days=9)
    rev = V.analyst(later["est_snapshots"], later["analyst_actions"], cap, "continuation")
    assert rev["available"] and rev["eps_revision_pct"] > 30 and rev["bull"] == 10
    never = C.load_inputs(svc.store, "ZZZ", cap)
    assert all(v is None for v in never.values())
    assert V.options_flow(never["flow"], "2026-11-03", cap, "continuation")["reason"] == "NOT_COLLECTED"


def test_real_service_path_the_governor_enforces_the_kill_switch_and_the_authorization(tmp_path, monkeypatch):
    """The production RefreshService with a FAKE fetcher: a new V2 kind obeys the same kill switch and cap as V1 requests."""
    class Fetcher(L.Fetcher):
        mode = "LIVE"

        def __init__(self):
            self.n = 0

        def fetch(self, kind, symbol, params):
            self.n += 1
            return json.dumps(BODIES[kind]).encode()
    clock = gv.FakeClock(T0)
    f = Fetcher()
    cfg = L.LiveConfig(mode="LIVE", store_dir=tmp_path / "st", refresh_enabled=False, authorized_daily=0, spacing_s=2.0)
    svc = L.RefreshService(cfg, f, clock)
    tasks = C.plan(["A"], svc.store, T0)
    acc = C.run(svc, tasks, max_requests=10)                                           # kill switch engaged + not authorized: nothing is sent
    assert f.n == 0 and acc["ok"] == 0 and acc["failed"] == len(tasks)
    svc.cfg.refresh_enabled, svc.cfg.authorized_daily, svc.gov.authorized_daily, svc.gov.enabled = True, 3, 3, True
    svc.gov.hourly_shared_cap = svc.gov.offhours_hourly_cap = 10 ** 6
    svc.gov.reserve_existing_hourly = svc.gov.reserve_existing_offhours = 0
    acc2 = C.run(svc, C.plan(["A"], svc.store, T0), max_requests=10)
    assert f.n == 3 and acc2["ok"] == 3 and acc2["failed"] == 2                        # the authorization cap of 3 stops the rest
    assert len(svc.store.read("ledger")) >= 3
    assert any(e["kind"] == "uw_flow_alerts" for e in svc.store.read("requests")) or any(e["kind"] == "uw_analysts" for e in svc.store.read("requests"))


def test_repeat_plan_lists_what_adds_information_and_never_manufactures_history(tmp_path):
    svc = FakeSvc(tmp_path, gv.FakeClock(T0))
    C.run(svc, C.plan(["A"], svc.store, T0), max_requests=10)
    p = C.repeat_plan(svc.store, ["A", "NEW"], T0 + timedelta(hours=1))
    a, n = p["by_symbol"]["A"], p["by_symbol"]["NEW"]
    assert a["uw_estimates"]["snapshots"] == 1 and a["uw_estimates"]["due_now"] is False and a["uw_estimates"]["next_useful"].startswith("2026-11-08")      # a second snapshot is needed a week later
    assert a["uw_inst_own"]["next_useful"].startswith("2026-12-01") and all(v["next_useful"] == "now" for v in n.values())
    assert p["requests_due_now"] == 5 and "plan only" in p["note"]
    later = C.repeat_plan(svc.store, ["A"], T0 + timedelta(days=9))
    assert later["by_symbol"]["A"]["uw_estimates"]["due_now"] is True and later["by_symbol"]["A"]["uw_inst_own"]["due_now"] is False
