"""
Which provider powers which screen, and what happens when it is down.

The settings screen listed providers and whether their keys worked. It never
said what any of them were *for*, so "Twelve Data: rate limited" meant nothing
until the trend parameters silently went blank -- which is exactly what
happened, and took a profiling session to trace back.

This is that map, written down once. It is deliberately hand-maintained
rather than derived: a call graph would list every import, and what a reader
needs is the half-dozen places a provider actually decides what appears on a
screen, plus the honest answer to "what do I lose if this one stops".

Freshness is stated in the same words used elsewhere in the app, because a
provider's value is mostly how old its answer is.
"""

from __future__ import annotations

from typing import Optional

# One row per provider. ``powers`` names the screens and parameters in the
# words they carry in the UI; ``fallback`` is what genuinely happens when the
# provider is unavailable -- "nothing" where that is the truth.
PROVIDERS: list[dict] = [
    {
        "key": "UNUSUAL_WHALES",
        "name": "Unusual Whales",
        "env_var": "UNUSUAL_WHALES_API_KEY",
        "freshness": "Live during the session",
        "cost": "Your subscription",
        "powers": [
            "Quotes, charts, intraday and daily candles",
            "Options tape, flow alerts and the unusual filter",
            "Option chain: quotes, implied volatility and greeks",
            "Open-interest change, IV rank, max pain, volatility surface",
            "Gamma exposure and dealer positioning",
            "Earnings, the expected move, dividends, analyst actions",
            "Insider transactions and institutional ownership",
            "Headlines, fundamentals and the market screener",
            "Dark-pool prints and short interest",
            "Today's intraday parameters: VWAP, opening range, relative volume",
        ],
        "fallback": ("Nothing. This is the only market-data feed: without the "
                     "key, prices, charts, options and earnings all report as "
                     "unconfigured rather than degrading to a weaker source."),
    },
    {
        "key": "SEC",
        "name": "SEC EDGAR",
        "env_var": None,
        "freshness": "Filings appear within about a minute of acceptance",
        "cost": "Free",
        "powers": [
            "Company Events panel (8-K items)",
            "Mergers & Deals, Funding & Debt, Management Changes parameters",
            "Insider & Ownership parameter (Form 4, 13D/13G)",
            "Fund Buying & Selling (13F), and the live 13F watcher",
            "Monthly fund holdings (N-PORT)",
        ],
        "fallback": "No substitute. These parameters report as missing.",
    },
    {
        "key": "SPDR",
        "name": "State Street SPDR daily holdings",
        "env_var": None,
        "freshness": "Daily, published each evening",
        "cost": "Free",
        "powers": ["ETF holdings, daily -- the fastest ownership data there is"],
        "fallback": "The daily line disappears; 13F and N-PORT remain.",
    },
    {
        "key": "NASDAQ",
        "name": "Nasdaq dividends",
        "env_var": None,
        "freshness": "Same day",
        "cost": "Free, undocumented endpoint",
        "powers": ["Dividends panel", "Dividend Trend parameter"],
        "fallback": "Benzinga's dividend calendar, which covers fewer symbols.",
    },
    {
        "key": "ANTHROPIC",
        "name": "Claude",
        "env_var": "ANTHROPIC_API_KEY",
        "freshness": "On demand",
        "cost": "Per request, only when you press a button",
        "powers": [
            "Explain a call in plain English",
            "What a parameter means for this company",
            "News tone summaries",
        ],
        "fallback": ("Those buttons report why they could not answer. Claude "
                     "never supplies prices, scores or decisions -- it only "
                     "puts the app's own figures into words."),
    },
]


def _status_of(key: str) -> dict:
    """Ask each provider for its own state, where it can answer."""
    try:
        if key == "UNUSUAL_WHALES":
            import unusualwhales_service as uw

            return uw.provider_status()
        if key == "ANTHROPIC":
            import claude_service as claude

            return ({"status": "OK", "detail": "Key present; asked only on demand."}
                    if claude.configured()
                    else {"status": "NOT_CONFIGURED",
                          "detail": "ANTHROPIC_API_KEY is not set."})
        # Public feeds: no key to check, so the honest status is that they
        # need none. Reporting them as UNKNOWN made free sources look broken
        # next to paid ones that merely had a key on file.
        if key in ("SEC", "SPDR", "NASDAQ", "YAHOO_RSS"):
            return {"status": "OK", "detail": "Public feed; no key required."}

        import provider_config as cfg

        for spec in cfg.ALL_PROVIDERS:
            if spec.name.upper().replace(" ", "_") == key or spec.key_env.startswith(key):
                state = spec.status()
                return {"status": state.get("status"), "detail": state.get("detail")}
    except Exception as exc:  # noqa: BLE001 - a status must never break the page
        return {"status": "UNKNOWN", "detail": type(exc).__name__}
    return {"status": "UNKNOWN", "detail": None}


def usage(with_status: bool = True) -> dict:
    """The provider map, optionally with each provider's current state."""
    rows = []
    for provider in PROVIDERS:
        row = dict(provider)
        if with_status:
            state = _status_of(provider["key"])
            row["status"] = state.get("status")
            row["status_detail"] = state.get("detail")
        rows.append(row)
    return {
        "status": "OK",
        "providers": rows,
        "count": len(rows),
        "detail": ("What each provider powers, how fresh its data is, and "
                   "what the app does without it. Written down by hand: it "
                   "describes decisions, not imports."),
    }
