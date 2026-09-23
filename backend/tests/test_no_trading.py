"""
This application must never be able to place a trade.

Three independent layers enforce that, and each of these tests covers one of
them, so a regression in any single layer is caught rather than being masked by
the other two:

1. The source contains no order-placement call at all.
2. The IBKR connection is opened readonly=True.
3. Every order method on the live IB object is replaced with one that raises,
   which holds even if (1) or (2) regress or TWS's own Read-Only API setting is
   switched off.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent

# ib_insync entry points that create, modify or cancel an order.
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

# Files allowed to name these: the guard that blocks them, and this test.
ALLOWED = {"ibkr_client.py", "test_no_trading.py"}


def _source_files() -> list[Path]:
    return [
        path for path in BACKEND.rglob("*.py")
        if ".venv" not in path.parts and "__pycache__" not in path.parts
    ]


def test_no_order_placement_call_exists_in_the_source():
    """A trading call must not appear anywhere outside the guard itself."""
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
        "Order-placement API referenced outside the guard: " + ", ".join(offenders)
    )


def test_connection_is_opened_readonly():
    """readonly=True asks TWS itself to refuse orders from this client."""
    source = (BACKEND / "ibkr_client.py").read_text(encoding="utf-8")
    assert "readonly=True" in source, "IBKR connection must be readonly"


def test_every_order_method_is_blocked_on_the_live_object():
    """
    The last line of defence: even holding a connected IB object, calling any
    order method raises instead of reaching TWS.
    """
    import ib_bootstrap  # noqa: F401  (installs the event loop)
    from ib_insync import IB, MarketOrder, Stock

    from ibkr_client import (
        _ORDER_METHODS,
        OrderPlacementBlocked,
        _install_order_block,
    )

    ib = IB()
    present = [name for name in _ORDER_METHODS if hasattr(ib, name)]
    assert present, "expected ib_insync to expose order methods to block"

    _install_order_block(ib)

    for name in present:
        with pytest.raises(OrderPlacementBlocked):
            getattr(ib, name)(Stock("AAPL", "SMART", "USD"), MarketOrder("BUY", 1))


def test_the_guard_is_installed_on_connect():
    """The block must be wired into the connection path, not merely defined."""
    source = (BACKEND / "ibkr_client.py").read_text(encoding="utf-8")
    connect_section = source.split("async def _ensure_connected")[1]
    assert "_install_order_block" in connect_section, (
        "_install_order_block must run inside _ensure_connected, otherwise a "
        "live connection is unguarded"
    )
