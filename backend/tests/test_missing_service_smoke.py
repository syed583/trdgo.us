import pytest

from options_score_service import score_options
from trdgo_score_service import score_trdgo
from data_validation_service import validate_symbol_data
from risk_service import assess_risk
from analysis_service import build_analysis


def test_options_score_service_shape():
    result = score_options({
        "symbol": "NVDA",
        "status": "OK",
        "data_confidence": 50,
        "call_volume": 100,
        "put_volume": 50,
        "call_open_interest": 100,
        "put_open_interest": 50,
        "average_iv_percent": 50,
        "call_price": 5.0,
        "put_price": 5.0,
        "underlying_price": 100.0,
    })

    assert result["score"] <= 15
    assert result["max_score"] == 15
    assert result["bias"] in {
        "STRONG_BULLISH",
        "BULLISH",
        "NEUTRAL",
        "BEARISH",
        "STRONG_BEARISH",
        "INSUFFICIENT_DATA",
    }


def test_trdgo_score_and_meta_services_shape():
    trdgo = score_trdgo({
        "symbol": "NVDA",
        "fundamentals": {"score": 10, "max_score": 20},
        "estimates": {"score": 10, "max_score": 25},
        "technicals": {"score": 8, "max_score": 20},
        "earnings_history": {"score": 4, "max_score": 15},
        "options": {"score": 5, "max_score": 15},
        "market_environment": {"score": 2, "max_score": 5},
    })

    assert -100 <= trdgo["direction_score"] <= 100
    assert trdgo["confidence_score"] >= 0
    assert trdgo["confidence_label"] in {
        "LOW", "MEDIUM", "HIGH", "INSUFFICIENT_DATA"
    }

    risk = assess_risk({"confidence": 80, "expected_move_percent": 2.0})
    assert risk["risk_level"] in {"LOW", "MEDIUM", "HIGH", "EXTREME"}

    validation = validate_symbol_data("NVDA", {
        "source": "TEST",
        "status": "OK"
    })
    assert "warnings" in validation

    analysis = build_analysis({
        "symbol": "NVDA",
        "direction_score": 60,
        "confidence_score": 80,
        "decision": "BUY",
        "risk": {"risk_level": "LOW"},
        "expected_move": {"percent": 2.0},
        "components": {},
    })
    assert analysis["symbol"] == "NVDA"
    assert analysis["decision"] == "BUY"
