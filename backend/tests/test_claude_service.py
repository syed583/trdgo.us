"""
The Claude layer: words only, written once, never allowed to break a screen.

Claude is stubbed throughout -- these tests cost nothing and need no network.
What they pin is the contract around it: an explanation is generated at most
once per call and then stored; a failure comes back as a status rather than
an exception; and the request carries the call's figures and nothing that
would let the model invent its own.
"""

import json
from types import SimpleNamespace

import calls_service as cs
import claude_service as cl


def _stored_call():
    result = {
        "status": "OK", "decision": "BUY", "direction_score": 66.0,
        "confidence": 70.0, "agreement_pct": 72.0, "coverage_pct": 90.0,
        "actionable": True, "market": {"session": "OPEN"},
        "reasons": [], "blocked_reasons": [],
        "signals": [
            {"name": "options_flow", "label": "Options Flow", "weight": 10,
             "available": True, "directional": True, "points": 4.0,
             "points_label": "+4.0 of 10", "detail": "62% bullish premium"},
            {"name": "rsi", "label": "RSI (14)", "weight": 5,
             "available": True, "directional": True, "points": -1.0,
             "points_label": "-1.0 of 5", "detail": "RSI 74"},
        ],
    }
    return cs.record("ZZCLAUDE", result, horizon="TOMORROW", origin="analysis",
                     price=50.0, spy=500.0)


def _cleanup():
    from database import SessionLocal
    from models_calls import TradeCall

    db = SessionLocal()
    db.query(TradeCall).filter(TradeCall.symbol == "ZZCLAUDE").delete()
    db.commit()
    db.close()


def test_an_explanation_is_written_once_then_stored(monkeypatch):
    calls = {"n": 0}

    def fake(call):
        calls["n"] += 1
        return {"status": "OK", "text": "Flow leaned bullish. RSI is stretched.",
                "model": "claude-opus-5"}

    monkeypatch.setattr(cl, "explain_call", fake)
    call_id = _stored_call()
    try:
        first = cs.explain(call_id, symbol_news=False)
        second = cs.explain(call_id, symbol_news=False)
        assert first["explanation"] == "Flow leaned bullish. RSI is stretched."
        assert second["explanation"] == first["explanation"]
        assert second["cached"] is True
        assert calls["n"] == 1, "a stored explanation must not be paid for twice"
        assert cs.get(call_id)["why"]["explanation"] == first["explanation"]
    finally:
        _cleanup()


def test_a_failed_explanation_is_a_status_not_a_crash(monkeypatch):
    monkeypatch.setattr(cl, "explain_call",
                        lambda call: {"status": "RATE_LIMITED", "detail": "slow down"})
    call_id = _stored_call()
    try:
        out = cs.explain(call_id, symbol_news=False)
        assert out["status"] == "RATE_LIMITED"
        # Nothing is stored on failure, so the next attempt tries again.
        assert cs.get(call_id)["why"]["explanation"] is None
    finally:
        _cleanup()


def test_the_request_carries_the_calls_figures_and_nothing_else(monkeypatch):
    seen = {}

    def fake_ask(system, payload):
        seen["system"], seen["payload"] = system, payload
        return {"status": "OK", "text": "x"}

    monkeypatch.setattr(cl, "_ask", fake_ask)
    call_id = _stored_call()
    try:
        cl.explain_call(cs.get(call_id))
        body = json.loads(seen["payload"])
        assert body["decision"] == "BUY"
        assert body["outlook"] == "TOMORROW"
        assert [p["parameter"] for p in body["pushed_toward_decision"]] == ["Options Flow"]
        assert "Never add a price" in seen["system"]
    finally:
        _cleanup()


def test_no_key_means_a_clear_status(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = cl._ask("system", "payload")
    assert out["status"] == "NOT_CONFIGURED"


def test_a_refusal_is_reported_not_shown_as_text(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    # The API answers over HTTP now; stand in for the transport, not an SDK.
    monkeypatch.setattr(cl, "_post", lambda body: {
        "stop_reason": "refusal", "content": [], "model": "claude-opus-5"})
    assert cl._ask("s", "p")["status"] == "REFUSED"


def test_a_transport_failure_is_a_status_not_a_crash(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    def boom(body):
        raise cl._HTTPError("RATE_LIMITED", "slow down")

    monkeypatch.setattr(cl, "_post", boom)
    out = cl._ask("s", "p")
    assert out["status"] == "RATE_LIMITED" and "slow" in out["detail"]


def test_a_normal_answer_comes_back_as_text(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(cl, "_post", lambda body: {
        "stop_reason": "end_turn", "model": "claude-opus-5",
        "content": [{"type": "text", "text": "Flow leaned bullish. RSI is stretched."}],
        "usage": {"input_tokens": 10, "output_tokens": 5}})
    out = cl._ask("s", "p")
    assert out["status"] == "OK"
    assert out["text"] == "Flow leaned bullish. RSI is stretched."


def test_news_tone_needs_headlines():
    assert cl.news_tone("AAPL", [])["status"] == "NO_DATA"
