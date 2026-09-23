import asyncio

asyncio.set_event_loop(asyncio.new_event_loop())

from ib_insync import IB, Option

from options_market_service import get_option_market_context


# ---------------------------------------------------------
# IBKR SETTINGS
# ---------------------------------------------------------

IBKR_HOST = "127.0.0.1"
IBKR_PORT = 7496
IBKR_CLIENT_ID = 63


# ---------------------------------------------------------
# ENSURE EVENT LOOP
# ---------------------------------------------------------

def _ensure_event_loop():
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(
            asyncio.new_event_loop()
        )


# ---------------------------------------------------------
# RESOLVE ONE OPTION CONTRACT
# ---------------------------------------------------------

def _resolve_option_contract(
    ib,
    symbol,
    expiration,
    strike,
    right,
    trading_class
):
    """
    Ask IBKR to resolve the actual listed option contract.

    reqSecDefOptParams gives valid expiration and strike
    sets, but not every expiration/strike combination is
    guaranteed to exist.

    reqContractDetails is therefore used to verify the
    actual contract before TRDGO uses it.
    """

    option = Option(
        symbol,
        expiration,
        float(strike),
        right,
        "SMART",
        currency="USD",
        tradingClass=trading_class
    )

    details = ib.reqContractDetails(
        option
    )

    if not details:
        return None

    # Prefer an exact strike/right/trading-class match.
    for detail in details:

        contract = detail.contract

        if (
            float(contract.strike) == float(strike)
            and contract.right == right
            and contract.tradingClass == trading_class
        ):
            return contract

    return details[0].contract


# ---------------------------------------------------------
# FIND NEAREST VALID ATM PAIR
# ---------------------------------------------------------

def _find_valid_atm_pair(
    ib,
    symbol,
    expiration,
    target_price,
    strikes,
    trading_class
):
    """
    Search strikes nearest to the underlying price.

    A strike is accepted only when BOTH:
        CALL exists
        PUT exists

    This prevents TRDGO from selecting a strike that appears
    in the chain definition but has no actual listed pair.
    """

    valid_strikes = []

    for strike in strikes:

        try:
            strike = float(strike)
        except (TypeError, ValueError):
            continue

        if strike <= 0:
            continue

        valid_strikes.append(
            strike
        )

    valid_strikes.sort(
        key=lambda strike:
            abs(strike - target_price)
    )

    # Limit the search so we do not send unnecessary
    # contract-detail requests to IBKR.
    candidates = valid_strikes[:12]

    for strike in candidates:

        call = _resolve_option_contract(
            ib=ib,
            symbol=symbol,
            expiration=expiration,
            strike=strike,
            right="C",
            trading_class=trading_class
        )

        if call is None:
            continue

        put = _resolve_option_contract(
            ib=ib,
            symbol=symbol,
            expiration=expiration,
            strike=strike,
            right="P",
            trading_class=trading_class
        )

        if put is None:
            continue

        return {
            "strike": strike,
            "call": call,
            "put": put
        }

    return None


# ---------------------------------------------------------
# CONTRACT TO DICTIONARY
# ---------------------------------------------------------

def _contract_to_dict(contract):
    return {
        "con_id":
            contract.conId,

        "symbol":
            contract.symbol,

        "expiration":
            contract.lastTradeDateOrContractMonth,

        "strike":
            contract.strike,

        "right":
            contract.right,

        "exchange":
            contract.exchange,

        "currency":
            contract.currency,

        "local_symbol":
            contract.localSymbol,

        "trading_class":
            contract.tradingClass,

        "multiplier":
            contract.multiplier
    }


# ---------------------------------------------------------
# GET ATM CALL + PUT CONTRACTS
# ---------------------------------------------------------

def get_atm_option_contracts(symbol: str):
    """
    Find a verified ATM CALL and PUT pair.

    Process:

        underlying price
              ↓
        SMART chain
              ↓
        nearest expiration
              ↓
        strikes ordered by distance from price
              ↓
        IBKR contract-detail verification
              ↓
        first strike with valid CALL + PUT
    """

    _ensure_event_loop()

    symbol = symbol.upper()

    # -----------------------------------------------------
    # GET OPTION MARKET CONTEXT
    # -----------------------------------------------------

    context = get_option_market_context(
        symbol
    )

    if context.get("status") != "OK":
        return {
            "symbol": symbol,
            "status": context.get(
                "status",
                "MARKET_CONTEXT_ERROR"
            )
        }

    underlying_price = context.get(
        "underlying_price"
    )

    expiration = context.get(
        "nearest_expiration"
    )

    trading_class = context.get(
        "trading_class"
    )

    if (
        underlying_price is None
        or not expiration
        or not trading_class
    ):
        return {
            "symbol": symbol,
            "status": "INVALID_OPTION_CONTEXT"
        }

    # -----------------------------------------------------
    # GET FULL SMART CHAIN
    # -----------------------------------------------------

    from options_data_service import (
        get_smart_option_chain
    )

    chain = get_smart_option_chain(
        symbol
    )

    if chain.get("status") != "OK":
        return {
            "symbol": symbol,
            "status": chain.get(
                "status",
                "SMART_CHAIN_ERROR"
            )
        }

    strikes = chain.get(
        "strikes",
        []
    )

    # -----------------------------------------------------
    # CONNECT TO IBKR
    # -----------------------------------------------------

    ib = IB()

    try:

        ib.connect(
            IBKR_HOST,
            IBKR_PORT,
            clientId=IBKR_CLIENT_ID,
            readonly=True
        )

        # -------------------------------------------------
        # FIND ACTUAL LISTED ATM PAIR
        # -------------------------------------------------

        pair = _find_valid_atm_pair(
            ib=ib,
            symbol=symbol,
            expiration=expiration,
            target_price=float(
                underlying_price
            ),
            strikes=strikes,
            trading_class=trading_class
        )

        if pair is None:
            return {
                "symbol": symbol,
                "status":
                    "NO_VALID_ATM_OPTION_PAIR",

                "underlying_price":
                    underlying_price,

                "requested_expiration":
                    expiration
            }

        call = pair["call"]
        put = pair["put"]

        selected_strike = pair[
            "strike"
        ]

        # -------------------------------------------------
        # RETURN VERIFIED CONTRACTS
        # -------------------------------------------------

        return {
            "symbol":
                symbol,

            "status":
                "OK",

            "underlying_price":
                underlying_price,

            "requested_expiration":
                expiration,

            "selected_strike":
                selected_strike,

            "distance_from_underlying":
                round(
                    abs(
                        float(selected_strike)
                        - float(underlying_price)
                    ),
                    4
                ),

            "exchange":
                "SMART",

            "trading_class":
                trading_class,

            "call":
                _contract_to_dict(
                    call
                ),

            "put":
                _contract_to_dict(
                    put
                )
        }

    finally:

        if ib.isConnected():
            ib.disconnect()