"""Kit S1: the AttributeError that ended an analysis, and the agents that ran before the check.

Reproduced from the live traceback (app.log, 2026-11-23 09:27:35, symbol "P", horizon TOMORROW):

    horizon_model.score_horizon -> live_market_service.get_chart -> _bars_fallback_note
    status = provider.last_status
    AttributeError: module 'unusualwhales_service' has no attribute 'last_status'

The important part is WHEN that note is built: get_chart calls it to explain an EMPTY DAILY CHART.
So this was never limited to unknown tickers - any symbol with no daily bars reached it, including
a thin or newly listed one, a provider outage, and an exhausted request budget.
"""
import sys
import types

import pytest

import live_market_service as market
import provider_config as cfg


@pytest.fixture(autouse=True)
def _leave_the_shared_cache_as_we_found_it():
    """Put the process-wide TTL cache back after every test in this file.

    live_market_service.cache is shared by the whole backend (symbol_service imports the same
    object) and is capped at 5000 entries, evicting oldest-first. Entries added here can therefore
    push out an entry a LATER test in the run depends on - which is exactly what happened: these
    tests passed alone and in pairs, then took an unrelated Unusual Whales caching test down when
    the full selection ran. The failure was in the tests, not the fix.
    """
    saved = dict(market.cache._data)
    try:
        yield
    finally:
        market.cache._data.clear()
        market.cache._data.update(saved)



# --------------------------------------------------------------------------- the crash
def _provider(**attrs):
    """A stand-in provider module, so nothing here touches a real feed."""
    m = types.ModuleType("fake_provider")
    m.SOURCE = "UNUSUAL_WHALES"
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


def test_the_note_does_not_raise_when_the_provider_has_no_public_status(monkeypatch):
    """THE REGRESSION. A module with only the private name must not take the page down.

    This is exactly the shape unusualwhales_service had: `_last_status` and no `last_status`.
    """
    mod = _provider(_last_status=cfg.RATE_LIMITED)
    monkeypatch.setattr(market, "_bar_providers", lambda: [mod])

    note = market._bars_fallback_note(False)        # must not raise

    assert "rate-limited" in note.lower()


def test_the_note_reads_a_callable_accessor(monkeypatch):
    """The real fix: the provider now offers last_status() and it is used."""
    mod = _provider(last_status=lambda: cfg.ENTITLEMENT_REQUIRED)
    monkeypatch.setattr(market, "_bar_providers", lambda: [mod])

    assert "rejected the configured key" in market._bars_fallback_note(False)


def test_a_provider_that_cannot_say_degrades_instead_of_crashing(monkeypatch):
    """No status of any kind, and a status getter that throws: both must still produce a note.

    A sentence explaining why there are no bars must never be the reason a page fails.
    """
    for mod in (_provider(), _provider(last_status=lambda: (_ for _ in ()).throw(RuntimeError("x")))):
        monkeypatch.setattr(market, "_bar_providers", lambda m=mod: [m])
        note = market._bars_fallback_note(False)
        assert "not been reached yet" in note


def test_the_real_provider_exposes_last_status():
    """unusualwhales_service must offer the public accessor both call sites assumed."""
    import unusualwhales_service as uw

    assert callable(getattr(uw, "last_status", None)), "last_status() is missing again"
    assert uw.last_status() == uw._last_status


# --------------------------------------------------------------------------- the agents
def test_an_unknown_symbol_is_rejected_without_running_the_agents(monkeypatch):
    """"P" must come back UNKNOWN_SYMBOL having spent nothing on the twelve agents."""
    import directional_score_service as ds
    import symbol_service as symbols

    called = {"gather": 0}

    def _boom(*a, **k):
        called["gather"] += 1
        raise AssertionError("the agents ran for an unknown symbol")

    monkeypatch.setattr(ds, "_gather", _boom)
    monkeypatch.setattr(symbols, "known_to_sec", lambda s: False)
    monkeypatch.setattr(ds, "_symbol_exists", lambda s: False)
    monkeypatch.setattr(ds, "get_cached_score", lambda *a, **k: None, raising=False)

    out = ds.get_directional_score("P")

    assert out["status"] == "UNKNOWN_SYMBOL"
    assert called["gather"] == 0, "the whole point is that no agent ran"


def test_a_real_symbol_pays_nothing_extra_for_the_new_check(monkeypatch):
    """A company the SEC registry knows must NOT trigger the feed lookup.

    The registry is free and cached for a day; validate() calls the provider. Getting this
    backwards would add a request to every analysis, which is why the check is ordered this way.
    """
    import directional_score_service as ds
    import symbol_service as symbols

    paid = {"validate": 0}
    monkeypatch.setattr(symbols, "known_to_sec", lambda s: True)
    monkeypatch.setattr(ds, "_symbol_exists",
                        lambda s: paid.__setitem__("validate", paid["validate"] + 1) or True)
    monkeypatch.setattr(ds, "_gather", lambda *a, **k: (_ for _ in ()).throw(_Stop()))

    with pytest.raises(_Stop):          # proves we got PAST the pre-check and on to the work
        ds.get_directional_score("NVDA")

    assert paid["validate"] == 0, "a known company must not pay for the feed lookup"


def test_it_fails_open_when_the_registry_cannot_answer(monkeypatch):
    """Registry unavailable (None) must let the analysis proceed.

    Calling a real company unknown because SEC was unreachable is the worse of the two mistakes.
    """
    import directional_score_service as ds
    import symbol_service as symbols

    monkeypatch.setattr(symbols, "known_to_sec", lambda s: None)
    monkeypatch.setattr(ds, "_gather", lambda *a, **k: (_ for _ in ()).throw(_Stop()))

    with pytest.raises(_Stop):
        ds.get_directional_score("NVDA")


class _Stop(Exception):
    """Marker: the pre-check let us through and the real work started."""
