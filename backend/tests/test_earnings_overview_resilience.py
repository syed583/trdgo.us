"""
One provider failing must not blank the whole Earnings Analysis screen.

The overview assembles a chart, an option chain, a score and the earnings
history. It read the history's statistics by subscript, so a provider that
answered with a status and no statistics -- rate limited, not configured, a
symbol it does not carry -- raised KeyError inside the request handler. The
browser got a 500 and rendered nothing at all: no history, and no chart,
chain or score either, none of which had anything to do with the failure.
"""

import live_score_service as scores


def _history(payload, monkeypatch):
    import earnings_intelligence_service as ei
    monkeypatch.setattr(ei, "get_history", lambda s, q=8: payload)


def test_a_history_with_no_statistics_does_not_raise(monkeypatch):
    """The shape a provider returns when it cannot answer."""
    _history({"symbol": "NVDA", "status": "RATE_LIMITED",
              "detail": "Too many requests"}, monkeypatch)

    import earnings_intelligence_service as ei
    payload = ei.get_history("NVDA")
    stats = payload.get("stats") or {}

    # This is the exact access the screen makes.
    history = {
        "beat_rate": stats.get("beat_rate"),
        "quarters": payload.get("quarters") or [],
        "status": payload.get("status", "DATA_UNAVAILABLE"),
        "basis_detail": stats.get("detail") or payload.get("detail"),
    }
    assert history["beat_rate"] is None
    assert history["quarters"] == []
    assert history["status"] == "RATE_LIMITED"
    # And the reason survives, so the panel can say why rather than sit empty.
    assert history["basis_detail"] == "Too many requests"


def test_the_overview_reads_every_statistic_defensively():
    """
    Guard against the pattern coming back: a subscript on a provider payload
    is a 500 waiting for that provider to have a bad afternoon.
    """
    import inspect

    source = inspect.getsource(scores.get_earnings_overview)
    start = source.index('stats = history_payload.get("stats")')
    block = source[start:start + 1200]
    for field in ("beat_rate", "average_surprise", "sample_size", "basis"):
        assert f'stats["{field}"]' not in block, \
            f'stats["{field}"] will raise the moment the provider is unhappy'
        assert f'stats.get("{field}")' in block
