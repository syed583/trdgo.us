"""
The provider map: what each one powers, and what breaks without it.

A status nobody can interpret is not information -- "Twelve Data: rate
limited" meant nothing until the trend parameters silently went blank. So
every provider in this table has to say what it powers and what the app does
without it, and a free feed must not be reported as broken merely because it
has no key to check.
"""

import provider_usage as pu


def test_every_provider_says_what_it_powers_and_what_is_lost():
    for provider in pu.PROVIDERS:
        assert provider["powers"], provider["key"]
        assert provider["fallback"], provider["key"]
        assert provider["freshness"], provider["key"]
        assert provider["cost"], provider["key"]


def test_the_providers_the_model_depends_on_are_all_listed():
    listed = {p["key"] for p in pu.PROVIDERS}
    for required in ("UNUSUAL_WHALES", "SEC", "ANTHROPIC"):
        assert required in listed, required


def test_a_public_feed_is_not_reported_as_unknown():
    """SEC, Nasdaq, Yahoo and the SPDR files need no key; that is a status."""
    for key in ("SEC", "SPDR", "NASDAQ", "YAHOO_RSS"):
        state = pu._status_of(key)
        assert state["status"] == "OK", key
        assert "no key" in (state["detail"] or "").lower()


def test_a_provider_whose_status_raises_does_not_break_the_page(monkeypatch):
    import unusualwhales_service as uw

    def boom():
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(uw, "provider_status", boom)
    out = pu.usage()
    row = next(p for p in out["providers"] if p["key"] == "UNUSUAL_WHALES")
    assert row["status"] == "UNKNOWN"
    assert out["count"] == len(pu.PROVIDERS)


def test_the_map_can_be_read_without_probing_anything(monkeypatch):
    def forbidden(key):
        raise AssertionError("no status should be probed")

    monkeypatch.setattr(pu, "_status_of", forbidden)
    out = pu.usage(with_status=False)
    assert out["count"] == len(pu.PROVIDERS)
    assert "status" not in out["providers"][0]
