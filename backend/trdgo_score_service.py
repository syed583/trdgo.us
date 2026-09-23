# ---------------------------------------------------------
# TRDGO FINAL SCORE SERVICE
# ---------------------------------------------------------


def _component_payload_map(payload: dict):
    """
    Normalize component map from either a payload object or
    a nested `components` dictionary.
    """

    if not isinstance(payload, dict):
        return {}

    if "components" in payload and isinstance(payload["components"], dict):
        comps = payload["components"]
    else:
        comps = payload

    return {
        "fundamentals": comps.get("fundamentals") or comps.get("component_fundamentals"),
        "estimates": comps.get("estimates") or comps.get("component_estimates"),
        "technicals": comps.get("technicals") or comps.get("component_technicals"),
        "earnings_history": comps.get("earnings_history") or comps.get("component_earnings_history"),
        "options": comps.get("options") or comps.get("component_options"),
        "market_environment": comps.get("market_environment") or comps.get("component_market_environment"),
    }


def score_trdgo(payload: dict):
    """
    Conservative TRDGO score integration.

    Only verified component scores contribute to direction. Missing,
    unavailable, test, stale, conflict, or provider-offline components
    are explicitly flagged as missing evidence and reduce confidence.
    """

    symbol = str(payload.get("symbol", payload.get("name", "UNKNOWN"))).upper()
    components = _component_payload_map(payload)

    weights = {
        "fundamentals": 20,
        "estimates": 25,
        "technicals": 20,
        "earnings_history": 15,
        "options": 15,
        "market_environment": 5,
    }

    allowed_status = {
        "OK",
        "UNAVAILABLE",
        "INSUFFICIENT_DATA",
        "TEST_DATA",
        "STALE_DATA",
        "PROVIDER_OFFLINE",
        "DATA_CONFLICT",
    }

    missing_components = []
    warnings = []
    direction_score = 0
    confidence_total_score = 0
    confidence_weight_total = 0

    for component_name, weight in weights.items():
        comp = components.get(component_name)
        if not comp:
            missing_components.append(component_name)
            warnings.append(f"Missing or insufficient {component_name} component data")
            continue

        raw_status = str(comp.get("status") or comp.get("data_quality") or comp.get("source_quality") or "OK").upper()

        # Normalize vendor/source status names to the contract statuses.
        if raw_status in {"NO_DATA", "NO_OPTIONS", "MISSING_OPTION_CONTRACT", "IBKR_UNAVAILABLE", "UNKNOWN"}:
            raw_status = "UNAVAILABLE"
        elif raw_status == "TEST_DATA":
            raw_status = "TEST_DATA"
        elif raw_status == "STALE_DATA":
            raw_status = "STALE_DATA"
        elif raw_status == "PROVIDER_OFFLINE":
            raw_status = "PROVIDER_OFFLINE"
        elif raw_status == "DATA_CONFLICT":
            raw_status = "DATA_CONFLICT"

        if raw_status == "TEST_DATA":
            missing_components.append(component_name)
            warnings.append(f"{component_name} component was TEST_DATA and not used")
            continue

        if raw_status == "STALE_DATA":
            missing_components.append(component_name)
            warnings.append(f"{component_name} component was STALE_DATA and not used")
            continue

        if raw_status == "PROVIDER_OFFLINE":
            missing_components.append(component_name)
            warnings.append(f"{component_name} component provider is offline")
            continue

        if raw_status == "UNAVAILABLE" or raw_status == "INSUFFICIENT_DATA" or raw_status == "DATA_CONFLICT":
            missing_components.append(component_name)
            warnings.append(f"{component_name} component status is {raw_status} and does not contribute a valid score")
            continue

        if isinstance(comp.get("data_quality"), str) and comp.get("data_quality").upper() == "TEST_DATA":
            missing_components.append(component_name)
            warnings.append(f"{component_name} component was TEST_DATA and not used")
            continue

        if str(comp.get("source") or "").upper() == "TEST":
            missing_components.append(component_name)
            warnings.append(f"{component_name} component source is TEST and is not production verified")
            continue

        if str(comp.get("source_quality") or "").upper() == "TEST":
            missing_components.append(component_name)
            warnings.append(f"{component_name} source_quality is TEST and component is not production verified")
            continue

        try:
            score = float(comp.get("score") or 0)
        except (TypeError, ValueError):
            missing_components.append(component_name)
            warnings.append(f"{component_name} component has invalid score and is not used")
            continue

        if comp.get("score") is None:
            missing_components.append(component_name)
            warnings.append(f"{component_name} component has no valid score")
            continue

        max_score = float(comp.get("max_score") or weight)
        if max_score <= 0:
            missing_components.append(component_name)
            warnings.append(f"{component_name} component has invalid max_score")
            continue

        confidence = float(comp.get("confidence", comp.get("data_confidence", comp.get("confidence_score", 50))) or 0)

        # Normalize score into the component's max range and map to weight.
        contribution = (score / max_score) * weight
        direction_score += contribution

        # Confidence is separate from direction. Keep a weighted average of component confidence.
        confidence_total_score += confidence * weight
        confidence_weight_total += weight

    # Clamp direction into the requested range.
    direction_score = max(-100, min(100, round(direction_score)))

    # Confidence should reflect confidence of available data; missing components reduce confidence.
    if confidence_weight_total:
        confidence_score = confidence_total_score / confidence_weight_total
    else:
        confidence_score = 0

    # Missing component penalty.
    confidence_score -= min(45, len(missing_components) * 8)

    # Force confidence into 0..100 and label it.
    confidence_score = max(0, min(100, round(confidence_score, 2)))

    if confidence_score < 50:
        confidence_label = "INSUFFICIENT_DATA"
    elif confidence_score < 70:
        confidence_label = "LOW"
    elif confidence_score < 85:
        confidence_label = "MEDIUM"
    else:
        confidence_label = "HIGH"

    # Final gate against strong decisions from weak confidence.
    decision = "WAIT"
    if confidence_score < 50:
        decision = "WAIT"
    elif confidence_score < 70:
        decision = "WATCH"
    elif direction_score >= 75:
        decision = "STRONG_BUY"
    elif direction_score >= 55:
        decision = "BUY"
    elif direction_score >= 30:
        decision = "BULLISH_WAIT"
    elif direction_score <= -75:
        decision = "STRONG_SELL"
    elif direction_score <= -55:
        decision = "SELL"
    elif direction_score <= -30:
        decision = "BEARISH_WAIT"
    else:
        decision = "WAIT"

    return {
        "symbol": symbol,
        "direction_score": direction_score,
        "decision": decision,
        "confidence_score": confidence_score,
        "confidence_label": confidence_label,
        "missing_components": missing_components,
        "warnings": warnings,
        "max_score": 100,
    }
