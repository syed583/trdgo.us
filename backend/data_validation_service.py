# ---------------------------------------------------------
# DATA VALIDATION SERVICE
# ---------------------------------------------------------

import math
from datetime import datetime, timezone


def _clean_number(value):
    if value is None:
        return None

    try:
        value = float(value)
    except (TypeError, ValueError):
        return None

    if math.isnan(value) or math.isinf(value):
        return None

    return value


def validate_symbol_data(symbol: str, payload: dict):
    """
    Validate a symbol payload for missing values, stale values,
    TEST sources, numeric anomalies, and IBKR/SEC availability signals.

    Returns a structured validation object with warnings.
    """

    symbol = str(symbol or "UNKNOWN").upper()
    warnings = []
    missing_fields = []
    test_data = False

    if not payload:
        warnings.append("No data payload received")
        return {
            "symbol": symbol,
            "warnings": warnings,
            "status": "NO_DATA",
            "missing_fields": ["payload"],
            "test_data": False,
            "ibkr_available": None,
            "sec_available": None,
            "source_quality": "UNKNOWN",
        }

    source = payload.get("source") or payload.get("data_source") or payload.get("source_quality")
    if source and str(source).upper() == "TEST":
        warnings.append("TEST record detected; data must not be treated as production verified")
        test_data = True

    # Missing field detection.
    for field in ("status", "symbol", "underlying_price", "call", "put"):
        if field not in payload or payload.get(field) in (None, ""):
            missing_fields.append(field)

    if missing_fields:
        warnings.append("Missing required fields: " + ", ".join(missing_fields))

    # Numeric safety.
    if payload.get("underlying_price") is not None:
        price = _clean_number(payload.get("underlying_price"))
        if price is not None and price <= 0:
            warnings.append("Underlying price is not positive")

    # Provider flags.
    status = str(payload.get("provider_status")
                 or payload.get("status") or "").upper()
    if status in ("PROVIDER_OFFLINE", "DATA_UNAVAILABLE",
                  "PROVIDER_NOT_CONFIGURED"):
        warnings.append(f"Market data unavailable ({status})")

    # Time/source freshness example.
    timestamp = payload.get("timestamp") or payload.get("updated_at")
    if timestamp:
        try:
            dt = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
            if age_hours > 24:
                warnings.append("Data appears stale")
        except Exception:
            warnings.append("Timestamp could not be parsed")

    return {
        "symbol": symbol,
        "warnings": warnings,
        "status": payload.get("status", "UNKNOWN"),
        "missing_fields": missing_fields,
        "test_data": test_data,
        "ibkr_available": payload.get("ibkr_available", None),
        "sec_available": payload.get("sec_available", None),
        "source_quality": "TEST" if test_data else "VERIFIED",
    }
