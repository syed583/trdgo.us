"""Smart Money Move LIVE publisher: append-only prediction ledger, status file, window deadlines, look-ahead and duplicate guards, calendar horizon, request limits. Mocked provider (Stub fetcher), tmp stores; no network."""
import importlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(HERE))
import smart_move_collection_jobs as J  # noqa: E402
import smart_move_engine20 as E20  # noqa: E402
import smart_move_live as L  # noqa: E402
import smart_move_live_publisher as PUB  # noqa: E402
from test_engine20 import NOW, Stub, make, row, select  # noqa: E402
T_Stub, make_svc = Stub, make

UTC = timezone.utc


def synth(sym, sig, evidence=(), **kw):
    r = row(sig, ["RULE"] if sig in ("BUY", "SELL") else ["BELOW_MARGIN"], 30, 5e7)
    r.update(symbol=sym, company=sym + " INC", evidence=[{"kind": k, "symbol": sym, "retrieved_at": t, "raw_sha256": "h" + k} for k, t in evidence], **kw)
    return r


EV = (("ohlc", "2026-11-02T20:00:00+00:00"), ("earnings", "2026-11-02T20:01:00+00:00"))


def test_every_signal_class_is_published_with_generation_time_evidence_times_and_eastern_time(tmp_path):
    rows = [synth("AAA", "BUY", EV), synth("BBB", "SELL", EV), synth("CCC", "NO_TRADE", EV)]
    st = PUB.publish_rows(rows, NOW, tmp_path, scan_id="s1", mode="LIVE", scan_ok=True, limits={"run_limit": 150, "daily_limit": 700}, requests_today=40)
    led = PUB.read_ledger(tmp_path / PUB.LEDGER)
    assert led["ok"] and [r["result"] for r in led["records"]] == ["BUY", "SELL", "NO_TRADE"]
    r = led["records"][0]
    assert r["generated_at"] == NOW.isoformat() and r["generated_at_et"] == "2026-11-02 15:30:00 EST" and r["evidence_latest"] == "2026-11-02T20:01:00+00:00" and len(r["evidence"]) == 2
    assert r["window"] == "OPEN" and r["model_version"] == "score40-v1" and r["mode"] == "LIVE" and r["record_type"] == "PREDICTION"
    assert st["requests"] == {"used_today": 40, "remaining_today": 660, "ny_date": "2026-11-02"} and st["events"]["open_scanned"] == 3 and st["events"]["published_by_result"] == {"BUY": 1, "SELL": 1, "NO_TRADE": 1}


def test_the_ledger_result_is_exactly_the_frozen_engines_classification(tmp_path):
    rows = [synth("AAA", "BUY", EV), synth("BBB", "NO_TRADE", EV)]
    PUB.publish_rows(rows, NOW, tmp_path, scan_id="s", mode="LIVE", scan_ok=True)
    for r, rec in zip(rows, PUB.read_ledger(tmp_path / PUB.LEDGER)["records"]):
        c = E20.classify(r, NOW)
        assert (rec["result_text"], rec["bull"], rec["bear"], rec["coverage_points"], rec["liquidity"], rec["reason_codes"]) == (c["result"], c["bull"], c["bear"], c["coverage_points"], c["liquidity"], c["reason_codes"])


def test_insufficient_evidence_stays_no_valid_prediction_never_a_neutral_view(tmp_path):
    r = row("NO_TRADE", ["INSUFFICIENT_COVERAGE", "ILLIQUID_OR_UNKNOWN"], 10, 5e6)
    r.update(symbol="LOW", evidence=[])
    PUB.publish_rows([r], NOW, tmp_path, scan_id="s", mode="LIVE", scan_ok=True)
    rec = PUB.read_ledger(tmp_path / PUB.LEDGER)["records"][0]
    assert rec["result"] == "NO_VALID_PREDICTION" and rec["evidence_latest"] is None


def test_an_unchanged_event_is_not_written_twice_and_new_evidence_makes_exactly_one_new_record(tmp_path):
    rows = [synth("AAA", "BUY", EV)]
    PUB.publish_rows(rows, NOW, tmp_path, scan_id="s1", mode="LIVE", scan_ok=True)
    st2 = PUB.publish_rows(rows, NOW + timedelta(hours=1), tmp_path, scan_id="s2", mode="LIVE", scan_ok=True)
    assert st2["events"]["unchanged"] == 1 and st2["events"]["published_new"] == 0 and len(PUB.read_ledger(tmp_path / PUB.LEDGER)["records"]) == 1
    newer = [synth("AAA", "BUY", EV + (("ohlc", "2026-11-02T20:40:00+00:00"),))]
    st3 = PUB.publish_rows(newer, NOW + timedelta(hours=2), tmp_path, scan_id="s3", mode="LIVE", scan_ok=True)
    led = PUB.read_ledger(tmp_path / PUB.LEDGER)["records"]
    assert st3["events"]["published_new"] == 1 and len(led) == 2 and led[1]["generated_at"] == (NOW + timedelta(hours=2)).isoformat() and led[0]["generated_at"] == NOW.isoformat()   # the first record is untouched


def test_a_dry_run_writes_status_but_no_prediction(tmp_path):
    st = PUB.publish_rows([synth("AAA", "BUY", EV)], NOW, tmp_path, scan_id="d", mode="DRY_RUN", scan_ok=True)
    assert st["mode"] == "DRY_RUN" and st["events"]["open_scanned"] == 1 and st["events"]["published_new"] == 0 and st["last_successful_scan_at"] is None
    assert not (tmp_path / PUB.LEDGER).exists() or PUB.read_ledger(tmp_path / PUB.LEDGER)["records"] == []


def test_a_closed_window_gets_a_missed_marker_once_and_never_a_prediction(tmp_path):
    closed = synth("OLD", "BUY", EV, plan={"window_start_utc": "2026-10-31T11:00:00+00:00", "cutoff_utc": "2026-11-01T11:00:00+00:00"})
    st = PUB.publish_rows([closed], NOW, tmp_path, scan_id="s1", mode="LIVE", scan_ok=True)
    PUB.publish_rows([closed], NOW + timedelta(hours=1), tmp_path, scan_id="s2", mode="LIVE", scan_ok=True)
    recs = PUB.read_ledger(tmp_path / PUB.LEDGER)["records"]
    assert [r["record_type"] for r in recs] == ["WINDOW_MISSED"] and recs[0]["predicted"] is False and "result" not in recs[0] and st["events"]["window_missed_noted"] == 1


def test_an_event_whose_window_has_not_opened_is_neither_scanned_nor_published(tmp_path):
    early = synth("NEXT", "BUY", EV, plan={"window_start_utc": "2026-11-03T11:00:00+00:00", "cutoff_utc": "2026-11-04T11:00:00+00:00"})
    st = PUB.publish_rows([early], NOW, tmp_path, scan_id="s", mode="LIVE", scan_ok=True)
    assert st["events"]["upcoming"] == 1 and st["events"]["open_scanned"] == 0 and st["events"]["published_new"] == 0


def test_evidence_stamped_after_the_scan_instant_is_refused_as_look_ahead(tmp_path):
    fut = synth("AAA", "BUY", (("ohlc", (NOW + timedelta(minutes=30)).isoformat()),))
    st = PUB.publish_rows([fut], NOW, tmp_path, scan_id="s", mode="LIVE", scan_ok=True)
    assert st["events"]["published_new"] == 0 and any("look-ahead" in k for k in st["events"]["skipped"])


def test_unconfirmed_or_unknown_session_events_are_skipped_with_a_reason(tmp_path):
    a = synth("AAA", "BUY", EV)
    a["blocked_by"] = ["VENDOR_DISAGREEMENT"]
    b = synth("BBB", "BUY", EV)
    b["kind"] = "UNKNOWN"
    st = PUB.publish_rows([a, b], NOW, tmp_path, scan_id="s", mode="LIVE", scan_ok=True)
    assert st["events"]["skipped"] == {"calendar not confirmed by two vendors": 1, "announcement session unknown": 1} and st["events"]["published_new"] == 0


def test_the_hash_chain_detects_an_edit_or_a_removed_line_and_publishing_then_refuses(tmp_path):
    PUB.publish_rows([synth("AAA", "BUY", EV), synth("BBB", "SELL", EV), synth("CCC", "NO_TRADE", EV)], NOW, tmp_path, scan_id="s", mode="LIVE", scan_ok=True)
    f = tmp_path / PUB.LEDGER
    good = f.read_text(encoding="utf-8")
    lines = good.splitlines()
    f.write_text("\n".join([lines[0], lines[1].replace('"SELL"', '"BUY"'), lines[2]]) + "\n", encoding="utf-8")
    assert PUB.read_ledger(f)["ok"] is False
    f.write_text("\n".join([lines[0], lines[2]]) + "\n", encoding="utf-8")
    assert PUB.read_ledger(f)["ok"] is False
    before = f.read_text(encoding="utf-8")
    with pytest.raises(PUB.LedgerError):
        PUB.publish_rows([synth("DDD", "BUY", EV)], NOW, tmp_path, scan_id="s2", mode="LIVE", scan_ok=True)
    assert f.read_text(encoding="utf-8") == before                                              # nothing was appended to a broken chain
    f.write_text(good, encoding="utf-8")
    assert PUB.read_ledger(f)["ok"] is True


def test_a_half_written_last_line_is_ignored_not_treated_as_damage(tmp_path):
    PUB.publish_rows([synth("AAA", "BUY", EV)], NOW, tmp_path, scan_id="s", mode="LIVE", scan_ok=True)
    with open(tmp_path / PUB.LEDGER, "a", encoding="utf-8") as fh:
        fh.write('{"schema": "smart_move.live_prediction.v1", "record_type": "PRED')           # a writer was interrupted
    led = PUB.read_ledger(tmp_path / PUB.LEDGER)
    assert led["ok"] and len(led["records"]) == 1


def test_last_successful_scan_is_kept_when_a_later_scan_fails(tmp_path):
    PUB.publish_rows([synth("AAA", "BUY", EV)], NOW, tmp_path, scan_id="s1", mode="LIVE", scan_ok=True)
    st = PUB.publish_rows([synth("AAA", "BUY", EV)], NOW + timedelta(hours=1), tmp_path, scan_id="s2", mode="LIVE", scan_ok=False, run={"halted": "CONSECUTIVE_FAILURES"})
    assert st["scan_ok"] is False and st["last_successful_scan_at"] == NOW.isoformat() and st["run"]["halted"] == "CONSECUTIVE_FAILURES"


# ---------------------------------------------------------------- calendar horizon
def test_the_calendar_horizon_covers_both_sessions_of_every_trading_day_through_day_plus_three():
    monday = datetime(2026, 11, 2, 20, 30, tzinfo=UTC)                                           # Monday 15:30 ET
    slots = J.horizon_calendar_slots(monday, 3)
    dates = {d.isoformat() for d, _ in slots}
    assert {"2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05"} <= dates and max(dates) >= "2026-11-05"
    assert ("2026-11-05", "premarket") in {(d.isoformat(), s) for d, s in slots} and ("2026-11-05", "afterhours") in {(d.isoformat(), s) for d, s in slots}
    assert set(J.useful_calendar_slots(monday)) <= set(slots)                                    # everything the old refresh fetched is still fetched
    assert len(slots) == len(set(slots)) and len(slots) <= 12
    friday = datetime(2026, 11, 6, 20, 30, tzinfo=UTC)
    assert max(d for d, _ in J.horizon_calendar_slots(friday, 3)).isoformat() >= "2026-11-09"       # over the weekend: Monday is still reached


# ---------------------------------------------------------------- runner integration
def test_the_runner_publishes_a_dry_run_status_with_zero_requests(tmp_path):
    import smart_move_collection_runner as RUNNER
    store = tmp_path / "store"
    store.mkdir()
    from test_engine20 import cal_rows
    (store / "cal_obs.jsonl").write_text("\n".join(json.dumps(r) for s in ("AAA", "BBB") for r in cal_rows(s)) + "\n", encoding="utf-8")
    pub = tmp_path / "live"
    code = RUNNER.main(["--store-dir", str(store), "--state-dir", str(tmp_path / "st"), "--profile", "engine20", "--select-date", "2026-11-04", "--select-session", "BMO", "--as-of", NOW.isoformat(),
                        "--publish-dir", str(pub), "--calendar-days-ahead", "3"])
    st = json.loads((pub / PUB.STATUS).read_text(encoding="utf-8"))
    assert code == 0 and st["mode"] == "DRY_RUN" and st["events"]["published_new"] == 0 and not (pub / PUB.LEDGER).exists() and st["last_successful_scan_at"] is None
    assert json.loads((tmp_path / "st" / "engine20_plan.json").read_text(encoding="utf-8"))["provider_requests"] == 0


def test_a_live_pass_with_a_mocked_provider_collects_scores_publishes_and_respects_the_ceiling(tmp_path, monkeypatch):
    import argparse
    import smart_move_collection_runner as RUNNER
    f = Stub()
    svc = make(tmp_path, f, authorized=150)
    svc.store.kill_file.parent.mkdir(parents=True, exist_ok=True)
    svc.store.kill_file.write_text("engaged", encoding="utf-8")
    a = argparse.Namespace(select_top=5, select_policy="deadline", select_symbols=None, select_date="2026-11-04", select_session="BMO", with_f7=False, ceiling=150, daily_limit=700, authorize_requests=150,
                           state_dir=str(tmp_path / "st"), publish_dir=str(tmp_path / "live"), calendar_days_ahead=None)
    monkeypatch.setattr(RUNNER.G, "needs_refresh", lambda *x, **k: False)
    assert RUNNER.engine20_profile(a, svc, NOW, live=True) == 0
    st = json.loads((tmp_path / "live" / PUB.STATUS).read_text(encoding="utf-8"))
    led = PUB.read_ledger(tmp_path / "live" / PUB.LEDGER)
    assert st["mode"] == "LIVE" and st["scan_ok"] and st["last_successful_scan_at"] and led["ok"] and len(led["records"]) == 5
    assert len(f.calls) == 1 + 10 + 3 and st["run"]["requests_attempted"] == 14 and st["limits"] == {"run_limit": 150, "daily_limit": 700, "concurrency": 1}
    assert all(r["generated_at"] == NOW.isoformat() and r["mode"] == "LIVE" for r in led["records"]) and svc.store.kill_file.exists()           # kill switch re-engaged afterwards
    n = len(f.calls)
    assert RUNNER.engine20_profile(a, svc, NOW, live=True) == 0 and len(f.calls) == n               # a second pass finds everything fresh: zero new requests, zero duplicate records
    assert len(PUB.read_ledger(tmp_path / "live" / PUB.LEDGER)["records"]) == 5


def test_the_approved_design_limits_are_honoured_by_the_runner_constants(monkeypatch):
    import smart_move_collection_runner as RUNNER
    monkeypatch.setenv("SMART_MOVE_RUN_MAX", "150")
    monkeypatch.setenv("SMART_MOVE_DAILY_MAX", "700")
    R2 = importlib.reload(RUNNER)
    try:
        assert (R2.PILOT_MAX, R2.DAILY_LIMIT_MAX) == (150, 700)
        assert R2.main(["--store-dir", ".", "--state-dir", ".", "--mode", "live", "--arm", "--authorize-requests", "151", "--profile", "engine20"]) == 6          # above the run limit
        assert R2.main(["--store-dir", ".", "--state-dir", ".", "--mode", "live", "--arm", "--authorize-requests", "150", "--daily-limit", "701", "--profile", "engine20"]) == 6   # above the daily limit
    finally:
        monkeypatch.delenv("SMART_MOVE_RUN_MAX")
        monkeypatch.delenv("SMART_MOVE_DAILY_MAX")
        importlib.reload(RUNNER)


def test_the_runner_reads_the_applications_env_file_when_told_and_a_ledger_failure_has_its_own_exit_code(tmp_path, monkeypatch):
    import argparse
    import smart_move_collection_runner as RUNNER
    monkeypatch.setenv("SMART_MOVE_ENV_FILE", str(tmp_path / "backend" / ".env"))
    R2 = importlib.reload(RUNNER)
    try:
        assert R2.ENV == tmp_path / "backend" / ".env"
        svc = make_svc(tmp_path / "svc", T_Stub())
        live = tmp_path / "live"
        live.mkdir()
        (live / PUB.LEDGER).write_text('{"not": "a ledger line"}\n', encoding="utf-8")                  # a damaged ledger
        svc.store.kill_file.parent.mkdir(parents=True, exist_ok=True)
        svc.store.kill_file.write_text("x", encoding="utf-8")
        monkeypatch.setattr(R2.G, "needs_refresh", lambda *a, **k: False)
        a = argparse.Namespace(select_top=3, select_policy="deadline", select_symbols=None, select_date="2026-11-04", select_session="BMO", with_f7=False, ceiling=150, daily_limit=700, authorize_requests=150,
                               state_dir=str(tmp_path / "st"), publish_dir=str(live), calendar_days_ahead=None)
        assert R2.engine20_profile(a, svc, NOW, live=True) == 8                                    # 8 = ledger; 7 is the shared-key gate, 6 the live gates, 4 the token configuration
        assert svc.store.kill_file.exists()                                                         # and the kill switch is back
    finally:
        monkeypatch.delenv("SMART_MOVE_ENV_FILE")
        importlib.reload(RUNNER)
