import asyncio
import math

asyncio.set_event_loop(asyncio.new_event_loop())

from ib_insync import IB, Option

from option_contract_service import get_atm_option_contracts


# ---------------------------------------------------------
# IBKR SETTINGS
# ---------------------------------------------------------

IBKR_HOST = "127.0.0.1"
IBKR_PORT = 7496
IBKR_CLIENT_ID = 66


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
# SAFE NUMBER
# ---------------------------------------------------------

def _clean_number(value):
    """
    Convert IBKR numeric values into normal floats.

    Returns None for:
        None
        NaN
        invalid values
    """

    if value is None:
        return None

    try:
        value = float(value)
    except (TypeError, ValueError):
        return None

    if math.isnan(value):
        return None

    return value


# ---------------------------------------------------------
# BUILD OPTION CONTRACT FROM VERIFIED DATA
# ---------------------------------------------------------

def _build_option_contract(data):
    return Option(
        symbol=data["symbol"],
        lastTradeDateOrContractMonth=data["expiration"],
        strike=float(data["strike"]),
        right=data["right"],
        exchange="SMART",
        currency=data.get("currency", "USD"),
        multiplier=data.get("multiplier", "100"),
        tradingClass=data.get("trading_class")
    )


# ---------------------------------------------------------
# EXTRACT OPTION QUOTE
# ---------------------------------------------------------

def _extract_quote(ticker):
    """
    Extract option quote + model Greeks.

    IBKR modelGreeks may contain:
        impliedVol
        delta
        gamma
        vega
        theta
        optPrice
        undPrice
    """

    model = ticker.modelGreeks

    quote = {
        "bid":
            _clean_number(
                ticker.bid
            ),

        "ask":
            _clean_number(
                ticker.ask
            ),

        "last":
            _clean_number(
                ticker.last
            ),

        "close":
            _clean_number(
                ticker.close
            ),

        "volume":
            _clean_number(
                ticker.volume
            ),

        "open_interest":
            _clean_number(
                getattr(
                    ticker,
                    "openInterest",
                    None
                )
            ),

        "implied_volatility":
            None,

        "delta":
            None,

        "gamma":
            None,

        "vega":
            None,

        "theta":
            None,

        "model_option_price":
            None,

        "model_underlying_price":
            None
    }

    if model is not None:

        quote["implied_volatility"] = (
            _clean_number(
                model.impliedVol
            )
        )

        quote["delta"] = (
            _clean_number(
                model.delta
            )
        )

        quote["gamma"] = (
            _clean_number(
                model.gamma
            )
        )

        quote["vega"] = (
            _clean_number(
                model.vega
            )
        )

        quote["theta"] = (
            _clean_number(
                model.theta
            )
        )

        quote["model_option_price"] = (
            _clean_number(
                model.optPrice
            )
        )

        quote["model_underlying_price"] = (
            _clean_number(
                model.undPrice
            )
        )

    # -----------------------------------------------------
    # MID PRICE
    # -----------------------------------------------------

    bid = quote.get("bid")
    ask = quote.get("ask")

    if (
        bid is not None
        and ask is not None
        and bid >= 0
        and ask >= 0
    ):
        quote["mid"] = round(
            (bid + ask) / 2,
            4
        )

    else:
        quote["mid"] = None

    return quote


# ---------------------------------------------------------
# GET ATM OPTION QUOTES
# ---------------------------------------------------------

def get_atm_option_quotes(symbol: str):
    """
    Fetch live ATM CALL and PUT market data.

    Returns:
        bid
        ask
        mid
        last
        volume
        IV
        Delta
        Gamma
        Vega
        Theta

    Open Interest is also requested where IBKR
    provides it for the subscribed market data.
    """

    _ensure_event_loop()

    symbol = symbol.upper()

    # -----------------------------------------------------
    # GET VERIFIED CALL + PUT CONTRACTS
    # -----------------------------------------------------

    contracts = get_atm_option_contracts(
        symbol
    )

    if contracts.get("status") != "OK":

        return {
            "symbol":
                symbol,

            "status":
                contracts.get(
                    "status",
                    "CONTRACT_ERROR"
                )
        }

    call_data = contracts.get(
        "call"
    )

    put_data = contracts.get(
        "put"
    )

    if not call_data or not put_data:

        return {
            "symbol":
                symbol,

            "status":
                "MISSING_OPTION_CONTRACT"
        }

    # -----------------------------------------------------
    # BUILD CONTRACT OBJECTS
    # -----------------------------------------------------

    call = _build_option_contract(
        call_data
    )

    put = _build_option_contract(
        put_data
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
        # QUALIFY USING VERIFIED CONTRACT DETAILS
        # -------------------------------------------------

        qualified = ib.qualifyContracts(
            call,
            put
        )

        if len(qualified) < 2:

            return {
                "symbol":
                    symbol,

                "status":
                    "QUOTE_CONTRACT_QUALIFICATION_FAILED",

                "qualified_count":
                    len(qualified)
            }

        call = qualified[0]
        put = qualified[1]

        # -------------------------------------------------
        # REQUEST MARKET DATA
        #
        # Generic tick 100:
        # option volume
        #
        # Generic tick 101:
        # option open interest
        #
        # Generic tick 106:
        # option implied volatility
        #
        # Greeks are normally included in
        # option computation/model data.
        # -------------------------------------------------

        generic_ticks = "100,101,106"

        call_ticker = ib.reqMktData(
            call,
            generic_ticks,
            False,
            False
        )

        put_ticker = ib.reqMktData(
            put,
            generic_ticks,
            False,
            False
        )

        # Allow IBKR time to populate
        # market data and Greeks.
        ib.sleep(3)

        # -------------------------------------------------
        # EXTRACT DATA
        # -------------------------------------------------

        call_quote = _extract_quote(
            call_ticker
        )

        put_quote = _extract_quote(
            put_ticker
        )

        # -------------------------------------------------
        # CANCEL STREAMS
        # -------------------------------------------------

        ib.cancelMktData(
            call
        )

        ib.cancelMktData(
            put
        )

        # -------------------------------------------------
        # RETURN
        # -------------------------------------------------

        return {
            "symbol":
                symbol,

            "status":
                "OK",

            "underlying_price":
                contracts.get(
                    "underlying_price"
                ),

            "requested_expiration":
                contracts.get(
                    "requested_expiration"
                ),

            "selected_strike":
                contracts.get(
                    "selected_strike"
                ),

            "call_contract":
                call_data,

            "put_contract":
                put_data,

            "call":
                call_quote,

            "put":
                put_quote
        }

    finally:

        if ib.isConnected():
            ib.disconnect()