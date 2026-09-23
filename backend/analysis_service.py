# ---------------------------------------------------------
# ANALYSIS SERVICE
# ---------------------------------------------------------


def build_analysis(payload: dict):
    """
    Build a frontend-friendly analysis payload from the final
    score object, component object map, risk model, and options data.

    Returned shape:
    symbol
    direction_score
    decision
    confidence
    risk
    expected_move
    components
    bullish_reasons
    bearish_reasons
    warnings
    data_quality
    provider_status
    """

    if not payload:
        return {
            "symbol": "UNKNOWN",
            "direction_score": 0,
            "decision": "WAIT",
            "confidence": 0,
            "risk": {"risk_level": "HIGH"},
            "expected_move": {"percent": None},
            "components": {},
            "bullish_reasons": [],
            "bearish_reasons": [],
            "warnings": ["No analysis payload available"],
            "data_quality": {"status": "UNAVAILABLE", "confidence": 0},
            "provider_status": {
                "database": "UNAVAILABLE",
                "sec": "UNAVAILABLE",
                "ibkr": "PROVIDER_OFFLINE",
                "estimates": "UNAVAILABLE",
            },
        }

    symbol = str(payload.get("symbol", "UNKNOWN")).upper()

    direction_score = payload.get("direction_score")
    if direction_score is None:
        direction_score = payload.get("score", 0)

    decision = payload.get("decision", "WAIT")

    confidence = payload.get("confidence_score")
    if confidence is None:
        confidence = payload.get("confidence")
    if confidence is None:
        confidence = 0

    risk = payload.get("risk") or {"risk_level": "MEDIUM"}
    expected_move = payload.get("expected_move") or {"percent": None}
    components = payload.get("components") or {}

    bullish_reasons = payload.get("bullish_reasons") or []
    bearish_reasons = payload.get("bearish_reasons") or []
    warnings = payload.get("warnings") or []

    data_quality = payload.get("data_quality") or {
        "status": "UNAVAILABLE",
        "confidence": confidence,
    }

    provider_status = payload.get("provider_status")
    if not isinstance(provider_status, dict):
        provider_status = {
            "database": str(payload.get("database_status") or "UNAVAILABLE").upper(),
            "sec": str(payload.get("sec_status") or "UNAVAILABLE").upper(),
            "ibkr": str(payload.get("ibkr_status") or "PROVIDER_OFFLINE").upper(),
            "estimates": str(payload.get("estimate_status") or "UNAVAILABLE").upper(),
        }

    return {
        "symbol": symbol,
        "direction_score": direction_score,
        "decision": decision,
        "confidence": confidence,
        "risk": risk,
        "expected_move": expected_move,
        "components": components,
        "bullish_reasons": bullish_reasons,
        "bearish_reasons": bearish_reasons,
        "warnings": warnings,
        "data_quality": data_quality,
        "provider_status": provider_status,
    }
