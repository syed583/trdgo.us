"""Shared-key gate, attempt log, 429 / auth stops, pilot stage caps (calendar <=14, Snapshot B <=18, no Snapshot A), earliest-cutoff-first order, kill switch before / during / after. Stub provider only."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_collection_jobs as J  # noqa: E402
import smart_move_collection_runner as RUNNER  # noqa: E402
import smart_move_live as L  # noqa: E402
import smart_move_shared_key_gate as SK  # noqa: E402
import test_pilot_preflight as T  # noqa: E402
from smvshadow import governor as gv  # noqa: E402

UTC = timezone.utc
OFF = datetime(2026, 11, 2, 23, 0, tzinfo=UTC)          # 18:00 ET Monday: outside the session
IN = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)           # 10:00 ET Monday: in session


def att(tmp_path, at=OFF, **over):
    d = {"attested_by": "owner", "attested_at": at.isoformat(), "acknowledges_shared_token_three_slot_limit": True, "acknowledges_production_and_other_app_use_not_visible": True}
    d.update(over)
    f = tmp_path / "att.json"
    f.write_text(json.dumps(d))
    return f


nothing = lambda: []  # noqa: E731


# ------------------------------------------------------------------ the shared-key gate
def test_without_an_attestation_the_gate_reports_not_verified_and_blocks_even_when_nothing_local_runs():
    g = SK.evaluate(OFF, None, processes=nothing, tasks=nothing)
    assert g["status"] == "SHARED KEY SAFETY NOT VERIFIED" and g["blocks_live"] and "3 simultaneous" in g["provider_limit"]
    assert any(c["check"].startswith("production server") and c["result"] == "NOT_VISIBLE" for c in g["checks"])


def test_a_running_or_enabled_local_consumer_blocks_regardless_of_any_attestation(tmp_path):
    f = att(tmp_path)
    run = SK.evaluate(OFF, f, processes=lambda: ["python C:/x/scripts/record.py --workers 4"], tasks=nothing)
    assert run["blocks_live"] and run["checks"][0]["result"] == "RUNNING"
    ena = SK.evaluate(OFF, f, processes=nothing, tasks=lambda: ["Prod-Signals-3-recorder"])
    assert ena["blocks_live"] and ena["checks"][1]["result"] == "ENABLED"
    unreadable = SK.evaluate(OFF, f, processes=lambda: None, tasks=nothing)
    assert unreadable["blocks_live"] and unreadable["checks"][0]["result"] == "UNREADABLE"


def test_a_valid_recent_attestation_with_no_local_conflict_opens_the_gate_but_never_claims_verification(tmp_path):
    g = SK.evaluate(OFF, att(tmp_path), processes=nothing, tasks=nothing)
    assert g["status"] == "OWNER_ATTESTED" and not g["blocks_live"] and "not proof" in g["note"]


@pytest.mark.parametrize("over,why", [({"attested_by": ""}, "required acknowledgements"), ({"acknowledges_shared_token_three_slot_limit": False}, "required acknowledgements"),
                                       ({"attested_at": (OFF - timedelta(hours=30)).isoformat()}, "older"), ({"attested_at": (OFF + timedelta(hours=2)).isoformat()}, "future")])
def test_incomplete_old_or_future_dated_attestations_are_rejected(tmp_path, over, why):
    g = SK.evaluate(OFF, att(tmp_path, **over), processes=nothing, tasks=nothing)
    assert g["blocks_live"] and why in g["attestation"]["why"]


def test_in_session_the_attestation_must_explicitly_accept_market_hours(tmp_path):
    assert SK.evaluate(IN, att(tmp_path, at=IN), processes=nothing, tasks=nothing)["blocks_live"]
    ok = SK.evaluate(IN, att(tmp_path, at=IN, market_hours_ok=True), processes=nothing, tasks=nothing)
    assert not ok["blocks_live"] and ok["status"] == "OWNER_ATTESTED"
    assert SK.in_us_session(IN) and not SK.in_us_session(OFF) and not SK.in_us_session(datetime(2026, 11, 1, 15, tzinfo=UTC))


def test_the_runner_refuses_a_live_run_when_the_gate_blocks_and_creates_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("SMART_MOVE_COLLECTION_LIVE", "1")
    monkeypatch.setattr(RUNNER.SK, "evaluate", lambda now, path=None, **k: {"status": "SHARED KEY SAFETY NOT VERIFIED", "blocks_live": True, "reason": "test"})
    rc = RUNNER.main(["--store-dir", str(tmp_path / "s"), "--state-dir", str(tmp_path / "st"), "--mode", "live", "--arm", "--authorize-requests", "50", "--profile", "pilot50"])
    assert rc == 7 and not (tmp_path / "st").exists()


# ------------------------------------------------------------------ attempt log, 429 and auth stops
def test_every_attempt_is_logged_with_its_status_and_no_credentials(tmp_path):
    prov = T.provider_for(T.WINDOW, fail_symbols={"WAA"})
    log = tmp_path / "state" / "attempts.jsonl"
    prov2 = J.AttemptRecorder(prov, log, gv.FakeClock(T.NOW))
    svc, _ = T.service(tmp_path, prov2)
    job = J.run_cycle(svc, now=T.NOW, state_dir=tmp_path / "state", mode="LIVE", armed=True, ceiling=50, batch_size=5, news_pages=1, calendar_requests=14)
    rows = [json.loads(x) for x in log.read_text().splitlines()]
    assert len(rows) == len(prov.calls) == sum(svc.gov.used_day.values()) and job["requests"] == len(rows)
    assert any(r["result"] == "ERROR" and r["symbol"] == "WAA" and r["error_kind"] == "transient" for r in rows) and all(r["result"] in ("OK", "ERROR") for r in rows)
    assert "Authorization" not in log.read_text() and "token" not in log.read_text().lower()


def test_http_429_stops_the_run_at_once_with_no_retry_and_blocks_the_rest_of_the_day(tmp_path):
    class Limited(T.Provider):
        def fetch(self, kind, symbol, params):
            if kind in ("uw_estimates", "uw_news") and any(c[0] in ("uw_estimates", "uw_news") for c in self.calls):
                self.calls.append((kind, symbol, dict(params)))
                raise gv.ProviderError("rate_limited", 429)
            return super().fetch(kind, symbol, params)
    prov = Limited(*T.vendor_set(T.WINDOW))
    svc, _ = T.service(tmp_path, prov, retries=0)
    job = J.run_cycle(svc, now=T.NOW, state_dir=tmp_path / "state", mode="LIVE", armed=True, ceiling=50, batch_size=1, news_pages=1, calendar_requests=14)
    n = len(prov.calls)
    assert job["status"] in ("PARTIAL", "FAILED") and job["halted"] in ("PROVIDER_STOP", "PROVIDER_STOPPED") and sum(1 for c in prov.calls if c[0] in ("uw_estimates", "uw_news")) == 2
    again = J.run_cycle(svc, now=T.NOW, state_dir=tmp_path / "state", mode="LIVE", armed=True, ceiling=50, batch_size=1)
    assert again["status"] == "BLOCKED" and "PROVIDER_STOPPED_TODAY" in again["reason"] and len(prov.calls) == n


# ------------------------------------------------------------------ pilot allocation caps and order
def test_pilot_caps_snapshot_b_at_18_never_runs_snapshot_a_and_leaves_the_rest_to_news(tmp_path):
    many = [(T.sym("W", i), "2026-11-03", "AMC") for i in range(30)] + [(T.sym("S", i), "2026-11-12", "AMC") for i in range(5)]
    prov = T.provider_for(many)
    svc, _ = T.service(tmp_path, prov)
    job = J.run_cycle(svc, now=T.NOW, state_dir=tmp_path / "state", mode="LIVE", armed=True, ceiling=50, batch_size=5, news_pages=1, calendar_requests=14, stage_caps={"snapshot_a": 0, "snapshot_b": 18})
    cal = prov.count("uw_calendar") + prov.count("finviz_calendar") + prov.count("finnhub_calendar")
    assert cal <= 14 and prov.count("uw_estimates") == 18 and not any(c[1].startswith("S") for c in prov.calls if c[0] == "uw_estimates")
    assert prov.count("uw_news") == 50 - cal - 18 and len(prov.calls) == 50 and job["status"] == "PARTIAL"


def test_earliest_cutoff_is_served_first_within_a_stage(tmp_path):
    ev = [("LAT", "2026-11-04", "BMO"), ("EAR", "2026-11-03", "AMC"), ("MID", "2026-11-04", "BMO")]
    prov = T.provider_for(ev)
    svc, _ = T.service(tmp_path, prov)
    J.run_cycle(svc, now=T.NOW, state_dir=tmp_path / "state", mode="LIVE", armed=True, ceiling=50, batch_size=5, news_pages=1, calendar_requests=14, stage_caps={"snapshot_a": 0, "snapshot_b": 18})
    est = [c[1] for c in prov.calls if c[0] == "uw_estimates"]
    assert est[0] == "EAR"                                                                              # EAR's cutoff (2 Nov 21:00 UTC) is earlier than the 4 Nov BMO cutoffs (3 Nov 11:00 UTC)


# ------------------------------------------------------------------ kill switch before, during and after
def test_kill_switch_is_respected_before_released_only_during_and_reengaged_after(tmp_path):
    prov = T.provider_for(T.WINDOW)
    svc, _ = T.service(tmp_path, prov)
    svc.engage_kill_switch("start state")
    pre = J.run_cycle(svc, now=T.NOW, state_dir=tmp_path / "s0", mode="LIVE", armed=True, ceiling=50, batch_size=5)
    assert pre["status"] == "BLOCKED" and prov.calls == [] and svc.kill_engaged                         # before: nothing is sent
    job = RUNNER.execute(svc, now=T.NOW, state_dir=tmp_path / "s1", live=True, authorized=50, ceiling=50, batch=5, news_pages=1, calendar_requests=14)
    assert job["status"] in ("COMPLETED", "PARTIAL") and prov.calls
    assert svc.store.kill_file.exists() and svc.kill_engaged                                            # after: re-engaged


def test_kill_switch_is_reengaged_even_when_the_run_raises(tmp_path, monkeypatch):
    prov = T.provider_for(T.WINDOW)
    svc, _ = T.service(tmp_path, prov)
    svc.engage_kill_switch("start state")
    monkeypatch.setattr(RUNNER.J, "run_cycle", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        RUNNER.execute(svc, now=T.NOW, state_dir=tmp_path / "s2", live=True, authorized=50, ceiling=50, batch=5)
    assert svc.store.kill_file.exists()


def test_dry_run_leaves_the_kill_switch_untouched_and_sends_nothing(tmp_path):
    prov = T.provider_for(T.WINDOW)
    svc, _ = T.service(tmp_path, prov, live=False)
    svc.engage_kill_switch("x")
    job = RUNNER.execute(svc, now=T.NOW, state_dir=tmp_path / "s3", live=False, authorized=0, ceiling=50, batch=5)
    assert job["mode"] == "DRY_RUN" and prov.calls == [] and svc.store.kill_file.exists()
