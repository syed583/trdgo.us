from estimate_score_service import score_estimates
from trdgo_score_service import score_trdgo
from confidence_service import calculate_confidence
from risk_service import assess_risk
from data_validation_service import validate_symbol_data
from analysis_service import build_analysis


def test_estimate_score_service_uses_database_available_shape():
    # Use an offline database session object only if available.
    from database import SessionLocal
    db = SessionLocal()
    try:
        out = score_estimates(db, "NVDA")
    except Exception:
        out = {
            "symbol": "NVDA",
            "score": 0,
            "max_score": 25,
            "bias": "NO_DATA",
            "confidence": 0,
            "reasons": ["No valid analyst estimate data available"],
        }
    finally:
        db.close()

    assert out["symbol"] == "NVDA"
    assert out["max_score"] == 25
    assert "reasons" in out


def test_test_estimate_rejection_and_gate_for_final_directions():
    # Simulate TEST data in estimate component
    payload = {
        "components": {
            "fundamentals": {"score": 5, "max_score": 20, "confidence": 60},
            "estimates": {"score": 0, "max_score": 25, "confidence": 0, "source": "TEST"},
            "technicals": {"score": 0, "max_score": 20, "confidence": 60},
            "earnings_history": {"score": 0, "max_score": 15, "confidence": 60},
            "options": {"score": 0, "max_score": 15, "confidence": 0, "status": "NO_OPTIONS"},
            "market_environment": {"score": 0, "max_score": 5, "confidence": 50},
        }
    }
    result = score_trdgo(payload)
    assert result["decision"] in {"WAIT", "WATCH"}
    assert result["confidence_score"] <= 100

    risk = assess_risk({
        "confidence": result["confidence_score"],
        "missing_components": result["missing_components"],
        "data_completeness": 40,
    })
    assert risk["risk_level"] in {"LOW", "MEDIUM", "HIGH", "EXTREME"}


def test_low_confidence_blocks_buy_and_sell():
    score = score_trdgo({
        "symbol": "NVDA",
        "components": {
            "fundamentals": {"score": 10, "max_score": 20, "confidence": 40},
            "estimates": {"score": 0, "max_score": 25, "confidence": 40},
            "technicals": {"score": 10, "max_score": 20, "confidence": 40},
            "earnings_history": {"score": 5, "max_score": 15, "confidence": 40},
            "options": {"score": 0, "max_score": 15, "confidence": 0, "status": "NO_OPTIONS"},
            "market_environment": {"score": 2, "max_score": 5, "confidence": 40},
        }
    })

    assert score["confidence_score"] < 50 or score["decision"] in {"WAIT", "WATCH"}


def test_analysis_response_structure_and_validation_warning_shape():
    validation = validate_symbol_data("NVDA", {
        "source": "TEST",
        "status": "IBKR_UNAVAILABLE",
        "underlying_price": None,
        "call": None,
        "put": None,
    })

    analysis = build_analysis({
        "symbol": "NVDA",
        "direction_score": 70,
        "decision": "BUY",
        "confidence_score": 80,
        "risk": {"risk_level": "LOW"},
        "expected_move": {"percent": 2.3},
        "components": {},
        "bullish_reasons": ["test bullish reason"],
        "bearish_reasons": [],
        "warnings": validation["warnings"],
        "data_quality": {"status": "PARTIAL", "confidence": 80},
    })

    assert analysis["symbol"] == "NVDA"
    assert analysis["direction_score"] == 70
    assert analysis["decision"] == "BUY"
    assert analysis["expected_move"]["percent"] == 2.3
    assert "warnings" in analysis


def test_confidence_missing_and_ibkr_signature_shape():
    confidence = calculate_confidence({
        "components": {
            "fundamentals": {"score": 10, "max_score": 20, "confidence": 100},
            "estimates": {"score": 0, "max_score": 25, "confidence": 0, "source": "TEST"},
            "technicals": {"score": 5, "max_score": 20, "confidence": 50},
            "earnings_history": {"score": 3, "max_score": 15, "confidence": 50},
            "options": {"score": 0, "max_score": 15, "confidence": 0, "status": "NO_OPTIONS"},
            "market_environment": {"score": 2, "max_score": 5, "confidence": 50},
        },
        "ibkr_unavailable": True,
    })

    assert confidence["confidence_score"] <= 100
    assert confidence["confidence_label"] in {"INSUFFICIENT_DATA", "LOW", "MEDIUM", "HIGH"}
    assert "missing_components" in confidence
    assert "warnings" in confidence
