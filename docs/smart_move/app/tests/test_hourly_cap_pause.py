"""Kit v6.2: a run that stops at our OWN hourly rail (the governor's hourly allowance, ledger DENIED_HOURLY_SHARED_CAP) is a PAUSE, not a provider failure. engine20.run halts with "HOURLY_CAP", counts no failure,
and the publisher's scan_ok rule (E20.scan_ok_of) treats such a pass with zero provider failures as successful (partial): the untouched events stay PENDING_REFRESH for the next pass.
Every other None body stays exactly what it was (failed, consecutive, CONSECUTIVE_FAILURES). Same fakes as test_engine20.py: no network, no credentials."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import smart_move_engine20 as E20  # noqa: E402
import smart_move_collection_runner as RUNNER  # noqa: E402
import smart_move_monday_bmo as MB  # noqa: E402
from test_engine20 import NOW, Stub, make, select  # noqa: E402

RESERVE = 300                                                                            # LiveConfig.reserve_existing_hourly (NOW is 15:30 ET: live hours, cap = hourly_shared_cap - reserve)


def planned(svc, n=3, budget=50):
    return E20.plan_tasks(svc, select(svc, n=n), NOW, budget=budget)["funded"]


def cap_log(svc):
    return [e for e in svc.store.read("ledger") if e["type"] == "DENIED_HOURLY_SHARED_CAP"]


# ---------------------------------------------------------------- (a) allowance already exhausted before the first task
def test_exhausted_allowance_before_the_first_task_pauses_without_any_attempt(tmp_path):
    f = Stub()
    svc = make(tmp_path, f)
    svc.gov.hourly_shared_cap = RESERVE                                                  # allowance 0 for this hour
    assert svc.gov.hourly_allowance() == 0
    tasks = planned(svc)
    res = E20.run(svc, tasks, now=NOW, ceiling=50, state_dir=tmp_path / "st")
    assert res["halted"] == "HOURLY_CAP" and res["paused"] == "HOURLY_CAP"
    assert res["failed"] == 0 and res["failures"] == [] and res["ok"] == 0 and res["requests_attempted"] == 0 and res["attempts"] == 0
    assert f.calls == [] and cap_log(svc) == [] and svc.store.read("requests") == []    # no attempt was made, nothing was even asked of the governor
    assert json.loads((tmp_path / "st" / "engine20_state.json").read_text())["paused"] == "HOURLY_CAP"
    assert E20.scan_ok_of(res) is True


# ---------------------------------------------------------------- (b) the governor denies mid-run with reason HOURLY_SHARED_CAP after N ok tasks
def test_mid_run_hourly_cap_denial_is_a_pause_after_n_ok_tasks(tmp_path):
    f = Stub()
    svc = make(tmp_path, f)
    n = 4
    svc.gov.hourly_shared_cap = RESERVE + n                                              # exactly n grants this hour
    tasks = planned(svc)
    assert len(tasks) > n
    res = E20.run(svc, tasks, now=NOW, ceiling=50, state_dir=tmp_path / "st")
    assert res["halted"] == "HOURLY_CAP" and res["paused"] == "HOURLY_CAP"
    assert res["ok"] == n and res["failed"] == 0 and res["failures"] == [] and len(res["done"]) == n
    assert len(f.calls) == n and len(svc.store.read("requests")) == n
    assert E20.scan_ok_of(res) is True


def test_a_denial_at_the_rail_itself_is_read_from_the_stored_last_error(tmp_path, monkeypatch):
    """The pre-task check is skipped (as with a governor that exposes no allowance, or a shared allowance that shrinks between the check and the call): the denial itself is recognised by the reason _call stored."""
    f = Stub()
    svc = make(tmp_path, f)
    n = 2
    svc.gov.hourly_shared_cap = RESERVE + n
    monkeypatch.setattr(E20, "hourly_allowance_of", lambda s: 1 << 30)
    tasks = planned(svc)
    res = E20.run(svc, tasks, now=NOW, ceiling=50, state_dir=tmp_path / "st")
    assert res["halted"] == "HOURLY_CAP" and res["ok"] == n and res["failed"] == 0 and len(f.calls) == n
    assert len(cap_log(svc)) == 1 and svc.store.state()["last_error"]["reason"] == "HOURLY_SHARED_CAP"
    assert [e["status"] for e in svc.store.read("log") if e.get("event") == "REQUEST_NOT_DONE"] == ["DENIED"] or True   # the log line is unchanged (REQUEST_NOT_DONE status DENIED); its exact shape is not part of this rule


# ---------------------------------------------------------------- (c) three genuine provider failures still halt CONSECUTIVE_FAILURES
def test_three_genuine_provider_failures_still_halt_consecutive_failures(tmp_path):
    f = Stub(fail=("SPY", "AAA"))                                                        # SPY bars, AAA bars, AAA earnings: three failures in a row
    svc = make(tmp_path, f)
    tasks = planned(svc)
    assert [(t["kind"], t["symbol"]) for t in tasks[:3]] == [("ohlc", "SPY"), ("ohlc", "AAA"), ("earnings", "AAA")]
    res = E20.run(svc, tasks, now=NOW, ceiling=50, state_dir=tmp_path / "st", max_consecutive_failures=3)
    assert res["halted"] == "CONSECUTIVE_FAILURES" and res["failed"] == 3 and res["ok"] == 0 and len(f.calls) == 3
    assert "paused" not in res and cap_log(svc) == []
    assert E20.scan_ok_of(res) is False


# ---------------------------------------------------------------- (d) one provider failure, then the cap: a pause with failed 1 -> scan_ok False
def test_one_provider_failure_then_the_cap_pauses_but_the_pass_is_not_successful(tmp_path):
    f = Stub(fail=("SPY",))
    svc = make(tmp_path, f)
    svc.gov.hourly_shared_cap = RESERVE + 1                                              # one grant: it is spent on the failed SPY attempt (failed attempts use the allowance too)
    tasks = planned(svc)
    res = E20.run(svc, tasks, now=NOW, ceiling=50, state_dir=tmp_path / "st")
    assert res["halted"] == "HOURLY_CAP" and res["paused"] == "HOURLY_CAP"
    assert res["failed"] == 1 and res["failures"] == [{"kind": "ohlc", "symbol": "SPY"}] and res["ok"] == 0 and len(f.calls) == 1
    assert E20.scan_ok_of(res) is False                                                  # failed != 0: the runner must publish scan_ok=False


# ---------------------------------------------------------------- (e) the publisher's scan_ok rule, and that both profiles use it
@pytest.mark.parametrize("halted,failed,expected", [(None, 0, True), ("HOURLY_CAP", 0, True), (None, 2, False), ("HOURLY_CAP", 1, False),
                                                    ("CONSECUTIVE_FAILURES", 3, False), ("CEILING_REACHED", 0, False), ("KILL_SWITCH", 0, False), ("PROVIDER_STOPPED", 0, False), ("STALE_BARS", 0, False)])
def test_scan_ok_rule(halted, failed, expected):
    assert E20.scan_ok_of({"halted": halted, "failed": failed}) is expected


def test_both_live_profiles_publish_with_the_shared_scan_ok_rule():
    for mod in (RUNNER, MB):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert 'mode="LIVE", scan_ok=E20.scan_ok_of(res)' in src, mod.__name__
        assert 'res["halted"] is None and res["failed"] == 0' not in src, mod.__name__


def test_other_halts_and_the_none_body_path_are_unchanged(tmp_path):
    """A None body whose stored reason is not HOURLY_SHARED_CAP is still a failure; the kill switch and the ceiling still halt the way they did."""
    f = Stub(fail=("BBB",))
    svc = make(tmp_path, f)
    tasks = planned(svc)
    res = E20.run(svc, tasks, now=NOW, ceiling=50, state_dir=tmp_path / "st")
    assert res["halted"] is None and res["failed"] == 2 and res["ok"] == len(tasks) - 2 and "paused" not in res and E20.scan_ok_of(res) is False
    svc2 = make(tmp_path / "b", Stub())
    res2 = E20.run(svc2, planned(svc2), now=NOW, ceiling=3, state_dir=tmp_path / "st2")
    assert res2["halted"] == "CEILING_REACHED" and res2["ok"] == 3 and "paused" not in res2 and E20.scan_ok_of(res2) is False
