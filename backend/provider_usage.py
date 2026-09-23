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
        "key": "IBKR",
        "name": "Interactive Brokers (TWS)",
        "env_var": None,
        "freshness": "Live while TWS is running",
        "cost": "Included with your account; market data subscriptions extra",
        "powers": [
            "Live prices, pre-market and after-hours quotes",
            "Charts and daily history",
            "Option chain, gamma exposure and dealer positioning",
            "Today's intraday parameters: VWAP, opening range, relative volume",
        ],
        "fallback": ("Prices fall back to Twelve Data, then Alpha Vantage, "
                     "then the app's own cached chart. Gamma and dealer "
                     "positioning have no fallback and report as missing."),
    },
    {
        "key": "UNUSUAL_WHALES",
        "name": "Unusual Whales",
        "env_var": "UNUSUAL_WHALES_API_KEY",
        "freshness": "Live during the session",
        "cost": "Your subscription",
        "powers": [
            "Options tape, flow alerts and the unusual filter",
            "Open-interest change, IV rank, max pain, volatility surface",
            "Intraday and daily candles when TWS is busy",
            "Earnings history, dividends, analyst actions",
            "Insider transactions and institutional ownership",
            "Headlines, fundamentals and the market screener",
            "Dark-pool prints and short interest",
        ],
        "fallback": ("The free providers it replaces stay in place behind "
                     "it -- Finviz, Benzinga, Nasdaq, Twelve Data, Alpha "
                     "Vantage, Marketaux and Yahoo -- so a lapsed key "
                     "degrades the app rather than blanking it."),
    },
    {
        "key": "FINVIZ",
        "name": "Finviz Elite",
        "env_var": "FINVIZ_AUTH_TOKEN",
        "freshness": "Delayed; their options are 15 minutes behind",
        "cost": "$39.50/month",
        "powers": [
            "Market-wide screening across ~8,000 stocks",
            "Fundamentals: valuation, margins, growth, float",
            "Groups, calendars, insider table, managers and fund holdings",
            "A second opinion on the option chain",
        ],
        "fallback": "Screening and fundamentals are unavailable; nothing else "
                    "in the app depends on it.",
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
        "key": "BENZINGA",
        "name": "Benzinga",
        "env_var": "BENZINGA_API_KEY",
        "freshness": "Daily",
        "cost": "Paid plan already on your account",
        "powers": [
            "Earnings calendar and results history",
            "Merger details behind the SEC tag (who bought whom, for how much)",
            "Analyst ratings feeding Event Radar",
            "Share offerings and dividends where Nasdaq has none",
        ],
        "fallback": "Alpha Vantage for earnings history; merger detail falls "
                    "back to the SEC tag alone.",
    },
    {
        "key": "TWELVE_DATA",
        "name": "Twelve Data",
        "env_var": "TWELVE_DATA_API_KEY",
        "freshness": "End of day, with a live last candle",
        "cost": "Free tier: 800 requests a day",
        "powers": [
            "Daily bars when IBKR is down: EMA / Trend, RSI, Price Action",
            "Quotes when IBKR is down",
        ],
        "fallback": "Alpha Vantage, then the app's own cached chart. Until "
                    "that fallback existed, a spent quota blanked the trend "
                    "parameters on a stock with a year of history in cache.",
    },
    {
        "key": "ALPHA_VANTAGE",
        "name": "Alpha Vantage",
        "env_var": "ALPHA_VANTAGE_API_KEY",
        "freshness": "End of day",
        "cost": "Free tier: 25 requests a day",
        "powers": ["Analyst estimates and revisions",
                   "Daily bars and earnings history as a last resort"],
        "fallback": "The app's own cached chart.",
    },
    {
        "key": "YAHOO_RSS",
        "name": "Yahoo Finance headlines",
        "env_var": None,
        "freshness": "Minutes",
        "cost": "Free",
        "powers": ["News & Sentiment pages", "Headline tone behind Event Radar"],
        "fallback": "Marketaux, when its daily quota allows.",
    },
    {
        "key": "MARKETAUX",
        "name": "Marketaux",
        "env_var": "MARKETAUX_API_TOKEN",
        "freshness": "Minutes",
        "cost": "Free tier: 100 requests a day",
        "powers": ["News with the provider's own per-article sentiment"],
        "fallback": ("Yahoo headlines with a keyword tone estimate. Currently "
                     "the default: NEWS_PROVIDER=yahoo in backend/.env."),
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
        if key == "FINVIZ":
            import finviz_service as finviz

            return finviz.provider_status()
        if key == "IBKR":
            import live_market_service as market

            clock = market.market_clock() or {}
            connected = bool((market.connection_status() or {}).get("connected")
                             if hasattr(market, "connection_status") else None)
            return {"status": "OK" if connected else "OFFLINE",
                    "detail": (f"TWS {'connected' if connected else 'not connected'}"
                               f" · {clock.get('label', '')}")}
        if key == "ANTHROPIC":
            import claude_service as claude

            return ({"status": "OK", "detail": "Key present; asked only on demand."}
                    if claude.configured()
                    else {"status": "NOT_CONFIGURED",
                          "detail": "ANTHROPIC_API_KEY is not set."})
        if key == "MARKETAUX":
            import marketaux_news_service as mx

            if mx.blocked():
                return {"status": "RATE_LIMITED",
                        "detail": ("Daily quota spent or switched off; "
                                   "headlines come from Yahoo.")}
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
