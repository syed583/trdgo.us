import asyncio
from datetime import datetime

asyncio.set_event_loop(asyncio.new_event_loop())

from ib_insync import IB, Stock

from options_data_service import get_smart_option_chain


# ---------------------------------------------------------
# IBKR SETTINGS
# ---------------------------------------------------------

IBKR_HOST = "127.0.0.1"
IBKR_PORT = 7496
IBKR_CLIENT_ID = 62


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
# GET UNDERLYING PRICE
# ---------------------------------------------------------

def _get_underlying_price(
    ib,
    symbol: str
):
    """
    Get a usable underlying stock price from IBKR.

    Priority:
        last
        marketPrice
        midpoint
        close
    """

    stock = Stock(
        symbol.upper(),
        "SMART",
        "USD"
    )

    qualified = ib.qualifyContracts(
        stock
    )

    if not qualified:
        raise ValueError(
            f"Unable to qualify stock "
            f"contract for {symbol.upper()}"
        )

    stock = qualified[0]

    ticker = ib.reqMktData(
        stock,
        "",
        False,
        False
    )

    ib.sleep(2)

    price = None
    price_source = None

    # -----------------------------------------------------
    # LAST PRICE
    # -----------------------------------------------------

    if (
        ticker.last is not None
        and ticker.last == ticker.last
        and ticker.last > 0
    ):
        price = float(
            ticker.last
        )

        price_source = "LAST"

    # -----------------------------------------------------
    # MARKET PRICE
    # -----------------------------------------------------

    if price is None:

        market_price = ticker.marketPrice()

        if (
            market_price is not None
            and market_price == market_price
            and market_price > 0
        ):
            price = float(
                market_price
            )

            price_source = "MARKET_PRICE"

    # -----------------------------------------------------
    # MIDPOINT
    # -----------------------------------------------------

    if price is None:

        if (
            ticker.bid is not None
            and ticker.ask is not None
            and ticker.bid == ticker.bid
            and ticker.ask == ticker.ask
            and ticker.bid > 0
            and ticker.ask > 0
        ):

            price = (
                float(ticker.bid)
                + float(ticker.ask)
            ) / 2

            price_source = "MIDPOINT"

    # -----------------------------------------------------
    # PREVIOUS CLOSE
    # -----------------------------------------------------

    if price is None:

        if (
            ticker.close is not None
            and ticker.close == ticker.close
            and ticker.close > 0
        ):

            price = float(
                ticker.close
            )

            price_source = "CLOSE"

    ib.cancelMktData(
        stock
    )

    return {
        "contract": stock,
        "price": price,
        "price_source": price_source,
        "bid": (
            float(ticker.bid)
            if (
                ticker.bid is not None
                and ticker.bid == ticker.bid
            )
            else None
        ),
        "ask": (
            float(ticker.ask)
            if (
                ticker.ask is not None
                and ticker.ask == ticker.ask
            )
            else None
        ),
        "last": (
            float(ticker.last)
            if (
                ticker.last is not None
                and ticker.last == ticker.last
            )
            else None
        ),
        "close": (
            float(ticker.close)
            if (
                ticker.close is not None
                and ticker.close == ticker.close
            )
            else None
        )
    }


# ---------------------------------------------------------
# FIND NEAREST VALID EXPIRATION
# ---------------------------------------------------------

def _find_nearest_expiration(
    expirations
):
    """
    Find the nearest expiration that has
    not already expired.
    """

    today = datetime.now().date()

    valid = []

    for expiration in expirations:

        try:
            expiration_date = datetime.strptime(
                expiration,
                "%Y%m%d"
            ).date()

        except ValueError:
            continue

        if expiration_date >= today:
            valid.append(
                (
                    expiration_date,
                    expiration
                )
            )

    if not valid:
        return None

    valid.sort(
        key=lambda item: item[0]
    )

    return valid[0][1]


# ---------------------------------------------------------
# FIND ATM STRIKE
# ---------------------------------------------------------

def _find_atm_strike(
    strikes,
    stock_price
):
    """
    Find strike closest to underlying price.
    """

    if stock_price is None:
        return None

    valid_strikes = [
        float(strike)
        for strike in strikes
        if strike is not None
        and float(strike) > 0
    ]

    if not valid_strikes:
        return None

    return min(
        valid_strikes,
        key=lambda strike:
            abs(
                strike
                - stock_price
            )
    )


# ---------------------------------------------------------
# GET OPTION MARKET CONTEXT
# ---------------------------------------------------------

def get_option_market_context(
    symbol: str
):
    """
    Build the basic option-market context.

    Returns:
        underlying price
        bid
        ask
        last
        close
        SMART chain
        nearest expiration
        ATM strike

    This does NOT yet request individual
    option quotes or Greeks.
    """

    _ensure_event_loop()

    symbol = symbol.upper()

    # -----------------------------------------------------
    # GET SMART CHAIN
    # -----------------------------------------------------

    chain = get_smart_option_chain(
        symbol
    )

    if chain.get("status") != "OK":

        return {
            "symbol": symbol,
            "status":
                chain.get(
                    "status",
                    "CHAIN_ERROR"
                )
        }

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
        # UNDERLYING PRICE
        # -------------------------------------------------

        market = _get_underlying_price(
            ib,
            symbol
        )

        stock_price = market.get(
            "price"
        )

        if stock_price is None:

            return {
                "symbol": symbol,
                "status":
                    "NO_UNDERLYING_PRICE"
            }

        # -------------------------------------------------
        # NEAREST EXPIRATION
        # -------------------------------------------------

        nearest_expiration = (
            _find_nearest_expiration(
                chain.get(
                    "expirations",
                    []
                )
            )
        )

        if nearest_expiration is None:

            return {
                "symbol": symbol,
                "status":
                    "NO_VALID_EXPIRATION"
            }

        # -------------------------------------------------
        # ATM STRIKE
        # -------------------------------------------------

        atm_strike = (
            _find_atm_strike(
                chain.get(
                    "strikes",
                    []
                ),
                stock_price
            )
        )

        if atm_strike is None:

            return {
                "symbol": symbol,
                "status":
                    "NO_VALID_STRIKE"
            }

        # -------------------------------------------------
        # RETURN
        # -------------------------------------------------

        return {
            "symbol": symbol,

            "status": "OK",

            "underlying_price":
                round(
                    stock_price,
                    4
                ),

            "price_source":
                market.get(
                    "price_source"
                ),

            "bid":
                market.get(
                    "bid"
                ),

            "ask":
                market.get(
                    "ask"
                ),

            "last":
                market.get(
                    "last"
                ),

            "close":
                market.get(
                    "close"
                ),

            "exchange":
                chain.get(
                    "exchange"
                ),

            "trading_class":
                chain.get(
                    "trading_class"
                ),

            "multiplier":
                chain.get(
                    "multiplier"
                ),

            "nearest_expiration":
                nearest_expiration,

            "atm_strike":
                atm_strike,

            "expiration_count":
                chain.get(
                    "expiration_count"
                ),

            "strike_count":
                chain.get(
                    "strike_count"
                )
        }

    finally:

        if ib.isConnected():
            ib.disconnect()