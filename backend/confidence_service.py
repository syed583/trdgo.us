# ---------------------------------------------------------
# CONFIDENCE ENGINE V1
# ---------------------------------------------------------


def calculate_confidence(payload: dict):
    """
    Conservative confidence calculation.

    Considers:
        data completeness
        data freshness
        source quality
        cross-component agreement
        estimate-history availability
        options availability

    Returns an object with confidence_score 0-100,
    confidence_label, missing_components, warnings.
    """

    if not payload:
        return {
            "confidence_score": 0,
            "confidence_label": "INSUFFICIENT_DATA",
            "missing_components": ["all"],
            "warnings": ["No scoring payload available"],
            "test_data": False,
            "stale_components": [],
            "source_quality": "UNKNOWN",
        }

    components = payload.get("components") or {}
    if not isinstance(components, dict):
        components = {}

    missing = []
    warnings = []
    stale_components = []
    source_quality = "UNKNOWN"

    # Start at full confidence and subtract evidence penalties.
    confidence = 100

    # Required component list.
    required = [
        "fundamentals",
        "estimates",
        "technicals",
        "earnings_history",
        "options",
        "market_environment",
    ]

    for comp_name in required:
        comp = components.get(comp_name)
        if not comp:
            missing.append(comp_name)
            confidence -= 12
            warnings.append(f"Missing component: {comp_name}")
            continue

        comp_status = str(comp.get("status") or comp.get("data_quality") or "OK").upper()
        if comp_status in {"NO_DATA", "NO_OPTIONS", "MISSING_OPTION_CONTRACT", "PROVIDER_OFFLINE",
                           "DATA_UNAVAILABLE", "UNKNOWN"}:
            comp_status = "UNAVAILABLE"

        if comp_status == "TEST_DATA":
            missing.append(comp_name)
            confidence -= 30
            warnings.append(f"{comp_name} component is TEST_DATA and cannot produce real evidence")
            source_quality = "TEST"
            continue

        if str(comp.get("source") or "").upper() == "TEST":
            missing.append(comp_name)
            confidence -= 30
            warnings.append(f"{comp_name} component source is TEST")
            source_quality = "TEST"
            continue

        if str(comp.get("source_quality") or "").upper() == "TEST":
            missing.append(comp_name)
            confidence -= 30
            warnings.append(f"{comp_name} source_quality is TEST")
            source_quality = "TEST"
            continue

        if comp_status == "STALE_DATA":
            missing.append(comp_name)
            confidence -= 20
            stale_components.append(comp_name)
            warnings.append(f"{comp_name} component is STALE_DATA")
            continue

        if comp_status == "PROVIDER_OFFLINE":
            missing.append(comp_name)
            confidence -= 25
            warnings.append(f"{comp_name} component provider is offline")
            continue

        if comp_status == "UNAVAILABLE":
            missing.append(comp_name)
            confidence -= 20
            warnings.append(f"{comp_name} component unavailable")
            continue

        if comp_status == "INSUFFICIENT_DATA":
            missing.append(comp_name)
            confidence -= 20
            warnings.append(f"{comp_name} component insufficient data")
            continue

        if comp_status == "DATA_CONFLICT":
            missing.append(comp_name)
            confidence -= 20
            warnings.append(f"{comp_name} component data conflict")
            continue

        # Component confidence low means lower overall confidence.
        comp_conf = comp.get("confidence")
        if comp_conf is None:
            comp_conf = comp.get("data_confidence")
        if comp_conf is not None:
            try:
                conf = float(comp_conf)
            except (TypeError, ValueError):
                conf = 0
            if conf < 50:
                confidence -= 8
                warnings.append(f"Low component confidence for {comp_name}: {conf}")

        # Data freshness and staleness signals.
        if comp.get("stale"):
            stale_components.append(comp_name)
            confidence -= 8
            warnings.append(f"Stale component data: {comp_name}")

    # IBKR/SEC impact.
    if payload.get("ibkr_unavailable") or payload.get("provider_status", {}).get("ibkr") == "PROVIDER_OFFLINE":
        confidence -= 20
        warnings.append("IBKR unavailable; live option data cannot be verified")

    if payload.get("sec_unavailable"):
        confidence -= 12
        warnings.append("SEC data unavailable")

    # Test data signal in payload.
    test_data = bool(payload.get("test_data") or source_quality == "TEST")
    if test_data:
        confidence -= 10
        warnings.append("TEST data detected and treated as non-production evidence")

    # Option data missing signal.
    options = components.get("options")
    if options and options.get("status") in ("NO_OPTIONS", "PROVIDER_OFFLINE", "DATA_UNAVAILABLE",
                                       "MISSING_OPTION_CONTRACT"):
        confidence -= 20
        warnings.append("Options data unavailable or missing")

    confidence = max(0, min(100, round(confidence, 2)))

    if confidence < 50:
        confidence_label = "INSUFFICIENT_DATA"
    elif confidence < 70:
        confidence_label = "LOW"
    elif confidence < 85:
        confidence_label = "MEDIUM"
    else:
        confidence_label = "HIGH"

    return {
        "confidence_score": confidence,
        "confidence_label": confidence_label,
        "missing_components": missing,
        "warnings": warnings,
        "test_data": test_data,
        "stale_components": stale_components,
        "source_quality": source_quality,
    }
