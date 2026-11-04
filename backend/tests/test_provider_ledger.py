"""The provider ledger: counts real requests per provider and day, classifies how they ended, persists atomically, never invents a cost, and never changes what the provider modules return."""
import io
import json
import urllib.error
from pathlib import Path

import pytest

import provider_ledger as PL


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("PROVIDER_LEDGER_DIR", str(tmp_path / "ledger"))
    monkeypatch.delenv("PROVIDER_PLAN_USD_MONTH", raising=False)
    PL.reset_for_tests()
    t = {"now": 1_793_700_000.0}                         # a fixed instant (2026-11-03, New York)
    monkeypatch.setattr(PL, "_clock", lambda: t["now"])
    yield t
    PL.reset_for_tests()


def test_counts_outcomes_and_latency_per_provider_and_never_invents_a_cost(fresh):
    PL.record("FINNHUB", "OK", 120)
    PL.record("FINNHUB", "OK", 80)
    PL.record("FINNHUB", PL.classify_http(code=429), 10)
    PL.record("FINVIZ", PL.classify_http(exc=TimeoutError("timed out")), 30000)
    snap = PL.snapshot()
    fh, fz = snap["providers"]["FINNHUB"], snap["providers"]["FINVIZ"]
    assert fh["today_requests"] == 3 and fh["today_outcomes"] == {"OK": 2, "RATE_LIMITED": 1} and fh["today_error_rate"] == round(1 / 3, 4) and fh["avg_latency_ms"] == 70
    assert fz["today_outcomes"] == {"TIMEOUT": 1}
    assert fh["plan_usd_month"] is None and fh["effective_usd_per_1000_requests_month_to_date"] is None and fh["cost"].startswith("UNKNOWN")      # no price given, no cost shown
    assert fh["limit"] == "UNKNOWN"


def test_cost_appears_only_when_the_owner_sets_a_plan_price(fresh, monkeypatch):
    monkeypatch.setenv("PROVIDER_PLAN_USD_MONTH", "FINNHUB=50, BOGUS, UNUSUAL_WHALES=250")
    for _ in range(200):
        PL.record("FINNHUB", "OK", 5)
    s = PL.snapshot()["providers"]
    assert s["FINNHUB"]["plan_usd_month"] == 50.0 and s["FINNHUB"]["effective_usd_per_1000_requests_month_to_date"] == 250.0
    assert s["UNUSUAL_WHALES"]["plan_usd_month"] == 250.0 and "no daily cap" in s["UNUSUAL_WHALES"]["limit"]


def test_days_are_new_york_dates_and_the_ledger_survives_a_restart_atomically(fresh, tmp_path):
    PL.record("FINNHUB", "OK", 1)
    fresh["now"] += 24 * 3600
    PL.record("FINNHUB", "OK", 1)
    PL.record("FINNHUB", "OK", 1)
    assert PL.flush() is True
    saved = json.loads((tmp_path / "ledger" / "provider_ledger.json").read_text())
    assert [saved["days"][d]["FINNHUB"]["requests"] for d in sorted(saved["days"])] == [1, 2] and not list((tmp_path / "ledger").glob("*.tmp"))
    PL.reset_for_tests()                                                     # "restart": memory gone, the file remains
    PL.record("FINNHUB", "OK", 1)
    assert PL.snapshot()["providers"]["FINNHUB"]["today_requests"] == 3


def test_a_ledger_failure_never_reaches_the_caller(fresh, monkeypatch, tmp_path):
    monkeypatch.setenv("PROVIDER_LEDGER_DIR", str(tmp_path / "file_not_dir"))
    (tmp_path / "file_not_dir").write_text("x")                              # the directory cannot be created: flush fails quietly
    PL.record("FINNHUB", "OK", 1)
    assert PL.flush() is False and PL.snapshot()["providers"]["FINNHUB"]["today_requests"] == 1
    PL.record("FINNHUB", "NOT_A_REAL_OUTCOME", None)                         # unknown outcome is stored as OTHER, not an error
    assert PL.snapshot()["providers"]["FINNHUB"]["today_outcomes"]["OTHER"] == 1


def test_finnhub_get_is_counted_and_returns_exactly_what_it_did_before(fresh, monkeypatch):
    import finnhub_service as fh
    monkeypatch.setattr(fh, "_key", lambda: "k")
    monkeypatch.setattr(fh, "_rate_gate", lambda: None)
    fh._cache.clear()

    class Resp:
        def __init__(self, body): self.b = body
        def read(self): return self.b
        def __enter__(self): return self
        def __exit__(self, *a): return False
    seq = [Resp(b'{"ok": 1}'), urllib.error.HTTPError("u", 429, "Too Many", {}, io.BytesIO(b"")), TimeoutError("timed out")]

    def fake_open(req, timeout=0):
        x = seq.pop(0)
        if isinstance(x, BaseException):
            raise x
        return x
    monkeypatch.setattr(fh.urllib.request, "urlopen", fake_open)
    assert fh._get("/a", ttl=0) == {"ok": 1}
    assert fh._get("/b", ttl=0) is None and fh._get("/c", ttl=0) is None                 # failures still read as "no data"
    o = PL.snapshot()["providers"]["FINNHUB"]["today_outcomes"]
    assert o == {"OK": 1, "RATE_LIMITED": 1, "TIMEOUT": 1}


def test_finviz_get_is_counted_and_returns_exactly_what_it_did_before(fresh, monkeypatch):
    import finviz_service as fv
    monkeypatch.setattr(fv, "_token", lambda: "t")

    class Resp:
        status = 200
        def read(self): return b"a,b"
        def __enter__(self): return self
        def __exit__(self, *a): return False
    seq = [Resp(), urllib.error.HTTPError("u", 503, "Unavailable", {}, io.BytesIO(b""))]

    def fake_open(req, timeout=0):
        x = seq.pop(0)
        if isinstance(x, BaseException):
            raise x
        return x
    monkeypatch.setattr(fv.urllib.request, "urlopen", fake_open)
    assert fv._get({"v": "1"}) == "a,b" and fv._get({"v": "1"}) is None
    assert PL.snapshot()["providers"]["FINVIZ"]["today_outcomes"] == {"OK": 1, "SERVER_ERROR": 1}


def _fake_uw(monkeypatch, count, day="2026-11-03"):
    import sys, types
    m = types.ModuleType("unusualwhales_service")
    m._spent = {"day": day, "count": count}
    m._metrics = {"errors_by": {"429": 2}, "no_data": 1, "lat": [100, 200]}
    m._today = lambda: day
    monkeypatch.setitem(sys.modules, "unusualwhales_service", m)
    return m


def test_unusual_whales_total_survives_a_restart_without_double_counting(fresh, monkeypatch):
    uw = _fake_uw(monkeypatch, 100)
    assert PL.snapshot()["providers"]["UNUSUAL_WHALES"]["today_requests"] == 100
    uw._spent["count"] = 160
    assert PL.snapshot()["providers"]["UNUSUAL_WHALES"]["today_requests"] == 160              # same process: follows the counter, not added to it
    assert PL.snapshot()["providers"]["UNUSUAL_WHALES"]["today_requests"] == 160              # asking twice never adds
    PL.flush()
    # --- restart: new process id, UW counter back at zero, then 25 new calls
    monkeypatch.setattr(PL, "_BOOT_ID", "another-process")
    PL._loaded = False
    PL._uw.update(day=None, base=0, last=0, boot=None)
    uw._spent["count"] = 25
    assert PL.snapshot()["providers"]["UNUSUAL_WHALES"]["today_requests"] == 185             # 160 banked + 25
    uw._spent["count"] = 40
    assert PL.snapshot()["providers"]["UNUSUAL_WHALES"]["today_requests"] == 200
    # --- a counter that goes down inside one process is also treated as a reset
    uw._spent["count"] = 5
    assert PL.snapshot()["providers"]["UNUSUAL_WHALES"]["today_requests"] == 205
    # --- a new New York day starts from the counter again
    PL.flush()
    fresh["now"] += 24 * 3600
    uw._spent.update(day="2026-11-04", count=7)
    uw._today = lambda: "2026-11-04"
    assert PL.snapshot()["providers"]["UNUSUAL_WHALES"]["today_requests"] == 7


def test_limits_and_remaining_quota_are_unknown_unless_configured(fresh, monkeypatch):
    for _ in range(10):
        PL.record("FINNHUB", "OK", 1)
    PL.record("FINVIZ", "OK", 1)
    f = PL.snapshot()["providers"]
    assert f["FINVIZ"]["limits"]["per_day"] == "UNKNOWN" and f["FINVIZ"]["remaining_today"] == "UNKNOWN" and f["FINVIZ"]["limit"] == "UNKNOWN"
    assert "not a verified provider quota" in f["FINNHUB"]["limits"]["per_minute"]
    monkeypatch.setenv("PROVIDER_LIMITS", '{"FINNHUB": {"per_minute": 60, "per_day": 100}}')
    g = PL.snapshot()["providers"]["FINNHUB"]
    assert g["limits"]["per_minute"] == 60 and g["remaining_today"] == 90 and g["limits"]["source"].startswith("owner configuration")
