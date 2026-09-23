from market_data_service import get_historical_bars
from technical_service import calculate_technicals


# ---------------------------------------------------------
# MARKET ENVIRONMENT SCORE
# ---------------------------------------------------------

def score_market_environment():
    """
    TRDGO Market Environment Score V1

    Score range:
        -5 to +5

    Uses:
        - SPY trend
        - QQQ trend
        - SPY position vs EMA20/EMA50
        - QQQ position vs EMA20/EMA50
    """

    score = 0
    reasons = []

    # -----------------------------------------------------
    # LOAD SPY
    # -----------------------------------------------------

    spy_bars = get_historical_bars("SPY")
    spy = calculate_technicals(spy_bars)

    # -----------------------------------------------------
    # LOAD QQQ
    # -----------------------------------------------------

    qqq_bars = get_historical_bars("QQQ")
    qqq = calculate_technicals(qqq_bars)

    # calculate_technicals returns None when there are no bars. Without this
    # guard every spy.get(...) below raises AttributeError and the endpoint
    # answers 500 instead of saying which provider is missing.
    if spy is None or qqq is None:
        missing = [name for name, value in (("SPY", spy), ("QQQ", qqq))
                   if value is None]
        return {
            "score": None,
            "max_score": 5,
            "bias": "INSUFFICIENT_DATA",
            "status": "PROVIDER_OFFLINE",
            "reasons": [
                "No daily bars for " + ", ".join(missing)
                + " — market environment needs a price feed (IBKR or "
                  "Alpha Vantage)."
            ],
        }

    # -----------------------------------------------------
    # SPY TREND
    # MAX +/- 2
    # -----------------------------------------------------

    spy_trend = spy.get("trend")

    if spy_trend == "BULLISH":
        score += 2
        reasons.append(
            "SPY trend is bullish"
        )

    elif spy_trend == "BEARISH":
        score -= 2
        reasons.append(
            "SPY trend is bearish"
        )

    else:
        reasons.append(
            "SPY trend is neutral"
        )

    # -----------------------------------------------------
    # QQQ TREND
    # MAX +/- 2
    # -----------------------------------------------------

    qqq_trend = qqq.get("trend")

    if qqq_trend == "BULLISH":
        score += 2
        reasons.append(
            "QQQ trend is bullish"
        )

    elif qqq_trend == "BEARISH":
        score -= 2
        reasons.append(
            "QQQ trend is bearish"
        )

    else:
        reasons.append(
            "QQQ trend is neutral"
        )

    # -----------------------------------------------------
    # MARKET CONFIRMATION
    # MAX +/- 1
    # -----------------------------------------------------

    spy_close = spy.get("close")
    spy_ema20 = spy.get("ema_20")
    spy_ema50 = spy.get("ema_50")

    qqq_close = qqq.get("close")
    qqq_ema20 = qqq.get("ema_20")
    qqq_ema50 = qqq.get("ema_50")

    bullish_confirmation = (
        spy_close is not None
        and spy_ema20 is not None
        and spy_ema50 is not None
        and qqq_close is not None
        and qqq_ema20 is not None
        and qqq_ema50 is not None
        and spy_close > spy_ema20 > spy_ema50
        and qqq_close > qqq_ema20 > qqq_ema50
    )

    bearish_confirmation = (
        spy_close is not None
        and spy_ema20 is not None
        and spy_ema50 is not None
        and qqq_close is not None
        and qqq_ema20 is not None
        and qqq_ema50 is not None
        and spy_close < spy_ema20 < spy_ema50
        and qqq_close < qqq_ema20 < qqq_ema50
    )

    if bullish_confirmation:
        score += 1
        reasons.append(
            "SPY and QQQ both confirm bullish market structure"
        )

    elif bearish_confirmation:
        score -= 1
        reasons.append(
            "SPY and QQQ both confirm bearish market structure"
        )

    else:
        reasons.append(
            "SPY and QQQ do not fully confirm the same market structure"
        )

    # -----------------------------------------------------
    # CLAMP SCORE
    # -----------------------------------------------------

    score = max(
        -5,
        min(5, score)
    )

    # -----------------------------------------------------
    # BIAS
    # -----------------------------------------------------

    if score >= 4:
        bias = "STRONG_BULLISH"

    elif score >= 2:
        bias = "BULLISH"

    elif score <= -4:
        bias = "STRONG_BEARISH"

    elif score <= -2:
        bias = "BEARISH"

    else:
        bias = "NEUTRAL"

    # -----------------------------------------------------
    # RETURN
    # -----------------------------------------------------

    return {
        "score": score,
        "max_score": 5,
        "bias": bias,
        "reasons": reasons,
        "spy": {
            "close": spy.get("close"),
            "ema_20": spy.get("ema_20"),
            "ema_50": spy.get("ema_50"),
            "trend": spy.get("trend")
        },
        "qqq": {
            "close": qqq.get("close"),
            "ema_20": qqq.get("ema_20"),
            "ema_50": qqq.get("ema_50"),
            "trend": qqq.get("trend")
        }
    }