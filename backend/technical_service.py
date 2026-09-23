import math


def calculate_ema(values, period):
    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)
    ema = sum(values[:period]) / period

    for price in values[period:]:
        ema = (price - ema) * multiplier + ema

    return ema


def calculate_rsi(values, period=14):
    if len(values) <= period:
        return None

    gains = []
    losses = []

    for i in range(1, len(values)):
        change = values[i] - values[i - 1]

        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calculate_atr(bars, period=14):
    if len(bars) <= period:
        return None

    true_ranges = []

    for i in range(1, len(bars)):
        high = bars[i]["high"]
        low = bars[i]["low"]
        previous_close = bars[i - 1]["close"]

        true_range = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close)
        )

        true_ranges.append(true_range)

    return sum(true_ranges[-period:]) / period


def calculate_macd(values):
    if len(values) < 26:
        return None

    ema_12 = calculate_ema(values, 12)
    ema_26 = calculate_ema(values, 26)

    return ema_12 - ema_26


def calculate_technicals(bars):
    if not bars:
        return None

    closes = [bar["close"] for bar in bars]
    volumes = [bar["volume"] for bar in bars]

    ema_20 = calculate_ema(closes, 20)
    ema_50 = calculate_ema(closes, 50)
    ema_200 = calculate_ema(closes, 200)

    rsi_14 = calculate_rsi(closes, 14)
    atr_14 = calculate_atr(bars, 14)
    macd = calculate_macd(closes)

    avg_volume_20 = None

    if len(volumes) >= 20:
        avg_volume_20 = sum(volumes[-20:]) / 20

    latest_close = closes[-1]

    if ema_20 and ema_50:
        if latest_close > ema_20 > ema_50:
            trend = "BULLISH"
        elif latest_close < ema_20 < ema_50:
            trend = "BEARISH"
        else:
            trend = "NEUTRAL"
    else:
        trend = "INSUFFICIENT_DATA"

    return {
        "close": latest_close,
        "ema_20": ema_20,
        "ema_50": ema_50,
        "ema_200": ema_200,
        "rsi_14": rsi_14,
        "macd": macd,
        "atr_14": atr_14,
        "avg_volume_20": avg_volume_20,
        "trend": trend
    }