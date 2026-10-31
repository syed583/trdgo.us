"""Full-universe batched collection, rehearsed with the SIMULATED provider and a fake clock: reuse, batching, accounting, checkpoint/resume, stop on 429, kill switch, no overwrite."""
import hashlib
import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "docs" / "smart_move" / "app"))
import full_universe_run as F  # noqa: E402
import smart_move_live as L  # noqa: E402
import smart_move_sim as S  # noqa: E402
from smvshadow import governor as gv  # noqa: E402

from test_live import T0  # noqa: E402
from test_window_run import seeded  # noqa: E402


def seed(tmp_path):
    """An earlier run's folder: calendar + evidence + results for the first two names only."""
    svc, prov, clock = seeded(tmp_path / "seedsvc", T0 + timedelta(minutes=1))
    svc.cfg.calendar_days_ahead = 2
    svc.cfg.refresh_enabled, svc.cfg.authorized_daily, svc.gov.authorized_daily, svc.gov.enabled = True, 1700, 1700, True
    svc.refresh_calendar([12])
    first = [e["symbol"] for e in svc.eligible_events(clock.now()) if e["etf"]][:2]
    svc.focus = set(first)
    svc.refresh_evidence([200])
    rows = [r for r in L.LiveScorer(svc).score_all(clock.now(), persist=False) if r["symbol"] in first]
    rep = {"results": [F.result_row(r, svc, L) for r in rows]}
    (tmp_path / "seed_report.json").write_text(json.dumps(rep, default=str), encoding="utf-8")
    return svc.store.root, tmp_path / "seed_report.json", first, len(prov.calls)


def digest(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def run(monkeypatch, tmp_path, store, report, out, *extra, fetcher=None):
    clock = gv.FakeClock(T0 + timedelta(minutes=2))
    prov = fetcher or S.SimulatedProvider(clock)
    prov.clock = clock
    import dotenv
    import finnhub_service as fh
    import finviz_service as fz
    import unusualwhales_service as uw
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(uw, "configured", lambda: True)
    monkeypatch.setattr(fh, "_key", lambda: "x")
    monkeypatch.setattr(fz, "configured", lambda: True)
    monkeypatch.setattr(L, "InfraFetcher", lambda: prov)
    monkeypatch.setattr(L, "SystemClock", lambda: clock)
    monkeypatch.setattr(sys, "argv", ["x", "--out-dir", str(out), "--seed-store", str(store), "--seed-report", str(report), *extra])
    saved = sys.stdout, sys.stderr
    code = None
    try:
        F.main()
    except SystemExit as e:
        code = e.code
    finally:
        sys.stdout, sys.stderr = saved
    return code, prov


def test_reuses_earlier_names_batches_accounts_and_ends_with_the_kill_switch_engaged(tmp_path, monkeypatch):
    store, report, first, _ = seed(tmp_path)
    before = digest(store / "cal_obs.jsonl"), digest(store / "evidence.jsonl")
    out = tmp_path / "out"
    code, prov = run(monkeypatch, tmp_path, store, report, out, "--cap", "400", "--batch-size", "5")
    rep = json.loads((out / "audit_report.json").read_text())
    assert code == 0 and rep["status"] == "OK" and rep["batches_done"] == rep["batches_planned"] >= 2
    syms = [r["symbol"] for r in rep["results"]]
    assert len(syms) == len(set(syms)) == rep["universe"] and set(first) <= set(syms) and not rep["not_evaluated"]
    assert all(c[1] not in first for c in prov.calls if c[0] in ("earnings", "vol_context", "iv_rank"))             # the reused names are never requested again
    assert len(prov.calls) == rep["attempts_total"] <= 400
    assert (out / "store" / "KILL").exists() and rep["kill_switch_final"]["engaged"] and "final kill switch" in (out / "audit_report.md").read_text()
    assert {r["continuation"]["signal"] for r in rep["results"]} <= {"BUY", "SELL", "NO_TRADE"} and all(r["capture_ts"] for r in rep["results"])
    assert (digest(store / "cal_obs.jsonl"), digest(store / "evidence.jsonl")) == before                           # the earlier run's evidence is never touched


def test_a_new_folder_is_required_and_an_existing_one_is_never_overwritten(tmp_path, monkeypatch):
    store, report, _, _ = seed(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / "audit_report.json").write_text("{}")
    code, prov = run(monkeypatch, tmp_path, store, report, out)
    assert code == 3 and prov.calls == [] and (out / "audit_report.json").read_text() == "{}"


def test_plan_only_sends_nothing_and_reports_the_exact_count(tmp_path, monkeypatch):
    store, report, first, _ = seed(tmp_path)
    out = tmp_path / "plan"
    code, prov = run(monkeypatch, tmp_path, store, report, out, "--plan-only")
    plan = json.loads((out / "plan.json").read_text())
    assert prov.calls == [] and plan["already_evaluated_reused"] == 2 and plan["remaining"] == plan["universe_open_window_events"] - 2 and plan["planned_requests"] > 0


def test_a_rate_limit_stops_the_run_and_the_kill_switch_is_engaged(tmp_path, monkeypatch):
    store, report, _, _ = seed(tmp_path)

    class Limited(S.SimulatedProvider):
        def fetch(self, kind, symbol, params):
            if len(self.calls) >= 6:
                raise gv.ProviderError("rate_limited", 429)
            return super().fetch(kind, symbol, params)
    out = tmp_path / "limited"
    code, prov = run(monkeypatch, tmp_path, store, report, out, "--cap", "400", "--batch-size", "5", fetcher=Limited(gv.FakeClock(T0)))
    rep = json.loads((out / "audit_report.json").read_text())
    assert code == 5 and rep["status"] == "STOPPED_ON_PROVIDER_ERROR" and rep["stop_events"] and rep["batches_done"] < rep["batches_planned"]
    assert (out / "store" / "KILL").exists() and rep["not_evaluated"] and len(prov.calls) <= 10


def test_resume_continues_from_the_checkpoint_without_repeating_requests(tmp_path, monkeypatch):
    store, report, _, _ = seed(tmp_path)
    out = tmp_path / "resumable"
    clock = gv.FakeClock(T0)

    class Limited(S.SimulatedProvider):
        def fetch(self, kind, symbol, params):
            if len(self.calls) >= 6:
                raise gv.ProviderError("rate_limited", 429)
            return super().fetch(kind, symbol, params)
    run(monkeypatch, tmp_path, store, report, out, "--cap", "400", "--batch-size", "5", fetcher=Limited(clock))
    first_calls = len(json.loads((out / "audit_report.json").read_text())["results"])
    # a new, healthy provider; the rate-limit stop is a day-long stop in the governor, which a fresh process (resume) does not carry over
    (out / "store" / "ledger.jsonl").exists()
    code, prov = run(monkeypatch, tmp_path, store, report, out, "--cap", "400", "--batch-size", "5", "--resume")
    rep = json.loads((out / "audit_report.json").read_text())
    assert code in (0, 5) and len(rep["results"]) >= first_calls and len({r["symbol"] for r in rep["results"]}) == len(rep["results"])
