"""Regression tests for the stale-calendar defect: the collector must keep the earnings calendar inside the frozen 6 h limit at EVERY capture, also after a pause or a suspended PC."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "docs" / "smart_move" / "app"))
import smart_move_calendar_guard as G  # noqa: E402
import smart_move_live as L  # noqa: E402
from smvshadow import governor as gv  # noqa: E402

from test_live import T0  # noqa: E402
from test_v2_live_run import V2Sim, run, v1_dir  # noqa: E402

UTC = timezone.utc


class FakeStore:
    def __init__(self, observed):
        self.rows = [{"observed_at": t.isoformat()} for t in observed]

    def read(self, name):
        return self.rows if name == "cal_obs" else []


def test_age_is_measured_from_the_newest_observation_at_the_moment_of_the_check():
    now = datetime(2026, 11, 1, 20, tzinfo=UTC)
    st = FakeStore([now - timedelta(hours=9), now - timedelta(hours=2)])
    assert G.calendar_age_h(st, now) == 2.0 and not G.needs_refresh(st, now)
    assert G.needs_refresh(st, now + timedelta(hours=3.5))                           # the same store is stale 3.5 h later: nothing "remembers" an earlier OK
    assert G.needs_refresh(FakeStore([]), now) and G.calendar_age_h(FakeStore([]), now) is None


def test_gap_watcher_records_a_suspended_pc():
    w = G.GapWatcher(threshold_s=120)
    t = datetime(2026, 11, 1, 11, tzinfo=UTC)
    assert w.tick(t) is None and w.tick(t + timedelta(seconds=30)) is None
    gap = w.tick(t + timedelta(hours=2, minutes=36))
    assert gap == 2 * 3600 + 36 * 60 - 30 and len(w.gaps) == 1 and w.gaps[0]["seconds"] == round(gap)


class Svc:
    def __init__(self, store, now, refresh_makes_fresh=True, done=13):
        self.store, self.clock, self.calls = store, gv.FakeClock(now), 0
        self._fresh, self._done = refresh_makes_fresh, done

    def refresh_calendar(self, budget):
        self.calls += 1
        budget[0] -= self._done
        if self._fresh:
            self.store.rows.append({"observed_at": self.clock.now().isoformat()})
        return self._done


def test_ensure_fresh_is_a_no_op_when_fresh_refreshes_when_stale_and_refuses_to_pass_a_failed_refresh():
    now = datetime(2026, 11, 1, 20, tzinfo=UTC)
    fresh = Svc(FakeStore([now - timedelta(hours=1)]), now)
    assert G.ensure_fresh(fresh, budget_left=100)["refreshed"] is False and fresh.calls == 0
    stale = Svc(FakeStore([now - timedelta(hours=7)]), now)
    r = G.ensure_fresh(stale, budget_left=100)
    assert r["ok"] and r["refreshed"] and r["requests"] == 13 and r["age_before_h"] == 7.0 and r["age_after_h"] == 0.0
    nofix = Svc(FakeStore([now - timedelta(hours=7)]), now, refresh_makes_fresh=False)
    assert G.ensure_fresh(nofix, budget_left=100)["reason"] == "REFRESH_DID_NOT_FRESHEN_THE_CALENDAR"
    poor = Svc(FakeStore([now - timedelta(hours=7)]), now)
    p = G.ensure_fresh(poor, budget_left=5)
    assert not p["ok"] and poor.calls == 0 and p["reason"].startswith("BUDGET_TOO_LOW")                # never starts a refresh the cap cannot finish
    halted = Svc(FakeStore([now - timedelta(hours=7)]), now)
    assert G.ensure_fresh(halted, budget_left=100, stopped=lambda: True)["reason"] == "PROVIDER_STOP" and halted.calls == 0


class JumpSim(V2Sim):
    """After `after` V2 requests the clock jumps `jump` (a suspended PC). `calendar_dies`: the calendar endpoints then fail."""

    def __init__(self, clock, after, jump, calendar_dies=False):
        super().__init__(clock)
        self.after, self.jump, self.calendar_dies, self.jumped = after, jump, calendar_dies, False

    def fetch(self, kind, symbol, params):
        if self.jumped and self.calendar_dies and kind in ("uw_calendar", "finviz_calendar", "finnhub_calendar"):
            raise gv.ProviderError("transient", "DOWN")
        out = super().fetch(kind, symbol, params)
        if not self.jumped and len(self.v2_calls) >= self.after:
            self.jumped = True
            self.clock.t += self.jump
        return out


def ages_at_capture(out):
    st = L.LiveStore(out / "store")
    obs = sorted(datetime.fromisoformat(o["observed_at"]) for o in st.read("cal_obs"))
    worst = 0.0
    for r in st.read("v2_evidence"):
        if r["status"] != "OK":
            continue                                                                       # rejected/invalid records are never scored
        at = datetime.fromisoformat(r["retrieved_at"])
        prior = [t for t in obs if t <= at]
        worst = max(worst, (at - max(prior)).total_seconds() / 3600)
    return worst


def test_after_a_long_pause_the_collector_refreshes_the_calendar_before_the_next_capture(tmp_path, monkeypatch):
    v1 = v1_dir(tmp_path)
    clock = gv.FakeClock(T0 + timedelta(minutes=2))
    prov = JumpSim(clock, after=12, jump=timedelta(hours=7))
    prov.clock = clock
    out = tmp_path / "v2run"
    assert run(monkeypatch, "probe", out, v1, prov, clock, "--cap", "300") == 0
    run(monkeypatch, "full", out, v1, prov, clock, "--cap", "300", "--calendar-budget", "30")
    rep = json.loads((out / "report.json").read_text())
    assert prov.jumped and rep["calendar_refreshes"], "a 7 h jump must trigger a calendar refresh"
    assert rep["calendar_refreshes"][0]["age_before_h"] > 5 and rep["calendar_refreshes"][0]["ok"] and rep["calendar_refreshes"][0]["age_after_h"] < 0.5
    assert ages_at_capture(out) <= 6.0                                                   # EVERY scored V2 capture happened against a calendar inside the frozen limit
    st = L.LiveStore(out / "store")
    assert any(r["status"] == "REJECTED_STALE_CALENDAR" for r in st.read("v2_evidence"))   # the request that straddled the pause was rejected, then re-requested after the refresh
    assert rep["sleep_gaps"] and rep["sleep_gaps"][0]["seconds"] >= 7 * 3600 - 600
    assert (out / "store" / "KILL").exists()


def test_if_the_calendar_cannot_be_refreshed_no_further_capture_is_made(tmp_path, monkeypatch):
    v1 = v1_dir(tmp_path)
    clock = gv.FakeClock(T0 + timedelta(minutes=2))
    prov = JumpSim(clock, after=12, jump=timedelta(hours=7), calendar_dies=True)
    prov.clock = clock
    out = tmp_path / "v2run"
    assert run(monkeypatch, "probe", out, v1, prov, clock, "--cap", "300") == 0
    rc = run(monkeypatch, "full", out, v1, prov, clock, "--cap", "300", "--calendar-budget", "30")
    rep = json.loads((out / "report.json").read_text())
    assert rc == 5 and rep["status_full"] == "STOPPED_STALE_CALENDAR" and any(e["error"] == "STALE_CALENDAR" for e in rep["errors"])
    assert len(prov.v2_calls) <= 12 + 6                                                  # nothing after the pause except the stock that was already in flight
    assert (out / "store" / "KILL").exists()
    assert ages_at_capture(out) <= 6.0
