from confidence_service import calculate_confidence
from risk_service import assess_risk
from data_validation_service import validate_symbol_data


def test_confidence_service_returns_expected_keys():
    result = calculate_confidence({
        "components": {
            "fundamentals": {"score": 10},
            "estimates": {"score": 10},
            "technicals": {"score": 10},
            "earnings_history": {"score": 10},
            "options": {"score": 10},
            "market_environment": {"score": 10},
        },
        "ibkr_unavailable": True,
        "stale_components": ["options"],
        "test_data": False,
    })

    assert result["confidence_score"] >= 0
    assert result["confidence_score"] <= 100
    assert result["confidence_label"] in {
        "INSUFFICIENT_DATA", "LOW", "MEDIUM", "HIGH"
    }
    assert "warnings" in result


def test_risk_and_validation_services_are_safe_from_missing_data():
    risk = assess_risk({
        "confidence": 40,
        "expected_move_percent": None,
        "data_completeness": 45,
    })
    assert risk["risk_level"] in {"LOW", "MEDIUM", "HIGH", "EXTREME"}

    validation = validate_symbol_data("NVDA", {
        "source": "TEST",
        "status": "PROVIDER_OFFLINE"
    })
    assert validation["test_data"] is True
    assert any("TEST" in w or "Missing" in w for w in validation["warnings"])
