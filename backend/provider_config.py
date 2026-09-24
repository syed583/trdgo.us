"""
The status vocabulary every service speaks.

This file was once the shared HTTP layer for a row of free data providers --
Benzinga, Alpha Vantage, Twelve Data, Marketaux -- with a generic
``fetch_json``, key redaction, per-provider cooldowns and a configuration
matrix. Those providers were removed; the one paid feed and the public SEC /
Nasdaq / SPDR sources each carry their own client now, so all of that
plumbing went with them.

What remains, and why it stays here, is the shared status vocabulary. A
service does not return a bare ``None`` when it cannot answer -- it returns
one of these strings, so a screen can tell "you have not configured this"
(``PROVIDER_NOT_CONFIGURED``) apart from "the provider is down"
(``PROVIDER_OFFLINE``) apart from "there is genuinely no data"
(``DATA_UNAVAILABLE``). Keeping the words in one place keeps them spelled the
same everywhere they are compared.
"""

from __future__ import annotations

# --- status vocabulary -----------------------------------------------------
OK = "OK"
PROVIDER_NOT_CONFIGURED = "PROVIDER_NOT_CONFIGURED"
PROVIDER_OFFLINE = "PROVIDER_OFFLINE"
ENTITLEMENT_REQUIRED = "ENTITLEMENT_REQUIRED"
DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
PARTIAL_DATA = "PARTIAL_DATA"
RATE_LIMITED = "RATE_LIMITED"
TEST_DATA = "TEST_DATA"


def provider_matrix() -> dict:
    """
    Configuration state of the keyed providers, for the Settings screen.

    There are no longer any providers configured through this module -- the
    one paid feed reads its own key and reports for itself (see
    ``/api/providers`` in api_routes). This returns an empty matrix that the
    route then fills in, kept as the seam so the route does not have to know
    the list is currently empty.
    """
    return {}
