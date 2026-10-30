"""The shared governor next to the warmer, simulated with a fake clock (small event count so the suite stays fast)."""
import simulate_budget as sim


def run(tmp_path, **kw):
    kw.setdefault("n_events", 30)
    kw.setdefault("full_cap", 4)
    kw.setdefault("core_cap", 220)
    return sim.simulate(tmp_path, **kw)


def test_warmer_at_its_whole_live_cap_leaves_nothing_in_live_hours_but_work_finishes_overnight(tmp_path):
    r = run(tmp_path, warmer_live=406, warmer_off=1000, authorized=1700)
    assert r["max_live_hour_requests"] == 0 and r["shared_hourly_limits_respected"] and r["daily_authorization_respected"]
    assert r["processed"] == r["events_scheduled"] and r["missed_deadline"] == 0


def test_warmer_at_its_reservation_leaves_the_provisional_100_per_live_hour(tmp_path):
    r = run(tmp_path, warmer_live=300, warmer_off=1000, authorized=1700)
    assert 0 < r["max_live_hour_requests"] <= 100 and r["shared_hourly_limits_respected"] and r["missed_deadline"] == 0


def test_core_requests_are_protected_when_the_daily_authorization_is_short(tmp_path):
    full = run(tmp_path / "a", warmer_live=406, warmer_off=1000, authorized=1700)
    tight = run(tmp_path / "b", warmer_live=406, warmer_off=1000, authorized=140)
    assert tight["core_endpoint_calls"] >= full["core_endpoint_calls"] - 2 * 4      # core calls survive
    assert tight["full_extra_calls"] < full["full_extra_calls"] and tight["daily_authorization_respected"]


def test_zero_authorization_sends_nothing_and_logs_every_miss(tmp_path):
    r = run(tmp_path, warmer_live=406, warmer_off=1000, authorized=0)
    assert r["requests_total"] == 0 and r["missed_deadline"] == r["events_scheduled"] and r["ledger_counts"]["MISSED_DEADLINE"] == r["events_scheduled"]
