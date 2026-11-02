"""Phase 14 collector extension: earnings-payload and daily-bar jobs. A stub fetcher counts every call; no network, no credentials."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).parent))
import smart_move_collection_ext as X  # noqa: E402
import smart_move_collection_jobs as J  # noqa: E402
import smart_move_collection_runner as RUNNER  # noqa: E402
import smart_move_live as L  # noqa: E402
import smart_move_v2_collect as C  # noqa: E402
from smvshadow import governor as gv  # noqa: E402
from test_collection_jobs import CUT, Stub, T_OPEN, cycle, events, make  # noqa: E402

UTC = timezone.utc


def bars_body(retrieved_after_close=True, bad=False):
    rows = [{"date": "2026-11-02", "open": "100", "high": "101", "low": "99", "close": "100.5", "volume": 10, "market_time": "r"},
            {"date": "2026-11-03", "open": "100.5", "high": "103", "low": "100", "close": "102", "volume": 10, "market_time": "r"}]
    if bad:
        rows.append({"date": "2026-11-04", "open": "5", "high": "1", "low": "9", "close": "3", "volume": 1, "market_time": "r"})        # high < low
    return {"data": rows}


class ExtStub(Stub):
    def __init__(self, ohlc=None, **kw):
        super().__init__(**kw)
        self.ohlc = ohlc or bars_body()

    def fetch(self, kind, symbol, params):
        if kind == "uw_earnings":
            self.calls.append((kind, symbol, dict(params)))
            if symbol in self.fail:
                raise gv.ProviderError("transient", "boom")
            return json.dumps({"data": [{"report_date": "2026-11-04", "ending_fiscal_quarter": "2026-09-30", "report_time": "premarket", "source": "company"}]}).encode()
        if kind == "uw_ohlc":
            self.calls.append((kind, symbol, dict(params)))
            return json.dumps(self.ohlc).encode()
        return super().fetch(kind, symbol, params)


CAPS = {"snapshot_a": 0, "snapshot_b": 14, "earnings": 12, "ohlc": 3, "news": 17}


# ---------------------------------------------------------------- planning
def ev(sym, hours):
    return {"symbol": sym, "event_date": "2026-11-04", "cutoff_utc": (CUT + timedelta(hours=hours)).isoformat()}


def test_earnings_tasks_are_one_per_symbol_earliest_cutoff_first_capped_and_deduplicated():
    now = T_OPEN
    t = X.earnings_tasks_due([ev("B", 5), ev("A", 1), ev("A", 2), ev("C", 9)], [], now, cap=2)
    assert [x["symbol"] for x in t] == ["A", "B"] and all(x["kind"] == "uw_earnings" and x["stage"] == "E" for x in t) and t[0]["id"] == "uw_earnings-A-" + now.strftime("%Y%m%d")
    recent = [{"kind": "uw_earnings", "symbol": "A", "status": "OK", "retrieved_at": (now - timedelta(days=3)).isoformat()}]
    old = [{"kind": "uw_earnings", "symbol": "A", "status": "OK", "retrieved_at": (now - timedelta(days=8)).isoformat()}]
    failed = [{"kind": "uw_earnings", "symbol": "A", "status": "INVALID", "retrieved_at": (now - timedelta(hours=1)).isoformat()}]
    assert [x["symbol"] for x in X.earnings_tasks_due([ev("A", 1)], recent, now)] == []
    assert [x["symbol"] for x in X.earnings_tasks_due([ev("A", 1)], old, now)] == ["A"] and [x["symbol"] for x in X.earnings_tasks_due([ev("A", 1)], failed, now)] == ["A"]


def test_bar_tasks_need_a_recorded_session_a_closed_reaction_session_and_bars_retrieved_after_it():
    ev_ = [{"symbol": "A", "event_date": "2026-11-03", "session": "AMC"}, {"symbol": "B", "event_date": "2026-11-03", "session": "UNKNOWN"}, {"symbol": "C", "event_date": "2026-11-05", "session": "BMO"}]
    before_close = datetime(2026, 11, 4, 20, 0, tzinfo=UTC)                        # AMC 11-03 reacts on 11-04: closes 21:00Z, +30 min
    assert X.ohlc_tasks_due(ev_, [], before_close) == []
    after = datetime(2026, 11, 4, 21, 45, tzinfo=UTC)
    t = X.ohlc_tasks_due(ev_, [], after)
    assert [x["symbol"] for x in t] == ["A"] and t[0]["kind"] == "uw_ohlc" and t[0]["stage"] == "O"                 # B has no recorded session, C has not happened
    early = [{"kind": "uw_ohlc", "symbol": "A", "status": "OK", "retrieved_at": datetime(2026, 11, 4, 19, 0, tzinfo=UTC).isoformat()}]
    assert [x["symbol"] for x in X.ohlc_tasks_due(ev_, early, after)] == ["A"]                                          # bars fetched before the close are partial: still due
    late = [{"kind": "uw_ohlc", "symbol": "A", "status": "OK", "retrieved_at": after.isoformat()}]
    assert X.ohlc_tasks_due(ev_, late, after) == []
    assert X.ohlc_tasks_due(ev_, [], after + timedelta(days=11)) == []                                                  # too old for the bar window


def test_extension_tasks_exist_only_with_a_cap_and_never_include_snapshot_a():
    tb = J.build_tasks([{"symbol": "A", "event_date": "2026-11-04", "kind": "AMC", "plan": {"window_start_utc": CUT - timedelta(hours=24), "cutoff_utc": CUT, "assumed_release_utc": CUT + timedelta(hours=24)}}], [],
                       type("S", (), {"read": staticmethod(lambda n: [])})(), T_OPEN, {"snapshot_a": 0, "snapshot_b": 14})
    assert tb["earnings"] == [] and tb["ohlc"] == [] and not any(t.get("stage") == "A" for t in tb["tasks"])
    tb2 = J.build_tasks([{"symbol": "A", "event_date": "2026-11-04", "kind": "AMC", "plan": {"window_start_utc": CUT - timedelta(hours=24), "cutoff_utc": CUT, "assumed_release_utc": CUT + timedelta(hours=24)}}], [],
                        type("S", (), {"read": staticmethod(lambda n: [])})(), T_OPEN, CAPS)
    assert [t["kind"] for t in tb2["tasks"]][0] == "uw_earnings" and not any(t.get("stage") == "A" for t in tb2["tasks"])           # earnings payload first, still no Snapshot A


# ---------------------------------------------------------------- execution through the existing governed path
def test_new_jobs_run_through_the_governor_in_priority_order_and_store_raw_evidence_append_only(tmp_path):
    f = ExtStub()
    svc, _ = make(tmp_path, f)
    out = cycle(svc, tmp_path, events(["A", "B"]), stage_caps=CAPS)
    assert out["status"] in ("COMPLETED", "PARTIAL")
    kinds = [c[0] for c in f.calls]
    assert kinds[:2] == ["uw_earnings", "uw_earnings"] and "uw_estimates" in kinds and "uw_news" in kinds and kinds.index("uw_estimates") > 1
    recs = [r for r in svc.store.read("v2_evidence") if r["kind"] == "uw_earnings"]
    assert {r["symbol"] for r in recs} == {"A", "B"} and all(r["status"] == "OK" and r["raw_sha256"] and r["retrieved_at"] and r["params"] == {"limit": 24} for r in recs)
    body = json.loads(svc.store.get(recs[0]["raw_sha256"]))
    assert body["data"][0]["ending_fiscal_quarter"] == "2026-09-30"                                                     # the raw provider body is preserved
    n = len(f.calls)
    again = cycle(svc, tmp_path, events(["A", "B"]), stage_caps=CAPS, refresh_calendar=False)
    assert len(f.calls) == n and again["tasks_planned"] == 0                                                           # nothing is requested twice


def test_the_same_ceiling_covers_the_new_jobs_and_failures_count(tmp_path):
    f = ExtStub(fail=("B",))
    svc, _ = make(tmp_path, f, authorized=3)                       # the runner sets the governor's daily authorization to --authorize-requests: the hard cap on attempts, retries included
    out = cycle(svc, tmp_path, events(["A", "B", "C", "D"]), stage_caps=CAPS, ceiling=3)
    assert len(f.calls) <= 3 and out["requests"] <= 3
    assert out["status"] in ("PARTIAL", "FAILED") and any(c[1] == "B" for c in f.calls)


def test_a_failed_earnings_request_is_not_marked_done_and_is_retried_in_a_later_run(tmp_path):
    f = ExtStub(fail=("A",))
    svc, _ = make(tmp_path, f)
    out = cycle(svc, tmp_path, events(["A"]), stage_caps={"earnings": 5})
    assert out["tasks_done"] == 0 and not [r for r in svc.store.read("v2_evidence") if r["kind"] == "uw_earnings" and r["status"] == "OK"]
    f.fail = set()
    out2 = cycle(svc, tmp_path, events(["A"]), stage_caps={"earnings": 5}, refresh_calendar=False)
    assert [r for r in svc.store.read("v2_evidence") if r["kind"] == "uw_earnings" and r["status"] == "OK"]


def test_bar_jobs_validate_quarantine_and_reject_empty_responses(tmp_path):
    def run(body, now):
        f = ExtStub(ohlc=body)
        svc, _ = make(tmp_path / str(abs(hash(json.dumps(body)))), f, start=now)
        ev_ = [{"symbol": "A", "event_date": "2026-11-03", "session": "AMC"}]
        svc.store.append("cal_obs", {"observed_at": now.isoformat(), "symbol": "ANY"})
        J.run_cycle(svc, now=now, state_dir=tmp_path / ("st" + str(abs(hash(json.dumps(body))))), mode="LIVE", armed=True, events_fn=lambda n, h=None: [], stage_caps={"ohlc": 3}, outcome_events_fn=lambda n: ev_)
        return [r for r in svc.store.read("v2_evidence") if r["kind"] == "uw_ohlc"], svc, f
    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    good, svc, f = run(bars_body(), now)
    assert len(good) == 1 and good[0]["status"] == "OK" and good[0]["bars_accepted"] == 2 and good[0]["bars_quarantined"] == 0 and good[0]["last_bar_date"] == "2026-11-03" and f.calls[0][2] == {"timeframe": "1M"}
    part, svc, _ = run(bars_body(bad=True), now)
    assert part[0]["status"] == "OK" and part[0]["bars_accepted"] == 2 and part[0]["bars_quarantined"] == 1 and "OHLC_INCONSISTENT" in part[0]["quarantine_reasons"]
    assert json.loads(svc.store.get(part[0]["raw_sha256"]))["data"].__len__() == 3                                      # the raw body still holds all three rows
    none, _, _ = run({"data": [{"date": "2026-11-04", "open": "5", "high": "1", "low": "9", "close": "3", "market_time": "r"}]}, now)
    assert none[0]["status"] == "INVALID" and none[0]["invalid_reason"] == "NO_VALID_BARS"


def test_saved_bars_feed_the_outcome_audit_without_the_quarantined_rows(tmp_path):
    import hashlib
    import gzip
    import smart_move_outcome_audit as OA
    from smvshadow.store import rel_for
    body = json.dumps(bars_body(bad=True)).encode()
    sha = hashlib.sha256(body).hexdigest()
    p = tmp_path / rel_for(sha)
    p.parent.mkdir(parents=True)
    p.write_bytes(gzip.compress(body))
    (tmp_path / "v2_evidence.jsonl").write_text(json.dumps({"kind": "uw_ohlc", "symbol": "A", "status": "OK", "retrieved_at": "2026-11-05T12:00:00+00:00", "raw_sha256": sha}) + "\n", encoding="utf-8")
    got = OA.load_bars([str(tmp_path)])
    assert [r["date"] for r in got["A"][0]["rows"]] == ["2026-11-02", "2026-11-03"]


# ---------------------------------------------------------------- InfraFetcher (the existing choke point) and the runner profile
def test_infrafetcher_serves_the_new_kinds_only_after_the_attestation_gate_and_never_through_another_client(monkeypatch):
    import smart_move_shared_key_gate as G
    monkeypatch.setattr(G, "require_live_allowed", lambda *a, **k: (_ for _ in ()).throw(PermissionError("no attestation")))
    with pytest.raises(PermissionError):
        L.InfraFetcher()
    monkeypatch.setattr(G, "require_live_allowed", lambda *a, **k: None)
    import unusualwhales_service as uw
    saved = uw.BG_MAX_RETRIES
    seen = []
    monkeypatch.setattr(uw, "get", lambda path, params: (seen.append((path, dict(params), uw._background_flag() if hasattr(uw, "_background_flag") else None)), {"status": "OK", "data": [{"x": 1}]})[1])
    try:
        fx = L.InfraFetcher()
        assert uw.BG_MAX_RETRIES == 0
        assert json.loads(fx.fetch("uw_earnings", "AAPL", {"limit": 24})) == {"data": [{"x": 1}]}
        fx.fetch("uw_ohlc", "AAPL", {"timeframe": "1M"})
    finally:
        uw.BG_MAX_RETRIES = saved
    assert [(s[0], s[1]) for s in seen] == [("/api/earnings/AAPL", {"limit": 24}), ("/api/stock/AAPL/ohlc/1d", {"timeframe": "1M"})]
    src = (ROOT / "backend" / "smart_move_collection_ext.py").read_text(encoding="utf-8")
    assert not [ln for ln in src.splitlines() if ln.startswith(("import ", "from ")) and any(w in ln for w in ("requests", "httpx", "urllib", "socket", "unusualwhales_service", "smart_move_live"))]


def test_the_batch2_profile_keeps_the_hard_fifty_attempt_ceiling_and_the_live_gate(tmp_path):
    assert RUNNER.main(["--store-dir", str(tmp_path / "s"), "--state-dir", str(tmp_path / "st"), "--mode", "live", "--arm", "--authorize-requests", "51", "--profile", "batch2"]) == 6
    store = tmp_path / "store"
    store.mkdir()
    (store / "cal_obs.jsonl").write_text("")
    assert RUNNER.main(["--store-dir", str(store), "--state-dir", str(tmp_path / "state"), "--profile", "batch2", "--as-of", "2026-11-03T22:00:00+00:00"]) == 0                  # dry run: zero requests
    st = J._read_state(tmp_path / "state")
    job = list(st["jobs"].values())[0]
    assert job["mode"] == "DRY_RUN" and job.get("provider_requests", 0) == 0 and job["preflight"]["ceiling"] == 50
    assert RUNNER.main(["--store-dir", str(store), "--state-dir", str(tmp_path / "st2"), "--mode", "live", "--arm", "--authorize-requests", "50", "--profile", "batch2"]) == 6   # no env flag, no attestation: blocked
