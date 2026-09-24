"""
Pricing an earnings move the provider has not priced yet.

Their expected move comes from the front-month straddle and exists only
once the report is inside it, so a company reporting in five weeks showed
nothing -- which on screen reads as missing data rather than "not priced
yet". The chain can answer instead.

What matters more than the number is what the app is allowed to say about
it. A straddle bought weeks early and held past the report is mostly time
value, so it is an upper bound on the earnings move. Compared against the
stock's typical reaction it would read as "options expect something huge"
every time, which is a false reading drawn from a figure that was never
measuring the event alone.
"""

import uw_earnings_calendar as cal


def _chain(monkeypatch, spot=100.0, call=3.0, put=2.5):
    import unusualwhales_service as uw
    monkeypatch.setattr(uw, "expirations", lambda s: {
        "status": "OK", "source": uw.SOURCE,
        "data": [{"expires": "2026-10-02"}, {"expires": "2026-10-30"},
                 {"expires": "2026-11-20"}]})
    monkeypatch.setattr(uw, "load_chain", lambda s, e="": {
        "status": "OK", "spot": spot, "rows": [
            {"strike": 95.0, "right": "C", "mid": 7.0},
            {"strike": 95.0, "right": "P", "mid": 1.0},
            {"strike": 100.0, "right": "C", "mid": call},
            {"strike": 100.0, "right": "P", "mid": put},
            {"strike": 110.0, "right": "C", "mid": 0.5},
            {"strike": 110.0, "right": "P", "mid": 9.0},
        ]})


def test_the_first_expiry_after_the_report_is_the_one_priced(monkeypatch):
    _chain(monkeypatch)
    out = cal.implied_move("AAPL", "2026-10-29")
    assert out["expiry"] == "2026-10-30", "an expiry before the report prices nothing"


def test_the_straddle_is_taken_at_the_strike_nearest_spot(monkeypatch):
    _chain(monkeypatch, spot=100.0, call=3.0, put=2.5)
    out = cal.implied_move("AAPL", "2026-10-29")
    assert out["strike"] == 100.0
    assert out["dollars"] == 5.5          # 3.0 + 2.5
    assert out["percent"] == 5.5          # against a spot of 100


def test_it_says_where_the_number_came_from(monkeypatch):
    _chain(monkeypatch)
    basis = cal.implied_move("AAPL", "2026-10-29")["basis"]
    assert "Computed here" in basis
    assert "time value" in basis, "an upper bound has to admit it is one"


def test_a_chain_reading_never_claims_options_are_expensive():
    """The comparison the provider's figure earns, this one does not."""
    computed = {"expiry": "2026-10-30", "percent": 6.5}
    said = cal._chain_verdict(6.5, 2.5, computed)
    assert "includes time value" in said
    assert "typically moved 2.5%" in said
    for phrase in ("more than this stock usually moves",
                   "less than this stock usually moves"):
        assert phrase not in said


def test_the_providers_own_figure_still_gets_the_comparison():
    assert "more than this stock usually moves" in cal._verdict(9.0, 4.0)


def test_no_chain_means_no_invented_number(monkeypatch):
    import unusualwhales_service as uw
    monkeypatch.setattr(uw, "expirations", lambda s: {
        "status": "OK", "data": [], "source": uw.SOURCE})
    assert cal.implied_move("AAPL", "2026-10-29") is None
