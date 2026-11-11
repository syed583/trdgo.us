"""End-to-end LIVE pass through the REAL runner entry point and the REAL provider adapter, with the provider itself mocked.

Proves (handoff P0-03) that a planned evidence request travels main() -> live gates -> plan -> E20.run -> AttemptRecorder
-> InfraFetcher -> unusualwhales_service.get and that the response is validated, stored, scored and published. Every
earlier test stopped short of InfraFetcher (it is never built in tests by design); this one builds it against stub modules.
No network: the three provider modules are replaced in sys.modules before the runner imports them.
"""
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "docs" / "smart_move" / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import smart_move_calendar_tiers as CT  # noqa: E402
import smart_move_collection_runner as RUNNER  # noqa: E402
import smart_move_live as L  # noqa: E402
import smart_move_live_publisher as PUB  # noqa: E402
import smart_move_shared_key_gate as SK  # noqa: E402
from smvshadow import governor as gv  # noqa: E402
from test_engine20 import NOW, bars_body, make  # noqa: E402

UTC = timezone.utc


class _UW:
    """Stand-in for unusualwhales_service: the only provider the evidence requests reach."""
    BG_MAX_RETRIES = 3

    def __init__(self):
        self.calls, self.background = [], None

    def configured(self):
        return True

    def set_background(self, flag):
        self.background = flag

    def get(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if path.endswith("/ohlc/1d"):
            return {"status": "OK", "data": bars_body()["data"]}
        if path.startswith("/api/earnings/"):
            return {"status": "OK", "data": [{"report_date": "2026-08-01", "report_time": "premarket", "ending_fiscal_quarter": "2026-06-30", "post_earnings_move_1d": "0.02"}]}
        return {"status": "ERROR", "data": None}


@pytest.fixture
def live_env(tmp_path, monkeypatch):
    uw = _UW()
    monkeypatch.setitem(sys.modules, "unusualwhales_service", uw)
    monkeypatch.setitem(sys.modules, "finviz_service", types.SimpleNamespace(_get=lambda *a, **k: (_ for _ in ()).throw(AssertionError("finviz must not be called"))))
    monkeypatch.setitem(sys.modules, "finnhub_service", types.SimpleNamespace(_get=lambda *a, **k: (_ for _ in ()).throw(AssertionError("finnhub must not be called"))))
    monkeypatch.setattr(SK, "require_live_allowed", lambda: None)
    monkeypatch.setattr(RUNNER, "persistent_live_flag", lambda: False)
    monkeypatch.setattr(RUNNER.SK, "evaluate", lambda now, path=None, **k: {"blocks_live": False, "status": "OWNER_ATTESTED", "reason": "test"})
    monkeypatch.setattr(RUNNER.SC, "inspect", lambda env, old_fingerprint=None: {"source": "ENV", "shape_problems": [], "rotated_from_old": None, "notes": []})
    env_file = tmp_path / "empty.env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(RUNNER, "ENV", env_file)
    monkeypatch.setenv("SMART_MOVE_COLLECTION_LIVE", "1")
    monkeypatch.delenv("UW_MAX_INFLIGHT", raising=False)

    class _FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz else NOW.replace(tzinfo=None)
    monkeypatch.setattr(RUNNER, "datetime", _FakeDT)
    monkeypatch.setattr(L, "SystemClock", lambda: gv.FakeClock(NOW))
    # the store: five vendor-confirmed BMO events on 2026-11-04 (window open at NOW), observed an hour ago; kill switch engaged
    svc = make(tmp_path)
    svc.store.kill_file.parent.mkdir(parents=True, exist_ok=True)
    svc.store.kill_file.write_text("engaged", encoding="utf-8")
    # every calendar slot of the 30-day horizon freshly refreshed: this pass needs no calendar request, only evidence
    st = CT.record_refresh({"version": 1, "slots": {}, "symbol_index": {}, "refreshes": 0},
                           [{"date": d.isoformat(), "side": side, "pages_fetched": 1, "symbols": [], "complete": True, "reason": None} for d, side in CT.horizon_slots(NOW, 30)], NOW)
    cal_dir = tmp_path / "calstate"
    cal_dir.mkdir()
    (cal_dir / CT.STATE_FILE).write_text(json.dumps(st), encoding="utf-8")
    return {"uw": uw, "store": tmp_path / "store", "state": tmp_path / "state", "pub": tmp_path / "pub", "cal": cal_dir, "kill": svc.store.kill_file}


def _argv(e, authorize="50", daily="100", extra=()):
    return ["--store-dir", str(e["store"]), "--state-dir", str(e["state"]), "--publish-dir", str(e["pub"]), "--calendar-state-dir", str(e["cal"]),
            "--mode", "live", "--arm", "--authorize-requests", authorize, "--daily-limit", daily, "--profile", "engine20",
            "--select-policy", "deadline_priority", "--select-top", "5", "--calendar-horizon-days", "30"] + list(extra)


def test_main_live_with_a_mocked_provider_sends_persists_and_publishes(live_env):
    e = live_env
    rc = RUNNER.main(_argv(e))
    assert rc == 0
    # 1. every provider attempt went through the real adapter and was recorded, with nothing secret in the record
    att = [json.loads(l) for l in (e["state"] / "attempts.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert att and len(att) == len(e["uw"].calls)
    raw = (e["state"] / "attempts.jsonl").read_text(encoding="utf-8").lower()
    assert "authorization" not in raw and "token" not in raw
    assert e["uw"].background is False                                              # set_background(True) ... finally set_background(False)
    # 2. the provider's answers are in the store as evidence with a readable payload
    ev = [json.loads(l) for l in (e["store"] / "evidence.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    kinds = {(r["kind"], r["symbol"]) for r in ev}
    assert ("ohlc", "SPY") in kinds and ("ohlc", "AAA") in kinds and ("earnings", "AAA") in kinds
    req = [l for l in (e["store"] / "requests.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(req) == len(e["uw"].calls)
    led_store = (e["store"] / "ledger.jsonl").read_text(encoding="utf-8") if (e["store"] / "ledger.jsonl").exists() else ""
    assert "DENIED_" not in led_store
    # 3. the pass was scored and published from that evidence
    st = json.loads((e["pub"] / PUB.STATUS).read_text(encoding="utf-8"))
    led = PUB.read_ledger(e["pub"] / PUB.LEDGER)
    assert st["mode"] == "LIVE" and st["scan_ok"] is True and st["last_successful_scan_at"]
    assert st["run"]["requests_attempted"] == len(att) and st["run"]["requests_attempted"] > 0
    assert led["ok"] and len(led["records"]) == 5 and all(r["mode"] == "LIVE" for r in led["records"])
    plan = json.loads((e["state"] / "engine20_plan.json").read_text(encoding="utf-8")) if (e["state"] / "engine20_plan.json").exists() else None
    assert plan is None or plan.get("total_attempts_planned", 1) > 0
    # 4. the kill switch is engaged again
    assert e["kill"].exists()


def test_a_governor_denial_leaves_no_attempt_line_but_a_ledger_line(tmp_path):
    """Forensic rule for a zero-request run: a request the governor DENIES never reaches the provider adapter, so it is
    absent from attempts.jsonl; the reason is in the store ledger."""
    import smart_move_collection_jobs as J
    from test_engine20 import Stub
    attempts = tmp_path / "attempts.jsonl"
    svc = make(tmp_path, J.AttemptRecorder(Stub(), attempts, gv.FakeClock(NOW)), authorized=0)
    svc.gov.enabled = True
    out = svc._call("ohlc", "AAA", {"timeframe": "3Y"}, NOW)
    assert (out is None or out[0] is None) and not attempts.exists()
    led = (tmp_path / "store" / "ledger.jsonl").read_text(encoding="utf-8")
    assert "DENIED_" in led
