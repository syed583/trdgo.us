# ---------------------------------------------------------
# OPTIONS SCORE SERVICE V1
# ---------------------------------------------------------


def _safe_divide(a, b):
    if a is None or b in (None, 0):
        return None

    try:
        return float(a) / float(b)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def score_options(data: dict):
    """
    Conservative V1 options score.

    Inputs are expected to come from options analytics,
    or directly from an options intelligence payload.

    Returns a consistent object:
        symbol
        score
        max_score
        bias
        confidence
        reasons
        warnings
        data_quality
    """

    symbol = str(data.get("symbol", "UNKNOWN")).upper()
    reasons = []
    warnings = []

    if not data or data.get("status") != "OK":
        return {
            "symbol": symbol,
            "score": 0,
            "max_score": 15,
            "bias": "INSUFFICIENT_DATA",
            "confidence": 0,
            "reasons": ["No verified options data available"],
            "warnings": ["Options data unavailable or not verified"],
            "data_quality": "NO_DATA",
        }

    confidence = float(data.get("data_confidence", data.get("confidence", 0)) or 0)
    call_volume = data.get("call_volume")
    put_volume = data.get("put_volume")
    call_oi = data.get("call_open_interest")
    put_oi = data.get("put_open_interest")
    call_iv = data.get("call_iv")
    put_iv = data.get("put_iv")
    avg_iv = data.get("average_iv_percent")

    score = 0

    # Guard against insufficient options data.
    if confidence <= 0:
        return {
            "symbol": symbol,
            "score": 0,
            "max_score": 15,
            "bias": "INSUFFICIENT_DATA",
            "confidence": 0,
            "reasons": ["Insufficient options evidence"],
            "warnings": ["Options data confidence is too low for a directional signal"],
            "data_quality": "NO_DATA",
        }

    # Ratio scoring based on verified call/put volume and open interest.
    vol_ratio = _safe_divide(call_volume, put_volume)
    oi_ratio = _safe_divide(call_oi, put_oi)

    if vol_ratio is not None:
        if vol_ratio >= 1.6:
            score += 5
            reasons.append("Call volume exceeds put volume significantly")
        elif vol_ratio >= 1.2:
            score += 3
            reasons.append("Call volume modestly exceeds put volume")
        elif vol_ratio <= 0.65:
            score -= 5
            reasons.append("Put volume exceeds call volume significantly")
        elif vol_ratio <= 0.85:
            score -= 3
            reasons.append("Put volume modestly exceeds call volume")

    if oi_ratio is not None:
        if oi_ratio >= 1.6:
            score += 5
            reasons.append("Call open interest exceeds put open interest significantly")
        elif oi_ratio >= 1.2:
            score += 3
            reasons.append("Call open interest modestly exceeds put open interest")
        elif oi_ratio <= 0.65:
            score -= 5
            reasons.append("Put open interest exceeds call open interest significantly")
        elif oi_ratio <= 0.85:
            score -= 3
            reasons.append("Put open interest modestly exceeds call open interest")

    # IV structure: only use if available and not a fabricated assumption.
    if avg_iv is not None:
        if avg_iv >= 80:
            reasons.append("Average ATM IV is high")
            warnings.append("High IV may reduce confidence in direction")
        elif avg_iv <= 20:
            reasons.append("Average ATM IV is low")

    # Use delta/gamma etc if present in the payload.
    if data.get("call_delta") is not None or data.get("put_delta") is not None:
        # Conservative: prefer only if both are present and structured.
        c_delta = data.get("call_delta")
        p_delta = data.get("put_delta")
        if c_delta is not None and p_delta is not None:
            if c_delta > p_delta:
                score += 2
                reasons.append("Call delta structure is stronger than put delta")
            elif p_delta > c_delta:
                score -= 2
                reasons.append("Put delta structure is stronger than call delta")

    # Confidence gate lowers data_quality and can reduce score.
    if confidence < 50:
        score = max(score, 0)
        warnings.append("Options confidence below 50; score is conservative")
        bias = "INSUFFICIENT_DATA"
    elif score >= 12:
        bias = "STRONG_BULLISH"
    elif score >= 5:
        bias = "BULLISH"
    elif score <= -12:
        bias = "STRONG_BEARISH"
    elif score <= -5:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"

    # If no ratio evidence or very low confidence, do not force bullish/bearish.
    if not reasons and confidence < 50:
        bias = "INSUFFICIENT_DATA"

    # Ensure a conservative score range.
    score = max(-15, min(15, score))

    # Data-quality label.
    if confidence >= 85:
        data_quality = "HIGH"
    elif confidence >= 60:
        data_quality = "MEDIUM"
    elif confidence > 0:
        data_quality = "LOW"
    else:
        data_quality = "NO_DATA"

    return {
        "symbol": symbol,
        "score": score,
        "max_score": 15,
        "bias": bias,
        "confidence": round(confidence, 2),
        "reasons": reasons,
        "warnings": warnings,
        "data_quality": data_quality,
    }
