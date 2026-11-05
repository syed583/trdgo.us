"""F7: shared (and own) evidence retrieved BEFORE an event's prediction window opened is rejected by the frozen scorer (RETRIEVED_BEFORE_WINDOW) but the planner counted it as fresh, so later-opening windows stayed PENDING.
Fix (opt-in `window_aware_evidence`, default OFF = installed behaviour): the planner also requests evidence whose stored copy predates the window. Everything here goes through the REAL scorer (`LiveScorer.score_all` + `classify`); stub provider, temp folders."""
import json
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "docs" / "smart_move" / "shadow_impl"))
import smart_move_collection_runner as RUNNER  # noqa: E402
import smart_move_engine20 as E20  # noqa: E402
import smart_move_live as L  # noqa: E402
import smart_move_universe as UNI  # noqa: E402
from smvshadow import governor as gv  # noqa: E402

UTC = timezone.utc
SAT22, SUN04, SUN13 = datetime(2026, 11, 7, 22, tzinfo=UTC), datetime(2026, 11, 8, 4, tzinfo=UTC), datetime(2026, 11, 8, 13, tzinfo=UTC)
EMPTY = {"version": 1, "slots": {}, "symbol_index": {}, "refreshes": 0}


class Prov(L.Fetcher):
    mode = "SIMULATION"

    def __init__(self, clock, fail=()):
        self.clock, self.calls, self.fail = clock, [], set(fail)

    def fetch(self, kind, symbol, params):
        self.calls.append((kind, symbol))
        if symbol in self.fail:
            raise gv.ProviderError("transient", "boom")
        if kind == "ohlc":
            last, rows, d, i = L.expected_session(self.clock.now()), [], None, 0
            d = last
            while len(rows) < 800:
                if d.weekday() < 5:
                    c = 100 + 6 * math.sin((i + sum(map(ord, symbol))) / 8.0) + 0.01 * (800 - i)
                    rows.append({"date": d.isoformat(), "open": "%.2f" % c, "high": "%.2f" % (c + 1), "low": "%.2f" % (c - 1), "close": "%.2f" % c, "volume": 1_000_000, "total_volume": 1_000_000, "market_time": "r"})
                    i += 1
                d -= timedelta(days=1)
            return json.dumps({"data": sorted(rows, key=lambda r: r["date"])}).encode()
        out, d = [], date(2026, 7, 29)
        for q in range(8):
            while d.weekday() >= 4:
                d -= timedelta(days=1)
            out.append({"report_date": d.isoformat(), "report_time": "premarket", "ending_fiscal_quarter": (d - timedelta(days=30)).isoformat(), "post_earnings_move_1d": "0.02"})
            d -= timedelta(days=91)
        return json.dumps({"data": out}).encode()


EVENTS = {"MON1": ("2026-11-09", "AMC", "Technology"), "MON2": ("2026-11-09", "AMC", "Technology"),
          "TUE1": ("2026-11-10", "BMO", "Energy"), "TUE2": ("2026-11-10", "BMO", "Technology")}


def world(tmp, aware, retries=0, fail=()):
    clock = gv.FakeClock(SAT22)
    prov = Prov(clock, fail)
    cfg = L.LiveConfig(mode="SIMULATION", store_dir=tmp / "store", refresh_enabled=True, authorized_daily=100, spacing_s=0.0, max_retries=retries, window_aware_evidence=aware)
    return L.RefreshService(cfg, prov, clock), prov, clock


def run_at(svc, prov, clock, now, tmp, budget=50, events=EVENTS):
    clock.t = now
    for s, (d, k, sec) in events.items():
        base = {"symbol": s, "clock": None, "mode": "LIVE", "observed_at": (now - timedelta(hours=1)).isoformat(), "request_id": "r", "company": s, "sector": sec, "marketcap": "5000000000", "date": d}
        svc.store.append("cal_obs", dict(base, source="UW_CALENDAR", label="premarket" if k == "BMO" else "afterhours", vendor_flag="company", raw_sha256="u" + s))
        svc.store.append("cal_obs", dict(base, source="FINNHUB", label=k.lower(), vendor_flag=None, raw_sha256="f" + s))
    rows = L.LiveScorer(svc).score_all(now)
    pending = {x["symbol"] for x in svc.plan_evidence(now) if x.get("symbol") and x["kind"] in ("ohlc", "earnings")}
    sel = E20.select_top(rows, {s: 5e9 for s in events}, now, n=20, policy="deadline", pending=pending)
    plan = E20.plan_tasks(svc, sel, now, budget=budget)
    n0 = len(prov.calls)
    res = E20.run(svc, plan["funded"], now=now, ceiling=budget, state_dir=tmp / "st", max_consecutive_failures=99)
    after = {r["symbol"]: E20.classify(r, now) for r in L.LiveScorer(svc).score_all(now) if r["symbol"] in events}
    return {"plan": plan, "res": res, "calls": prov.calls[n0:], "cls": after}


def valid(c):
    return c["result"] in ("BUY", "SELL", "NO TRADE (genuine)")


# ------------------------------------------------------------------ the defect as installed (flag OFF)
def test_the_installed_planner_never_re_requests_shared_evidence_and_the_later_window_stays_pending(tmp_path):
    svc, prov, clock = world(tmp_path, aware=False)
    r1 = run_at(svc, prov, clock, SAT22, tmp_path)
    assert valid(r1["cls"]["MON1"]) and valid(r1["cls"]["MON2"])
    r2 = run_at(svc, prov, clock, SUN13, tmp_path)
    assert ("ohlc", "SPY") not in r2["calls"] and ("ohlc", "XLK") not in r2["calls"]                      # the planner calls the Saturday copies fresh
    for s in ("TUE1", "TUE2"):
        assert r2["cls"][s]["result"] == "PENDING FINAL REFRESH" and "RETRIEVED_BEFORE_WINDOW" in r2["cls"][s]["reason"]
    r3 = run_at(svc, prov, clock, SUN13 + timedelta(hours=2), tmp_path)
    assert r3["calls"] == [] and r3["cls"]["TUE1"]["result"] == "PENDING FINAL REFRESH"                   # and it never heals on its own


# ------------------------------------------------------------------ the fix (flag ON)
def test_window_aware_planning_re_requests_only_what_predates_the_new_window_and_every_open_event_becomes_valid(tmp_path):
    svc, prov, clock = world(tmp_path, aware=True)
    r1 = run_at(svc, prov, clock, SAT22, tmp_path)
    assert {c for c in r1["calls"] if c[0] == "ohlc" and c[1] in ("SPY", "XLK")} == {("ohlc", "SPY"), ("ohlc", "XLK")}
    r2 = run_at(svc, prov, clock, SUN13, tmp_path)
    shared = {c for c in r2["calls"] if c[1] in ("SPY", "XLK", "XLE")}
    assert shared == {("ohlc", "SPY"), ("ohlc", "XLE"), ("ohlc", "XLK")}                                   # SPY (every event), the new Energy sector, and Technology again because TUE2 (Technology) opened after the Saturday copy was taken
    assert ("ohlc", "MON1") not in r2["calls"] and ("earnings", "MON1") not in r2["calls"]                  # Monday's own evidence is reused
    for s in EVENTS:
        assert valid(r2["cls"][s]), (s, r2["cls"][s]["reason"])


def test_a_sector_etf_is_reused_only_while_it_postdates_every_window_that_needs_it(tmp_path):
    svc, prov, clock = world(tmp_path, aware=True)
    run_at(svc, prov, clock, SAT22, tmp_path)
    r2 = run_at(svc, prov, clock, SUN13, tmp_path)
    assert ("ohlc", "XLK") in r2["calls"]                                                                  # TUE2 is Technology and its window (Sun 11:00Z) opened after the Saturday copy: re-requested
    r3 = run_at(svc, prov, clock, SUN13 + timedelta(hours=1), tmp_path)
    assert r3["calls"] == []                                                                               # nothing predates any window now: no duplicate request


def test_weekend_boundary_nothing_new_opens_between_saturday_night_and_early_sunday_so_nothing_is_re_requested(tmp_path):
    svc, prov, clock = world(tmp_path, aware=True)
    run_at(svc, prov, clock, SAT22, tmp_path)
    quiet = run_at(svc, prov, clock, SUN04, tmp_path)                                                      # 23:00 ET Saturday: the Tuesday window (Sun 11:00Z) has not opened
    assert quiet["calls"] == [] and all(valid(quiet["cls"][s]) for s in ("MON1", "MON2"))
    loud = run_at(svc, prov, clock, SUN13, tmp_path)
    assert ("ohlc", "SPY") in loud["calls"]                                                                # the Friday session is still the latest completed one; only the window changed


def test_evidence_retrieved_exactly_at_the_window_start_counts_like_the_scorer_says(tmp_path):
    svc, prov, clock = world(tmp_path, aware=True)
    run_at(svc, prov, clock, SAT22, tmp_path)
    start = datetime(2026, 11, 8, 11, 0, tzinfo=UTC)                                                       # Tuesday BMO window start
    r = run_at(svc, prov, clock, start, tmp_path)
    assert ("ohlc", "SPY") in r["calls"] and all(valid(r["cls"][s]) for s in EVENTS)                       # retrieved AT the start: not 'before'
    assert run_at(svc, prov, clock, start + timedelta(minutes=1), tmp_path)["calls"] == []


# ------------------------------------------------------------------ budget: funded first, never claimed when unfunded
def test_shared_requests_are_in_the_plan_and_budget_before_execution(tmp_path):
    svc, prov, clock = world(tmp_path, aware=True)
    run_at(svc, prov, clock, SAT22, tmp_path)
    clock.t = SUN13
    for s, (d, k, sec) in EVENTS.items():
        base = {"symbol": s, "clock": None, "mode": "LIVE", "observed_at": (SUN13 - timedelta(hours=1)).isoformat(), "request_id": "r", "company": s, "sector": sec, "marketcap": "5000000000", "date": d}
        svc.store.append("cal_obs", dict(base, source="UW_CALENDAR", label="premarket" if k == "BMO" else "afterhours", vendor_flag="company", raw_sha256="u" + s))
        svc.store.append("cal_obs", dict(base, source="FINNHUB", label=k.lower(), vendor_flag=None, raw_sha256="f" + s))
    n0 = len(prov.calls)
    plan = E20.plan_tasks(svc, E20.select_top(L.LiveScorer(svc).score_all(SUN13), {s: 5e9 for s in EVENTS}, SUN13, n=20, policy="deadline"), SUN13, budget=50)
    assert len(prov.calls) == n0                                                                           # planning sends nothing
    assert plan["funded"][0]["kind"] == "ohlc" and plan["funded"][0]["symbol"] == "SPY"                    # funded first, inside the budget, before anything is sent
    assert plan["attempts_planned"] == len(plan["funded"]) <= 50 and {t["symbol"] for t in plan["funded"] if t["kind"] == "ohlc"} >= {"SPY", "XLE", "XLK", "TUE1", "TUE2"}


def test_an_unfunded_shared_request_leaves_the_event_pending_and_the_universe_says_why(tmp_path):
    svc, prov, clock = world(tmp_path, aware=True)
    run_at(svc, prov, clock, SAT22, tmp_path)
    r = run_at(svc, prov, clock, SUN13, tmp_path, budget=0)                                                # nothing can be funded
    assert r["calls"] == [] and all(r["cls"][s]["result"] == "PENDING FINAL REFRESH" and not valid(r["cls"][s]) for s in ("TUE1", "TUE2"))
    uni = UNI.build(svc, SUN13, horizon_days=30, slot_state=EMPTY, funded_symbols=set(), selected_symbols=set())
    row = next(x for x in uni["rows"] if x["symbol"] == "TUE1")
    assert row["evidence"]["F8_market"] == "BEFORE_WINDOW" and row["collection_status"] == "DEFERRED" and "retrieved before this event's prediction window opened" in row["deferred_reason"]
    small = run_at(svc, prov, clock, SUN13, tmp_path, budget=1)                                            # SPY only: still not enough for the Tuesday events' own evidence
    assert small["calls"] == [("ohlc", "SPY")] and all(not valid(small["cls"][s]) for s in ("TUE1", "TUE2"))


def test_retries_and_failures_on_the_shared_request_stay_inside_the_ceiling_and_never_produce_a_valid_claim(tmp_path):
    svc, prov, clock = world(tmp_path, aware=True, retries=2, fail=("SPY",))
    svc.cfg.window_aware_evidence = True
    run_at(svc, prov, clock, SAT22, tmp_path, budget=50)                                                   # the Saturday SPY request fails too: Monday cannot be valid either
    r2 = run_at(svc, prov, clock, SUN13, tmp_path, budget=10)
    assert E20.attempts_of(svc) <= 100 and r2["res"]["attempts"] <= 10 and all(not valid(r2["cls"][s]) for s in EVENTS if EVENTS[s][1] == "BMO")


# ------------------------------------------------------------------ the option reaches the runner; defaults unchanged
def test_the_runner_option_and_the_horizon_option_turn_it_on_and_the_default_is_off(tmp_path):
    assert L.LiveConfig().window_aware_evidence is False
    assert RUNNER.build_service(tmp_path / "a", live=False, authorized=0).cfg.window_aware_evidence is False
    assert RUNNER.build_service(tmp_path / "b", live=False, authorized=0, window_aware=True).cfg.window_aware_evidence is True
    store = tmp_path / "store"
    store.mkdir()
    (store / "cal_obs.jsonl").write_text("", encoding="utf-8")
    base = ["--store-dir", str(store), "--state-dir", str(tmp_path / "st"), "--profile", "engine20", "--as-of", SAT22.isoformat()]
    assert RUNNER.main(base + ["--window-aware-evidence"]) == 0
    assert RUNNER.main(base + ["--calendar-horizon-days", "30"]) == 0


# ------------------------------------------------------------------ the read-only history check finds exactly the defect, and nothing when the fix is on
def _history(tmp_path, aware):
    import importlib.util
    spec = importlib.util.spec_from_file_location("f7_history_check", ROOT / "docs" / "smart_move" / "p1" / "f7_history_check.py")
    H = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(H)
    svc, prov, clock = world(tmp_path, aware=aware)
    run_at(svc, prov, clock, SAT22, tmp_path)
    run_at(svc, prov, clock, SUN13, tmp_path)
    before = {p.name: p.read_bytes() for p in (tmp_path / "store").iterdir() if p.is_file()}
    out = H.check(tmp_path / "store")
    assert {p.name: p.read_bytes() for p in (tmp_path / "store").iterdir() if p.is_file()} == before                       # read only
    return out


def test_the_history_check_flags_the_tuesday_events_without_the_fix_and_none_with_it(tmp_path):
    bad = _history(tmp_path / "off", aware=False)
    assert bad["events_checked"] == 4 and bad["events_with_shared_before_window"] == 2 and bad["events_with_own_before_window"] == 0
    assert {r["symbol"] for r in bad["affected"]} == {"TUE1", "TUE2"} and all(r["SPY"] == "BEFORE_WINDOW" for r in bad["affected"])
    good = _history(tmp_path / "on", aware=True)
    assert good["events_checked"] == 4 and good["events_with_any_before_window"] == 0
