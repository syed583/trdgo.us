from option_quote_service import get_atm_option_quotes


# ---------------------------------------------------------
# SAFE DIVIDE
# ---------------------------------------------------------

def _safe_divide(a, b):
    if a is None or b in (None, 0):
        return None

    try:
        return float(a) / float(b)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


# ---------------------------------------------------------
# PICK USABLE OPTION PRICE
# ---------------------------------------------------------

def _usable_option_price(quote: dict):
    """
    Preferred price order:

        mid
        last
        model option price
    """

    if not quote:
        return None

    for key in (
        "mid",
        "last",
        "model_option_price"
    ):

        value = quote.get(key)

        if value is None:
            continue

        try:
            value = float(value)
        except (TypeError, ValueError):
            continue

        if value >= 0:
            return value

    return None


# ---------------------------------------------------------
# OPTIONS ANALYTICS
# ---------------------------------------------------------

def calculate_options_analytics(
    symbol: str
):
    """
    Build core options analytics from verified
    ATM CALL and PUT quote data.

    Calculates:

        ATM call price
        ATM put price
        ATM straddle
        expected move $
        expected move %
        lower expected range
        upper expected range
        average IV
        call/put volume ratio
        call/put OI ratio
        data confidence

    NOTE:
    This is NOT yet the final Options Score /15.
    """

    symbol = symbol.upper()

    data = get_atm_option_quotes(
        symbol
    )

    if data.get("status") != "OK":
        return {
            "symbol":
                symbol,

            "status":
                data.get(
                    "status",
                    "OPTION_QUOTE_ERROR"
                )
        }

    underlying_price = data.get(
        "underlying_price"
    )

    call = data.get(
        "call",
        {}
    )

    put = data.get(
        "put",
        {}
    )

    # -----------------------------------------------------
    # OPTION PRICES
    # -----------------------------------------------------

    call_price = _usable_option_price(
        call
    )

    put_price = _usable_option_price(
        put
    )

    straddle = None

    if (
        call_price is not None
        and put_price is not None
    ):
        straddle = (
            call_price
            + put_price
        )

    # -----------------------------------------------------
    # EXPECTED MOVE
    # -----------------------------------------------------

    expected_move_percent = None
    lower_range = None
    upper_range = None

    if (
        straddle is not None
        and underlying_price is not None
        and underlying_price > 0
    ):

        expected_move_percent = (
            straddle
            / underlying_price
        ) * 100

        lower_range = (
            underlying_price
            - straddle
        )

        upper_range = (
            underlying_price
            + straddle
        )

    # -----------------------------------------------------
    # IMPLIED VOLATILITY
    # -----------------------------------------------------

    call_iv = call.get(
        "implied_volatility"
    )

    put_iv = put.get(
        "implied_volatility"
    )

    iv_values = []

    if call_iv is not None:
        iv_values.append(
            float(call_iv)
        )

    if put_iv is not None:
        iv_values.append(
            float(put_iv)
        )

    average_iv = None

    if iv_values:
        average_iv = (
            sum(iv_values)
            / len(iv_values)
        )

    # IBKR commonly returns IV as decimal:
    # 0.62 = 62%
    average_iv_percent = None

    if average_iv is not None:
        average_iv_percent = (
            average_iv
            * 100
        )

    # -----------------------------------------------------
    # VOLUME RATIO
    # -----------------------------------------------------

    call_volume = call.get(
        "volume"
    )

    put_volume = put.get(
        "volume"
    )

    call_put_volume_ratio = (
        _safe_divide(
            call_volume,
            put_volume
        )
    )

    # -----------------------------------------------------
    # OPEN INTEREST RATIO
    # -----------------------------------------------------

    call_oi = call.get(
        "open_interest"
    )

    put_oi = put.get(
        "open_interest"
    )

    call_put_oi_ratio = (
        _safe_divide(
            call_oi,
            put_oi
        )
    )

    # -----------------------------------------------------
    # DATA CONFIDENCE
    # -----------------------------------------------------

    confidence_points = 0
    confidence_total = 7

    if underlying_price is not None:
        confidence_points += 1

    if call_price is not None:
        confidence_points += 1

    if put_price is not None:
        confidence_points += 1

    if call_iv is not None:
        confidence_points += 1

    if put_iv is not None:
        confidence_points += 1

    if (
        call_volume is not None
        and put_volume is not None
    ):
        confidence_points += 1

    if (
        call_oi is not None
        and put_oi is not None
    ):
        confidence_points += 1

    confidence = round(
        (
            confidence_points
            / confidence_total
        ) * 100,
        2
    )

    if confidence >= 85:
        data_quality = "HIGH"

    elif confidence >= 60:
        data_quality = "MEDIUM"

    elif confidence > 0:
        data_quality = "LOW"

    else:
        data_quality = "NO_DATA"

    # -----------------------------------------------------
    # RETURN
    # -----------------------------------------------------

    return {
        "symbol":
            symbol,

        "status":
            "OK",

        "underlying_price":
            underlying_price,

        "expiration":
            data.get(
                "requested_expiration"
            ),

        "strike":
            data.get(
                "selected_strike"
            ),

        "call_price":
            round(
                call_price,
                4
            )
            if call_price is not None
            else None,

        "put_price":
            round(
                put_price,
                4
            )
            if put_price is not None
            else None,

        "atm_straddle":
            round(
                straddle,
                4
            )
            if straddle is not None
            else None,

        "expected_move_dollars":
            round(
                straddle,
                4
            )
            if straddle is not None
            else None,

        "expected_move_percent":
            round(
                expected_move_percent,
                2
            )
            if expected_move_percent is not None
            else None,

        "expected_range": {
            "lower":
                round(
                    lower_range,
                    4
                )
                if lower_range is not None
                else None,

            "upper":
                round(
                    upper_range,
                    4
                )
                if upper_range is not None
                else None
        },

        "call_iv":
            call_iv,

        "put_iv":
            put_iv,

        "average_iv_percent":
            round(
                average_iv_percent,
                2
            )
            if average_iv_percent is not None
            else None,

        "call_volume":
            call_volume,

        "put_volume":
            put_volume,

        "call_put_volume_ratio":
            round(
                call_put_volume_ratio,
                4
            )
            if call_put_volume_ratio is not None
            else None,

        "call_open_interest":
            call_oi,

        "put_open_interest":
            put_oi,

        "call_put_oi_ratio":
            round(
                call_put_oi_ratio,
                4
            )
            if call_put_oi_ratio is not None
            else None,

        "data_confidence":
            confidence,

        "data_quality":
            data_quality
    }