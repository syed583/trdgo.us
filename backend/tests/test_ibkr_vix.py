"""Real VIX from IBKR first; VIXY only as a labelled fallback."""
import ibkr_vix
import kimi_service as ki


def _reset():
    ibkr_vix._cache.update(at=0.0, val=None)


def test_unconfigured_returns_none(monkeypatch):
    monkeypatch.delenv("IBKR_CPAPI_URL", raising=False)
    _reset()
    assert ibkr_vix.get_vix() is None


def test_reads_vix_when_authenticated(monkeypatch):
    monkeypatch.setenv("IBKR_CPAPI_URL", "https://localhost:5000/v1/api")
    _reset()
    calls = []

    def fake(path, timeout=4.0):
        calls.append(path)
        if "auth/status" in path:
            return {"authenticated": True}
        return [{"conid": 13455763, "31": "C17.52"}]          # C prefix = closed session

    monkeypatch.setattr(ibkr_vix, "_get", fake)
    assert ibkr_vix.get_vix() == {"value": 17.52, "source": "IBKR VIX index"}
    assert all("order" not in c and "account" not in c for c in calls)   # read-only


def test_unauthenticated_gateway_gives_none(monkeypatch):
    monkeypatch.setenv("IBKR_CPAPI_URL", "https://localhost:5000/v1/api")
    _reset()
    monkeypatch.setattr(ibkr_vix, "_get", lambda p, timeout=4.0: {"authenticated": False})
    assert ibkr_vix.get_vix() is None


def test_ibkr_is_primary_and_fallback_is_labelled(monkeypatch):
    monkeypatch.setattr(ibkr_vix, "get_vix", lambda: {"value": 21.0, "source": "IBKR VIX index"})
    assert ki.vix_level() == (21.0, "IBKR VIX index")
    monkeypatch.setattr(ibkr_vix, "get_vix", lambda: None)
    import unusualwhales_service as uw
    monkeypatch.setattr(uw, "get_daily_bars",
                        lambda s, d: [{"close": 10}] * 5 + [{"close": 11}])
    v, src = ki.vix_level()
    assert "fallback" in src and abs(v - 10.0) < 1e-9
