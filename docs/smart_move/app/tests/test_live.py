"""Live scoring and governed refresh, tested with the SIMULATED provider and a fake clock only. No real request, no database."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_live as L  # noqa: E402
import smart_move_local_server as srv  # noqa: E402
import smart_move_routes as R  # noqa: E402
import smart_move_sim as S  # noqa: E402
from smvshadow import governor as gv  # noqa: E402

UTC = timezone.utc
T0 = datetime(2026, 11, 3, 11, 5, tzinfo=UTC)          # Tuesday 06:05 ET; the simulated events (4 Nov after close) have their capture window open


def make(tmp_path, *, enabled=True, authorized=1700, start=T0, fetcher=None, usage=None, **cfg_kw):
    clock = gv.FakeClock(start)
    cfg = L.LiveConfig(mode="SIMULATION", store_dir=tmp_path / "live", refresh_enabled=enabled, authorized_daily=authorized, spacing_s=0.0, **cfg_kw)
    prov = fetcher or S.SimulatedProvider(clock)
    return L.RefreshService(cfg, prov, clock, existing_usage_fn=usage), prov, clock


def rows_of(svc, now=None):
    return L.LiveScorer(svc).score_all(now or svc.clock.now())


def test_safe_by_default_nothing_is_sent(tmp_path):
    svc, prov, _ = make(tmp_path, enabled=False, authorized=0)
    out = svc.run()
    f = svc.feed()
    assert out["status"] == "KILL_SWITCH" and out["requests"] == 0 and prov.calls == []
    assert f["status"] == "DISABLED_KILL_SWITCH" and f["next_refresh"] is None and {b["code"] for b in f["blockers"]} >= {"KILL_SWITCH", "NOT_AUTHORIZED"}
    c = L.LiveConfig()
    assert c.refresh_enabled is False and c.authorized_daily == 0 and c.planning_ceiling == 1700


def test_enabled_but_not_authorized_still_sends_nothing(tmp_path):
    svc, prov, _ = make(tmp_path, enabled=True, authorized=0)
    out = svc.run()
    assert out["status"] == "NOT_AUTHORIZED" and prov.calls == [] and svc.feed()["status"] == "NOT_AUTHORIZED"


def test_authorized_run_collects_scores_and_counts_every_request(tmp_path):
    svc, prov, _ = make(tmp_path)
    out = svc.run()
    assert out["status"] == "OK" and out["requests"] == len(prov.calls) == 80 and out["scored"] == 16
    f = svc.feed()
    assert f["requests_today"] == 80 and sum(f["requests_today_by_provider"].values()) == 80 and f["requests_today"] <= f["authorized_daily"] <= f["planning_ceiling"]
    assert f["status"] == "OK" and f["last_updated"] and f["freshness"]["calendar"]["state"] == "FRESH" and f["freshness"]["prices"]["state"] == "FRESH"
    rows = rows_of(svc)
    assert len(rows) == 16 and all(r["available_points"] == 40 and r["evidence_mode"] == "SIMULATION" for r in rows)
    sigs = {r["engine"][v]["signal"] for r in rows for v in ("continuation", "contrarian")}
    assert {"BUY", "SELL", "NO_TRADE"} <= sigs
    for r in rows:
        assert r["calendar"]["status_now"] == "VENDOR_CONFIRMED" and not r["blocked_by"] and r["missing_factors"] == []
        for e in r["evidence"]:
            assert len(e["raw_sha256"]) == 64 and e["retrieved_at"] and e["mode"] == "SIMULATION" and svc.store.get(e["raw_sha256"])
    assert len(svc.store.read("requests")) == 80 and svc.store.read("predictions")


def test_the_frozen_scorer_decides_gates_only_remove_signals(tmp_path):
    svc, _, _ = make(tmp_path)
    svc.run()
    rows = rows_of(svc)
    for r in rows:
        for v in ("continuation", "contrarian"):
            e = r["engine"][v]
            assert e["signal"] == e["score_before_gate"] or e["signal"] == "NO_TRADE"
            if e["signal"] in ("BUY", "SELL"):
                assert e["bull"] + e["bear"] > 0 and e["available"] >= 24 and "RULE" in e["reason_codes"]
    assert any(r["engine"]["continuation"]["signal"] != r["engine"]["contrarian"]["signal"] for r in rows)      # the variants are scored separately


def test_a_short_authorization_never_forces_a_signal(tmp_path):
    svc, prov, _ = make(tmp_path, authorized=30)
    svc.run()
    f = svc.feed()
    assert f["requests_today"] <= 30 and len(prov.calls) <= 30
    ledger = svc.store.read("ledger")
    assert any(e["type"].startswith("DENIED") for e in ledger)
    rows = rows_of(svc)
    short = [r for r in rows if r["available_points"] < 24]
    assert short and len(short) < len(rows) + 1
    for r in short:
        for v in ("continuation", "contrarian"):
            assert r["engine"][v]["signal"] == "NO_TRADE"                      # incomplete evidence: no BUY/SELL, the missing factors and reasons are shown
        assert r["engine"]["continuation"]["reason_codes"] and r["missing_factors"]
    for r in rows:
        for v in ("continuation", "contrarian"):
            if r["engine"][v]["signal"] in ("BUY", "SELL"):
                assert r["available_points"] >= 24                              # a signal only where the frozen coverage rule is met by evidence that was really collected


def test_kill_switch_stops_requests_and_survives_a_restart(tmp_path):
    svc, prov, clock = make(tmp_path)
    svc.run()
    n = len(prov.calls)
    svc.engage_kill_switch("test")
    assert svc.run()["status"] == "KILL_SWITCH" and len(prov.calls) == n
    svc2 = L.RefreshService(svc.cfg, prov, clock)
    assert svc2.kill_engaged and svc2.run()["requests"] == 0 and svc2.feed()["status"] == "DISABLED_KILL_SWITCH"


def test_retry_limit_and_provider_stops(tmp_path):
    class Flaky(S.SimulatedProvider):
        def __init__(self, clock, mode):
            super().__init__(clock)
            self.m, self.failed = mode, 0

        def fetch(self, kind, symbol, params):
            if kind == "earnings" and self.failed < 1:
                self.failed += 1
                raise gv.ProviderError(self.m, 503 if self.m == "transient" else 429)
            return super().fetch(kind, symbol, params)
    clock = gv.FakeClock(T0)
    svc, prov, _ = make(tmp_path / "a", fetcher=Flaky(clock, "transient"))
    svc.clock = prov.clock = svc.gov.clock = clock
    svc.run()
    retries = [e for e in svc.store.read("ledger") if e["type"] == "PROVIDER_FAILURE"]
    assert len(retries) == 1 and svc.store.read("requests")                        # one retry, then success
    svc2, prov2, _ = make(tmp_path / "b", fetcher=Flaky(gv.FakeClock(T0), "rate_limited"))
    svc2.run()
    n = len(prov2.calls)
    assert any(e["type"] == "STOP_429" for e in svc2.store.read("ledger")) and svc2.feed()["status"] == "PROVIDER_STOPPED"
    assert svc2.run()["status"] in ("KILL_SWITCH", "NOT_AUTHORIZED", "OK") and len(prov2.calls) == n


def test_shared_warmer_cap_blocks_live_hours_requests(tmp_path):
    svc, prov, _ = make(tmp_path, usage=lambda ts: 400)                             # the warmer is using the whole live-hours cap
    out = svc.run()
    assert prov.calls == [] and out["requests"] == 0
    assert any(e["type"] == "DENIED_HOURLY_SHARED_CAP" for e in svc.store.read("ledger"))
    svc2, prov2, _ = make(tmp_path / "n", start=datetime(2026, 11, 3, 5, 0, tzinfo=UTC) + timedelta(hours=0), usage=lambda ts: 400)    # 00:00 ET is off-hours: 6,000/h cap
    assert svc2.run()["requests"] > 0


def test_automatic_refresh_does_not_rerequest_fresh_data_and_refreshes_stale_data(tmp_path):
    svc, prov, clock = make(tmp_path)
    svc.run()
    n = len(prov.calls)
    clock.sleep(600)
    assert svc.run()["requests"] == 0 and len(prov.calls) == n                      # everything is fresh: nothing to do
    clock.sleep(7 * 3600)                                                           # calendar older than 6 h
    out = svc.run()
    assert out["requests"] > 0 and len(prov.calls) > n
    assert any(c[0] == "uw_calendar" for c in prov.calls[n:])


def test_stale_calendar_blocks_new_signals(tmp_path):
    svc, _, clock = make(tmp_path)
    svc.run()
    assert any(r["engine"]["continuation"]["signal"] in ("BUY", "SELL") for r in rows_of(svc))
    clock.sleep(8 * 3600)                                                           # 8 h later: calendar limit (6 h inside 4 days) exceeded, window still open
    rows = rows_of(svc)
    assert rows and all(r["engine"][v]["signal"] == "NO_TRADE" for r in rows for v in ("continuation", "contrarian"))
    assert all(r["calendar"]["status_now"] == "STALE" and r["blocked_by"] for r in rows)
    f = svc.feed()
    assert f["status"] == "STALE" and f["freshness"]["calendar"]["state"] == "STALE"


def test_stale_price_evidence_is_treated_as_missing(tmp_path):
    svc, _, _ = make(tmp_path)
    svc.run()
    p = svc.store.root / "evidence.jsonl"
    recs = [json.loads(x) for x in p.read_text().splitlines()]
    for r in recs:
        if r["kind"] == "ohlc" and r["symbol"] == "SIM00":
            r["last_bar_date"] = "2026-10-29"
    p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    r0 = [r for r in rows_of(svc) if r["symbol"] == "SIM00"][0]
    assert {m["factor"] for m in r0["missing_factors"]} >= {"F5", "F3"} and r0["engine"]["continuation"]["signal"] == "NO_TRADE" and r0["available_points"] < 24


def test_f7_only_from_a_prospective_capture_in_the_window(tmp_path):
    class NoVol(S.SimulatedProvider):
        def fetch(self, kind, symbol, params):
            if kind in ("vol_context", "iv_rank"):
                raise gv.ProviderError("transient", 500)
            return super().fetch(kind, symbol, params)
    clock = gv.FakeClock(T0)
    svc, _, _ = make(tmp_path, fetcher=NoVol(clock))
    svc.clock = svc.gov.clock = clock
    svc.run()
    rows = rows_of(svc)
    assert rows and all(any(m["factor"] == "F7" and m["reason"] == "NOT_CAPTURED_LIVE_IN_WINDOW" for m in r["missing_factors"]) for r in rows)
    assert max(r["available_points"] for r in rows) == 30 and all(r["available_points"] <= 30 for r in rows)          # F7 never counts without its own prospective capture
    assert any(e["type"] == "DENIED_HOURLY_SHARED_CAP" for e in svc.store.read("ledger")) or all(r["available_points"] == 30 for r in rows)     # failed attempts also use the shared hourly allowance


def test_no_hindsight_scoring_uses_only_evidence_retrieved_before_the_scoring_time(tmp_path):
    svc, _, _ = make(tmp_path)
    svc.run()
    early = rows_of(svc, T0 - timedelta(hours=1))
    assert early == [] or all(r["available_points"] == 0 or r["engine"]["continuation"]["signal"] == "NO_TRADE" for r in early)
    late = rows_of(svc)
    assert late and any(r["available_points"] == 40 for r in late)


def test_exploratory_sec_f3_is_separate_and_unavailable(tmp_path):
    svc, _, _ = make(tmp_path)
    svc.run()
    for r in rows_of(svc):
        assert r["exploratory"]["sec_f3"]["available"] is False and "SEC_F3" not in json.dumps(r["engine"])


def test_manual_refresh_obeys_the_same_limits(tmp_path):
    svc, prov, clock = make(tmp_path)
    a = svc.manual_refresh("all", max_requests=7)
    assert a["status"] == "OK" and a["requests"] <= 7 and len(prov.calls) <= 7
    b = svc.manual_refresh("all", max_requests=7)
    assert b["status"] == "TOO_SOON" and b["requests"] == 0
    clock.sleep(120)
    assert svc.manual_refresh("prices", max_requests=400)["requests"] <= 50             # the manual cap
    svc.engage_kill_switch()
    clock.sleep(120)
    assert svc.manual_refresh("all")["status"] == "KILL_SWITCH"


# ----------------------------------------------------------------------------- API
@pytest.fixture
def api(tmp_path, monkeypatch):
    R.reset_live_service()
    client = TestClient(srv.app)
    yield client, tmp_path
    R.reset_live_service()


def test_api_default_server_shows_exact_blockers_and_refuses_to_refresh(api, monkeypatch):
    client, tmp = api
    monkeypatch.setenv("SMART_MOVE_LIVE_DIR", str(tmp / "live"))
    monkeypatch.delenv("SMART_MOVE_REFRESH_ENABLED", raising=False)
    monkeypatch.delenv("SMART_MOVE_AUTHORIZED_DAILY", raising=False)
    monkeypatch.delenv("SMART_MOVE_MODE", raising=False)
    f = client.get("/api/smart-move/feed").json()
    assert f["status"] == "DISABLED_KILL_SWITCH" and f["kill_switch"] is True and f["authorized_daily"] == 0 and f["next_refresh"] is None and f["requests_today"] == 0
    lv = client.get("/api/smart-move/live").json()
    assert lv["rows"] == [] and {b["code"] for b in lv["blockers"]} >= {"KILL_SWITCH", "NOT_AUTHORIZED", "NO_LIVE_EVIDENCE"}
    r = client.post("/api/smart-move/refresh", json={"scope": "all"})
    assert r.status_code == 403 and r.json()["detail"]["requests_sent"] == 0 and r.json()["detail"]["status"] == "REFRESH_REFUSED"
    assert client.get("/api/smart-move/feed").json()["requests_today"] == 0


def test_api_simulated_server_refreshes_scores_and_labels_everything(api, monkeypatch):
    client, tmp = api
    monkeypatch.setenv("SMART_MOVE_MODE", "SIMULATION")
    monkeypatch.setenv("SMART_MOVE_LIVE_DIR", str(tmp / "sim"))
    clock = gv.FakeClock(T0)
    svc, _, _ = make(tmp, start=T0)
    R._service = svc
    d0 = client.get("/api/smart-move/live").json()
    assert d0["counts"]["total"] == 0 and any(b["code"] == "NO_LIVE_EVIDENCE" for b in d0["blockers"])
    capped = client.post("/api/smart-move/refresh", json={"scope": "all"}).json()
    assert capped["status"] == "OK" and capped["requests"] == 50                                     # the manual cap (50 requests) applies even though more is due
    svc._last_manual = None
    svc.run()                                                                                        # the automatic path finishes the collection under the same governor
    d = client.get("/api/smart-move/live?limit=1000").json()
    assert d["counts"]["total"] == 16 and "SIMULATED PROVIDER" in d["label"] and d["mode"] == "SIMULATION"
    assert d["summary"]["continuation"]["BUY"] + d["summary"]["continuation"]["SELL"] > 0 and all(r["evidence_mode"] == "SIMULATION" for r in d["rows"])
    assert client.get("/api/smart-move/live?signal=BUY&variant=continuation").json()["counts"]["filtered"] == d["summary"]["continuation"]["BUY"]
    f = client.get("/api/smart-move/feed").json()
    assert f["status"] == "OK" and f["requests_today"] == 80 and f["last_updated"] and f["evidence_mode"] == "SIMULATION"
    svc._last_manual = svc.clock.now()
    assert client.post("/api/smart-move/refresh", json={"scope": "all"}).json()["status"] == "TOO_SOON"
    k = client.post("/api/smart-move/refresh/kill", json={"reason": "test"}).json()
    assert k["kill_switch"] is True and client.post("/api/smart-move/refresh").status_code == 403
    assert client.get("/api/smart-move/feed").json()["status"] == "DISABLED_KILL_SWITCH"
    assert client.post("/api/smart-move/refresh", json={"scope": "nope"}).status_code in (403, 422)


def test_live_dataset_is_never_filled_from_the_saved_pack_or_the_replay(api, monkeypatch):
    client, tmp = api
    monkeypatch.setenv("SMART_MOVE_LIVE_DIR", str(tmp / "live"))
    monkeypatch.delenv("SMART_MOVE_REFRESH_ENABLED", raising=False)
    monkeypatch.delenv("SMART_MOVE_MODE", raising=False)
    assert client.get("/api/smart-move/live").json()["counts"]["total"] == 0
    assert client.get("/api/smart-move/calendar").json()["counts"]["total"] == 271              # the saved observation stays a separate dataset
