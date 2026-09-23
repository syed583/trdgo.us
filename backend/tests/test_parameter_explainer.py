"""
Explaining one parameter: the filings go in, words come out, nothing else.

Claude is stubbed, so these cost nothing. What they pin is the contract: the
prompt carries only evidence this app measured, the answer is written once per
reading and then cached, a failure is a status rather than an exception, and
the filings behind the answer come back with it so a reader can check instead
of trusting.
"""

import json

import parameter_explainer as pe

SIGNAL = {
    "name": "funding_activity", "label": "Funding & Debt", "weight": 4,
    "available": True, "directional": True, "points_label": "-2.0 of 4",
    "detail": "share offering 43d ago", "rule": "Equity sold privately...",
    "evidence": {"filings": 3},
}

FILINGS = {"status": "OK", "events": [
    {"filed": "2026-08-11", "days_ago": 43, "category": "funding",
     "headline": "Share offering", "detail": "Equity was sold.",
     "form": "8-K", "url": "https://sec.gov/x"},
    {"filed": "2026-08-11", "days_ago": 43, "category": "deal",
     "headline": "Material agreement signed", "detail": "A deal.",
     "form": "8-K", "url": "https://sec.gov/y"},
]}


def _stub(monkeypatch, answer=None, seen=None):
    import claude_service as claude
    import corporate_events_service as events

    monkeypatch.setattr(events, "get_events", lambda symbol, *a, **k: FILINGS)

    def ask(system, payload):
        if seen is not None:
            seen.append((system, payload))
        return answer or {"status": "OK", "text": "Intel sold shares.",
                          "model": "claude-opus-5"}

    monkeypatch.setattr(claude, "ask", ask)
    pe.cache.purge("ZZP")


def test_the_prompt_carries_the_filings_this_parameter_reads(monkeypatch):
    seen = []
    _stub(monkeypatch, seen=seen)
    pe.explain("ZZP", "funding_activity", SIGNAL)

    system, payload = seen[0]
    body = json.loads(payload)
    assert body["company"] == "ZZP"
    assert body["parameter"] == "Funding & Debt"
    filings = body["evidence"]["filings"]
    # The funding parameter reads funding filings, not the deal next to them.
    assert [f["event"] for f in filings] == ["Share offering"]
    assert "Never add a price" in system


def test_an_answer_is_written_once_and_then_cached(monkeypatch):
    calls = []
    _stub(monkeypatch, seen=calls)
    first = pe.explain("ZZP", "funding_activity", SIGNAL)
    second = pe.explain("ZZP", "funding_activity", SIGNAL)

    assert first["status"] == "OK" and first["cached"] is False
    assert second["cached"] is True
    assert second["text"] == first["text"]
    assert len(calls) == 1, "a cached reading must not be paid for twice"


def test_the_filings_behind_the_answer_come_back_with_it(monkeypatch):
    _stub(monkeypatch)
    out = pe.explain("ZZP", "funding_activity", SIGNAL)
    assert out["filings"][0]["url"] == "https://sec.gov/x"
    assert "adds no data of its own" in out["note"]


def test_a_refusal_or_outage_is_a_status_not_a_crash(monkeypatch):
    _stub(monkeypatch, answer={"status": "RATE_LIMITED", "detail": "slow down"})
    out = pe.explain("ZZP", "funding_activity", SIGNAL)
    assert out["status"] == "RATE_LIMITED" and "slow down" in out["detail"]


def test_a_parameter_the_model_never_scored_is_refused(monkeypatch):
    import directional_score_service as score

    monkeypatch.setattr(score, "get_directional_score",
                        lambda symbol, *a, **k: {"signals": []})
    out = pe.explain("ZZP", "not_a_parameter")
    assert out["status"] == "NO_DATA"


def test_a_new_reading_is_explained_again(monkeypatch):
    """The cache key is the reading, so tomorrow's number gets its own answer."""
    calls = []
    _stub(monkeypatch, seen=calls)
    pe.explain("ZZP", "funding_activity", SIGNAL)
    pe.explain("ZZP", "funding_activity", {**SIGNAL, "points_label": "-3.0 of 4"})
    assert len(calls) == 2
