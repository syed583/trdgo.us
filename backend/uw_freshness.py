"""How fast each kind of Unusual Whales data really changes -- and so how long one answer may be
reused by EVERY module that asks for it.

The client keeps a request-level memo under all the module caches: two modules asking for the
same endpoint with the same parameters inside the window share one provider call. The window
depends on the data, not on one universal number:

    prices / quotes ........ seconds         (a live price must not be reused)
    options flow ........... tens of seconds (prints keep arriving)
    options positioning .... minutes         (greeks, levels, chains)
    earnings / fundamentals  hours           (quarterly information)
    ownership / insiders ... half a day      (filings, short interest)

so ownership and earnings data are never re-fetched every few minutes just because some module
has a short cache of its own, and a live price is never served stale.
"""
from __future__ import annotations

import re
from typing import Optional

import tab_settings as cfg

# (compiled pattern, data type) -- first match wins. Order matters.
_RULES = [
    (r"/api/stock/[^/]+/quote$", "quote"),
    (r"/api/stock/[^/]+/ohlc/(1m|5m|15m|30m|1h)$", "bars_intraday"),
    (r"/api/stock/[^/]+/ohlc/", "bars_daily"),
    (r"/api/option-activity/unusual|/api/stock/[^/]+/(flow-alerts|flow-recent|net-prem-ticks)|"
     r"/api/option-trades/flow-alerts|/api/market/market-tide|/api/darkpool/", "options_flow"),
    (r"/api/stock/[^/]+/(options-volume|stock-volume-price-levels)", "options_volume"),
    (r"/api/stock/[^/]+/(option-contracts|greek-exposure|max-pain|iv-rank|oi-change|volatility|"
     r"option/volume-oi-expiry|option-chains|expiry-breakdown)", "options_positioning"),
    (r"/api/(companies|earnings)/|/api/stock/[^/]+/(earnings|fundamental-breakdown|info)|"
     r"/api/screener/analysts|/api/companies/[^/]+/dividends", "earnings_fundamentals"),
    (r"/api/insider/|/api/institution/|/api/shorts/", "ownership_insider"),
    (r"/api/news/", "news"),
]
_COMPILED = [(re.compile(p), t) for p, t in _RULES]


def classify(path: str) -> str:
    for rx, t in _COMPILED:
        if rx.search(path or ""):
            return t
    return "other"


def memo_ttl(path: str) -> float:
    """Seconds an identical request may be answered from the memo (0 = never reuse)."""
    return float(cfg.UW_MEMO_TTL_S.get(classify(path), 0.0))


def type_of_signal(name: str) -> Optional[str]:
    return cfg.SIGNAL_DATA_TYPE.get(name)


def limits(data_type: str) -> tuple[float, float]:
    """(live_s, max_entry_s) for an intraday data type."""
    live, mx = cfg.INTRADAY_FRESHNESS[data_type]
    return float(live), float(mx)
