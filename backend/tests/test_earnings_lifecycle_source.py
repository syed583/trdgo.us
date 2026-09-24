"""
The next scheduled report comes from whoever actually knows it.

Benzinga's calendar is a cache somebody has to sync, so on a fresh install
it holds nothing -- and the lifecycle strip read "no scheduled earnings
event on record" for companies reporting in three weeks. That sentence is a
claim about the company; the truth was a claim about our cache.
"""

import earnings_intelligence_service as ei
import uw_company_service as uwc


def test_the_paid_feed_supplies_the_next_report(monkeypatch):
    monkeypatch.setattr(uwc, "next_report", lambda s: {
        "symbol": s, "date": "2026-11-18", "lifecycle": "SCHEDULED",
        "eps_estimate": 2.47, "source": "UNUSUAL_WHALES"})

    out = ei.get_event_lifecycle("NVDA")
    assert out["status"] == "OK"
    assert out["source"] == "UNUSUAL_WHALES"
    assert out["stage"] == "SCHEDULED"
    assert out["event"]["date"] == "2026-11-18"


def test_benzinga_still_answers_when_the_paid_feed_cannot(monkeypatch):
    monkeypatch.setattr(uwc, "next_report", lambda s: None)
    monkeypatch.setattr(ei.benzinga, "get_upcoming", lambda a, b, c: {
        "rows": [{"symbol": "NVDA", "date": "2026-11-18",
                  "lifecycle": "SCHEDULED"}]})

    out = ei.get_event_lifecycle("NVDA")
    assert out["status"] == "OK" and out["source"] == "BENZINGA"


def test_with_no_date_anywhere_it_does_not_blame_a_provider(monkeypatch):
    monkeypatch.setattr(uwc, "next_report", lambda s: None)
    monkeypatch.setattr(ei.benzinga, "get_upcoming", lambda a, b, c: {"rows": []})
    monkeypatch.setattr(ei, "_legacy_rows", lambda db, s: [])

    out = ei.get_event_lifecycle("ZZZZ")
    assert out["status"] != "OK"
    detail = out["detail"].lower()
    assert "has not announced" in detail or "no configured provider" in detail


def test_revisions_state_what_they_measured(monkeypatch):
    """
    A one-week revision count is not a 90-day snapshot comparison, and the
    payload has to say which it is rather than let the screen imply the
    stronger one.
    """
    monkeypatch.setattr(uwc, "earnings_estimates", lambda s: {
        "status": "OK", "rows": [{
            "period_ending": "2099-10-31", "horizon": "fiscal quarter",
            "eps_estimate": 2.47, "analysts": 43.0,
            "revisions_up": 5.0, "revisions_down": 1.0,
            "revision_lean": "UP"}]})

    out = uwc.estimate_revisions("NVDA")
    assert out["status"] == "OK"
    assert out["revision_lean"] == "UP"
    assert out["horizons_available"] == [0]
    assert "7/30/60/90-day comparison" in out["detail"]
