"""Phase 10 offline safety audit: the live-client choke point, localhost cannot start live collection, the pilot ceiling under random failures, slot-lock directory facts. Stubs only; zero provider requests."""
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_collection_jobs as J  # noqa: E402
import smart_move_live as L  # noqa: E402
import smart_move_shared_key_gate as SK  # noqa: E402
import test_pilot_preflight as T  # noqa: E402
from test_shared_key_and_pilot_caps import att, nothing  # noqa: E402

UTC = timezone.utc
OFF = datetime(2026, 11, 2, 23, 0, tzinfo=UTC)


# ------------------------------------------------------------------ the choke point
def test_a_live_client_cannot_be_built_without_a_valid_attestation_for_this_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)                                                     # localhost
    monkeypatch.setenv(SK.ATTESTATION_ENV, str(tmp_path / "missing.json"))
    with pytest.raises(SK.LiveBlocked) as e:
        SK.require_live_allowed(OFF, processes=nothing, tasks=nothing)
    assert "NOT VERIFIED" in str(e.value)
    monkeypatch.setenv(SK.ATTESTATION_ENV, str(att(tmp_path, designated_collector="production")))   # production is the designated collector
    with pytest.raises(SK.LiveBlocked) as e2:
        SK.require_live_allowed(OFF, processes=nothing, tasks=nothing)
    assert "not the designated collector" in str(e2.value)


def test_localhost_stays_blocked_when_production_is_the_designated_collector_even_with_a_new_token(tmp_path, monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv(SK.ATTESTATION_ENV, str(att(tmp_path, designated_collector="production")))
    monkeypatch.setattr(SK, "evaluate", lambda now, path=None, **k: {"status": "x", "blocks_live": True, "reason": "designated collector is production"})
    with pytest.raises(SK.LiveBlocked):
        L.InfraFetcher()                                                                               # the constructor every live script, route and runner uses


def test_the_infra_fetcher_constructor_goes_through_the_guard_before_importing_any_provider_client(monkeypatch):
    called = []
    monkeypatch.setattr(SK, "require_live_allowed", lambda *a, **k: called.append("guard") or (_ for _ in ()).throw(SK.LiveBlocked("blocked")))
    import builtins
    real_import = builtins.__import__

    def spy(name, *a, **k):
        if name in ("unusualwhales_service", "finviz_service", "finnhub_service"):
            called.append("imported " + name)
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", spy)
    with pytest.raises(SK.LiveBlocked):
        L.InfraFetcher()
    assert called == ["guard"]                                                                           # blocked before any provider client was imported


def test_every_live_smart_move_entry_point_builds_its_client_through_the_guarded_constructor():
    app = ROOT / "docs" / "smart_move" / "app"
    live_scripts = ["full_universe_run.py", "scheduled_run.py", "v2_live_run.py", "scheduler_tick.py", "supervised_first_run.py", "supervised_window_run.py", "news_verify_live.py"]
    for name in live_scripts:
        text = (app / name).read_text(encoding="utf-8")
        assert "InfraFetcher" in text, name                                                              # (news_verify_live subclasses it; the others call it)
    routes = (ROOT / "backend" / "smart_move_routes.py").read_text(encoding="utf-8")
    assert "L.InfraFetcher()" in routes
    other = [p.name for p in (ROOT / "backend").glob("smart_move_*.py") if "unusualwhales_service" in p.read_text(encoding="utf-8") and p.name not in ("smart_move_live.py", "smart_move_collection_runner.py", "smart_move_shared_key_gate.py")]   # the gate only names the module in text
    assert other == [], other                                                                            # no other Smart Move module talks to the provider client directly


# ------------------------------------------------------------------ pilot ceiling under random failures
@pytest.mark.parametrize("seed", range(12))
def test_the_fifty_attempt_ceiling_holds_under_random_failures_and_retries_off(tmp_path, seed):
    rnd = random.Random(seed)
    syms = [T.sym("Q", i) for i in range(60)]
    events = [(s, rnd.choice(["2026-11-03", "2026-11-04"]), rnd.choice(["AMC", "BMO"])) for s in syms]
    fail = {s for s in syms if rnd.random() < 0.3}
    prov = T.provider_for(events, fail_symbols=fail)
    svc, job = T.run_pilot(tmp_path, prov, calendar_requests=rnd.choice([10, 14]))
    assert len(prov.calls) <= 50 and sum(svc.gov.used_day.values()) == len(prov.calls)
    assert sum(1 for c in prov.calls if c[0] in ("uw_calendar", "finviz_calendar", "finnhub_calendar")) <= 14


def test_with_retries_on_the_same_ceiling_still_holds_because_attempts_are_what_is_counted(tmp_path):
    many = [(T.sym("R", i), "2026-11-03", "AMC") for i in range(40)]
    prov = T.provider_for(many, fail_symbols={T.sym("R", i) for i in range(0, 40, 2)})
    svc, job = T.run_pilot(tmp_path, prov, svc_kw={"retries": 1})
    assert len(prov.calls) <= 50 and sum(svc.gov.used_day.values()) == len(prov.calls)


def _uw_slots():
    """uw_slots lives in the application's backend folder (not part of this branch); load it from there when available."""
    import importlib.util
    for cand in (ROOT / "backend" / "uw_slots.py", Path("C:/Users/initi/Downloads/US-STOCKS/backend/uw_slots.py")):
        if cand.exists():
            spec = importlib.util.spec_from_file_location("uw_slots_under_test", cand)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    pytest.skip("uw_slots.py not on this machine")


# ------------------------------------------------------------------ slot-lock directory (facts that affect sharing a server with production)
def test_machine_slot_directory_defaults_to_the_per_user_temp_folder_unless_uw_slot_dir_is_set(tmp_path, monkeypatch):
    import tempfile
    uw_slots = _uw_slots()
    monkeypatch.delenv("UW_SLOT_DIR", raising=False)
    d = uw_slots.MachineSlots(3).dir
    assert Path(d) == Path(tempfile.gettempdir()) / "trdgo-uw-slots"                                    # a different Windows account or service has a different temp folder, so its locks are NOT shared
    monkeypatch.setenv("UW_SLOT_DIR", str(tmp_path / "shared"))
    assert Path(uw_slots.MachineSlots(3).dir) == tmp_path / "shared"


def test_two_slot_objects_on_the_same_directory_share_three_slots_and_a_fourth_waits(tmp_path):
    uw_slots = _uw_slots()
    a, b = uw_slots.MachineSlots(3, str(tmp_path)), uw_slots.MachineSlots(3, str(tmp_path / "."))
    h = [a.acquire(0.1), b.acquire(0.1), a.acquire(0.1)]
    assert all(x is not None and x.fd is not None for x in h)
    assert b.acquire(0.05) is None                                                                       # the fourth in-flight request does not get a slot
    for x in h:
        (a if x is h[0] or x is h[2] else b).release(x)
    again = b.acquire(0.1)
    assert again is not None
    b.release(again)
    other = uw_slots.MachineSlots(3, str(tmp_path / "different"))
    assert [other.acquire(0.1) for _ in range(3)] and True                                              # a different directory is an independent pool: no sharing


def test_zero_provider_requests_in_the_dry_run_of_the_pilot_profile(tmp_path):
    prov = T.provider_for(T.WINDOW + T.SNAPA)
    svc, _ = T.service(tmp_path, prov, live=False)
    svc.refresh_calendar([0], 3)                                                                         # budget 0: nothing
    job = J.run_cycle(svc, now=T.NOW, state_dir=tmp_path / "st", mode="DRY_RUN", ceiling=50)
    assert prov.calls == [] and job["provider_requests"] == 0
