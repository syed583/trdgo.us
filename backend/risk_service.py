# ---------------------------------------------------------
# RISK SERVICE V1
# ---------------------------------------------------------


def assess_risk(payload: dict):
    """
    Conservative risk assessment object.

    Returns:
      risk_level: LOW/MEDIUM/HIGH/EXTREME
    """

    confidence = float(payload.get("confidence", payload.get("confidence_score", 0)) or 0)
    expected_move_percent = payload.get("expected_move_percent")
    if expected_move_percent is None:
        expected_move_percent = payload.get("expected_move")
    if isinstance(expected_move_percent, dict):
        expected_move_percent = expected_move_percent.get("percent")

    try:
        expected_move_percent = float(expected_move_percent)
    except (TypeError, ValueError):
        expected_move_percent = None

    completeness = float(payload.get("data_completeness", 100) or 100)
    liquidity = payload.get("liquidity")
    if liquidity is None:
        liquidity = payload.get("options_liquidity")

    missing_components = payload.get("missing_components") or []
    test_data = payload.get("test_data") or False

    # Base levels.
    if confidence < 50 or completeness < 50 or missing_components:
        risk_level = "HIGH"
    elif test_data:
        risk_level = "HIGH"
    elif expected_move_percent is not None and expected_move_percent >= 4:
        risk_level = "EXTREME"
    elif expected_move_percent is not None and expected_move_percent >= 2:
        risk_level = "HIGH"
    elif liquidity == "LOW":
        risk_level = "HIGH"
    elif confidence >= 75 and completeness >= 75:
        risk_level = "LOW"
    else:
        risk_level = "MEDIUM"

    return {
        "risk_level": risk_level,
        "confidence": round(max(0, min(100, confidence)), 2),
        "expected_move_percent": expected_move_percent,
        "data_completeness": round(max(0, min(100, completeness)), 2),
        "liquidity": liquidity,
        "warnings": [
            "Risk assessment is a conservative V1 estimate and does not execute trades."
        ],
        "missing_components": missing_components,
        "test_data": bool(test_data),
    }
