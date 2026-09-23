import asyncio

asyncio.set_event_loop(asyncio.new_event_loop())

from ib_insync import IB, Stock


# ---------------------------------------------------------
# IBKR SETTINGS
# ---------------------------------------------------------

IBKR_HOST = "127.0.0.1"
IBKR_PORT = 7496
IBKR_CLIENT_ID = 61


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
# GET ALL OPTION CHAIN DEFINITIONS
# ---------------------------------------------------------

def get_option_chain(symbol: str):
    """
    Fetch all option chain definitions from IBKR.

    Returns:
        - underlying contract information
        - exchanges
        - trading classes
        - expirations
        - strikes
        - multiplier

    This function does NOT yet calculate:
        - IV
        - Greeks
        - Open Interest
        - Put/Call Ratio
        - Implied Move
        - GEX
    """

    _ensure_event_loop()

    ib = IB()

    try:

        # -------------------------------------------------
        # CONNECT TO IBKR
        # -------------------------------------------------

        ib.connect(
            IBKR_HOST,
            IBKR_PORT,
            clientId=IBKR_CLIENT_ID,
            readonly=True
        )

        # -------------------------------------------------
        # CREATE UNDERLYING STOCK CONTRACT
        # -------------------------------------------------

        stock = Stock(
            symbol.upper(),
            "SMART",
            "USD"
        )

        # -------------------------------------------------
        # QUALIFY STOCK CONTRACT
        # -------------------------------------------------

        qualified = ib.qualifyContracts(
            stock
        )

        if not qualified:
            raise ValueError(
                f"Unable to qualify stock contract "
                f"for {symbol.upper()}"
            )

        stock = qualified[0]

        # -------------------------------------------------
        # REQUEST OPTION CHAIN PARAMETERS
        # -------------------------------------------------

        chains = ib.reqSecDefOptParams(
            stock.symbol,
            "",
            stock.secType,
            stock.conId
        )

        # -------------------------------------------------
        # NO OPTIONS FOUND
        # -------------------------------------------------

        if not chains:
            return {
                "symbol": symbol.upper(),
                "status": "NO_OPTIONS",
                "underlying_con_id": stock.conId,
                "chains": []
            }

        # -------------------------------------------------
        # FORMAT ALL CHAINS
        # -------------------------------------------------

        results = []

        for chain in chains:

            expirations = sorted(
                list(chain.expirations)
            )

            strikes = sorted(
                list(chain.strikes)
            )

            results.append({
                "exchange":
                    chain.exchange,

                "underlying_con_id":
                    chain.underlyingConId,

                "trading_class":
                    chain.tradingClass,

                "multiplier":
                    chain.multiplier,

                "expiration_count":
                    len(expirations),

                "strike_count":
                    len(strikes),

                "expirations":
                    expirations,

                "strikes":
                    strikes
            })

        # -------------------------------------------------
        # RETURN ALL CHAINS
        # -------------------------------------------------

        return {
            "symbol":
                symbol.upper(),

            "status":
                "OK",

            "underlying_con_id":
                stock.conId,

            "primary_exchange":
                stock.primaryExchange,

            "currency":
                stock.currency,

            "chain_count":
                len(results),

            "chains":
                results
        }

    finally:

        if ib.isConnected():
            ib.disconnect()


# ---------------------------------------------------------
# GET STANDARD SMART OPTION CHAIN
# ---------------------------------------------------------

def get_smart_option_chain(symbol: str):
    """
    Return the standard SMART option chain
    for the requested stock.

    Example for NVDA:

        exchange = SMART
        trading_class = NVDA

    This avoids selecting special/adjusted
    trading classes such as 2NVDA.
    """

    symbol = symbol.upper()

    # -----------------------------------------------------
    # GET ALL AVAILABLE CHAINS
    # -----------------------------------------------------

    data = get_option_chain(
        symbol
    )

    # -----------------------------------------------------
    # CHECK REQUEST STATUS
    # -----------------------------------------------------

    if data.get("status") != "OK":
        return data

    # -----------------------------------------------------
    # FIND STANDARD SMART CHAIN
    # -----------------------------------------------------

    for chain in data.get(
        "chains",
        []
    ):

        exchange = chain.get(
            "exchange"
        )

        trading_class = chain.get(
            "trading_class"
        )

        if (
            exchange == "SMART"
            and
            trading_class == symbol
        ):

            return {
                "symbol":
                    symbol,

                "status":
                    "OK",

                "underlying_con_id":
                    data.get(
                        "underlying_con_id"
                    ),

                "exchange":
                    exchange,

                "trading_class":
                    trading_class,

                "multiplier":
                    chain.get(
                        "multiplier"
                    ),

                "expiration_count":
                    chain.get(
                        "expiration_count"
                    ),

                "strike_count":
                    chain.get(
                        "strike_count"
                    ),

                "expirations":
                    chain.get(
                        "expirations"
                    ),

                "strikes":
                    chain.get(
                        "strikes"
                    )
            }

    # -----------------------------------------------------
    # SMART STANDARD CHAIN NOT FOUND
    # -----------------------------------------------------

    return {
        "symbol":
            symbol,

        "status":
            "SMART_CHAIN_NOT_FOUND",

        "underlying_con_id":
            data.get(
                "underlying_con_id"
            )
    }