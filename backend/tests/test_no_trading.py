"""
This application must never be able to place a trade.

It used to hold a live broker connection, and three layers stood between that
connection and an order: no order call anywhere in the source, ``readonly=True``
on the socket, and every order method on the live IB object replaced with one
that raises.

The broker connection is gone. There is no socket to a brokerage anywhere in
this codebase now, which makes the guarantee structural rather than enforced:
the app reads market data over HTTP and has nothing to place an order *with*.

These tests hold that line. The first is the same check as before, now with
no exemptions at all. The second is the one that matters after the removal --
that no order-capable broker library is imported back in without this test
failing first.
"""

from __future__ import annotations

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent

# Entry points that create, modify or cancel an order in the broker libraries
# this project has used.
FORBIDDEN_CALLS = (
    "placeOrder",
    "cancelOrder",
    "reqGlobalCancel",
    "exerciseOptions",
    "bracketOrder",
    "oneCancelsAll",
    "MarketOrder",
    "LimitOrder",
    "StopOrder",
    "StopLimitOrder",
)

# Libraries that can reach a brokerage.
BROKER_LIBRARIES = ("ib_insync", "ibapi", "alpaca", "tradier", "robin_stocks")

# Only this test may name them.
ALLOWED = {"test_no_trading.py"}


def _source_files() -> list[Path]:
    return [
        path for path in BACKEND.rglob("*.py")
        if ".venv" not in path.parts and "__pycache__" not in path.parts
    ]


def test_no_order_placement_call_exists_in_the_source():
    """A trading call must not appear anywhere in the source."""
    offenders: list[str] = []
    for path in _source_files():
        if path.name in ALLOWED:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for symbol in FORBIDDEN_CALLS:
            # Word boundary so `order_by` and similar never match.
            if re.search(rf"\b{symbol}\b", text):
                offenders.append(f"{path.name}: {symbol}")
    assert not offenders, (
        "Order-placement API referenced in the source: " + ", ".join(offenders)
    )


def test_no_broker_library_is_imported():
    """
    Nothing here may import a library that can reach a brokerage.

    This is the check that replaces the readonly-socket and order-block
    tests. Those guarded a connection that existed; this one asserts the
    connection does not come back. If a broker client is ever reintroduced,
    this fails first and the three layers above have to be restored with it.
    """
    offenders: list[str] = []
    for path in _source_files():
        if path.name in ALLOWED:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for library in BROKER_LIBRARIES:
            if re.search(rf"^\s*(import|from)\s+{library}\b", text, re.M):
                offenders.append(f"{path.name}: {library}")
    assert not offenders, (
        "Broker library imported: " + ", ".join(offenders)
    )
