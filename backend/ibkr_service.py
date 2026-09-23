import asyncio
asyncio.set_event_loop(asyncio.new_event_loop())
from ib_insync import IB, Stock


IBKR_HOST = "127.0.0.1"
IBKR_PORT = 7496
IBKR_CLIENT_ID = 31


def create_ib_connection():
    asyncio.set_event_loop(asyncio.new_event_loop())

    ib = IB()

    ib.connect(
        IBKR_HOST,
        IBKR_PORT,
        clientId=IBKR_CLIENT_ID,
        readonly=True
    )

    return ib


def get_stock_quote(symbol: str):
    ib = create_ib_connection()

    try:
        contract = Stock(
            symbol.upper(),
            "SMART",
            "USD"
        )

        ib.qualifyContracts(contract)

        ticker = ib.reqMktData(
            contract,
            "",
            False,
            False
        )

        ib.sleep(3)

        result = {
            "symbol": symbol.upper(),
            "bid": ticker.bid,
            "ask": ticker.ask,
            "last": ticker.last,
            "close": ticker.close
        }

        ib.cancelMktData(contract)

        return result

    finally:
        ib.disconnect()