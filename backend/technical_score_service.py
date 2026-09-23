def score_technicals(technicals: dict):
    if not technicals:
        return {
            "score": 0,
            "max_score": 20,
            "bias": "NO_DATA",
            "reasons": ["No technical data available"]
        }

    score = 0
    reasons = []

    close = technicals.get("close")
    ema_20 = technicals.get("ema_20")
    ema_50 = technicals.get("ema_50")
    ema_200 = technicals.get("ema_200")
    rsi_14 = technicals.get("rsi_14")
    macd = technicals.get("macd")
    trend = technicals.get("trend")

    # ---------------------------------------------------------
    # TREND STRUCTURE: MAX 8 POINTS
    # ---------------------------------------------------------

    if (
        close is not None
        and ema_20 is not None
        and ema_50 is not None
        and ema_200 is not None
    ):
        if close > ema_20 > ema_50 > ema_200:
            score += 8
            reasons.append("Strong bullish EMA alignment")

        elif close > ema_20 > ema_50:
            score += 5
            reasons.append("Bullish short-term EMA alignment")

        elif close < ema_20 < ema_50 < ema_200:
            score -= 8
            reasons.append("Strong bearish EMA alignment")

        elif close < ema_20 < ema_50:
            score -= 5
            reasons.append("Bearish short-term EMA alignment")

        else:
            reasons.append("Mixed EMA structure")

    # ---------------------------------------------------------
    # RSI MOMENTUM: MAX 4 POINTS
    # ---------------------------------------------------------

    if rsi_14 is not None:
        if 55 <= rsi_14 <= 70:
            score += 4
            reasons.append("RSI shows healthy bullish momentum")

        elif 50 <= rsi_14 < 55:
            score += 2
            reasons.append("RSI slightly bullish")

        elif 30 <= rsi_14 < 45:
            score -= 4
            reasons.append("RSI shows bearish momentum")

        elif 45 <= rsi_14 < 50:
            score -= 2
            reasons.append("RSI slightly bearish")

        elif rsi_14 > 75:
            score -= 1
            reasons.append("RSI is overbought")

        elif rsi_14 < 25:
            score += 1
            reasons.append("RSI is oversold")

    # ---------------------------------------------------------
    # MACD MOMENTUM: MAX 4 POINTS
    # ---------------------------------------------------------

    if macd is not None:
        if macd > 0:
            score += 4
            reasons.append("MACD is positive")

        elif macd < 0:
            score -= 4
            reasons.append("MACD is negative")

    # ---------------------------------------------------------
    # TREND CONFIRMATION: MAX 4 POINTS
    # ---------------------------------------------------------

    if trend == "BULLISH":
        score += 4
        reasons.append("Trend engine confirms bullish structure")

    elif trend == "BEARISH":
        score -= 4
        reasons.append("Trend engine confirms bearish structure")

    # ---------------------------------------------------------
    # CLAMP SCORE BETWEEN -20 AND +20
    # ---------------------------------------------------------

    score = max(-20, min(20, score))

    if score >= 12:
        bias = "STRONG_BULLISH"
    elif score >= 5:
        bias = "BULLISH"
    elif score <= -12:
        bias = "STRONG_BEARISH"
    elif score <= -5:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"

    return {
        "score": score,
        "max_score": 20,
        "bias": bias,
        "reasons": reasons
    }